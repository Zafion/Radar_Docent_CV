"""Conservative, pre-write validation for the existing non-teaching formats."""
from collections import Counter
import json
import re
import unicodedata


class NonDocentExtractionError(ValueError):
    def __init__(self, reason, metrics):
        self.reason, self.metrics = reason, metrics
        super().__init__('non_docent_' + reason + ': ' + json.dumps(metrics, sort_keys=True))


class EmptyNonDocentDocument(NonDocentExtractionError):
    """A supported explicit empty notice; preserve any existing publication rows."""


class BufferedNonDocentRows:
    """Buffer only this parser's inserts until completeness has been checked."""
    def __init__(self, store):
        self.store = store
        self.commands = []
        self.snapshot_count = 0

    def get_staff_group_id_by_code(self, code):
        return self.store.get_staff_group_id_by_code(code)

    def insert_offered_position(self, **values):
        self.commands.append(('insert_offered_position', values, None))

    def insert_award(self, **values):
        self.commands.append(('insert_award', values, None))

    def insert_bag_snapshot(self, **values):
        self.snapshot_count += 1
        self.commands.append(('insert_bag_snapshot', values, self.snapshot_count))
        return self.snapshot_count

    def insert_bag_member(self, **values):
        self.commands.append(('insert_bag_member', values, None))

    def replay(self, store, publication_id):
        snapshots = {}
        for method, values, virtual_id in self.commands:
            values = dict(values)
            if 'publication_id' in values:
                values['publication_id'] = publication_id
            if 'snapshot_id' in values:
                values['snapshot_id'] = snapshots[values['snapshot_id']]
            result = getattr(store, method)(**values)
            if virtual_id is not None:
                snapshots[virtual_id] = result


def validate_non_docent_rows(family, text, lines, buffer, extracted):
    flat = ' '.join(text.split())
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', flat.lower()) if not unicodedata.combining(c))
    normalized = normalized.replace('’', "'")
    if family == 'non_docent_adc_award':
        marker_count = len(re.findall(r'\b(?:S[ií]|No)\s+\d+\s*[-–]', flat, re.I))
        method = 'insert_award'
        required = ('bag_code', 'bag_name', 'score', 'person_display_name', 'position_code', 'position_text')
        explicit_empty = any(s in normalized for s in ('no hay adjudicatarios', 'no hi ha persones adjudicades'))
        # The standard desertion/withdrawal template is NOT itself a zero claim.
        # Require no award markers, a procedure heading, and actual listed posts.
        section = re.search(r'(?:queden deserts|quedan desiertos|se han retirado|s\'han retirat).*', normalized)
        result_table_present = any(s in normalized for s in ('puntuacio', 'puntuacion', 'cognoms', 'apellidos'))
        explicit_empty |= bool('adjudicaci' in normalized and not result_table_present and section
                               and re.search(r'\b\d{3,6}\s*-', section.group()))
    elif family == 'non_docent_adc_call':
        marker_count = sum(bool(re.match(r'^\d{3,6}\s+[A-Z]\d?\s+\d+\s+[A-Z0-9]+\b', line)) for line in lines)
        method = 'insert_offered_position'
        required = ('position_code', 'classification', 'center_name')
        explicit_empty = any(s in normalized for s in ('no hay puestos ofertados', 'no hi ha llocs oferits', 'no hi ha llocs ofertats'))
    else:
        # Count identifiers, not "people": mixed/excluded sections may need a
        # different parser. Unaccounted identifiers block replacement, not invent rows.
        marker_count = len(re.findall(r'\*{3}\d{4}\*{2}', text))
        method = 'insert_bag_member'
        required = ('order_number', 'masked_dni', 'person_display_name', 'total_score')
        explicit_empty = any(s in normalized for s in ('no hay personas integrantes', 'no hi ha persones integrants'))

    rows = [values for name, values, _ in buffer.commands if name == method]
    missing = Counter()
    for row in rows:
        for field in required:
            if row.get(field) is None or row.get(field) == '':
                missing[field] += 1
        code = row.get('position_code')
        if code is not None and not re.fullmatch(r'\d{3,6}', str(code)):
            missing['valid_position_code'] += 1
        if method == 'insert_bag_member' and (not re.fullmatch(r'\*{3}\d{4}\*{2}', row.get('masked_dni') or '') or (row.get('order_number') or 0) < 1):
            missing['valid_bag_identity'] += 1
    for name, values, _ in buffer.commands:
        if name == 'insert_bag_snapshot' and values.get('bag_code') in (None, '', 'UNKNOWN'):
            missing['bag_context'] += 1

    metrics = {'family': family, 'markers': marker_count, 'extracted': extracted,
               'buffered': len(rows), 'unaccounted_markers': max(0, marker_count-len(rows)),
               'missing_fields': dict(missing)}
    if not flat:
        raise NonDocentExtractionError('unreadable_text', metrics)
    if missing:
        raise NonDocentExtractionError('missing_fundamental_fields', metrics)
    if extracted != len(rows) or len(rows) != marker_count:
        raise NonDocentExtractionError('incomplete_or_unsupported_sections', metrics)
    if not rows:
        if explicit_empty:
            raise EmptyNonDocentDocument('explicitly_empty', metrics)
        raise NonDocentExtractionError('unrecognized_format', metrics)
    return metrics
