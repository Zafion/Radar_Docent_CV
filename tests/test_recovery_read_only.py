import asyncio
import unittest
from unittest.mock import patch

import httpx
import psycopg
from psycopg.rows import dict_row

from app.api import main
from tests.postgres_isolated_validation import FreshCluster


class RecoveryReadOnlyTests(unittest.TestCase):
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

    def test_real_postgres_role_and_writes(self):
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True,row_factory=dict_row) as db:
                cluster.verify(db)
                db.execute('CREATE ROLE recovery_reader NOLOGIN')
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
