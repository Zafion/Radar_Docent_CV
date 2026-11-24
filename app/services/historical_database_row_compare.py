"""Document-scoped row comparison: no identity inferred from a name."""
from collections import Counter,defaultdict
import hashlib,hmac,json,os

FAMILIES={
 'award_results':('''SELECT r.list_scope,r.body_code,r.specialty_code,r.order_number,r.person_name_normalized,r.status,r.specialty_name
 FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',
 ('scope','body','specialty','order','person_check','status','specialty_name'),(0,1,2,3),(0,2,3),4),
 'award_assignments':('''SELECT r.list_scope,r.body_code,r.specialty_code,r.order_number,a.position_code,a.petition_number,r.person_name_normalized,a.center_code,a.locality,a.hours_text,a.assignment_kind,a.position_specialty_code
 FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',
 ('scope','body','specialty','order','position','petition','person_check','center','locality','hours','kind','position_specialty'),(0,1,2,3,4,5),(0,2,3,4),6),
 'offered_positions':('''SELECT p.body_code,p.specialty_code,p.center_code,p.position_code,p.center_name,p.locality,p.hours_text,p.is_itinerant,p.valenciano_required_text,p.observations
 FROM offered_positions p JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',
 ('body','specialty','center','position','center_name','locality','hours','itinerant','language_requirement','observations'),(0,1,2,3),(1,2,3),None),
 'bag_members':('''SELECT s.list_scope,s.position_scope,s.staff_kind,s.specialty_code,m.order_number,m.person_name_normalized,m.masked_dni,m.service_status,m.collective,m.habilitations,m.disabled_habilitation
 FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id JOIN documents d ON d.id=s.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',
 ('scope','position_scope','staff_kind','specialty','order','person_check','masked_identifier','status','collective','habilitations','disabled_habilitation'),(0,1,2,3,4),(0,1,2,3,4),5),
 'difficult_positions':('''SELECT p.body_code,p.specialty_code,p.center_code,p.position_code,p.center_name,p.locality,p.num_participants,p.sorteo_number
 FROM difficult_coverage_positions p JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',
 ('body','specialty','center','position','center_name','locality','participant_count','draw_number'),(0,1,2,3),(1,2,3),None),
 'difficult_candidates':('''SELECT p.body_code,p.specialty_code,p.center_code,p.position_code,c.row_number,c.full_name_normalized,c.is_selected,c.registration_code_or_bag_order,c.petition_number,c.assigned_position_code
 FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',
 ('body','specialty','center','position','order','person_check','selected','registration','petition','assigned_position'),(0,1,2,3,4),(1,2,3,4),5),
}

def _read(db,sql,sha,key_indices,required):
    groups=defaultdict(list);ambiguous=0;total=0
    with db.cursor(name='baseline_comparison_cursor') as cur:
        cur.execute(sql,(sha,))
        while True:
            batch=cur.fetchmany(1000)
            if not batch:break
            for row in batch:
                total+=1
                if any(row[i] is None or row[i]=='' for i in required):ambiguous+=1;continue
                groups[tuple(row[i] for i in key_indices)].append(row)
    return groups,ambiguous,total

def compare_document(old,new,sha,secret):
    families={}
    for family,(sql,names,keys,required,person_idx) in FAMILIES.items():
        a,unkeyed_a,total_a=_read(old,sql,sha,keys,required)
        b,unkeyed_b,total_b=_read(new,sql,sha,keys,required)
        counts=Counter();changes=[]
        for key in a.keys()|b.keys():
            left=a.get(key,[]);right=b.get(key,[])
            if len(left)>1 or len(right)>1:category='ambiguous_duplicate_key';fields=[]
            elif not left:category='reconstruction_only';fields=[]
            elif not right:category='baseline_only';fields=[]
            elif person_idx is not None and left[0][person_idx]!=right[0][person_idx]:category='ambiguous_person_conflict';fields=[]
            else:
                fields=[name for i,name in enumerate(names) if i not in keys and i!=person_idx and left[0][i]!=right[0][i]]
                category='different_fields' if fields else 'identical'
            counts[category]+=1
            if category!='identical':
                pseudonym=hmac.new(secret,json.dumps([sha,family,key],default=str,ensure_ascii=True).encode(),hashlib.sha256).hexdigest()
                changes.append({'key':pseudonym,'category':category,'changed_fields':fields})
        families[family]={'baseline_rows':total_a,'reconstruction_rows':total_b,'unkeyed_baseline':unkeyed_a,
                          'unkeyed_reconstruction':unkeyed_b,'categories':dict(counts),'differences':changes}
    return families

def compare_all(old,new,shas):
    secret=os.urandom(32);totals={f:Counter() for f in FAMILIES};documents=[]
    for sha in sorted(shas):
        result=compare_document(old,new,sha,secret)
        for family,data in result.items():
            totals[family].update(data['categories'])
            for k in ('baseline_rows','reconstruction_rows','unkeyed_baseline','unkeyed_reconstruction'):totals[family][k]+=data[k]
        documents.append({'sha256':sha,'publication_date_quality':'unknown_not_inferred','families':result})
    return {'status':'ROW_COMPARISON','common_documents':len(documents),'families':{f:dict(v) for f,v in totals.items()},
            'documents':documents,'personal_values_exported':False,'pseudonym_key_persisted':False,
            'limitations':['Document-local keys require unique scope and ordinal or position; duplicates and absent keys are ambiguous.',
             'Name is used only to detect a conflict, never to identify a person.',
             'Results reflect snapshots; dates of publication are not inferred from document dates.',
             'Different fields are not automatically parser improvements.']}
