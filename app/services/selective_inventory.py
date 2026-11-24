"""Conservative novelty screening; does not authenticate an inventory or source."""
from datetime import date, datetime, timezone
import re


def screen_candidate(*, canonical_url, sha256, observed_utc, official_date, cutover_utc, inventory):
    """Return a reason code, never an authorization to import or label live_new.

    `inventory` is a verified snapshot of (canonical URL, SHA) pairs supplied by
    the caller. Authenticity, snapshot completeness and official provenance must
    be established independently before relying on even a candidate result.
    """
    if (not isinstance(canonical_url,str) or not canonical_url
            or not isinstance(sha256,str) or not re.fullmatch('[0-9a-f]{64}',sha256)
            or not isinstance(observed_utc,datetime) or observed_utc.utcoffset() is None
            or not isinstance(cutover_utc,datetime) or cutover_utc.utcoffset() is None
            or type(official_date) is not date or not isinstance(inventory,(set,frozenset))):
        return 'invalid_or_incomplete_evidence'
    if not all(isinstance(i,tuple) and len(i)==2 and isinstance(i[0],str)
               and isinstance(i[1],str) and re.fullmatch('[0-9a-f]{64}',i[1]) for i in inventory):
        return 'invalid_inventory'
    if (canonical_url,sha256) in inventory:
        return 'known_version'
    if any(url==canonical_url or digest==sha256 for url,digest in inventory):
        return 'identity_or_version_change_requires_review'
    if observed_utc.astimezone(timezone.utc) <= cutover_utc.astimezone(timezone.utc):
        return 'observation_not_after_cutover'
    if official_date <= cutover_utc.astimezone(timezone.utc).date():
        return 'publication_date_not_proven_after_cutover'
    return 'candidate_requires_official_provenance'
