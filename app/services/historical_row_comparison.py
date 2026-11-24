"""Compare rows only when an independently attested source locator is present.

This module never opens a database, infers identity from names, or writes data.
Callers must derive and independently validate document-local locators first.
"""
from collections import Counter, defaultdict
import re


def _key(row):
    if not isinstance(row,dict) or row.get('locator_attested') is not True:
        return None
    sha=row.get('document_sha256');kind=row.get('record_kind');scope=row.get('scope');ordinal=row.get('source_ordinal')
    if (not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha)
            or not isinstance(kind,str) or not re.fullmatch(r'[a-z_]{1,40}',kind)
            or not isinstance(scope,str) or not re.fullmatch(r'(?:maestros|secundaria_otros|dificil_cobertura|continua|inicio_curso|desplazados|religion_primary_review|expertos)(?:\.[A-Z0-9]{1,8})?',scope)
            or type(ordinal) is not int or ordinal<1 or not isinstance(row.get('fields'),dict)
            or any(not isinstance(k,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',k) for k in row['fields'])):
        return None
    return sha,kind,scope,ordinal


def compare_attested_rows(baseline, reconstructed):
    """Return counts and field names only; never export field values or names.

    A row exclusive to either input is exclusive only to those two snapshots.
    Duplicate source locators and unverifiable locators remain ambiguous.
    """
    sides=[];unkeyed=[]
    for rows in (baseline,reconstructed):
        keyed=defaultdict(list);missing=0
        for row in rows:
            key=_key(row)
            if key is None:missing+=1
            else:keyed[key].append(row['fields'])
        sides.append(keyed);unkeyed.append(missing)
    counts=Counter();details=[]
    for key in sorted(set(sides[0])|set(sides[1])):
        old,new=sides[0].get(key,[]),sides[1].get(key,[])
        if len(old)>1 or len(new)>1:
            category='ambiguous_duplicate_locator';changed=[]
        elif not old:
            category='only_reconstructed_snapshot';changed=[]
        elif not new:
            category='only_baseline_snapshot';changed=[]
        else:
            changed=sorted(k for k in set(old[0])|set(new[0]) if k not in old[0] or k not in new[0] or old[0][k]!=new[0][k])
            category='identical' if not changed else 'different_fields'
        counts[category]+=1
        details.append({'document_sha256':key[0],'record_kind':key[1],'scope':key[2],
                        'source_ordinal':key[3],'category':category,'changed_fields':changed})
    return {'counts':dict(counts),'unkeyed_baseline_rows':unkeyed[0],
            'unkeyed_reconstructed_rows':unkeyed[1],'details':details,
            'identity_inference':'none','values_exported':False,
            'limitation':'Only attested document-local locators were compared; snapshot exclusivity does not prove production absence.'}
