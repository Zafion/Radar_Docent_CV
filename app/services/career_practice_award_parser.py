"""Career/practice awards: each result precedes its person's closing row.

Kept separate from the interino block format. Fail closed on unpaired results;
never attach a pending destination to a different closing person.
"""
from dataclasses import dataclass
import json
import re

COLLECTIVES = r"REINGRESSATS|PROVISIONALS|PRÀCTIQUES|ESTABILITZATS|NO APTES"
CLOSING_RE = re.compile(
    rf"^(?P<order>\d{{1,5}})\s*(?P<name>.*?)\s*(?P<collective>{COLLECTIVES})(?P<suffix>.*)$"
)
RESULT_RE = re.compile(r"(?:No adjudicat\.|Adjudicat\b)")
HEADING_RE = re.compile(r"^(?P<name>.+?)\s+\((?P<code>[0-9A-Z]{2,5})\)$")
FOOTER_RE = re.compile(r"P[àáa]g\..*$", re.IGNORECASE)
BODY_PREFIXES = ("PROFESSORS ", "PROFESORES ", "MESTRES", "MAESTROS", "CATEDRÀTICS", "CATEDRATICOS", "M. DE TALLER")


class CareerAwardValidationError(ValueError):
    def __init__(self, **metrics):
        self.metrics = metrics
        super().__init__('career_award_validation_failed: ' + json.dumps(metrics, sort_keys=True))


@dataclass
class CareerAwardRow:
    order_number: int
    person_display_name: str
    status: str
    body_name: str | None
    specialty_code: str | None
    specialty_name: str | None
    raw_block_text: str
    assignment: dict | None


def is_career_layout(lines: list[str]) -> bool:
    return any('Cognoms' in line and 'Col·lectiu' in line for line in lines) or (
        any(CLOSING_RE.match(line) for line in lines)
        and any(re.search(r"Adjudicat\s+(?:per la petició|d'ofici)|No adjudicat\.", line) for line in lines)
    )


def parse_career_awards(lines: list[str]) -> list[CareerAwardRow]:
    body = specialty_code = specialty_name = None
    pending = []
    rows = []
    context = None
    source_results = sum(len(RESULT_RE.findall(line)) for line in lines)

    def heading(line):
        nonlocal body, specialty_code, specialty_name
        if match := HEADING_RE.match(line):
            specialty_code, specialty_name = match['code'], match['name'].strip()
            return True
        if line.startswith(BODY_PREFIXES):
            if line == body:
                return True
            # Wrapped centre descriptions may also contain these words. Only
            # treat them as headings outside an unfinished person's record.
            if not pending:
                body, specialty_code, specialty_name = line, None, None
                return True
        return False

    for original in lines:
        line = FOOTER_RE.sub('', original).strip()
        if not line or line.startswith(('ADJUDICACIÓ', 'RESOLUCIÓ', 'Ordre Cognoms')):
            continue
        close = CLOSING_RE.match(line)
        if close and (not close['name'] or ',' in close['name']):
            record_context = context or (body, specialty_code, specialty_name)
            rows.append(_parse_record(pending, close, record_context, len(rows) + 1))
            pending = []
            context = None
            suffix = close['suffix'].strip()
            if suffix:
                heading(suffix)
            continue
        if heading(line):
            continue
        if not pending:
            context = (body, specialty_code, specialty_name)
        pending.append(line)

    pending_results = sum(len(RESULT_RE.findall(line)) for line in pending)
    if not rows or source_results != len(rows) or pending_results:
        raise CareerAwardValidationError(
            source_results=source_results, parsed_rows=len(rows), pending_results=pending_results,
        )
    return rows


def _parse_record(lines, close, context, block_number):
    # In 2026 the closing line contains the person; in 2025 the name is a
    # separate line after the result, before the order/collective.
    name = close['name'].strip()
    content = list(lines)
    if not name:
        result_index = next((i for i, line in enumerate(content) if RESULT_RE.search(line)), None)
        specialty_index = next((i for i, line in enumerate(content)
            if result_index is not None and i > result_index
            and re.match(r"^(?:I\s+)?[0-9][0-9A-Z]{1,4}\s+", line)), len(content))
        candidates = [(i, line) for i, line in enumerate(content)
                      if result_index is not None and result_index < i < specialty_index
                      and ',' in line and not re.match(r'^\d', line)]
        if len(candidates) != 1:
            raise CareerAwardValidationError(block_number=block_number, issue='ambiguous_person')
        index, _ = candidates[0]
        # Names may wrap onto another line before the numeric specialty. Keep
        # that continuation with the person, never as destination metadata.
        name = ' '.join(line for line in content[index:specialty_index] if line not in ('--', 'I'))
        del content[index:specialty_index]
    joined = ' '.join(content)
    results = list(RESULT_RE.finditer(joined))
    if len(results) != 1 or not name or ',' not in name:
        raise CareerAwardValidationError(block_number=block_number, issue='unpaired_result')
    result = results[0]
    status = 'No adjudicat' if result.group().startswith('No ') else 'Adjudicat'
    assignment = None
    if status == 'Adjudicat':
        destination = joined[:result.start()].strip()
        centers = list(re.finditer(r'\b(?P<code>\d{8})\s+', destination))
        position = re.search(r'\bLloc:\s*(?P<code>\d{6})(?!\d)', joined[result.end():])
        if len(centers) != 1 or position is None:
            raise CareerAwardValidationError(block_number=block_number, issue='missing_destination')
        center = centers[0]
        center_text = destination[center.end():].strip()
        center_name, separator, locality = center_text.partition(' - ')
        if not separator:
            # A centre code is reliable even when the locality delimiter changes.
            center_name, locality = center_text, None
        outcome = joined[result.end():]
        petition = re.search(r'petició número\s+(\d+)', outcome)
        tail = outcome[position.end():].strip()
        tail = re.sub(r'^\((?:Itinerant|Mixta)\)\s*', '', tail)
        specialty = re.match(r'^(?P<code>[0-9A-Z]{2,5})\s+(?P<name>.+)$', tail)
        assignment = dict(
            assignment_kind=None, locality=locality, center_code=center['code'],
            center_name=center_name or None,
            position_specialty_code=specialty['code'] if specialty else None,
            position_specialty_name=specialty['name'] if specialty else None,
            position_code=position['code'], hours_text=None, hours_value=None,
            petition_text=outcome.strip(), petition_number=int(petition[1]) if petition else None,
            request_type=None, raw_assignment_text=joined,
        )
    return CareerAwardRow(
        order_number=int(close['order']), person_display_name=name, status=status,
        body_name=context[0], specialty_code=context[1], specialty_name=context[2],
        raw_block_text='\n'.join(lines + [close.group()]), assignment=assignment,
    )
