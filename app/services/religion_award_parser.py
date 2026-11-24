"""Extract the masked Religion Primary awards table without inventing codes.

The document has its own layout. Coordinates come from Poppler word boxes;
no destination code or documentary date is inferred from the name or URL.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re,subprocess,xml.etree.ElementTree as ET
from pypdf import PdfReader

PARSER_KEY='religion_primary_award_parser'
PARSER_VERSION='0.1.0'
_MASK=re.compile(r'\*{2,}\d{3,}\*{2,}')
_NS={'x':'http://www.w3.org/1999/xhtml'}

@dataclass(frozen=True)
class ReligionAward:
    masked_identity:str
    petition_reference:int
    order_number:int
    local_order:int
    modality:int
    valencian_flag:int
    person_display_name:str
    destination_text:str
    raw_row_text:str

class ReligionAwardParserService:
    def parse(self,path:Path|str)->list[ReligionAward]:
        path=Path(path)
        source=PdfReader(str(path));identities=[_MASK.findall(page.extract_text() or '') for page in source.pages];counts=[len(x) for x in identities]
        if not sum(counts):raise ValueError('religion_no_masked_rows')
        result=subprocess.run(['pdftotext','-bbox-layout',str(path),'-'],capture_output=True,check=True,timeout=120)
        rows=self.parse_bbox(result.stdout,counts,identities)
        if len(rows)!=sum(counts):raise ValueError('religion_row_count_mismatch')
        return rows

    def parse_bbox(self,content:bytes,expected_counts:list[int],expected_identities:list[list[str]]|None=None)->list[ReligionAward]:
        root=ET.fromstring(content);pages=root.findall('.//x:page',_NS)
        if len(pages)!=len(expected_counts):raise ValueError('religion_page_count_mismatch')
        output=[];surname_heads=None
        for page_index,(page,expected) in enumerate(zip(pages,expected_counts)):
            words=page.findall('.//x:word',_NS)
            heads=sorted(float(w.get('xMin')) for w in words if (w.text or '').upper()=='APELLIDOS')
            if len(heads)==2:surname_heads=heads
            masks=[w for w in words if _MASK.fullmatch(w.text or '')]
            if len(masks)!=expected:raise ValueError('religion_page_rows_mismatch')
            if expected_identities is not None and [w.text for w in masks]!=expected_identities[page_index]:raise ValueError('religion_identity_sequence_mismatch')
            if masks and surname_heads is None:raise ValueError('religion_header_missing')
            for mask in masks:
                y=float(mask.get('yMin'));line=sorted((w for w in words if abs(float(w.get('yMin'))-y)<3),key=lambda w:float(w.get('xMin')))
                numbers=[w for w in line if (w.text or '').isdigit()]
                if len(numbers)!=5:raise ValueError('religion_ambiguous_numeric_fields')
                if float(numbers[0].get('xMin'))<=float(mask.get('xMax')):raise ValueError('religion_ambiguous_field_order')
                name_words=[w for w in line if float(w.get('xMin'))>float(numbers[0].get('xMax')) and float(w.get('xMax'))<float(numbers[1].get('xMin'))]
                boundary2=surname_heads[1]-7;boundary3=2*surname_heads[1]-surname_heads[0]-7;groups=[[],[],[]]
                for word in name_words:
                    x=float(word.get('xMin'));groups[0 if x<boundary2 else 1 if x<boundary3 else 2].append(word.text or '')
                if not all(groups):raise ValueError('religion_ambiguous_name_columns')
                nums=[int(w.text) for w in numbers]
                if nums[1]<=0:raise ValueError('religion_invalid_order')
                destination=[w.text or '' for w in line if float(w.get('xMin'))>float(numbers[4].get('xMax'))]
                if not destination:raise ValueError('religion_missing_destination')
                name=f"{' '.join(groups[0])} {' '.join(groups[1])}, {' '.join(groups[2])}"
                output.append(ReligionAward(mask.text or '',nums[0],nums[1],nums[2],nums[3],nums[4],name,' '.join(destination),' '.join(w.text or '' for w in line)))
        return output
