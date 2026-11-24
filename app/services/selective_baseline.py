"""Versioned baseline and stable decisions for the synthetic v3 runner."""
from datetime import date,datetime,timezone
import hashlib
import io
import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from app.services.remote_import_integrity import check_bytes,directory,regular


def baseline_digest(baseline_id,t0,items):
    if t0.utcoffset() is None: raise ValueError('baseline_timezone_required')
    normalized=sorted(items,key=lambda x:(x['url'],x['sha256'] or '',x['exception']))
    raw=json.dumps({'id':baseline_id,'t0':t0.astimezone(timezone.utc).isoformat(),'items':normalized},
                   sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest()


def _explicit_publication_date(entry,staging):
    ev=entry['evidence']
    with directory(staging) as root, regular(root,ev['html_file']) as handle:
        size=__import__('os').fstat(handle.fileno()).st_size
        if not 0<size<=5*1024*1024: raise ValueError('evidence_size')
        captured=io.BytesIO();check_bytes(handle,ev['html_sha256'],size,captured)
        html=captured.read()
    soup=BeautifulSoup(html,'html.parser'); bases=soup.find_all('base',href=True)
    if len(bases)>1:return None
    base=urljoin(ev['final_page_url'],bases[0]['href']) if bases else ev['final_page_url']
    matching=[a for a in soup.find_all('a',href=True) if urljoin(base,a['href'])==ev['linked_url']]
    if len(matching)!=1:return None
    block=matching[0].find_parent('article')
    if block is None:return None
    found=re.findall(r'(?i)fecha\s+de\s+publicaci[oó]n\s*:\s*(\d{2}/\d{2}/\d{4})',block.get_text(' ',strip=True))
    if len(found)!=1:return None
    try:return datetime.strptime(found[0],'%d/%m/%Y').date()
    except ValueError:return None


def classify_and_record(connection,baseline_id,entry,staging):
    """Persist one decision; callers must already hold the DB/flock fences.

    HTML provenance here is deliberately only a synthetic fixture assertion.
    The runner has no production authorization mode.
    """
    baseline=connection.execute('SELECT id,t0,items,sha256 FROM selective_baseline WHERE id=%s FOR SHARE',(baseline_id,)).fetchone()
    if baseline is None:raise ValueError('baseline_not_found')
    items=baseline['items'];t0=baseline['t0']
    if not isinstance(items,list) or baseline_digest(baseline_id,t0,items)!=baseline['sha256']:
        raise ValueError('baseline_integrity')
    url=entry['asset']['canonical_url']; sha=entry['sha256']
    existing=connection.execute('''SELECT decision FROM selective_baseline_decision
        WHERE baseline_id=%s AND canonical_url=%s AND sha256=%s FOR UPDATE''',(baseline_id,url,sha)).fetchone()
    if existing:return existing['decision']
    observed=datetime.fromisoformat(entry['evidence']['observed_utc'].replace('Z','+00:00'))
    if observed.utcoffset() is None or observed>datetime.now(timezone.utc):
        raise ValueError('invalid_first_observation')
    downloaded_text=entry['evidence'].get('downloaded_utc')
    downloaded=datetime.fromisoformat(downloaded_text.replace('Z','+00:00')) if downloaded_text else None
    if downloaded is not None and (downloaded.utcoffset() is None or downloaded<observed or downloaded>datetime.now(timezone.utc)):
        raise ValueError('invalid_download_time')
    same_url=[x for x in items if x['url']==url]
    previously_observed=connection.execute('''SELECT sha256 FROM selective_baseline_decision
        WHERE baseline_id=%s AND canonical_url=%s''',(baseline_id,url)).fetchall()
    known_asset=connection.execute('''SELECT a.document_version_id,v.sha256 FROM assets a
        LEFT JOIN document_versions v ON v.id=a.document_version_id WHERE a.canonical_url=%s''',(url,)).fetchall()
    same_sha_observed=connection.execute('''SELECT 1 FROM selective_baseline_decision
        WHERE baseline_id=%s AND sha256=%s LIMIT 1''',(baseline_id,sha)).fetchone()
    existing_version=connection.execute('SELECT 1 FROM document_versions WHERE sha256=%s',(sha,)).fetchone()
    if any(x['sha256']==sha for x in same_url): decision='historical'
    elif any(x['exception'] and x['sha256'] is None for x in same_url): decision='historical_recovered'
    elif any(x['sha256']==sha for x in known_asset): decision='historical'
    elif same_url or previously_observed or known_asset: decision='correction_review'
    elif any(x['sha256']==sha for x in items) or same_sha_observed or existing_version: decision='identity_review'
    elif observed<=t0: decision='historical'
    else:
        official=_explicit_publication_date(entry,staging)
        if downloaded is None or official is None or official>observed.astimezone(timezone.utc).date():decision='provenance_review'
        elif official<t0.astimezone(timezone.utc).date():decision='historical_recovered'
        elif official==t0.astimezone(timezone.utc).date():decision='date_precision_review'
        else:decision='live_new'
    official=_explicit_publication_date(entry,staging)
    connection.execute('''INSERT INTO selective_baseline_decision
        (baseline_id,canonical_url,sha256,first_observed_utc,downloaded_utc,official_date,html_sha256,decision,evidence_kind)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'synthetic_fixture')''',
        (baseline_id,url,sha,observed,downloaded,official,entry['evidence']['html_sha256'],decision))
    return decision
