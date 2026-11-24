"""Parse bilingual expert vacancies not awarded because no requests were made."""
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from pypdf import PdfReader

PARSER_KEY = 'unawarded_expert_vacancies_parser'
PARSER_VERSION = '0.1.0'
NS = {'x':'http://www.w3.org/1999/xhtml'}


@dataclass(frozen=True)
class UnawardedExpertVacancy:
    center_code: str
    source_position_id: str
    province: str
    locality: str
    center_name: str
    cycle: str
    modules: str
    teaching_hours: str
    total_hours: str
    valencian_source: str


def parse(path: Path | str) -> tuple[str, list[UnawardedExpertVacancy]]:
    path=Path(path)
    pdf=PdfReader(str(path))
    texts=[page.extract_text() or '' for page in pdf.pages]
    if len(texts)!=2 or 'Puestos vacantes no adjudicados por ausencia de solicitudes' not in texts[0]:
        raise ValueError('unawarded_expert_header_or_page_count')
    match=re.search(r'DEL\s+(\d{1,2})\s+DE\s+NOVIEMBRE\s+DE\s+(\d{4})', texts[0],re.I)
    if not match or 'Llocs vacants no adjudicats' not in texts[1]:
        raise ValueError('unawarded_expert_procedure_date_or_language_missing')
    date=f'{int(match.group(2)):04d}-11-{int(match.group(1)):02d}'
    from datetime import date as Date
    Date.fromisoformat(date)
    root=ET.fromstring(subprocess.run(['pdftotext','-bbox-layout',str(path),'-'],capture_output=True,check=True,timeout=60).stdout)
    pages=root.findall('.//x:page',NS)
    if len(pages)!=2:raise ValueError('unawarded_expert_geometry_page_count')
    parsed=[]
    for page in pages:
        words=page.findall('.//x:word',NS)
        xx=lambda w:float(w.get('xMin'))
        yy=lambda w:float(w.get('yMin'))
        anchors=sorted((w for w in words if re.fullmatch(r'(?:03|12|46)[0-9A-Z]{6}',w.text or '') and 170<=xx(w)<200),key=yy)
        if not anchors:raise ValueError('unawarded_expert_page_empty')
        page_rows=[]
        for i,anchor in enumerate(anchors):
            y=yy(anchor);low=(yy(anchors[i-1])+y)/2 if i else y-20
            high=(y+yy(anchors[i+1]))/2 if i+1<len(anchors) else float(page.get('height'))-20
            segment=[w for w in words if low<=yy(w)<high]
            def col(a,b):
                parts=sorted((w for w in segment if a<=xx(w)<b),key=lambda z:(round(yy(z)/3),xx(z)))
                return ' '.join(w.text or '' for w in parts).strip()
            positions=[w.text for w in segment if 340<=xx(w)<395 and re.fullmatch(r'\d{1,5}',w.text or '')]
            if len(positions)!=1:raise ValueError('unawarded_expert_position_ambiguous')
            fields=[col(55,110),col(110,170),col(225,340),col(400,520),col(520,685),col(685,735),col(735,825)]
            if any(not x for x in fields[:5]):raise ValueError('unawarded_expert_essential_cell_missing')
            page_rows.append((anchor.text or '',positions[0],*fields))
        parsed.append(page_rows)
    left,right=parsed
    if len(left)!=len(right) or [(x[0],x[1]) for x in left]!=[(x[0],x[1]) for x in right]:
        raise ValueError('unawarded_expert_bilingual_identity_mismatch')
    if len({(x[0],x[1]) for x in left})!=len(left):raise ValueError('unawarded_expert_duplicate_identifier')
    rows=[UnawardedExpertVacancy(*a, valencian_source=' | '.join(b[2:])) for a,b in zip(left,right)]
    return date,rows
