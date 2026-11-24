"""Inspect source identifiers only, without person or document contents."""
from scripts.integrate_03nov_trial import SHAS

def run(old,new):
    rows=[]
    for ver,sha in SHAS.items():
        source=new.execute('''SELECT s.source_key,s.source_url,s.label FROM documents d
          JOIN sources s ON s.id=d.source_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(sha,)).fetchone()
        keyed=old.execute('SELECT source_key,source_url,label FROM sources WHERE source_key=%s',(source[0],)).fetchall()
        url=old.execute('SELECT source_key,source_url,label FROM sources WHERE source_url=%s',(source[1],)).fetchall()
        rows.append({'version':ver,'source_key':source[0],'source_url':source[1],'source_label':source[2],
                     'key_matches':[list(x) for x in keyed],'url_matches':[list(x) for x in url]})
    old.rollback();new.rollback();return rows
