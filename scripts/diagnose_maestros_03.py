"""Aggregate documentary-key overlap without exporting personal values."""
from collections import Counter
import hashlib
import psycopg
from psycopg.rows import dict_row
from scripts.integrate_03nov_trial import SHAS

def run(source,socket):
    with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname='baseline17_trial',connect_timeout=5,row_factory=dict_row,options='-c default_transaction_read_only=on') as target:
        source.row_factory=dict_row
        source.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result={}
        try:
            for ver in (700,701):
                sha=SHAS[ver]
                query='''SELECT r.order_number,r.specialty_code,r.body_code,r.list_scope,r.status,r.raw_block_text
                  FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s'''
                left=target.execute(query,(sha,)).fetchall();right=source.execute(query,(sha,)).fetchall()
                report={'baseline_rows':len(left),'reconstruction_rows':len(right),'keys':{}}
                for name,fields in (('ordinal',('order_number',)),('specialty_ordinal',('specialty_code','order_number')),('raw_block',('raw_block_text',))):
                    def counter(rows):
                        keys=[]
                        for row in rows:
                            parts=tuple(row[f] for f in fields)
                            if any(x is None or x=='' for x in parts):continue
                            if name=='raw_block':parts=(hashlib.sha256(parts[0].encode()).hexdigest(),)
                            keys.append(parts)
                        return Counter(keys)
                    a=counter(left);b=counter(right);intersection=a.keys()&b.keys()
                    report['keys'][name]={'baseline_keyed':sum(a.values()),'reconstruction_keyed':sum(b.values()),
                        'baseline_duplicate_rows':sum(v for v in a.values() if v>1),'reconstruction_duplicate_rows':sum(v for v in b.values() if v>1),
                        'matched_unique_keys':sum(a[k]==b[k]==1 for k in intersection),
                        'baseline_only_keys':len(a.keys()-b.keys()),'reconstruction_only_keys':len(b.keys()-a.keys()),
                        'baseline_excess_rows':sum((a-b).values()),'reconstruction_excess_rows':sum((b-a).values())}
                result[str(ver)]=report
        finally:source.rollback()
    return result
