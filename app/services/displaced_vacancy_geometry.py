"""Conservative geometric parser for displaced-teacher vacancy tables."""
from collections import defaultdict
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from pypdf import PdfReader

NS = {'x': 'http://www.w3.org/1999/xhtml'}
CENTER = re.compile(r'(?:03|12|46)[0-9A-Z]{6}$')


def parse_displaced_vacancies(path: Path, source_type: str, resolve_overlap):
    from app.services.offered_positions_parser import ParsedOfferedPosition

    try:
        content = subprocess.run(['pdftotext', '-bbox-layout', str(path), '-'], capture_output=True,
                                 check=True, timeout=240).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('displaced_vacancy_pdf_geometry_unavailable') from exc
    pages = ET.fromstring(content).findall('.//x:page', NS)
    pdf = PdfReader(str(path))
    if len(pages) != len(pdf.pages):
        raise ValueError('displaced_vacancy_page_count_mismatch')
    results = []
    for page_number, page in enumerate(pages):
        words = page.findall('.//x:word', NS)
        def x(w): return float(w.get('xMin'))
        def y(w): return float(w.get('yMin'))
        def text(ws): return ' '.join((w.text or '') for w in sorted(ws, key=x)).strip()
        code_headers = [w for w in words if (w.text or '').upper() == 'CODI'
                        and any((other.text or '').upper() == 'ITIN.' and abs(y(other)-y(w))<2.5 for other in words)]
        if len(code_headers) != 1:
            raise ValueError('displaced_vacancy_header_ambiguous')
        header_y = y(code_headers[0])
        def unique_header(label):
            matches = [w for w in words if (w.text or '').upper() == label and abs(y(w)-header_y)<2.5]
            if len(matches) != 1:
                raise ValueError('displaced_vacancy_header_ambiguous')
            return matches[0]
        locality_x = x(unique_header('LOCALITAT'))
        center_x = x(unique_header('CENTRE'))
        code_x = x(unique_header('CODI'))
        flag_x = x(unique_header('ITIN.'))
        observation_x = x(unique_header('OBSERVACIONS'))
        has_lloc = any((w.text or '').upper() == 'LLOC' and abs(y(w)-header_y)<2.5 for w in words)
        if not (locality_x < center_x < code_x < flag_x < observation_x):
            raise ValueError('displaced_vacancy_column_order')
        anchors = sorted([w for w in words if CENTER.fullmatch(w.text or '')
                          and code_x-20 < x(w) < code_x+10 and y(w)>y(unique_header('CODI'))+3], key=y)
        if not anchors:
            raise ValueError('displaced_vacancy_page_without_rows')
        # The table repeats its specialty title on each page. Do not infer it
        # from filenames or carry it between unrelated pages.
        header_words = [w for w in words if header_y-45 < y(w) < header_y-4]
        header = text(header_words)
        match = re.search(r'(?<!\d)([0-9A-Z]{3})\s*-\s*(.+)', header)
        if not match:
            raise ValueError('displaced_vacancy_specialty_missing')
        specialty_code, specialty_name = match.group(1), match.group(2).strip()
        body_name = 'Secundaria y otros cuerpos' if has_lloc else 'Maestros'
        # Extract PDF text operands only for pages with visual overlap.
        overlaps = []
        for anchor in anchors:
            line = [w for w in words if abs(y(w)-y(anchor))<2.5]
            left = [w for w in line if locality_x-3 <= x(w) < center_x-3]
            if any(float(w.get('xMax')) > center_x-3 for w in left):
                overlaps.append(anchor)
        candidates = defaultdict(list)
        if overlaps:
            fragments = []
            def visitor(value, cm, tm, font, size):
                if value.strip():
                    fragments.append((value.strip(), float(tm[4])))
            pdf.pages[page_number].extract_text(visitor_text=visitor)
            for i, (value, _) in enumerate(fragments):
                if not CENTER.fullmatch(value):
                    continue
                before = fragments[max(0, i-5):i]
                if has_lloc:
                    centers = [z for z in before if abs(z[1]-center_x/0.75)<3]
                    localities = [z for z in before if abs(z[1]-locality_x/0.75)<3]
                    if len(centers)==1 and len(localities)==1:
                        candidates[(value, None)].append((localities[0][0], centers[0][0]))
                elif len(before)>=3 and abs(before[-1][1]-center_x/0.75)<3 and re.fullmatch(r'\d{6}', before[-3][0]):
                    candidates[(value, before[-3][0])].append((before[-2][0], before[-1][0]))
        for index, anchor in enumerate(anchors):
            row_y = y(anchor)
            line = [w for w in words if abs(y(w)-row_y)<2.5]
            identifiers = [w.text for w in line if re.fullmatch(r'\d{6}', w.text or '') and x(w)<locality_x-5]
            left = [w for w in line if locality_x-3 <= x(w)<center_x-3]
            right = [w for w in line if center_x-3 <= x(w)<code_x-20]
            if len(identifiers)!=1 or not left or not right:
                raise ValueError('displaced_vacancy_row_ambiguous')
            locality, center_name = text(left), text(right)
            if anchor in overlaps:
                key = (anchor.text, None if has_lloc else identifiers[0])
                locality, center_name = resolve_overlap(locality+center_name, candidates[key])
            next_y = y(anchors[index+1]) if index+1<len(anchors) else float(page.get('height'))-20
            segment = [w for w in words if row_y-2.5 <= y(w) < next_y-2.5]
            # A continuation in either text column needs a proven row split;
            # it must not silently disappear from a successful extraction.
            if any(y(w)>row_y+3 and locality_x-3<=x(w)<code_x-20 for w in segment):
                raise ValueError('displaced_vacancy_wrapped_text_ambiguous')
            flag_words = [w for w in segment if flag_x-5 <= x(w)<observation_x-3]
            if any((w.text or '') not in ('ü', '✓', '✔') for w in flag_words):
                raise ValueError('displaced_vacancy_unknown_itinerancy')
            obs_words = sorted((w for w in segment if x(w)>=observation_x-3), key=lambda w:(y(w), x(w)))
            obs_lines = []
            for word in obs_words:
                if not obs_lines or abs(y(word)-obs_lines[-1][0])>2.5:
                    obs_lines.append((y(word), [word]))
                else:
                    obs_lines[-1][1].append(word)
            observations = ' | '.join(text(group) for _, group in obs_lines) or None
            raw = ' | '.join(filter(None, [identifiers[0], locality, center_name, anchor.text, observations]))
            results.append(ParsedOfferedPosition(
                source_type=source_type, body_code=None, body_name=body_name,
                specialty_code=specialty_code, specialty_name=specialty_name, province=None,
                locality=locality, center_code=anchor.text, center_name=center_name,
                position_code=identifiers[0] if has_lloc else None, hours_text=None, hours_value=None,
                is_itinerant=True if flag_words else None, valenciano_required_text=None,
                position_type=None, composition=None, observations=observations, raw_row_text=raw))
    if not results:
        raise ValueError('displaced_vacancy_empty')
    return results
