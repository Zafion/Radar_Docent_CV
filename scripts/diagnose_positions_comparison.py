"""Compare representations of selected fields without exporting row values."""
from collections import Counter,defaultdict
from app.services.historical_database_row_compare import FAMILIES,_read

def describe(v):
    if v is None:return 'null'
    if v is True:return 'bool_true'
    if v is False:return 'bool_false'
    if isinstance(v,str):return 'empty_text' if not v else 'nonempty_text'
    return type(v).__name__

def run(old,new,shas):
    sql,names,keys,required,_=FAMILIES['offered_positions'];stats=Counter();examples={};samples=0
    independent=Counter()
    for sha in sorted(shas):
        a,_,_=_read(old,sql,sha,keys,required);b,_,_=_read(new,sql,sha,keys,required)
        # Independent SQL aggregation, with no parser comparator or _read keys.
        query='''SELECT p.body_code,p.specialty_code,p.center_code,p.position_code,count(*)
          FROM offered_positions p JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id
          WHERE v.sha256=%s AND p.specialty_code IS NOT NULL AND p.specialty_code<>''
          AND p.center_code IS NOT NULL AND p.center_code<>'' AND p.position_code IS NOT NULL AND p.position_code<>''
          GROUP BY p.body_code,p.specialty_code,p.center_code,p.position_code'''
        left={tuple(r[:4]):r[4] for r in old.execute(query,(sha,))};right={tuple(r[:4]):r[4] for r in new.execute(query,(sha,))}
        independent['unique_keys_both']+=sum(left[k]==right[k]==1 for k in left.keys()&right.keys())
        independent['duplicate_keys_either']+=sum(left[k]>1 or right[k]>1 for k in left.keys()&right.keys())
        independent['only_baseline_unique']+=sum(left[k]==1 for k in left.keys()-right.keys())
        independent['only_reconstruction_unique']+=sum(right[k]==1 for k in right.keys()-left.keys())
        for key in a.keys()&b.keys():
            if len(a[key])!=1 or len(b[key])!=1:continue
            left,right=a[key][0],b[key][0];samples+=1
            changed=[i for i in range(len(names)) if i not in keys and left[i]!=right[i]]
            if not changed:stats['exact_selected_fields']+=1
            for idx in changed:
                desc=names[idx]+':'+describe(left[idx])+'->'+describe(right[idx]);stats[desc]+=1
                examples.setdefault(desc,{'field':names[idx],'baseline_representation':describe(left[idx]),'reconstruction_representation':describe(right[idx])})
            stable=[4,5,6]
            if all(left[i]==right[i] for i in stable):stats['center_locality_hours_equal']+=1
    if independent['unique_keys_both']!=samples:raise ValueError('independent_key_census_mismatch')
    return {'matched_unique_keys':samples,'independent_sql_key_census':dict(independent),'representation_counts':dict(stats),'examples':list(examples.values()),
            'limitation':'Differences are retained; absent values are not equated with false or empty strings.'}
