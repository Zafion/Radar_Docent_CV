"""Conservative document-scoped reconciliation for a local owned trial.

Existing differing rows are never overwritten. A family with existing rows
requires a document-local comparison; only empty target families can be copied
automatically. Additional formats must provide their own explicit adapter.
"""
from collections import Counter
import hashlib,json
from pathlib import Path
from psycopg.rows import dict_row
from scripts.integrate_03nov_trial import cols,insert_row
from app.services.historical_database_row_compare import compare_document

def plan_mixed_rows(baseline,reconstructed,required_key_fields,compared_fields):
    """Classify document-local rows without name-based identity inference.

    Callers provide keys tied to an independently identified document and
    procedure. Rows with missing or non-unique keys never become insertions.
    Values are kept in memory only; returned actions contain source IDs and
    changed field names, not personal data.
    """
    def grouped(rows):
        result={};invalid=[]
        for row in rows:
            key=tuple(row.get(k) for k in required_key_fields)
            if any(v is None or v=='' for v in key):invalid.append(row['source_id']);continue
            result.setdefault(key,[]).append(row)
        return result,invalid
    old,old_invalid=grouped(baseline);new,new_invalid=grouped(reconstructed)
    actions=[]
    for source_id in new_invalid:actions.append({'source_id':source_id,'category':'E','reason':'insufficient_documentary_key'})
    for key,rows in new.items():
        prior=old.get(key,[])
        if len(rows)!=1 or len(prior)>1:
            actions.extend({'source_id':r['source_id'],'category':'E','reason':'non_unique_documentary_key'} for r in rows);continue
        current=rows[0]
        if not prior:
            # Missing keys in the target make absence unprovable; do not insert.
            category='E' if old_invalid else 'B'
            actions.append({'source_id':current['source_id'],'category':category,'reason':'target_has_unkeyed_rows' if old_invalid else 'verified_documentary_key_absent_in_target'});continue
        changed=[field for field in compared_fields if prior[0].get(field)!=current.get(field)]
        actions.append({'source_id':current['source_id'],'category':'C' if changed else 'A','changed_fields':changed})
    return {'actions':actions,'unkeyed_baseline':len(old_invalid),'baseline_only_keys':len(old.keys()-new.keys())}

def apply_mixed_plan(plan,insert_one,quarantine_one):
    """Execute only B actions; caller owns the transaction and evidence gate."""
    counts=Counter()
    for action in plan['actions']:
        category=action['category'];counts[category]+=1
        if category=='B':insert_one(action['source_id'],action['reason'])
        elif category in ('C','E'):quarantine_one(action['source_id'],category,action.get('changed_fields',[]))
    return dict(counts)

def load_mixed_rows(db,document_id,family):
    """Read one document at a time; source IDs never serve as cross-DB keys."""
    if family=='bag_members':
        query='''SELECT m.id AS source_id,s.list_scope,s.position_scope,s.staff_kind,s.specialty_code,m.order_number,
          m.person_name_normalized,m.masked_dni,m.service_status,m.collective FROM docent_bag_members m
          JOIN docent_bag_snapshots s ON s.id=m.snapshot_id WHERE s.document_id=%s'''
        keys=['list_scope','position_scope','staff_kind','specialty_code','order_number']
        fields=['person_name_normalized','masked_dni','service_status','collective']
    elif family=='difficult_candidates':
        query='''SELECT c.id AS source_id,p.body_code,p.specialty_code,p.center_code,p.position_code,c.row_number,
          c.full_name_normalized,c.is_selected,c.registration_code_or_bag_order,c.petition_number,c.assigned_position_code
          FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id WHERE p.document_id=%s'''
        # Body may legitimately be absent. It remains a compared field rather
        # than being equated with a value or used to discard every row.
        keys=['specialty_code','center_code','position_code','row_number']
        fields=['body_code','full_name_normalized','is_selected','registration_code_or_bag_order','petition_number','assigned_position_code']
    else:raise ValueError('mixed_adapter_not_supported')
    return list(db.execute(query,(document_id,))),keys,fields

def insert_mixed_difficult(new,dst,source_id,target_document_id):
    """Add one new candidate only if its destination parent is an exact match."""
    candidate=new.execute('SELECT * FROM difficult_coverage_candidates WHERE id=%s',(source_id,)).fetchone()
    parent=new.execute('SELECT * FROM difficult_coverage_positions WHERE id=%s',(candidate['position_id'],)).fetchone()
    targets=dst.execute('''SELECT * FROM difficult_coverage_positions WHERE document_id=%s AND position_code=%s
      AND center_code=%s AND specialty_code IS NOT DISTINCT FROM %s AND body_code IS NOT DISTINCT FROM %s''',
      (target_document_id,parent['position_code'],parent['center_code'],parent['specialty_code'],parent['body_code'])).fetchall()
    if len(targets)!=1:raise ValueError('mixed_parent_ambiguous')
    target=targets[0]
    comparable=('center_name','locality','num_participants','sorteo_number','registro_superior','registro_inferior')
    if any(parent.get(k)!=target.get(k) for k in comparable):raise ValueError('mixed_parent_fields_conflict')
    fields=copy_fields(new,dst,'difficult_coverage_candidates')
    values={k:candidate[k] for k in fields};values['position_id']=target['id']
    target_id=insert_row(dst,'difficult_coverage_candidates',values)
    insert_mapping(dst,'difficult_coverage_candidates',source_id,target_id)
    return target_id

def apply_mixed_difficult(new,dst,plan,document_id,sha):
    """Apply only uniquely new candidates and retain all other evidence."""
    counts=Counter()
    for action in plan['actions']:
        kind=action['category'];source_id=action['source_id'];counts[kind]+=1
        if kind=='B':
            try:
                with dst.transaction():
                    target=insert_mixed_difficult(new,dst,source_id,document_id)
                    dst.execute('''INSERT INTO recovery_batch.mixed_changes(source_sha,kind,source_id,target_id,justification)
                      VALUES(%s,%s,%s,%s,%s)''',(sha,'difficult_coverage_candidates',source_id,target,'unique_document_key_and_exact_parent'))
                    counts['inserted']+=1
            except ValueError:
                dst.execute('''INSERT INTO recovery_batch.quarantined_rows(kind,source_id,source_sha,reason)
                  VALUES('difficult_coverage_candidates',%s,%s,'parent_not_uniquely_compatible') ON CONFLICT DO NOTHING''',(source_id,sha))
                counts['new_rows_quarantined']+=1
        elif kind in ('C','E'):
            dst.execute('''INSERT INTO recovery_batch.quarantined_rows(kind,source_id,source_sha,reason)
              VALUES('difficult_coverage_candidates',%s,%s,%s) ON CONFLICT DO NOTHING''',
              (source_id,sha,'field_conflict' if kind=='C' else 'ambiguous_documentary_key'))
    return dict(counts)

def insert_mixed_bag(new,dst,source_id,target_document_id,source_document_id):
    """Insert a unique participant under a uniquely corresponding list."""
    if source_id in ambiguous_bag_ids(new,source_document_id):raise ValueError('mixed_bag_key_ambiguous')
    member=new.execute('SELECT * FROM docent_bag_members WHERE id=%s',(source_id,)).fetchone()
    if not member:raise ValueError('mixed_bag_source_missing')
    snapshot=new.execute('SELECT * FROM docent_bag_snapshots WHERE id=%s AND document_id=%s',
                         (member['snapshot_id'],source_document_id)).fetchone()
    if not snapshot:raise ValueError('mixed_bag_list_missing')
    if bag_cross_document_overlap(dst,target_document_id,member,snapshot):raise ValueError('mixed_bag_cross_document_overlap')
    attributes=('staff_kind','list_scope','position_scope','specialty_code','body_code','course_year','list_stage')
    common=set(cols(new,'docent_bag_snapshots')) & set(cols(dst,'docent_bag_snapshots'))
    if not set(attributes)<=common:raise ValueError('mixed_bag_scope_incomplete')
    conditions=' AND '.join(x+' IS NOT DISTINCT FROM %s' for x in attributes)
    targets=dst.execute('SELECT id FROM docent_bag_snapshots WHERE document_id=%s AND '+conditions,
                        (target_document_id,*[snapshot[x] for x in attributes])).fetchall()
    if len(targets)!=1:raise ValueError('mixed_bag_target_list_ambiguous')
    values={k:member[k] for k in copy_fields(new,dst,'docent_bag_members')};values['snapshot_id']=targets[0]['id']
    target=insert_row(dst,'docent_bag_members',values);insert_mapping(dst,'docent_bag_members',source_id,target)
    return target

def apply_mixed_bag(new,dst,plan,document_id,source_document_id,sha):
    """Apply only new document-keyed participants under a verified list."""
    counts=Counter()
    for action in plan['actions']:
        kind=action['category'];source_id=action['source_id'];counts[kind]+=1
        if kind=='B':
            try:
                with dst.transaction():
                    target=insert_mixed_bag(new,dst,source_id,document_id,source_document_id)
                    dst.execute('''INSERT INTO recovery_batch.mixed_changes(source_sha,kind,source_id,target_id,justification)
                      VALUES(%s,%s,%s,%s,%s)''',(sha,'docent_bag_members',source_id,target,'unique_document_key_and_exact_list'))
                    counts['inserted']+=1
            except ValueError:
                dst.execute('''INSERT INTO recovery_batch.quarantined_rows(kind,source_id,source_sha,reason)
                  VALUES('docent_bag_members',%s,%s,'list_or_key_not_uniquely_compatible') ON CONFLICT DO NOTHING''',(source_id,sha))
                counts['new_rows_quarantined']+=1
        elif kind in ('C','E'):
            dst.execute('''INSERT INTO recovery_batch.quarantined_rows(kind,source_id,source_sha,reason)
              VALUES('docent_bag_members',%s,%s,%s) ON CONFLICT DO NOTHING''',
              (source_id,sha,'field_conflict' if kind=='C' else 'ambiguous_documentary_key'))
    return dict(counts)

def classify_document(old,new,sha,kind,secret):
    comparison=compare_document(old,new,sha,secret);data=comparison[kind];categories=data['categories']
    other_conflicts=[];other_ambiguous=[]
    initial_insert=not data['baseline_rows'] and bool(data['reconstruction_rows'])
    for family,other in comparison.items():
        if family==kind:continue
        # A family absent from the destination can be a dependency of an
        # initial document-scoped insert; its adapter still validates it.
        if initial_insert and not other['baseline_rows'] and (kind,family) in {
            ('difficult_candidates','difficult_positions'),('award_results','award_assignments')}:continue
        c=other['categories']
        if other['unkeyed_baseline'] or other['unkeyed_reconstruction'] or c.get('ambiguous_duplicate_key') or c.get('ambiguous_person_conflict'):
            other_ambiguous.append(family)
        elif c.get('different_fields') or c.get('baseline_only') or c.get('reconstruction_only'):
            other_conflicts.append(family)
    data['other_families_with_conflicts']=other_conflicts
    data['other_families_ambiguous']=other_ambiguous
    if other_ambiguous:return 'E',data
    if other_conflicts:return 'C',data
    if initial_insert:
        return 'B',data
    if not data['reconstruction_rows']:
        return 'E',data
    if data['unkeyed_baseline'] or data['unkeyed_reconstruction'] or categories.get('ambiguous_duplicate_key') or categories.get('ambiguous_person_conflict'):
        return 'E',data
    if categories.get('different_fields') or categories.get('baseline_only') or categories.get('reconstruction_only'):
        return 'C',data
    return 'A',data

def copy_fields(new,dst,table):
    destination=set(cols(dst,table));return [x for x in cols(new,table) if x in destination and x!='id']

def insert_mapping(dst,kind,source_id,target_id):
    dst.execute('INSERT INTO recovery_batch.row_map(kind,source_id,target_id) VALUES(%s,%s,%s)',(kind,source_id,target_id))

def create_verified_document(new,dst,source,parse_run,asset):
    """Create missing metadata only when a verified official source maps exactly."""
    if not asset or asset.get('source_key')!='rrhh_main' or asset.get('source_url')!='https://ceice.gva.es/es/web/rrhh-educacion':
        raise ValueError('new_document_official_source_unavailable')
    target_source=dst.execute('SELECT id,source_url FROM sources WHERE source_key=%s',(asset['source_key'],)).fetchone()
    if not target_source or target_source['source_url']!=asset['source_url']:raise ValueError('new_document_source_conflict')
    version=new.execute('SELECT * FROM document_versions WHERE id=%s',(source['document_version_id'],)).fetchone()
    document=new.execute('SELECT * FROM documents WHERE id=%s',(source['id'],)).fetchone()
    vid=insert_row(dst,'document_versions',{c:version[c] for c in copy_fields(new,dst,'document_versions')})
    run={c:parse_run[c] for c in copy_fields(new,dst,'document_parse_runs')};run['document_version_id']=vid
    runid=insert_row(dst,'document_parse_runs',run)
    doc={c:document[c] for c in copy_fields(new,dst,'documents')}
    doc.update(document_version_id=vid,source_id=target_source['id'],validation_status='needs_validation',
               validation_parse_run_id=runid,historical_status='unknown',publication_date_quality='unknown')
    return insert_row(dst,'documents',doc)

def ambiguous_bag_ids(new,source_id):
    return {row['id'] for row in new.execute('''SELECT id FROM (
      SELECT m.id,s.list_scope,s.position_scope,s.staff_kind,s.specialty_code,m.order_number,
        count(*) OVER (PARTITION BY s.list_scope,s.position_scope,s.staff_kind,s.specialty_code,m.order_number) AS multiplicity
      FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id WHERE s.document_id=%s
    ) q WHERE multiplicity>1 OR list_scope IS NULL OR position_scope IS NULL OR staff_kind IS NULL
      OR specialty_code IS NULL OR specialty_code='' OR order_number IS NULL''',(source_id,))}

def potential_cross_document_bag_matches(new,dst,source_id,target_id):
    """Find masked-ID candidates across documents, never assert identity.

    A masked identifier and procedural scope can collide. We report possible
    overlaps and keep documentary rows separate, with no name-based merging.
    """
    affected=pairs=examined=eligible=0;documents=set()
    rows=new.execute('''SELECT m.masked_dni,s.list_scope,s.position_scope,s.staff_kind,s.specialty_code
      FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id WHERE s.document_id=%s''',(source_id,))
    for r in rows:
        examined+=1
        key=(r['masked_dni'],r['list_scope'],r['position_scope'],r['staff_kind'],r['specialty_code'])
        if any(v is None or v=='' for v in key):continue
        eligible+=1
        candidates=dst.execute('''SELECT s.document_id,count(*) AS n FROM docent_bag_members m
          JOIN docent_bag_snapshots s ON s.id=m.snapshot_id WHERE s.document_id<>%s
          AND m.masked_dni=%s AND s.list_scope=%s AND s.position_scope=%s AND s.staff_kind=%s AND s.specialty_code=%s
          GROUP BY s.document_id''',(target_id,*key)).fetchall()
        if candidates:
            affected+=1;pairs+=sum(x['n'] for x in candidates);documents.update(x['document_id'] for x in candidates)
    return {'source_rows_examined':examined,'source_rows_eligible_for_probe':eligible,'source_rows_with_possible_overlap':affected,'candidate_pairs':pairs,'other_documents':len(documents),
            'identity_established':False,'merged':0,'reason':'masked_identifier_and_scope_are_not_unique_identity'}

def bag_cross_document_overlap(dst,target_document_id,member,snapshot):
    key=(member.get('masked_dni'),snapshot.get('list_scope'),snapshot.get('position_scope'),snapshot.get('staff_kind'),snapshot.get('specialty_code'))
    if any(v is None or v=='' for v in key):return False
    return dst.execute('''SELECT 1 FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id
        WHERE s.document_id<>%s AND m.masked_dni=%s AND s.list_scope=%s AND s.position_scope=%s
        AND s.staff_kind=%s AND s.specialty_code=%s LIMIT 1''',(target_document_id,*key)).fetchone() is not None

def copy_bag(new,dst,source_id,target_id,official_url,source_sha):
    snapshots=copy_fields(new,dst,'docent_bag_snapshots');members=copy_fields(new,dst,'docent_bag_members')
    source=new.execute('SELECT * FROM docent_bag_snapshots WHERE document_id=%s ORDER BY id',(source_id,)).fetchall()
    if not source or any(x['staff_kind']!='career_practice' for x in source):raise ValueError('bag_procedure_ambiguous')
    cross_document=potential_cross_document_bag_matches(new,dst,source_id,target_id)
    total=0;pending=0;ambiguous=ambiguous_bag_ids(new,source_id)
    for snapshot in source:
        row={x:snapshot[x] for x in snapshots};row['document_id']=target_id
        if 'document_url' in row:row['document_url']=official_url
        if 'source_page_url' in row and row['source_page_url'] and not row['source_page_url'].startswith('https://ceice.gva.es/'):
            row['source_page_url']=None
        target_snapshot=insert_row(dst,'docent_bag_snapshots',row);insert_mapping(dst,'docent_bag_snapshots',snapshot['id'],target_snapshot)
        with new.cursor(name='batch_bag_members',row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM docent_bag_members WHERE snapshot_id=%s ORDER BY id',(snapshot['id'],))
            while batch:=cur.fetchmany(500):
                for item in batch:
                    overlap=bag_cross_document_overlap(dst,target_id,item,snapshot)
                    if item['id'] in ambiguous or overlap:
                        dst.execute('INSERT INTO recovery_batch.quarantined_rows(kind,source_id,source_sha,reason) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                            ('docent_bag_members',item['id'],source_sha,'ambiguous_documentary_key' if item['id'] in ambiguous else 'possible_cross_document_overlap'))
                        pending+=1;continue
                    row={x:item[x] for x in members};row['snapshot_id']=target_snapshot
                    target=insert_row(dst,'docent_bag_members',row);insert_mapping(dst,'docent_bag_members',item['id'],target);total+=1
    return {'bag_snapshots':len(source),'bag_members':total,'ambiguous_rows':pending,'cross_document_review':cross_document}

def copy_difficult(new,dst,source_id,target_id):
    positions=copy_fields(new,dst,'difficult_coverage_positions');candidates=copy_fields(new,dst,'difficult_coverage_candidates')
    source=new.execute('SELECT * FROM difficult_coverage_positions WHERE document_id=%s ORDER BY id',(source_id,)).fetchall()
    if not source:raise ValueError('positions_missing')
    keys=[(x['position_code'],x['center_code']) for x in source]
    if any(not a or not b for a,b in keys) or len(keys)!=len(set(keys)):raise ValueError('position_identity_ambiguous')
    total=0
    for position in source:
        duplicate=new.execute('''SELECT count(*) AS n FROM (SELECT row_number FROM difficult_coverage_candidates WHERE position_id=%s GROUP BY row_number HAVING count(*)>1 OR row_number IS NULL) s''',(position['id'],)).fetchone()['n']
        if duplicate:raise ValueError('candidate_order_ambiguous')
        row={x:position[x] for x in positions};row.update(document_id=target_id,availability_status='unknown',availability_reason='source_vigency_unverified',availability_checked_at=None,closed_by_document_id=None,closed_at=None)
        row={k:v for k,v in row.items() if k in positions}
        target_position=insert_row(dst,'difficult_coverage_positions',row);insert_mapping(dst,'difficult_coverage_positions',position['id'],target_position)
        with new.cursor(name='batch_candidates',row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM difficult_coverage_candidates WHERE position_id=%s ORDER BY id',(position['id'],))
            while batch:=cur.fetchmany(500):
                for item in batch:
                    row={x:item[x] for x in candidates};row['position_id']=target_position
                    target=insert_row(dst,'difficult_coverage_candidates',row);insert_mapping(dst,'difficult_coverage_candidates',item['id'],target);total+=1
    return {'difficult_positions':len(source),'difficult_candidates':total}

def copy_awards(new,dst,source_id,target_id,source_sha):
    """Copy uniquely keyed award results; ambiguous results remain at source."""
    result_fields=copy_fields(new,dst,'award_results');assignment_fields=copy_fields(new,dst,'award_assignments')
    ambiguous={r['id'] for r in new.execute('''SELECT id FROM (
      SELECT id,list_scope,body_code,specialty_code,order_number,
        count(*) OVER(PARTITION BY list_scope,body_code,specialty_code,order_number) AS multiplicity
      FROM award_results WHERE document_id=%s) q WHERE multiplicity>1 OR list_scope IS NULL OR list_scope=''
      OR specialty_code IS NULL OR specialty_code='' OR order_number IS NULL''',(source_id,))}
    total=pending=assignments=0
    with new.cursor(name='batch_award_results',row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM award_results WHERE document_id=%s ORDER BY id',(source_id,))
        while batch:=cur.fetchmany(500):
            for item in batch:
                if item['id'] in ambiguous:
                    dst.execute('INSERT INTO recovery_batch.quarantined_rows(kind,source_id,source_sha,reason) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                      ('award_results',item['id'],source_sha,'ambiguous_documentary_key'));pending+=1;continue
                source_assignments=new.execute('SELECT * FROM award_assignments WHERE award_result_id=%s ORDER BY id',(item['id'],)).fetchall()
                if any(a.get('matched_offered_position_id') is not None for a in source_assignments):raise ValueError('unaccredited_assignment_link')
                row={x:item[x] for x in result_fields};row['document_id']=target_id
                target=insert_row(dst,'award_results',row);insert_mapping(dst,'award_results',item['id'],target);total+=1
                for a in source_assignments:
                    data={x:a[x] for x in assignment_fields};data['award_result_id']=target
                    if 'matched_offered_position_id' in data:data['matched_offered_position_id']=None
                    aid=insert_row(dst,'award_assignments',data);insert_mapping(dst,'award_assignments',a['id'],aid);assignments+=1
    return {'award_results':total,'award_assignments':assignments,'ambiguous_rows':pending}
