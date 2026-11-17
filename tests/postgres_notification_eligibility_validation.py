"""F07 integration: fresh owned PostgreSQL only, no DSN or real transport."""
import argparse
from contextlib import ExitStack,redirect_stdout
from datetime import datetime,timezone,timedelta
import io,json,os,hashlib
from pathlib import Path
import sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests import postgres_isolated_validation as isolated

class Checks:
    def __init__(self,db,output):self.db=db;self.output=output;self.checks=[];self.seq=0
    def check(self,name,ok):
        self.checks.append({'name':name,'passed':bool(ok)})
        tmp=self.output/'checks.tmp';tmp.write_text(json.dumps(self.checks,indent=2)+'\n');tmp.replace(self.output/'live_checks.json')
    def scalar(self,sql,args=()):return self.db.execute(sql,args).fetchone()[0]
    def doc(self,family='non_docent_adc_award',intent='live_new',official=None,url=None):
        from app.storage.notification_eligibility_store import register_detection
        self.seq+=1;n=self.seq
        from tests.document_fixtures import pdf_bytes
        content=pdf_bytes('eligibility-'+str(n));path=self.output/('synthetic-'+str(n)+'.pdf');path.write_bytes(content)
        digest=hashlib.sha256(content).hexdigest()
        self.db.execute("INSERT INTO sources(source_key,source_url,label) VALUES('synthetic','https://example.invalid','synthetic') ON CONFLICT DO NOTHING")
        vid=self.scalar('''INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent)
            VALUES(%s,'synthetic.pdf',%s,%s,%s,now(),%s) RETURNING id''',(digest,f'synthetic-{n}.pdf',str(path),len(content),intent))
        did=self.scalar('''INSERT INTO documents(document_version_id,source_id,doc_family,title) VALUES(%s,
            (SELECT id FROM sources WHERE source_key='synthetic'),%s,'synthetic') RETURNING id''',(vid,family))
        url=url or f'https://example.invalid/{n}.pdf'
        self.db.execute('''INSERT INTO sync_runs(source_id,started_at,status) VALUES((SELECT id FROM sources WHERE source_key='synthetic'),now(),'success')''')
        self.db.execute('''INSERT INTO assets(source_id,sync_run_id,asset_role,title,url,canonical_url,document_version_id)
            VALUES((SELECT id FROM sources WHERE source_key='synthetic'),(SELECT max(id) FROM sync_runs),'pdf_candidate','synthetic',%s,%s,%s)''',(url,url,vid))
        register_detection(self.db,did,vid,url,official or datetime.now(timezone.utc).strftime('%d/%m/%Y'),intent)
        self.db.commit();return did,vid


def phase_a(c):
    from app.storage.notification_eligibility_store import register_detection,processing_started,processing_unsuccessful
    d,v=c.doc();register_detection(c.db,d,v,'https://example.invalid/other',None,'historical_import');c.db.commit()
    c.check('immutable_detection',c.scalar('SELECT intent FROM notification_document_state WHERE document_id=%s',(d,))=='live_new')
    c.check('deduplicated_detection',c.scalar('SELECT count(*) FROM notification_state_history WHERE document_id=%s',(d,))==1)
    processing_started(c.db,v)
    run=c.scalar("INSERT INTO document_parse_runs(document_version_id,parser_key,parser_version,status,started_at) VALUES(%s,'synthetic','1','failed',now()) RETURNING id",(v,))
    processing_unsuccessful(c.db,run,'failed');c.db.commit()
    c.check('state_trace_preserved',c.scalar('SELECT count(*) FROM notification_state_history WHERE document_id=%s',(d,))==3)
    c.check('no_queues_from_detection',c.scalar('SELECT count(*) FROM public_alerts')==0 and c.scalar('SELECT count(*) FROM push_notification_events')==0)
    again,_=c.doc(url='https://example.invalid/1.pdf')
    c.check('known_url_new_hash_marked_correction',c.scalar('SELECT corrected_url FROM notification_document_state WHERE document_id=%s',(again,)))
    sql=(ROOT/'app/storage/migrations/20261105_notification_eligibility.sql').read_text();c.db.execute(sql);c.db._conn.execute((ROOT/'app/storage/migrations/20261106_notification_dispatch.sql').read_text());c.db.commit()
    c.check('additive_migration_repeat_preserves_history',c.scalar('SELECT count(*) FROM notification_state_history WHERE document_id=%s',(d,))==3)


def parse_document(c,did,text=None):
    from app.services.non_docent_parser import NonDocentParserService
    from app.storage.non_docent_store import NonDocentStore
    from tests.test_non_docent_extraction_safety import AWARD
    original=NonDocentStore.list_documents_for_parser
    def selected(store,**kw):return [d for d in original(store,**kw) if d['document_id']==did]
    from app.services.parse_plan import preview
    from app.services.parse_selection import selection_scope,selection_from_plan,Selection
    digest=c.scalar('SELECT v.sha256 FROM document_versions v JOIN documents d ON d.document_version_id=v.id WHERE d.id=%s',(did,))
    ordinary=preview(shas=[digest])
    selection=Selection() if ordinary['entries'] else selection_from_plan(preview(shas=[digest],reprocess=True))
    with selection_scope(selection),patch.object(NonDocentStore,'list_documents_for_parser',selected),patch.object(NonDocentParserService,'_extract_pages_text',return_value=[AWARD if text is None else text]):
        result=NonDocentParserService().parse_all_documents()
    c.db.commit();return result


def phase_b(c):
    from app.services.notification_eligibility import reconcile_notifications
    from tests.test_non_docent_extraction_safety import AWARD
    d,v=c.doc();c.check('detection_no_available_notice',c.scalar('SELECT count(*) FROM public_alerts')==0)
    c.check('parser_success_attests',parse_document(c,d)[0]['status']=='success' and c.scalar('SELECT count(*) FROM notification_validation_attestations WHERE document_id=%s',(d,))==1)
    c.check('parser_commit_does_not_enqueue',c.scalar('SELECT count(*) FROM public_alerts')==0)
    # Recovery after the parser committed but before the reconciler ever ran.
    reconcile_notifications();c.db.commit()
    key=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(d,))
    c.check('recover_after_parse_commit',bool(key) and c.scalar('SELECT count(*) FROM public_alerts WHERE event_key=%s',(key,))==1)
    c.check('validated_eligible_queued_are_distinct_in_history',all(c.scalar('SELECT count(*) FROM notification_state_history WHERE document_id=%s AND phase=%s',(d,state))>=1 for state in ('detected','processing','validated','eligible','queued')))
    reconcile_notifications();c.db.commit()
    c.check('reconcile_idempotent',c.scalar('SELECT count(*) FROM public_alerts WHERE event_key=%s',(key,))==1)
    for label,kwargs,text in [
        ('historical',{'intent':'historical_import'},None),('unknown_intent',{'intent':'unknown'},None),
        ('unknown_family',{'family':'unknown'},None),('failed',{},'UNKNOWN FORMAT'),
        ('empty',{},'ADJUDICACION: No hay adjudicatarios.'),('partial',{},AWARD+' No 1234567 - CENTRO'),
        ('old_publication',{'official':'01/01/2020'},None),('correction',{'url':'https://example.invalid/1.pdf'},None)]:
        doc,_=c.doc(**kwargs)
        if label!='unknown_family':parse_document(c,doc,text)
        reconcile_notifications();c.db.commit()
        c.check(label+'_never_queued',c.scalar('SELECT queued_event_key IS NULL FROM notification_document_state WHERE document_id=%s',(doc,)))
    # An old success is not recertified as new by changing a parser version.
    old,vid=c.doc()
    c.db.execute("INSERT INTO document_parse_runs(document_version_id,parser_key,parser_version,status,started_at,rows_extracted) VALUES(%s,'old','old','success',now(),1)",(vid,));c.db.commit()
    parse_document(c,old);reconcile_notifications();c.db.commit()
    c.check('old_success_reprocess_not_new',c.scalar("SELECT reason FROM notification_document_state WHERE document_id=%s",(old,))=='explicit_reprocess')
    # Failure between outbox inserts must roll back the entire eligibility enqueue.
    crash,_=c.doc();parse_document(c,crash)
    from app.storage import push_event_store
    with patch.object(push_event_store,'enqueue_push_notification_event',side_effect=RuntimeError('synthetic_outbox_failure')):
        try:reconcile_notifications()
        except RuntimeError:pass
        else:raise AssertionError('injection_not_reached')
    c.db.commit()
    c.check('outbox_failure_atomic',c.scalar('SELECT queued_event_key IS NULL FROM notification_document_state WHERE document_id=%s',(crash,)) and c.scalar('SELECT count(*) FROM public_alerts')==1)
    reconcile_notifications();c.db.commit()
    c.check('outbox_failure_recoverable',c.scalar('SELECT queued_event_key IS NOT NULL FROM notification_document_state WHERE document_id=%s',(crash,)))
    # A failed/partial newer parse revokes the gate without deleting the old event.
    from app.storage.non_docent_store import NonDocentStore
    store=NonDocentStore();rid=store.create_parse_run(document_version_id=v,parser_key='synthetic_replay',parser_version='2',started_at=datetime.now(timezone.utc).isoformat());store.connection.commit()
    store.finish_parse_run(parse_run_id=rid,finished_at=datetime.now(timezone.utc).isoformat(),status='failed',rows_extracted=0,error_message='synthetic');store.connection.commit();store.close()
    from app.storage.notification_eligibility_store import eligible_event_sql
    c.check('failed_replay_revokes_gate',not c.scalar('SELECT '+eligible_event_sql('%s'),(key,)))
    c.check('failed_replay_keeps_original_event',c.scalar('SELECT count(*) FROM public_alerts WHERE event_key=%s',(key,))==1)


def phase_c(c):
    import asyncio,httpx
    from app.services.notification_eligibility import reconcile_notifications
    from app.storage.public_alert_store import (enqueue_public_alert,list_public_alerts,list_public_detections,list_pending_notification_posts)
    from app.storage.push_event_store import enqueue_push_notification_event,list_pending_push_notification_events
    from app.storage.notification_eligibility_store import claim_notification_delivery,lock_delivery_eligibility
    # A legacy promise remains byte-for-byte in DB, but is not republished as data.
    enqueue_public_alert(c.db,event_key='legacy_pending',event_type='synthetic',title='OLD',summary='LEGACY_UNSAFE_PROMISE',public_url='/old')
    enqueue_push_notification_event(c.db,event_key='legacy_pending',event_type='synthetic',title='OLD',body='OLD',url='/old')
    c.db.commit()
    legacy=c.scalar("SELECT md5(row_to_json(p)::text) FROM public_alerts p WHERE event_key='legacy_pending'")
    reconcile_notifications();c.db.commit()
    c.check('legacy_held_without_rewrite',c.scalar('SELECT count(*) FROM notification_legacy_holds')==2 and legacy==c.scalar("SELECT md5(row_to_json(p)::text) FROM public_alerts p WHERE event_key='legacy_pending'"))
    c.check('legacy_not_in_validated_web',all(r['event_key']!='legacy_pending' for r in list_public_alerts(c.db)))
    c.check('legacy_detection_neutral',any(r['information_kind']=='detection' for r in list_public_detections(c.db)) and all('LEGACY_UNSAFE_PROMISE' not in r['summary'] for r in list_public_detections(c.db)))
    c.check('legacy_not_in_transport_selectors',all(r['event_key']!='legacy_pending' for r in list_pending_notification_posts(c.db,channel='telegram')+list_pending_push_notification_events(c.db)))
    async def feeds():
        from app.api.main import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated.invalid') as client:
            for path in ('/avisos','/va/avisos','/feed.xml','/feed.json'):
                response=await client.get(path)
                c.check('public_route_'+path,response.status_code==200 and 'LEGACY_UNSAFE_PROMISE' not in response.text and 'legacy_pending' not in response.text)
                if path=='/va/avisos':c.check('validated_notice_copy_in_valencian','Document de la publicació oficial de' in response.text)
    asyncio.run(feeds())
    # Concurrent reconcilers cannot duplicate the same newly validated document.
    new,_=c.doc();parse_document(c,new);c.db.commit()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:reconcile_notifications(),range(2)))
    c.db.commit();key=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(new,))
    c.check('concurrent_reconciliation_unique',c.scalar('SELECT count(*) FROM public_alerts WHERE event_key=%s',(key,))==1)
    c.db.execute("INSERT INTO push_subscriptions(endpoint,p256dh_key,auth_key) VALUES('https://example.invalid/push','synthetic','synthetic') ON CONFLICT DO NOTHING");c.db.commit()
    import run_send_notifications as push
    import run_publish_notifications as telegram
    expected_push=len(list_pending_push_notification_events(c.db,limit=100));expected_tg=len(list_pending_notification_posts(c.db,channel='telegram',limit=100));c.db.commit()
    with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'1'}),patch.object(push,'is_push_configured',return_value=True),patch.object(telegram,'is_telegram_configured',return_value=True),patch.object(push,'dispatch_transport',return_value={'ok':True}) as ps,patch.object(telegram,'dispatch_transport',return_value={'ok':True,'result':{'message_id':1}}) as ts,redirect_stdout(io.StringIO()):
        push.main();telegram.main();push.main();telegram.main()
        c.check('simulated_send_once_per_eligible_queue',ps.call_count==expected_push and ts.call_count==expected_tg)
    c.db.commit()
    c.check('legacy_still_pending_not_fake_sent',c.scalar("SELECT status FROM push_notification_events WHERE event_key='legacy_pending'")=='pending' and c.scalar("SELECT np.status FROM notification_posts np JOIN public_alerts pa ON pa.id=np.alert_id WHERE pa.event_key='legacy_pending' AND np.channel='telegram'")=='pending')
    # Crash after durable claim, before network. It must never auto-send on restart.
    crash,_=c.doc();parse_document(c,crash);reconcile_notifications();c.db.commit()
    crash_key=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(crash,));qid=c.scalar('SELECT id FROM push_notification_events WHERE event_key=%s',(crash_key,))
    aid=claim_notification_delivery(c.db,'push',qid,crash_key)
    c.check('durable_claim_before_network',bool(aid) and c.scalar('SELECT state FROM notification_delivery_attempts WHERE id=%s',(aid,))=='sending')
    c.check('crash_claim_cannot_repeat',claim_notification_delivery(c.db,'push',qid,crash_key) is None)
    c.check('crashed_claim_does_not_starve_selector',all(r['id']!=qid for r in list_pending_push_notification_events(c.db)))
    # Transport exception is uncertain, not a success and not an automatic retry.
    failure,_=c.doc();parse_document(c,failure);reconcile_notifications();c.db.commit()
    fk=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(failure,))
    with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'1'}),patch.object(push,'is_push_configured',return_value=True),patch.object(push,'dispatch_transport',side_effect=RuntimeError('synthetic_uncertain')) as send,redirect_stdout(io.StringIO()):
        push.main();push.main();c.check('uncertain_not_auto_retried',send.call_count==1)
    c.db.commit()
    c.check('uncertain_audited_not_sent',c.scalar("SELECT count(*) FROM notification_delivery_attempts WHERE event_key=%s AND state='uncertain'",(fk,))==1 and c.scalar('SELECT sent_at IS NULL FROM push_notification_events WHERE event_key=%s',(fk,)))
    # A switched-off transport can retry later, retaining all attempt history.
    disabled,_=c.doc();parse_document(c,disabled);reconcile_notifications();c.db.commit()
    dk=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(disabled,))
    with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'1'}),patch.object(push,'is_push_configured',return_value=True),patch.object(push,'dispatch_transport',return_value={'disabled':True,'not_started':True}),redirect_stdout(io.StringIO()):push.main()
    c.db.commit();c.check('disabled_keeps_pending',c.scalar('SELECT status FROM push_notification_events WHERE event_key=%s',(dk,))=='pending')
    with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'1'}),patch.object(push,'is_push_configured',return_value=True),patch.object(push,'dispatch_transport',return_value={'ok':True}),redirect_stdout(io.StringIO()):push.main()
    c.db.commit();c.check('disabled_retry_history_retained',c.scalar("SELECT count(*) FROM notification_delivery_history h JOIN notification_delivery_attempts a ON a.id=h.attempt_id WHERE a.event_key=%s AND h.state='disabled'",(dk,))==1)
    manual,_=c.doc()
    with patch.dict(os.environ,{'NOTIFICATION_PROCESSING_INTENT':'reprocess'}):parse_document(c,manual)
    reconcile_notifications();c.db.commit()
    c.check('explicit_reprocess_never_creates_news',c.scalar("SELECT reason FROM notification_document_state WHERE document_id=%s",(manual,))=='explicit_reprocess' and c.scalar('SELECT queued_event_key IS NULL FROM notification_document_state WHERE document_id=%s',(manual,)))
    # A recorded sending intent cannot be claimed by two workers.
    concurrent,_=c.doc();parse_document(c,concurrent);reconcile_notifications();c.db.commit()
    ck=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(concurrent,))
    cq=c.scalar('SELECT id FROM push_notification_events WHERE event_key=%s',(ck,));c.db.commit()
    def claim(_):
        from app.storage.db import get_connection
        db=get_connection()
        try:return claim_notification_delivery(db,'push',cq,ck)
        finally:db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(claim,range(2)))
    c.check('two_workers_only_one_claim',sum(x is not None for x in claims)==1)
    # Verify the actual registry, not just the lower-level detection fixture.
    c.seq+=1
    version=c.scalar("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent) VALUES(%s,'listadodefinitivo_ADC_EDU_1_26.pdf','registry-live.pdf','synthetic.pdf',1,now(),'live_new') RETURNING id",(f'{c.seq:064x}',))
    url='https://example.invalid/registry-live.pdf'
    c.db.execute("INSERT INTO assets(source_id,sync_run_id,asset_role,title,url,canonical_url,document_version_id,publication_date_text) VALUES((SELECT id FROM sources WHERE source_key='synthetic'),(SELECT max(id) FROM sync_runs),'non_docent_adc_award_pdf','Adjudicación definitiva ADC',%s,%s,%s,%s)",(url,url,version,datetime.now(timezone.utc).strftime('%d/%m/%Y')))
    from tests.document_fixtures import pdf_bytes
    content=pdf_bytes('registry-live');path=c.output/'registry-live.pdf';path.write_bytes(content)
    c.db.execute('UPDATE document_versions SET sha256=%s,file_path=%s,size_bytes=%s WHERE id=%s',(hashlib.sha256(content).hexdigest(),str(path),len(content),version))
    c.db.commit()
    from app.services.document_registry import DocumentRegistryService
    registered=DocumentRegistryService().register_unclassified_documents(notification_intent='live_new')
    c.db.commit()
    c.check('real_registry_preserves_live_intent_and_date',len(registered)==1 and c.scalar("SELECT intent='live_new' AND date_provenance='official_source' FROM notification_document_state WHERE document_id=%s",(registered[0].document_id,)))
    parse_document(c,registered[0].document_id);reconcile_notifications();c.db.commit()
    c.check('real_registry_to_validated_outbox',c.scalar('SELECT queued_event_key IS NOT NULL FROM notification_document_state WHERE document_id=%s',(registered[0].document_id,)))
    # Cutover change blocks stale pending queues, even after transport reactivation.
    with patch.dict(os.environ,{'NOTIFICATION_CUTOVER_UTC':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}):
        c.check('new_cutover_does_not_release_old_pending',list_pending_notification_posts(c.db,channel='telegram')==[] and list_pending_push_notification_events(c.db)==[])


def phase_m(c):
    from app.storage.public_alert_store import enqueue_public_alert,list_public_alerts,list_public_detections
    from app.storage.push_event_store import enqueue_push_notification_event,list_pending_push_notification_events
    enqueue_public_alert(c.db,event_key='legacy_pre_migration',event_type='old',title='OLD',summary='OLD',public_url='/')
    enqueue_push_notification_event(c.db,event_key='legacy_pre_migration',event_type='old',title='OLD',body='OLD',url='/')
    c.db.execute("INSERT INTO sources(source_key,source_url,label) VALUES('legacy','https://example.invalid','legacy')")
    version=c.scalar("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at) VALUES(%s,'legacy.pdf','legacy.pdf','synthetic.pdf',1,now()) RETURNING id",('f'*64,))
    c.db.execute("INSERT INTO documents(document_version_id,source_id,doc_family,title) VALUES(%s,(SELECT id FROM sources WHERE source_key='legacy'),'unknown','legacy')",(version,))
    c.db.commit()
    def hashes():
        return {t:c.scalar(f"SELECT md5(coalesce(string_agg((to_jsonb(x)-'notification_ingest_intent')::text,'|' ORDER BY id),'')) FROM {t} x") for t in ('public_alerts','notification_posts','push_notification_events','document_versions','documents')}
    before=hashes()
    migration=(ROOT/'app/storage/migrations/20261105_notification_eligibility.sql').read_text()
    c.db.execute(migration);c.db.commit()
    c.check('upgrade_preserves_every_legacy_queue_field',before==hashes())
    c.db.execute(migration);c.db.commit()
    c.check('migration_idempotent_on_previous_schema',before==hashes())
    from app.services.notification_eligibility import reconcile_notifications
    reconcile_notifications();c.db.commit()
    c.check('legacy_hold_is_additive_not_fake_delivery',before==hashes() and c.scalar('SELECT count(*) FROM notification_legacy_holds')==2)
    c.check('legacy_detections_remain_readable',len(list_public_detections(c.db))==2)
    c.check('legacy_versions_default_to_unknown',c.scalar('SELECT notification_ingest_intent FROM document_versions WHERE id=%s',(version,))=='unknown')
    c.check('legacy_not_validated_or_sendable',list_public_alerts(c.db)==[] and list_pending_push_notification_events(c.db)==[])
    # A failed migration is rolled back as a unit, not a half-upgraded schema.
    c.db.execute('SAVEPOINT migration_retry')
    try:c.db.execute(migration+' SELECT missing_synthetic_column FROM public_alerts;')
    except Exception:c.db.execute('ROLLBACK TO SAVEPOINT migration_retry')
    c.db.commit();c.check('migration_transaction_failure_keeps_legacy_data',before==hashes())


def phase_dc_contract(c):
    from app.storage.difficult_coverage_store import DifficultCoverageStore
    d,v=c.doc(family='difficult_coverage_provisional',intent='unknown')
    store=DifficultCoverageStore()
    try:
        run=store.create_parse_run(document_version_id=v,parser_key='difficult_coverage_provisional_parser',parser_version='0.4.0',started_at=datetime.now(timezone.utc).isoformat())
        position=store.connection.execute("INSERT INTO difficult_coverage_positions(document_id,position_code,center_code,raw_header_text) VALUES(%s,'123456','00000001','synthetic') RETURNING id",(d,)).fetchone()[0]
        store.connection.execute("INSERT INTO difficult_coverage_candidates(position_id,full_name,full_name_normalized,raw_row_text) VALUES(%s,'PERSONA SINTETICA','persona sintetica','synthetic')",(position,))
        store.finish_parse_run(parse_run_id=run,finished_at=datetime.now(timezone.utc).isoformat(),status='success',rows_extracted=1)
        store.attest_validation(document_id=d,parse_run_id=run)
        store.connection.commit()
        c.check('dc_schema_contract_uses_full_name_normalized',c.scalar('SELECT rows_validated FROM notification_validation_attestations WHERE document_id=%s',(d,))==1)
        c.check('dc_contract_keeps_candidate',c.scalar('SELECT count(*) FROM difficult_coverage_candidates WHERE position_id=%s',(position,))==1)
    finally:store.close()


def phase_ingestion(c):
    import hashlib
    from tests.document_fixtures import pdf_bytes
    from scripts.import_remote_fetch import import_manifest
    from app.services.document_registry import DocumentRegistryService
    from app.services.notification_eligibility import reconcile_notifications
    for mode in ('historical_import','live_new'):
        incoming=c.output/('incoming_'+mode);(incoming/'files').mkdir(parents=True)
        content=pdf_bytes('synthetic_'+mode);name='listadodefinitivo_adc_edu_999_26.pdf'
        (incoming/'files'/name).write_bytes(content)
        url='https://example.invalid/ingestion-'+mode+'.pdf'
        asset=dict(source_key='ingestion_'+mode,source_url='https://example.invalid',asset_role='non_docent_adc_award_pdf',title='Adjudicación definitiva ADC',url=url,canonical_url=url,downloadable=True,publication_date_text=datetime.now(timezone.utc).strftime('%d/%m/%Y'))
        result=dict(status='downloaded',asset=asset,sha256=hashlib.sha256(content).hexdigest(),original_filename=name,stored_filename=mode+'.pdf',relative_file_path='files/'+name,size_bytes=len(content),downloaded_at=datetime.now(timezone.utc).isoformat(),content_type='application/pdf')
        manifest=incoming/'manifest.json';manifest.write_text(json.dumps({'schema_version':1,'sources':[dict(source_key=asset['source_key'],source_url=asset['source_url'],source_label='Synthetic ingestion',status='success',assets=[asset],results=[result])]}))
        kw={} if mode=='historical_import' else {'notification_intent':'live_new'}
        with redirect_stdout(io.StringIO()):import_manifest(manifest_path=manifest,incoming_dir=incoming,data_dir=c.output/'imported_files',**kw)
        # Simulate interruption after ingestion committed. A later live registrar
        # must not promote the first (historical) ingestion to a new publication.
        registered=DocumentRegistryService().register_unclassified_documents(notification_intent='live_new')
        c.check(mode+'_registered_after_ingestion_commit',len(registered)==1)
        parse_document(c,registered[0].document_id);reconcile_notifications();c.db.commit()
        queued=c.scalar('SELECT queued_event_key IS NOT NULL FROM notification_document_state WHERE document_id=%s',(registered[0].document_id,))
        c.check(mode+'_origin_survives_ingestion_registration_gap',queued==(mode=='live_new'))
        if mode=='historical_import':
            with redirect_stdout(io.StringIO()):import_manifest(manifest_path=manifest,incoming_dir=incoming,data_dir=c.output/'imported_files',notification_intent='live_new')
            repeated=DocumentRegistryService().register_unclassified_documents(notification_intent='live_new')
            c.db.commit()
            c.check('reimport_cannot_promote_first_ingestion',repeated==[] and c.scalar('SELECT notification_ingest_intent FROM document_versions WHERE id=(SELECT document_version_id FROM documents WHERE id=%s)',(registered[0].document_id,))=='historical_import')



def main(output,phase):
    output=output.resolve()
    if not output.is_relative_to((ROOT/'auditoria_local/resultados').resolve()) or output.exists():raise ValueError('new ignored output required')
    output.mkdir(parents=True);isolated.OUT=output
    report={'phase':phase,'status':'running','existing_database_used':False,'external_sends':0};c=None
    try:
        with isolated.FreshCluster() as cluster:
            (output/'cluster.json').write_text(json.dumps({'root':str(cluster.root),'data':str(cluster.data)})+'\n')
            now=datetime.now(timezone.utc)
            env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_'))}
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),PGPASSFILE=str(cluster.root/'absent'),ENABLE_NOTIFICATIONS='1',TELEGRAM_BOT_TOKEN='synthetic',TELEGRAM_CHANNEL_ID='@synthetic',RADAR_PUSH_VAPID_PUBLIC_KEY='',RADAR_PUSH_VAPID_PRIVATE_KEY='',INDEXNOW_KEY='',NOTIFICATION_CUTOVER_UTC=(now-timedelta(hours=1)).isoformat(),NOTIFICATION_MAX_AGE_DAYS='7')
            with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
                import psycopg
                with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:cluster.verify(admin);admin.execute('CREATE DATABASE funk_validation')
                spies=list(isolated.connection_guards(cluster,stack))
                from app.storage.db import init_db,get_connection,close_pool
                schema=ROOT/'app/storage/schema.sql'
                if phase=='M':
                    legacy=cluster.root/'legacy_schema.sql'
                    legacy.write_text(schema.read_text().split('-- F07 notification eligibility')[0]);schema=legacy
                init_db(str(schema));db=get_connection();c=Checks(db,output)
                if phase=='M':phase_m(c)
                else:phase_a(c)
                if phase=='T':phase_dc_contract(c)
                if phase=='I':phase_ingestion(c)
                if phase in ('B','C','D'):phase_b(c)
                if phase in ('C','D'):phase_c(c)
                c.check('zero_real_transports',all(spy.call_count==0 for _,spy in spies))
                report.update(checks=c.checks,status='passed' if all(x['passed'] for x in c.checks) else 'failed')
                db.close();close_pool()
        report['cluster_removed']=True
    except BaseException as exc:
        report.update(status='blocked_or_failed',error=isolated.safe_error(exc))
        if c:report['checks']=c.checks
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(report['status'],flush=True)
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--phase',choices=('A','B','C','D','M','T','I'),default='A');a=p.parse_args();raise SystemExit(main(a.output_dir,a.phase))
