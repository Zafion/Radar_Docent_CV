"""Assess seven preselected documentary versions with local PDF and isolated DBs."""
from collections import Counter
import difflib,re,unicodedata
import hashlib,json
from pathlib import Path
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from scripts.integrate_03nov_trial import ROOT,SHAS
from app.services.final_award_listing_maestros_parser import FinalAwardListingMaestrosParserService,STATUS_VALUES

OUT=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
VERSIONS=(436,79,439,77,138,62,243)

def row_summary(rows,field):
    keys=Counter(r[field] for r in rows if r[field] is not None and r[field]!='')
    return {'rows':len(rows),'keyed':sum(keys.values()),'duplicate_rows':sum(v for v in keys.values() if v>1),'distinct':len(keys)}

def run(source,socket,*,keys_only=False):
    plan={x['version_id']:x for x in json.loads((OUT/'plan_integral_reanudable.json').read_text())['entries']}
    oldkw={'host':str(socket),'port':55467,'user':'baseline_owner','options':'-c default_transaction_read_only=on'}
    report=[];discordant={}
    with psycopg.connect(**oldkw,dbname='baseline',row_factory=dict_row) as baseline,psycopg.connect(**oldkw,dbname='baseline17_trial',row_factory=dict_row) as trial:
        source.row_factory=dict_row;source.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        try:
            for ver in VERSIONS:
                entry=plan[ver];sha=entry['sha256'];item={'version':ver,'sha256':sha,'status':'C','pdf_verified':False}
                try:
                    pdf=Path(entry['local_sha_verified_paths'][0]);item['pdf_verified']=not pdf.is_symlink() and hashlib.sha256(pdf.read_bytes()).hexdigest()==sha
                    if not item['pdf_verified']:raise ValueError('pdf_sha_mismatch')
                    query='''SELECT r.id,r.order_number,r.status,r.raw_block_text,r.list_scope,r.specialty_code,r.body_code,r.person_name_normalized
                      FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s'''
                    old=baseline.execute(query,(sha,)).fetchall();now=trial.execute(query,(sha,)).fetchall();new=source.execute(query,(sha,)).fetchall()
                    item.update(baseline_rows=len(old),trial_rows=len(now),reconstructed_rows=len(new),scopes=sorted({r['list_scope'] for r in new if r['list_scope']}),
                        baseline_statuses=dict(Counter(r['status'] for r in old)),reconstructed_statuses=dict(Counter(r['status'] for r in new)))
                    item['ordinal']={'baseline':row_summary(old,'order_number'),'reconstruction':row_summary(new,'order_number')}
                    oa=Counter(r['order_number'] for r in old);ob=Counter(r['order_number'] for r in new)
                    item['ordinal'].update(baseline_excess=sum((oa-ob).values()),reconstruction_excess=sum((ob-oa).values()))
                    if all(v==1 for v in oa.values()) and all(v==1 for v in ob.values()) and None not in oa and None not in ob:
                        old_by={r['order_number']:r for r in old};new_by={r['order_number']:r for r in new};common=old_by.keys()&new_by.keys()
                        item['ordinal']['shared_status_conflicts']=sum(old_by[k]['status']!=new_by[k]['status'] for k in common)
                        item['ordinal']['shared_name_conflicts_as_guard_only']=sum(old_by[k]['person_name_normalized']!=new_by[k]['person_name_normalized'] for k in common)
                        old_names=Counter(r['person_name_normalized'] for r in old if r['person_name_normalized'])
                        missing_ordinals=new_by.keys()-old_by.keys()
                        item['ordinal']['missing_rows_with_prior_name_candidate']=sum(bool(old_names.get(new_by[k]['person_name_normalized'])) for k in missing_ordinals)
                    def blockcounts(rows):return Counter(hashlib.sha256(r['raw_block_text'].encode()).hexdigest() for r in rows if r['raw_block_text'])
                    a,b=blockcounts(old),blockcounts(new)
                    item['blocks']={'baseline_excess':sum((a-b).values()),'reconstruction_excess':sum((b-a).values()),
                        'baseline_duplicate_rows':sum(v for v in a.values() if v>1),'reconstruction_duplicate_rows':sum(v for v in b.values() if v>1)}
                    if not keys_only:
                        service=FinalAwardListingMaestrosParserService();lines=service._extract_clean_lines(pdf)
                        item['source_status_markers']=dict(Counter(line for line in lines if line in STATUS_VALUES))
                        try:
                            with patch.object(service,'_extract_clean_lines',return_value=lines):parsed=service._parse_document({'file_path':str(pdf)})
                            item['parser_rows']=len(parsed);item['parser_statuses']=dict(Counter(r.status for r in parsed))
                            item['parser_matches_reconstruction']=Counter((r.order_number,r.status) for r in parsed)==Counter((r['order_number'],r['status']) for r in new)
                        except Exception as exc:item['parser_error_type']=type(exc).__name__;item['parser_matches_reconstruction']=False
                    if ver==439:item.update(status='D',reason='career_practice_procedure_not_ordinary_interino')
                    elif ver==77:item.update(status='D',reason='career_practice_format_not_ordinary_interino')
                    elif len(old)<len(new) and item.get('parser_matches_reconstruction') and not (a-b):item.update(status='A',reason='verified_pdf_reconstruction_and_baseline_block_multiset_subset')
                    else:item.update(status='C',reason='baseline_block_or_parser_identity_not_fully_matched')
                except Exception as exc:item.update(status='C',error=type(exc).__name__)
                report.append(item)
            q='''SELECT r.id,r.order_number,r.status,r.person_name_normalized,r.raw_block_text FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s'''
            a=baseline.execute(q,(SHAS[700],)).fetchall();b=source.execute(q,(SHAS[700],)).fetchall();right={r['order_number']:r for r in b}
            for row in a:
                match=right.get(row['order_number'])
                if not match or row['raw_block_text']==match['raw_block_text']:continue
                def norm(text):return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',text)).strip()
                def assigns(db,ident):
                    return Counter(tuple(x.values()) for x in db.execute('''SELECT position_code,center_code,position_specialty_code,petition_number FROM award_assignments WHERE award_result_id=%s''',(ident,)).fetchall())
                left_assign=assigns(baseline,row['id']);right_assign=assigns(source,match['id'])
                discordant={'ordinal':row['order_number'],'source_sha256':SHAS[700],
                    'statuses_match':row['status']==match['status'],'normalized_name_matches_as_conflict_check_only':row['person_name_normalized']==match['person_name_normalized'],
                    'normalized_block_matches':norm(row['raw_block_text'])==norm(match['raw_block_text']),
                    'word_edit_segments':sum(tag!='equal' for tag,*_ in difflib.SequenceMatcher(a=row['raw_block_text'].split(),b=match['raw_block_text'].split(),autojunk=False).get_opcodes()),
                    'baseline_assignment_count':sum(left_assign.values()),'reconstruction_assignment_count':sum(right_assign.values()),
                    'assignment_documentary_fields_match':left_assign==right_assign,
                    'decision':'pending_independent_documentary_review'}
        finally:source.rollback()
    return {'documents':report,'maestros03_discordant_block':discordant,'personal_values_exported':False,'baseline_is_snapshot_not_latest_production':True}
