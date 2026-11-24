"""Read-only verification of documentary duplicate guards on real local data."""
import psycopg
from psycopg.rows import dict_row
from app.services.historical_batch_reconcile import ambiguous_bag_ids,potential_cross_document_bag_matches

SHA='acf9530d701a06adc5c3c44cc81c3a5554f53dae8e50c13b9f8b3e49a687fcb0'
def run(new,socket):
    new.row_factory=dict_row;new.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    src=new.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(SHA,)).fetchone()
    if not src:raise ValueError('source_404_missing')
    with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname='baseline_trial_20261123',row_factory=dict_row) as dst:
        dst.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        dest=dst.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(SHA,)).fetchone()
        if not dest:raise ValueError('target_404_missing')
        ambiguous=ambiguous_bag_ids(new,src['id'])
        quarantined={r['source_id'] for r in dst.execute("SELECT source_id FROM recovery_batch.quarantined_rows WHERE kind='docent_bag_members' AND source_sha=%s",(SHA,))}
        mapped={r['source_id'] for r in dst.execute("SELECT source_id FROM recovery_batch.row_map WHERE kind='docent_bag_members'")}
        if len(ambiguous)!=16 or ambiguous!=quarantined or ambiguous&mapped:raise ValueError('duplicate_guard_mismatch')
        review=potential_cross_document_bag_matches(new,dst,src['id'],dest['id'])
        dst.rollback()
    new.rollback()
    return {'status':'PASS','source_404_ambiguous_rows':16,'ambiguous_rows_in_quarantine':16,'ambiguous_rows_mapped':0,
            'cross_document_candidates':review,'explanation':'12 previously inserted rows collided across snapshots within the same document; masked identifiers across different documents are only possible overlaps.'}
