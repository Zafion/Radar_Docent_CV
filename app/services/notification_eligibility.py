"""Fail-closed notification policy, independent of transport configuration."""
from datetime import date,datetime,timezone
import os

POLICY_REVISION='notification.v1'
CONTRACT_REVISION='persisted_rows.v1'


def configured_policy():
    try:
        cutover=datetime.fromisoformat(os.environ['NOTIFICATION_CUTOVER_UTC'].replace('Z','+00:00'))
        days=int(os.getenv('NOTIFICATION_MAX_AGE_DAYS','7'))
        if cutover.tzinfo is None or not 1<=days<=30:return None
        return cutover.astimezone(timezone.utc),days
    except (KeyError,ValueError):return None


def eligibility_reason(item,policy,now=None):
    now=now or datetime.now(timezone.utc)
    if item.get('delivery_hold_reason'):return item['delivery_hold_reason']
    if item['intent']!='live_new':return 'non_live_intent'
    if item['corrected_url']:return 'known_url_new_version'
    if policy is None:return 'policy_not_configured'
    cutover,days=policy
    first=item['first_seen'];official=item['official_date']
    if not official or item.get('date_provenance')!='official_source':return 'official_date_unverified'
    if isinstance(official,str):official=date.fromisoformat(official)
    if first<cutover or first>now:return 'outside_detection_cutover'
    if official<cutover.date() or not 0<=(now.date()-official).days<=days:return 'outside_publication_window'
    return None


def reconcile_notifications():
    """Recover committed validations without calling any transport."""
    import hashlib
    from app.storage.db import get_connection
    from app.storage.public_alert_store import enqueue_public_alert
    from app.storage.push_event_store import enqueue_push_notification_event
    from app.storage.notification_eligibility_store import record_legacy_holds
    conn=get_connection();queued=[]
    try:
        # Existing documents have no authority to become live merely by upgrading.
        conn.execute('''INSERT INTO notification_document_state(document_id,document_version_id,intent,first_seen,phase,reason)
            SELECT id,document_version_id,'unknown',created_at,'held','legacy_without_eligibility'
            FROM documents ON CONFLICT(document_id) DO NOTHING''')
        rows=conn.execute('''SELECT ns.*,(SELECT status FROM document_parse_runs last WHERE last.document_version_id=ns.document_version_id ORDER BY id DESC LIMIT 1) AS latest_status,d.doc_family,d.list_scope,v.sha256,pr.status,pr.rows_extracted,
            va.contract_revision,va.rows_validated,
            EXISTS(SELECT 1 FROM document_parse_runs newer WHERE newer.document_version_id=ns.document_version_id AND newer.id>pr.id) AS stale_validation,
            EXISTS(SELECT 1 FROM document_parse_runs older WHERE older.document_version_id=ns.document_version_id
                AND older.id<pr.id AND older.status='success') AS prior_success
            FROM notification_document_state ns JOIN documents d ON d.id=ns.document_id
            JOIN document_versions v ON v.id=ns.document_version_id
            LEFT JOIN notification_validation_attestations va ON va.parse_run_id=ns.validation_run_id
            LEFT JOIN document_parse_runs pr ON pr.id=va.parse_run_id
            ORDER BY ns.document_id FOR UPDATE OF ns SKIP LOCKED''').fetchall()
        policy=configured_policy()
        for item in rows:
            if item['phase']=='queued':continue
            reason=eligibility_reason(item,policy)
            if not reason and (item['contract_revision']!=CONTRACT_REVISION or item['status']!='success'
                              or item['rows_validated']!=item['rows_extracted'] or item['stale_validation']):
                reason=('parsing_'+item['latest_status']) if item['latest_status'] in ('failed','skipped','running') else 'awaiting_current_validation'
            if reason:
                conn.execute("UPDATE notification_document_state SET phase='held',reason=%s,updated_at=now() WHERE document_id=%s",(reason,item['document_id']))
                continue
            if item['prior_success'] and not item['queued_event_key']:
                conn.execute("UPDATE notification_document_state SET phase='held',reason='historical_reprocess',updated_at=now() WHERE document_id=%s",(item['document_id'],))
                continue
            # Existing key is immutable across parser replay/reconciliation. No
            # new event for the same document even if derived row IDs change.
            key=item['queued_event_key'] or 'validated_document:v1:'+hashlib.sha256(
                (str(item['canonical_url'])+'|'+item['sha256']).encode()).hexdigest()
            family=item['doc_family'];scope=item['list_scope']
            kinds={
                'final_award_listing':('docent_awards_new','Adjudicaciones','/valencia-docentes'),
                'offered_positions':('docent_offered_positions_new','Plazas ofertadas','/plazas-ofertadas'),
                'difficult_coverage_provisional':('docent_difficult_coverage_new','Difícil cobertura','/dificil-cobertura'),
                'non_docent_adc_award':('non_docent_adc_award_new','Adjudicaciones no docentes','/no-docente/adjudicaciones'),
                'non_docent_adc_call':('non_docent_adc_call_new','Plazas no docentes','/no-docente/plazas'),
                'non_docent_bag_update':('non_docent_bags_new','Bolsas no docentes','/no-docente/consulta-persona'),
                'non_docent_funcion_publica_bag':('non_docent_bags_new','Bolsas no docentes','/no-docente/consulta-persona'),
            }
            if family not in kinds:continue
            kind,label,url=kinds[family]
            if family=='offered_positions' and scope=='dificil_cobertura':kind,label,url=kinds['difficult_coverage_provisional']
            title=label+': documento validado'
            summary=f"Documento de la publicación oficial de {item['official_date']} procesado y validado. Consulta su resultado y la fuente oficial."
            payload={'eligibility_policy':POLICY_REVISION,'document_ids':[item['document_id']],
                     'sha256':item['sha256'],'validation_run_id':item['validation_run_id']}
            # Distinct auditable eligibility transition; commit atomically with
            # the outbox so a crash leaves validation recoverable, never half-published.
            conn.execute("UPDATE notification_document_state SET phase='eligible',reason='policy_passed',updated_at=now() WHERE document_id=%s",(item['document_id'],))
            enqueue_public_alert(conn,event_key=key,event_type=kind,title=title,summary=summary,public_url=url,
                                 source_url=item['canonical_url'],payload=payload)
            enqueue_push_notification_event(conn,event_key=key,event_type=kind,title=title,body=summary,url=url,payload=payload)
            conn.execute("""UPDATE notification_document_state SET phase='queued',reason='eligible',policy_revision=%s,
                policy_cutover=%s,queued_event_key=%s,updated_at=now() WHERE document_id=%s""",
                (POLICY_REVISION,policy[0],key,item['document_id']))
            queued.append(key)
        record_legacy_holds(conn)
        conn.commit();return queued
    except BaseException:
        conn.rollback();raise
    finally:conn.close()


def reconcile_if_configured():
    # No implicit DB connection from scripts/tests when the new policy is off.
    return reconcile_notifications() if configured_policy() else []
