import asyncio
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock

import httpx
import psycopg
from psycopg.rows import dict_row

from app.api import main
from tests.postgres_isolated_validation import FreshCluster


class RecoveryReadOnlyTests(unittest.TestCase):
    def test_recovery_historical_accreditation_is_not_current_availability(self):
        item={'historical_status':'accredited','availability_status':'available'}
        with patch.object(main,'RECOVERY_READ_ONLY',True):
            main.mask_unaccredited_availability([item])
        self.assertEqual(item['availability_status'],'unknown')

    def test_recovery_non_docent_does_not_treat_legacy_default_as_current(self):
        conn=MagicMock();conn.execute.return_value.fetchone.return_value=(0,);conn.execute.return_value.fetchall.return_value=[]
        context=MagicMock();context.__enter__.return_value=conn
        with patch.object(main,'RECOVERY_READ_ONLY',True),patch.object(main,'get_connection',return_value=context),patch.object(main,'_register_normalize_function'):
            response=main.list_non_docent_positions(q=None,include_unavailable=False,order_dir='asc',limit=10,offset=0)
        self.assertEqual(response['total'],0)
        self.assertTrue(any('1=0' in str(c.args[0]) for c in conn.execute.call_args_list))

    def test_recovery_non_docent_publication_does_not_claim_current(self):
        conn=MagicMock();conn.execute.return_value.fetchone.return_value=(1,)
        conn.execute.return_value.fetchall.return_value=[{'availability_status':'available','is_current':True}]
        context=MagicMock();context.__enter__.return_value=conn
        with patch.object(main,'RECOVERY_READ_ONLY',True),patch.object(main,'get_connection',return_value=context),patch.object(main,'_register_normalize_function'):
            response=main.list_non_docent_publications(q=None,from_date=None,to_date=None,order_dir='asc',limit=10,offset=0)
        self.assertEqual(response['items'][0]['availability_status'],'unknown')
        self.assertIsNone(response['items'][0]['is_current'])

    def test_recovery_banner_is_localized_and_omits_external_fonts(self):
        from app.web.routes import TEMPLATES
        request=MagicMock();request.url_for.side_effect=lambda name,**kw:'/static/'+kw.get('path','')
        with patch.dict('os.environ',{'FUNK_RECOVERY_READ_ONLY':'1'}):
            template=TEMPLATES.get_template('base.html')
            es=template.render(request=request,current_lang='es')
            va=template.render(request=request,current_lang='va')
        self.assertIn('Consulta histórica: los datos pueden estar desactualizados',es)
        self.assertIn('Consulta històrica: les dades poden estar desactualitzades',va)
        self.assertNotIn('fonts.googleapis.com',es)

    def test_recovery_position_templates_default_to_historical_view(self):
        from app.web.routes import TEMPLATES
        request=MagicMock();request.url_for.side_effect=lambda name,**kw:'/static/'+kw.get('path','')
        with patch.dict('os.environ',{'FUNK_RECOVERY_READ_ONLY':'1'}):
            for filename,prefix in (('offered_positions.html','op'),('difficult_coverage.html','dc')):
                html=TEMPLATES.get_template(filename).render(request=request,current_lang='es')
                self.assertRegex(html,rf'id="{prefix}-include-history" type="checkbox"\s+checked')
                self.assertNotRegex(html,rf'id="{prefix}-latest-only" type="checkbox"\s+checked')
    def test_startup_refuses_enabled_notifications(self):
        with patch.object(main,'RECOVERY_READ_ONLY',True),patch.object(main,'DB_URL','postgresql://example.invalid/db'),patch.object(main,'get_pool'),patch.dict('os.environ',{'ENABLE_NOTIFICATIONS':'1'}):
            with self.assertRaisesRegex(RuntimeError,'notifications_must_be_disabled'):
                main.startup_event()

    def test_http_denies_mutations(self):
        async def check():
            with patch.object(main,'RECOVERY_READ_ONLY',True):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://local') as client:
                    for method in ('POST','PUT','PATCH','DELETE'):
                        response=await client.request(method,'/api/push/subscribe',json={})
                        self.assertEqual(response.status_code,405)
                        self.assertEqual(response.json()['detail'],'read_only_recovery_mode')
        asyncio.run(check())

    def test_health_fails_if_search_normalization_is_unavailable(self):
        conn=MagicMock();conn.execute.side_effect=psycopg.errors.InsufficientPrivilege('denied')
        context=MagicMock();context.__enter__.return_value=conn
        with patch.object(main,'RECOVERY_READ_ONLY',True),patch.object(main,'get_connection',return_value=context),patch.object(main,'_register_normalize_function'):
            with self.assertRaises(main.HTTPException) as error:
                main.health()
            self.assertEqual(error.exception.status_code,503)

    def test_real_postgres_role_and_writes(self):
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True,row_factory=dict_row) as db:
                cluster.verify(db)
                db.execute('CREATE ROLE recovery_reader NOLOGIN')
                db.execute('REVOKE TEMPORARY ON DATABASE postgres FROM PUBLIC')
                db.execute("""DO $$ DECLARE f record; BEGIN FOR f IN
                  SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                  WHERE n.nspname='pg_catalog' AND p.proname IN ('pg_notify','lo_create','lo_creat','lo_import','lo_unlink','lo_put')
                  LOOP EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',f.fn); END LOOP; END $$""")
                db.execute('CREATE TABLE public.recovery_fixture (id integer)')
                db.execute('GRANT USAGE ON SCHEMA public TO recovery_reader')
                db.execute('GRANT SELECT ON public.recovery_fixture TO recovery_reader')
                with self.assertRaisesRegex(RuntimeError,'elevated'):
                    main.verify_recovery_database_role(db)
                db.execute('SET ROLE recovery_reader')
                db.execute('SET default_transaction_read_only=on')
                main.verify_recovery_database_role(db)
                with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                    db.execute('INSERT INTO public.recovery_fixture VALUES (1)')
                db.execute('SET default_transaction_read_only=off')
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    db.execute('INSERT INTO public.recovery_fixture VALUES (1)')
                with self.assertRaisesRegex(RuntimeError,'transaction_not_read_only'):
                    main.verify_recovery_database_role(db)
                db.execute('RESET ROLE')
                db.execute('GRANT INSERT ON public.recovery_fixture TO recovery_reader')
                db.execute('SET ROLE recovery_reader')
                db.execute('SET default_transaction_read_only=on')
                with self.assertRaisesRegex(RuntimeError,'write_capabilities'):
                    main.verify_recovery_database_role(db)
                db.execute('RESET ROLE')
                db.execute('SET default_transaction_read_only=off')
                db.execute('REVOKE INSERT ON public.recovery_fixture FROM recovery_reader')
                db.execute('CREATE SEQUENCE public.recovery_fixture_seq')
                db.execute('GRANT USAGE ON SEQUENCE public.recovery_fixture_seq TO recovery_reader')
                db.execute('SET ROLE recovery_reader')
                db.execute('SET default_transaction_read_only=on')
                with self.assertRaisesRegex(RuntimeError,'write_capabilities'):
                    main.verify_recovery_database_role(db)
