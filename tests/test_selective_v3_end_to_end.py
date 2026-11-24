"""Real fetcher v3 -> supervised selective runner on disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone
import contextlib
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from io import BytesIO,StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
import psycopg
from psycopg.rows import dict_row
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject

from app.services.discovery.base import BaseDiscoveryAdapter
from app.services.discovery.http_policy import PolicyClient
from app.services.selective_lock_supervisor import LockSupervisor
from scripts import fetch_ceice_remote as fetcher
from tests.postgres_isolated_validation import FreshCluster,ROOT


def text_pdf(text):
    writer=PdfWriter(); page=writer.add_blank_page(width=1200,height=100)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 10 Tf 20 40 Td ('+text.encode('ascii')+b') Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    output=BytesIO();writer.write(output);return output.getvalue()


class FixtureHandler(BaseHTTPRequestHandler):
    path_name='/doc-a.pdf'
    payload=text_pdf('4-E. TERAPIA OCUPACIONAL 51.95 AE APELLIDO SINTETICO NOMBRE No 123 - CENTRO SINTETICO')
    publication_date=datetime.now(timezone.utc).strftime('%d/%m/%Y')
    def log_message(self,*args): pass
    def do_GET(self):
        if self.path=='/robots.txt':
            self.send_response(200);self.end_headers();self.wfile.write(b'User-agent: *\nAllow: /\n');return
        if self.path=='/page':
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
            label=('Fecha de publicación: '+self.publication_date+' ') if self.publication_date else ''
            self.wfile.write(('<article>'+label+'<a href="'+self.path_name+'">Adjudicacion definitiva ADC</a></article>').encode());return
        if self.path.startswith('/doc-'):
            self.send_response(302);self.send_header('Location','/payload.pdf');self.end_headers();return
        self.send_response(200);self.send_header('Content-Type','application/pdf');self.end_headers();self.wfile.write(self.payload)


class SelectiveV3EndToEnd(unittest.TestCase):
    def test_real_fetcher_to_runner(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def sender(url,headers,limit):
            parsed=urlsplit(url);conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            try:
                conn.request('GET',parsed.path,headers=headers);res=conn.getresponse();body=res.read(limit+1)
                if len(body)>limit:raise ValueError('response_limit')
                return httpx.Response(res.status,headers=dict(res.getheaders()),content=body,request=httpx.Request('GET',url))
            finally:conn.close()
        def policy(ua):
            c=PolicyClient(user_agent=ua);c._resolve=lambda host:'1.1.1.1';c._exchange=lambda url,headers,limit,ip:sender(url,headers,limit);return c
        class Adapter(BaseDiscoveryAdapter):
            source_key='synthetic_fixture';source_url='https://ceice.gva.es/page';source_label='Synthetic'
            def discover_assets(self):
                return self._crawl_seed_urls(seed_urls=[self.source_url],max_depth=0,asset_role='non_docent_adc_award_pdf')
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as c: cluster.verify(c);c.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn(),autocommit=True) as c: cluster.verify(c);c.execute((ROOT/'app/storage/schema.sql').read_text())
            with psycopg.connect(cluster.dsn(),autocommit=True) as c:
                c.execute('CREATE TABLE selective_cycle_authority(scope text PRIMARY KEY,generation bigint NOT NULL,token text NOT NULL,active boolean NOT NULL)')
                c.execute("INSERT INTO selective_cycle_authority VALUES('synthetic-cycle',1,'synthetic-fence-token',true)")
                c.execute('CREATE TABLE selective_baseline(id text PRIMARY KEY,t0 timestamptz NOT NULL,items jsonb NOT NULL,sha256 text NOT NULL)')
                c.execute('''CREATE TABLE selective_baseline_decision(baseline_id text NOT NULL,canonical_url text NOT NULL,
                    sha256 text NOT NULL,first_observed_utc timestamptz NOT NULL,downloaded_utc timestamptz,official_date date,html_sha256 text NOT NULL,
                    decision text NOT NULL,evidence_kind text NOT NULL,PRIMARY KEY(baseline_id,canonical_url,sha256))''')
                from app.services.selective_baseline import baseline_digest
                t0=datetime.now(timezone.utc)-timedelta(days=1)
                historical=text_pdf('historical known'); old_sha=hashlib.sha256(historical).hexdigest()
                items=[{'url':'https://ceice.gva.es/historical.pdf','sha256':old_sha,'exception':False},
                       {'url':'https://ceice.gva.es/recovered.pdf','sha256':None,'exception':True},
                       {'url':'https://ceice.gva.es/correction.pdf','sha256':'e'*64,'exception':False}]
                c.execute('INSERT INTO selective_baseline VALUES(%s,%s,%s::jsonb,%s)',
                    ('synthetic-e2e',t0,json.dumps(items),baseline_digest('synthetic-e2e',t0,items)))
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                sid=c.execute("INSERT INTO sources(source_key,source_url,label) VALUES('old','https://example.invalid','old') RETURNING id").fetchone()['id']
                vid=c.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent) VALUES(%s,'old','old','/absent',1,now(),'unknown') RETURNING id",('f'*64,)).fetchone()['id']
                old_id=c.execute("INSERT INTO documents(document_version_id,source_id,doc_family,title) VALUES(%s,%s,'unknown','old') RETURNING id",(vid,sid)).fetchone()['id']
                old_run=c.execute("INSERT INTO sync_runs(source_id,started_at,status) VALUES(%s,now(),'success') RETURNING id",(sid,)).fetchone()['id']
                c.execute("INSERT INTO assets(source_id,sync_run_id,asset_role,title,url,canonical_url,document_version_id) VALUES(%s,%s,'pdf_candidate','old','https://ceice.gva.es/db-old.pdf','https://ceice.gva.es/db-old.pdf',%s)",(sid,old_run,vid))
                old_before=c.execute('SELECT to_jsonb(d)::text AS row FROM documents d WHERE id=%s',(old_id,)).fetchone()['row']
            locks=[cluster.root/'one.lock',cluster.root/'two.lock']
            for p in locks:p.touch(mode=0o600)
            out=cluster.root/'fetch'
            def fetch():
                adapter=Adapter();adapter._network_client=policy(adapter.headers['User-Agent'])
                with patch.object(fetcher,'select_adapters',return_value=[adapter]),patch('sys.argv',['fetcher','--output-dir',str(out),'--download-delay','0','--redownload-known']),contextlib.redirect_stdout(StringIO()):
                    self.assertEqual(fetcher.main(policy_factory=lambda **kw:policy(fetcher.HEADERS['User-Agent'])),0)
                manifest=out/'current/selective_v3_manifest.json';raw=manifest.read_bytes()
                return manifest,hashlib.sha256(raw).hexdigest()
            env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))}
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),ENABLE_NOTIFICATIONS='0')
            def command(manifest,digest,token='synthetic-fence-token',generation=1):
                return [sys.executable,str(ROOT/'scripts/run_selective_v3.py'),'--synthetic-test-root',str(cluster.root),'--synthetic-token',cluster.token,
                        '--synthetic-fence-token',token,'--synthetic-fence-generation',str(generation),
                        '--synthetic-baseline-id','synthetic-e2e',
                        '--manifest',str(manifest),'--manifest-sha256',digest,'--staging',str(out/'current/selective_v3'),
                        '--lock-one',str(locks[0]),'--lock-two',str(locks[1])]
            def execute(manifest,digest,token='synthetic-fence-token',generation=1):
                guard=LockSupervisor(locks)
                try:
                    with patch.dict(os.environ,env,clear=True):return guard.run_once(command(manifest,digest,token,generation))
                finally:guard.close()
            FixtureHandler.path_name='/doc-a.pdf'
            FixtureHandler.payload=text_pdf('4-E. TERAPIA OCUPACIONAL 51.95 AE APELLIDO SINTETICO NOMBRE No 123 - CENTRO SINTETICO')
            manifest,digest=fetch()
            with patch.dict(os.environ,env,clear=True):
                self.assertEqual(subprocess.run(command(manifest,digest),capture_output=True).returncode,2)
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                self.assertEqual(c.execute('SELECT count(*) AS n FROM document_versions').fetchone()['n'],1)
            # A real SQL failure inside the import transaction must not leave
            # source, run, asset or version rows behind.
            with psycopg.connect(cluster.dsn(),autocommit=True) as c:
                c.execute("CREATE FUNCTION synthetic_fail_asset() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic failure'; END $$")
                c.execute('CREATE TRIGGER synthetic_fail BEFORE INSERT ON assets FOR EACH ROW EXECUTE FUNCTION synthetic_fail_asset()')
            self.assertEqual(execute(manifest,digest),2)
            with psycopg.connect(cluster.dsn(),autocommit=True,row_factory=dict_row) as c:
                self.assertEqual(c.execute('SELECT count(*) AS n FROM document_versions').fetchone()['n'],1)
                self.assertEqual(c.execute("SELECT count(*) AS n FROM sources WHERE source_key='synthetic_fixture'").fetchone()['n'],0)
                c.execute('DROP TRIGGER synthetic_fail ON assets')
            self.assertEqual(execute(manifest,digest),0)
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                saved_decision=c.execute("SELECT to_jsonb(d)::text AS row FROM selective_baseline_decision d WHERE decision='live_new'").fetchone()['row']
                self.assertEqual(c.execute('SELECT count(*) AS n FROM selective_baseline_decision WHERE downloaded_utc>=first_observed_utc AND official_date IS NOT NULL').fetchone()['n'],1)
            self.assertEqual(execute(manifest,digest),0)
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                before=[c.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('document_versions','documents','document_parse_runs','non_docent_awards')]
                self.assertEqual(before,[2,2,1,1])
                self.assertEqual(c.execute('SELECT status FROM document_parse_runs').fetchone()['status'],'success')
                self.assertEqual(c.execute("SELECT to_jsonb(d)::text AS row FROM selective_baseline_decision d WHERE decision='live_new'").fetchone()['row'],saved_decision)
            self.assertEqual(execute(manifest,'0'*64),2)
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                self.assertEqual(c.execute('SELECT count(*) AS n FROM document_versions').fetchone()['n'],2)
            FixtureHandler.payload=text_pdf('4-E. TERAPIA OCUPACIONAL 51.96 AE APELLIDO SINTETICO NOMBRE No 123 - CENTRO SINTETICO')
            manifest,digest=fetch();self.assertEqual(execute(manifest,digest),0)
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                self.assertEqual(c.execute('SELECT count(*) AS n FROM document_versions').fetchone()['n'],2)
                self.assertEqual(c.execute('SELECT count(*) AS n FROM non_docent_awards').fetchone()['n'],1)
                self.assertEqual(c.execute("SELECT count(*) AS n FROM selective_baseline_decision WHERE decision='correction_review'").fetchone()['n'],1)
            FixtureHandler.path_name='/doc-b.pdf';FixtureHandler.payload=text_pdf('UNKNOWN FORMAT')
            manifest,digest=fetch();self.assertEqual(execute(manifest,digest),2)
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                self.assertEqual(c.execute("SELECT count(*) AS n FROM document_parse_runs WHERE status='failed'").fetchone()['n'],1)
                self.assertEqual(c.execute('SELECT count(*) AS n FROM non_docent_awards').fetchone()['n'],1)
                for table in ('public_alerts','notification_posts','push_notification_events'):
                    self.assertEqual(c.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],0)
                self.assertEqual(c.execute('SELECT to_jsonb(d)::text AS row FROM documents d WHERE id=%s',(old_id,)).fetchone()['row'],old_before)
                self.assertEqual(c.execute('SELECT notification_ingest_intent FROM document_versions WHERE id=%s',(vid,)).fetchone()['notification_ingest_intent'],'unknown')
                self.assertEqual(c.execute('SELECT count(*) AS n FROM notification_document_state WHERE document_id=%s',(old_id,)).fetchone()['n'],0)

            # Hold the actual runner's import transaction inside a synthetic
            # trigger, while another connection attempts the official revocation.
            with psycopg.connect(cluster.dsn(),autocommit=True) as c:
                c.execute("""CREATE FUNCTION synthetic_slow_insert() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN PERFORM pg_sleep(2); RETURN NEW; END $$""")
                c.execute('CREATE TRIGGER synthetic_slow BEFORE INSERT ON document_versions FOR EACH ROW EXECUTE FUNCTION synthetic_slow_insert()')
            FixtureHandler.path_name='/doc-c.pdf';FixtureHandler.payload=text_pdf('4-E. TERAPIA OCUPACIONAL 52.01 AE APELLIDO SINTETICO NOMBRE No 123 - CENTRO SINTETICO')
            manifest,digest=fetch(); runner_result=[]; runner_errors=[]
            def run_cycle():
                try:runner_result.append(execute(manifest,digest))
                except BaseException as exc:runner_errors.append(type(exc).__name__)
            runner=threading.Thread(target=run_cycle);runner.start()
            with psycopg.connect(cluster.dsn(),autocommit=True,row_factory=dict_row) as observer:
                for _ in range(150):
                    row=observer.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname='funk_validation' AND wait_event='PgSleep' AND query LIKE '%INSERT INTO document_versions%'").fetchone()
                    if row['n']:break
                    if not runner.is_alive():self.fail('runner_ended_before_transaction_observed')
                    time.sleep(.02)
                else:self.fail('runner_transaction_not_observed')
            revoked=threading.Event(); revoke_errors=[]
            def revoke():
                try:
                    from app.services.selective_db_fence import TransactionAuthorization,revoke_authorization
                    with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                        revoke_authorization(c,TransactionAuthorization('synthetic-cycle',1,'synthetic-fence-token'))
                        c.commit()
                except BaseException as exc:revoke_errors.append(type(exc).__name__)
                finally:revoked.set()
            revoker=threading.Thread(target=revoke);revoker.start()
            self.assertFalse(revoked.wait(.3),'revocation_overtook_active_transaction')
            runner.join(10);revoker.join(10)
            self.assertFalse(runner.is_alive());self.assertFalse(revoker.is_alive())
            self.assertFalse(runner_errors);self.assertFalse(revoke_errors);self.assertTrue(revoked.is_set())
            self.assertEqual(runner_result,[0])
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                authority=c.execute("SELECT active,generation FROM selective_cycle_authority WHERE scope='synthetic-cycle'").fetchone()
                self.assertFalse(authority['active']);self.assertEqual(authority['generation'],2)
                counts=[c.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('document_versions','documents','document_parse_runs','selective_baseline_decision','public_alerts','notification_posts','push_notification_events')]
                self.assertEqual(counts[-3:],[0,0,0])
                current_sha=json.loads(manifest.read_text())['entries'][0]['sha256']
                partial=c.execute('''SELECT v.id,EXISTS(SELECT 1 FROM documents d WHERE d.document_version_id=v.id) AS registered
                    FROM document_versions v WHERE v.sha256=%s''',(current_sha,)).fetchone()
                self.assertIsNotNone(partial);self.assertTrue(partial['registered'])
            # Revocation and generation change block a previously issued token.
            self.assertEqual(execute(manifest,digest),2)
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                self.assertEqual([c.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('document_versions','documents','document_parse_runs','selective_baseline_decision','public_alerts','notification_posts','push_notification_events')],counts)
            from app.services.selective_db_fence import TransactionAuthorization,renew_authorization
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                replacement=renew_authorization(c,TransactionAuthorization('synthetic-cycle',1,'synthetic-fence-token'),'next-synthetic-token')
                c.commit()
            self.assertEqual(execute(manifest,digest),2)  # Old token stays invalid even after renewal.
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                self.assertEqual([c.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('document_versions','documents','document_parse_runs','selective_baseline_decision','public_alerts','notification_posts','push_notification_events')],counts)
            self.assertEqual(execute(manifest,digest,replacement.token,replacement.generation),0)
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                self.assertEqual(c.execute('SELECT count(*) AS n FROM document_versions').fetchone()['n'],counts[0])
                self.assertEqual(c.execute('SELECT count(*) AS n FROM documents').fetchone()['n'],counts[1])
                self.assertEqual(c.execute('SELECT count(*) AS n FROM public_alerts').fetchone()['n'],0)
            cases=[
                ('/historical.pdf',historical,datetime.now(timezone.utc).strftime('%d/%m/%Y'),'historical'),
                ('/recovered.pdf',text_pdf('recovered'),datetime.now(timezone.utc).strftime('%d/%m/%Y'),'historical_recovered'),
                ('/correction.pdf',text_pdf('changed'),datetime.now(timezone.utc).strftime('%d/%m/%Y'),'correction_review'),
                ('/db-old.pdf',text_pdf('changed unknown history'),datetime.now(timezone.utc).strftime('%d/%m/%Y'),'correction_review'),
                ('/old-date.pdf',text_pdf('old date'),(t0-timedelta(days=1)).strftime('%d/%m/%Y'),'historical_recovered'),
                ('/ambiguous.pdf',text_pdf('same day'),t0.strftime('%d/%m/%Y'),'date_precision_review'),
                ('/undated.pdf',text_pdf('no date'),None,'provenance_review'),
                ('/alias.pdf',text_pdf('4-E. TERAPIA OCUPACIONAL 51.95 AE APELLIDO SINTETICO NOMBRE No 123 - CENTRO SINTETICO'),
                 datetime.now(timezone.utc).strftime('%d/%m/%Y'),'identity_review'),
            ]
            for path,payload,pubdate,expected in cases:
                FixtureHandler.path_name=path;FixtureHandler.payload=payload;FixtureHandler.publication_date=pubdate
                # Every fixture document path is redirected to the synthetic PDF.
                manifest,digest=fetch()
                self.assertEqual(execute(manifest,digest,replacement.token,replacement.generation),0)
                with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                    decision=c.execute('SELECT decision,to_jsonb(d)::text AS row FROM selective_baseline_decision d WHERE baseline_id=%s AND canonical_url=%s',
                        ('synthetic-e2e','https://ceice.gva.es'+path)).fetchone()
                    self.assertEqual(decision['decision'],expected)
                    saved=decision['row']
                    for table in ('public_alerts','notification_posts','push_notification_events'):
                        self.assertEqual(c.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],0)
                self.assertEqual(execute(manifest,digest,replacement.token,replacement.generation),0)
                with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                    self.assertEqual(c.execute('SELECT to_jsonb(d)::text AS row FROM selective_baseline_decision d WHERE baseline_id=%s AND canonical_url=%s',
                        ('synthetic-e2e','https://ceice.gva.es'+path)).fetchone()['row'],saved)


if __name__=='__main__':unittest.main()
