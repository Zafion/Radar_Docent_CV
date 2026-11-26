"""Synthetic sequencing checks; these do not validate the real adapters."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from tests.postgres_isolated_validation import FreshCluster
from app.services.historical_cumulative_reconcile import reconcile_cumulative


class CumulativeOrchestrationTests(unittest.TestCase):
    def test_plan_order_and_independent_failure(self):
        with FreshCluster() as cluster, tempfile.TemporaryDirectory() as temp:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn('source_fixture'),row_factory=dict_row) as src, psycopg.connect(cluster.dsn(),row_factory=dict_row) as dst:
                keys=('700','436','701','expert','1000067','1000444','offer1','offer2')
                specs={}
                for key in keys:
                    path=Path(temp)/key;path.write_bytes(key.encode())
                    specs[key]={'document_sha':hashlib.sha256(path.read_bytes()).hexdigest(),'pdf_path':path}
                specs['expert'].update(procedure_key='expertos-2026-11-05',provenance={'html_bytes':b'','html_sha256':'a','page_url':'p','document_url':'d'})
                docs={k:specs[k] for k in keys[:6]};offers=[specs['offer1'],specs['offer2']]
                self.assertEqual(reconcile_cumulative(src,dst,documents=docs,offers=offers,synthetic_token=cluster.token)['status'],'planned')
                calls=[];progress=[]
                def maestros(*a,**kw):
                    calls.append(kw['document_sha'])
                    if kw['document_sha']==docs['436']['document_sha']:raise ValueError('fixture')
                    return {'inserted':1}
                def secondary(*a,**kw):calls.append(kw['document_sha']);return {'inserted':1}
                def expert(*a,**kw):calls.append(kw['sha256']);return {'inserted':1}
                def nov(*a,**kw):
                    calls.extend((kw['maestros']['document_sha'],kw['secondary']['document_sha']))
                    return {'documents':{'maestros':{'status':'applied'},'secondary':{'status':'applied'}},'offers':[]}
                with patch('app.services.historical_cumulative_reconcile.reconcile_unawarded',side_effect=maestros), \
                     patch('app.services.historical_cumulative_reconcile.reconcile_secondary',side_effect=secondary), \
                     patch('app.services.historical_cumulative_reconcile.reconcile_expert_procedure',side_effect=expert), \
                     patch('app.services.historical_cumulative_reconcile.reconcile_19nov',side_effect=nov), \
                     patch('app.services.historical_cumulative_reconcile._link_procedure',return_value={'status':'inserted'}):
                    report=reconcile_cumulative(src,dst,documents=docs,offers=offers,execute=True,synthetic_token=cluster.token,progress=lambda p:progress.append(p['status']))
                self.assertEqual(calls,[docs[k]['document_sha'] for k in keys[:6]])
                self.assertEqual(report['documents']['436']['status'],'failed')
                self.assertEqual(report['documents']['701']['status'],'applied')
                self.assertEqual(report['status'],'partial');self.assertGreater(len(progress),3)
