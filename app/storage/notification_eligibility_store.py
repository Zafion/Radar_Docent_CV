"""Durable detection/validation ledger. No network or automatic commits here."""
from datetime import datetime
import os
import re
from app.services.notification_eligibility import CONTRACT_REVISION,POLICY_REVISION


def register_detection(conn,document_id,document_version_id,canonical_url=None,publication_date_text=None,intent='unknown'):
    if intent not in {'live_new','historical_import','reprocess','unknown'}:raise ValueError('invalid_notification_intent')
    official=None
    if publication_date_text:
        try:official=datetime.strptime(publication_date_text.strip(),'%d/%m/%Y').date()
        except ValueError:pass
    conn.execute('''INSERT INTO notification_document_state(document_id,document_version_id,intent,
        canonical_url,first_seen,official_date,date_provenance,corrected_url)
        SELECT %s,v.id,CASE
            WHEN v.notification_ingest_intent='reprocess' OR %s='reprocess' THEN 'reprocess'
            WHEN v.notification_ingest_intent='historical_import' OR %s='historical_import' THEN 'historical_import'
            WHEN v.notification_ingest_intent='live_new' AND %s='live_new' THEN 'live_new'
            ELSE 'unknown' END,%s,v.created_at,%s,%s,EXISTS(
            SELECT 1 FROM assets a WHERE a.canonical_url=%s AND a.document_version_id<>v.id)
        FROM document_versions v WHERE v.id=%s
        ON CONFLICT(document_id) DO NOTHING''',
        (document_id,intent,intent,intent,canonical_url,official,'official_source' if official else None,canonical_url,document_version_id))


def processing_started(conn,version_id):
    from app.services.parse_selection import CURRENT_SELECTION
    mode='reprocess' if CURRENT_SELECTION.get().reprocess else os.getenv('NOTIFICATION_PROCESSING_INTENT','normal')
    if mode not in ('normal','reprocess'):raise ValueError('invalid_notification_processing_intent')
    if mode=='reprocess':
        conn.execute("""UPDATE notification_document_state SET delivery_hold_reason='explicit_reprocess',
            phase='held',reason='reprocess_requested',updated_at=now() WHERE document_version_id=%s""",(version_id,))
    conn.execute("""UPDATE notification_document_state SET phase='processing',reason='parsing',updated_at=now()
        WHERE document_version_id=%s""",(version_id,))


def processing_unsuccessful(conn,run_id,status):
    conn.execute("""UPDATE notification_document_state s SET phase='held',reason=%s,updated_at=now()
        FROM document_parse_runs p WHERE p.id=%s AND p.document_version_id=s.document_version_id""",(status,run_id))


def attest_validation(conn,document_id,run_id):
    """A parser calls this only after its validation/inserts, in the SAME transaction.

    Independently check persisted essential fields/counts. Uncovered families do
    not get an attestation. Empty/failed/partial data cannot become eligible.
    """
    doc=conn.execute('''SELECT d.doc_family,p.rows_extracted,p.status,p.document_version_id=d.document_version_id AS same_version
        FROM documents d JOIN document_parse_runs p ON p.id=%s WHERE d.id=%s''',(run_id,document_id)).fetchone()
    if not doc or not doc['same_version'] or doc['status']!='success' or doc['rows_extracted']<=0:return
    family=doc['doc_family']
    queries={
        'final_award_listing':("""SELECT count(*) AS n,count(*) FILTER(WHERE person_name_normalized IS NULL OR status IS NULL
            OR (status='Adjudicat' AND NOT EXISTS(SELECT 1 FROM award_assignments a WHERE a.award_result_id=r.id
            AND a.position_code IS NOT NULL AND a.center_code IS NOT NULL))) AS bad
            FROM award_results r WHERE document_id=%s""",(document_id,)),
        'offered_positions':('''SELECT count(*) AS n,count(*) FILTER(WHERE position_code IS NULL OR center_code IS NULL OR specialty_code IS NULL) AS bad
            FROM offered_positions WHERE document_id=%s''',(document_id,)),
        'difficult_coverage_provisional':('''SELECT count(*) AS n,count(*) FILTER(WHERE c.full_name_normalized IS NULL OR p.position_code IS NULL OR p.center_code IS NULL) AS bad
            FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id WHERE p.document_id=%s''',(document_id,)),
        'non_docent_adc_award':('''SELECT count(*) AS n,count(*) FILTER(WHERE a.person_name_normalized IS NULL OR a.position_code IS NULL OR a.score IS NULL) AS bad
            FROM non_docent_awards a JOIN non_docent_publications p ON p.id=a.publication_id WHERE p.document_id=%s''',(document_id,)),
        'non_docent_adc_call':('''SELECT count(*) AS n,count(*) FILTER(WHERE a.position_code IS NULL OR a.classification IS NULL OR a.center_name IS NULL) AS bad
            FROM non_docent_offered_positions a JOIN non_docent_publications p ON p.id=a.publication_id WHERE p.document_id=%s''',(document_id,)),
    }
    if family in ('non_docent_bag_update','non_docent_funcion_publica_bag'):
        query=('''SELECT count(*) AS n,count(*) FILTER(WHERE m.person_name_normalized IS NULL OR m.masked_dni IS NULL OR m.total_score IS NULL) AS bad
            FROM non_docent_bag_members m JOIN non_docent_bag_snapshots s ON s.id=m.snapshot_id
            JOIN non_docent_publications p ON p.id=s.publication_id WHERE p.document_id=%s''',(document_id,))
    else:query=queries.get(family)
    if query is None:return
    counts=conn.execute(*query).fetchone()
    if counts['n']!=doc['rows_extracted'] or counts['bad']:
        # Attestation is additional notification gating, not a replacement for
        # family parser diagnostics. Preserve successful data, hold notification.
        processing_unsuccessful(conn,run_id,'notification_contract_incomplete')
        return
    conn.execute('''INSERT INTO notification_validation_attestations(parse_run_id,document_id,contract_revision,rows_validated)
        VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(run_id,document_id,CONTRACT_REVISION,counts['n']))
    conn.execute('''UPDATE notification_document_state SET phase='validated',reason='validated',validation_run_id=%s,updated_at=now()
        WHERE document_id=%s''',(run_id,document_id))


def eligible_event_sql(event_expression):
    # Expression is a fixed SQL identifier supplied by our callers, never input.
    return f'''EXISTS(SELECT 1 FROM notification_document_state ns
        JOIN notification_validation_attestations va ON va.parse_run_id=ns.validation_run_id
        JOIN document_parse_runs pr ON pr.id=va.parse_run_id
        JOIN document_versions nv ON nv.id=ns.document_version_id
        WHERE ns.queued_event_key={event_expression} AND ns.phase='queued'
          AND ns.policy_revision='notification.v1' AND va.contract_revision='persisted_rows.v1'
          AND ns.intent='live_new' AND nv.notification_ingest_intent='live_new' AND ns.delivery_hold_reason IS NULL AND NOT ns.corrected_url AND ns.official_date IS NOT NULL
          AND va.document_id=ns.document_id AND pr.document_version_id=ns.document_version_id
          AND ns.date_provenance='official_source' AND pr.status='success' AND pr.rows_extracted=va.rows_validated
          AND NOT EXISTS(SELECT 1 FROM document_parse_runs newer
             WHERE newer.document_version_id=ns.document_version_id AND newer.id>pr.id))'''


def record_legacy_holds(conn):
    for channel,table,alias,join,key in (
        ('telegram','notification_posts','q','JOIN public_alerts pa ON pa.id=q.alert_id','pa.event_key'),
        ('push','push_notification_events','q','','q.event_key')):
        extra="AND q.channel='telegram'" if channel=='telegram' else ''
        conn.execute(f'''INSERT INTO notification_legacy_holds(channel,queue_id)
            SELECT %s,q.id FROM {table} q {join} WHERE q.status='pending' {extra}
            AND NOT EXISTS(SELECT 1 FROM notification_document_state ns WHERE ns.queued_event_key={key})
            ON CONFLICT DO NOTHING''',(channel,))


def lock_delivery_eligibility(conn,event_key):
    """Validate current eligibility under a SHORT authorization transaction.

    Callers must commit before any external IO. A subsequent revocation cannot
    cancel an already-authorized snapshot; it blocks future authorizations.
    """
    from app.services.notification_eligibility import configured_policy,eligibility_reason
    row=conn.execute('SELECT * FROM notification_document_state WHERE queued_event_key=%s FOR UPDATE',(event_key,)).fetchone()
    if not row or eligibility_reason(row,configured_policy()):return False
    return bool(conn.execute('SELECT '+eligible_event_sql('%s'),(event_key,)).fetchone()[0])


def claim_notification_delivery(conn,channel,queue_id,event_key,*,delivery_context=None,return_authorization=False):
    from app.services.notification_policy import notifications_enabled
    if not notifications_enabled():
        conn.rollback();return None
    if channel not in ('telegram','push'):raise ValueError('unsupported_notification_channel')
    conn.execute("SET LOCAL lock_timeout='2s'")
    conn.execute("SET LOCAL statement_timeout='5s'")
    if not lock_delivery_eligibility(conn,event_key):
        conn.rollback();return None
    hold=conn.execute('SELECT 1 FROM notification_legacy_holds WHERE channel=%s AND queue_id=%s',(channel,queue_id)).fetchone()
    if hold:
        conn.rollback();return None
    if channel=='telegram':
        exists=conn.execute("""SELECT q.*,p.event_type FROM notification_posts q JOIN public_alerts p ON p.id=q.alert_id
            WHERE q.id=%s AND q.channel='telegram' AND q.status='pending' AND p.event_key=%s FOR UPDATE OF q""",(queue_id,event_key)).fetchone()
    else:
        exists=conn.execute("SELECT * FROM push_notification_events WHERE id=%s AND event_key=%s AND status='pending' FOR UPDATE",(queue_id,event_key)).fetchone()
    if not exists:
        conn.rollback();return None
    import json,uuid
    from app.services.notification_eligibility import configured_policy
    policy=configured_policy()
    identity=conn.execute("""SELECT ns.document_id,ns.document_version_id,v.sha256,ns.validation_run_id,
        ns.policy_revision,ns.policy_cutover,ns.official_date,va.contract_revision,va.rows_validated
        FROM notification_document_state ns JOIN document_versions v ON v.id=ns.document_version_id
        JOIN notification_validation_attestations va ON va.parse_run_id=ns.validation_run_id
        WHERE ns.queued_event_key=%s""",(event_key,)).fetchone()
    snapshot=dict(identity=dict(identity),content=dict(exists),channel=channel,queue_id=queue_id,
                  event_key=event_key,cutover=policy[0].isoformat(),max_age_days=policy[1],
                  notifications_enabled=True,delivery_context=delivery_context or {})
    token=str(uuid.uuid4())
    row=conn.execute("""INSERT INTO notification_delivery_attempts(channel,queue_id,event_key,state,authorization_token,authorization_snapshot)
        VALUES(%s,%s,%s,'sending',%s::uuid,%s::jsonb) ON CONFLICT(channel,queue_id) DO UPDATE
        SET state='sending',reason=NULL,started_at=now(),finished_at=NULL,
            authorization_token=EXCLUDED.authorization_token,authorization_snapshot=EXCLUDED.authorization_snapshot
        WHERE notification_delivery_attempts.state IN ('disabled','blocked') RETURNING id""",
        (channel,queue_id,event_key,token,json.dumps(snapshot,default=str))) .fetchone()
    conn.commit()
    if row and return_authorization:
        return dict(attempt_id=int(row[0]),token=token,snapshot=snapshot)
    return int(row[0]) if row else None


def load_delivery_authorization(conn,attempt_id):
    row=conn.execute("SELECT authorization_token,authorization_snapshot FROM notification_delivery_attempts WHERE id=%s AND state='sending'",(attempt_id,)).fetchone()
    conn.commit()  # Leave even a read transaction closed before external IO.
    if not row or not row['authorization_token'] or not row['authorization_snapshot']:return None
    return dict(token=str(row['authorization_token']),snapshot=row['authorization_snapshot'])


def finish_delivery_attempt(conn,attempt_id,state,reason=None,*,token=None):
    if state not in {'sent','failed','disabled','blocked','uncertain'}:raise ValueError('invalid_delivery_outcome')
    # Legacy/obsolete workers without the exact authorization cannot confirm.
    if token is None:return False
    conn.execute("SET LOCAL lock_timeout='2s'")
    conn.execute("SET LOCAL statement_timeout='5s'")
    target=conn.execute('SELECT channel,queue_id FROM notification_delivery_attempts WHERE id=%s',(attempt_id,)).fetchone()
    if not target:return False
    # Same queue -> attempt order as the claim, avoiding an ack/claim deadlock.
    table='notification_posts' if target['channel']=='telegram' else 'push_notification_events'
    conn.execute(f'SELECT id FROM {table} WHERE id=%s FOR UPDATE',(target['queue_id'],)).fetchone()
    return conn.execute("""UPDATE notification_delivery_attempts SET state=%s,reason=%s,finished_at=now()
        WHERE id=%s AND state='sending' AND authorization_token=%s::uuid""",
        (state,reason,attempt_id,token)).rowcount==1


def deliverable_event_sql(event_expression):
    """Filter stale queues before LIMIT so they cannot starve current notices."""
    from app.services.notification_eligibility import configured_policy
    policy=configured_policy()
    if policy is None:return 'FALSE',()
    cutover,days=policy
    return eligible_event_sql(event_expression)+f''' AND EXISTS(SELECT 1 FROM notification_document_state fresh
        WHERE fresh.queued_event_key={event_expression} AND fresh.first_seen>=%s AND fresh.official_date>=%s
          AND fresh.official_date BETWEEN (now() AT TIME ZONE 'UTC')::date-%s AND (now() AT TIME ZONE 'UTC')::date)''',(cutover,cutover.date(),days)
