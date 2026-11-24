"""Read-only screening. A matching hash is not proof of official provenance."""
from datetime import datetime
import hashlib
import json
import re

from app.services.selective_inventory import screen_candidate
from app.services.selective_manifest import validate_technical_manifest


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate_inventory_key')
        result[key]=value
    return result


def dry_run(*, staging, manifest, manifest_sha256, inventory_bytes, inventory_sha256, fence):
    """Screen a sealed inventory and a manifest, without granting authority."""
    fence.check()
    if (not isinstance(inventory_bytes,bytes) or len(inventory_bytes)>16*1024*1024
            or not isinstance(inventory_sha256,str) or not re.fullmatch('[0-9a-f]{64}',inventory_sha256)
            or hashlib.sha256(inventory_bytes).hexdigest()!=inventory_sha256):
        raise ValueError('inventory_digest_or_size')
    data=json.loads(inventory_bytes,object_pairs_hook=_unique)
    if not isinstance(data,dict) or set(data)!={'format','cutover_utc','observed_utc','versions'} or data['format']!='funkcionario.inventory-screen.v1':
        raise ValueError('inventory_schema')
    cutover=datetime.fromisoformat(data['cutover_utc'].replace('Z','+00:00'))
    observed=datetime.fromisoformat(data['observed_utc'].replace('Z','+00:00'))
    if cutover.utcoffset() is None or observed.utcoffset() is None:
        raise ValueError('timestamp_timezone_required')
    if not isinstance(data['versions'],list): raise ValueError('inventory_versions')
    inventory=set()
    for item in data['versions']:
        if not isinstance(item,dict) or set(item)!={'canonical_url','sha256'}:
            raise ValueError('inventory_version_schema')
        pair=(item['canonical_url'],item['sha256'])
        if not all(isinstance(v,str) for v in pair) or pair in inventory:
            raise ValueError('inventory_version_invalid')
        inventory.add(pair)
    entries=validate_technical_manifest(staging,manifest,manifest_sha256)
    counts={}
    for entry in entries:
        asset=entry.get('asset')
        if not asset or entry['identity']!=asset['canonical_url']:
            raise ValueError('asset_identity_mismatch')
        try:
            official=datetime.strptime(asset['publication_date_text'],'%d/%m/%Y').date()
        except ValueError:
            raise ValueError('official_date_invalid') from None
        reason=screen_candidate(canonical_url=asset['canonical_url'],sha256=entry['sha256'],
                                observed_utc=observed,official_date=official,cutover_utc=cutover,inventory=inventory)
        counts[reason]=counts.get(reason,0)+1
    fence.check()
    return {'status':'STOP','reason':'official_provenance_and_inventory_not_authenticated',
            'technical_manifest_valid':True,'writes_performed':False,'counts':counts}
