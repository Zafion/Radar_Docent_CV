"""Read the distinct displaced/suppressed secondary awards table conservatively."""
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re,subprocess,unicodedata,xml.etree.ElementTree as ET
from pypdf import PdfReader
PARSER_KEY='displaced_secondary_award_parser'
PARSER_VERSION='0.1.1'
_NS={'x':'http://www.w3.org/1999/xhtml'}
_CENTER=re.compile(r'(?:03|12|46)\d{6}')
def _norm(s):return re.sub(r'[^A-Z]','',unicodedata.normalize('NFKD',s.upper()))
@dataclass(frozen=True)
class DisplacedAward:
    person_display_name:str
    position_code:str
    center_code:str
    center_name:str
    locality:str
    raw_block_text:str
class DisplacedSecondaryAwardParser:
 def parse(self,path:Path|str)->list[DisplacedAward]:
  path=Path(path);pdf=PdfReader(str(path));source=[page.extract_text() or '' for page in pdf.pages]
  head=_norm(source[0])
  if 'RELACIONDEPUESTOSADJUDICADOSADESPLAZADOSSUPRIMIDOSDELCUERPODESECUNDARIA' not in head:raise ValueError('displaced_header_mismatch')
  content=subprocess.run(['pdftotext','-bbox-layout',str(path),'-'],capture_output=True,check=True,timeout=120).stdout
  return self.parse_bbox(content,source)
 def parse_bbox(self,content:bytes,source_pages:list[str])->list[DisplacedAward]:
  root=ET.fromstring(content);pages=root.findall('.//x:page',_NS)
  if len(pages)!=len(source_pages):raise ValueError('displaced_page_count_mismatch')
  output=[]
  for page,source in zip(pages,source_pages):
   words=page.findall('.//x:word',_NS);centers=sorted([w for w in words if _CENTER.fullmatch(w.text or '') and 490<float(w.get('xMin'))<520],key=lambda w:float(w.get('yMin')))
   source_lines=source.splitlines();expected=Counter(re.findall(r'(?<!\d)(?:03|12|46)\d{6}(?!\d)',source))
   if Counter(w.text for w in centers)!=expected:raise ValueError('displaced_center_count_mismatch')
   for index,center in enumerate(centers):
    y=float(center.get('yMin'));line=sorted((w for w in words if abs(float(w.get('yMin'))-y)<3),key=lambda w:float(w.get('xMin')))
    groups=[[],[],[]];positions=[]
    for word in line:
     x=float(word.get('xMin'));t=word.text or ''
     if 90<x<200:groups[0].append(t)
     elif 200<=x<310:groups[1].append(t)
     elif 310<=x<420:groups[2].append(t)
     elif 430<x<490 and re.fullmatch(r'\d{6}',t):positions.append(t)
    if not groups[0] or not groups[2] or len(positions)!=1:raise ValueError('displaced_person_or_position_ambiguous')
    name=' '.join(groups[0]+groups[1])+', '+' '.join(groups[2])
    matching_lines=[line for line in source_lines if (center.text or '') in line]
    if not matching_lines or not any(_norm(name) in _norm(line) and positions[0] in line for line in matching_lines):raise ValueError('displaced_identity_source_mismatch')
    # The line beneath the centre is the locality, not a continuation of its name.
    next_y=float(centers[index+1].get('yMin')) if index+1<len(centers) else y+35
    center_words=sorted((w for w in words if float(w.get('xMin'))>=600 and abs(float(w.get('yMin'))-y)<3),
                       key=lambda w:(round(float(w.get('yMin')),1),float(w.get('xMin'))))
    locality_words=sorted((w for w in words if float(w.get('xMin'))>=600 and abs(float(w.get('yMin'))-(y+16))<3 and float(w.get('yMin'))<next_y-3),key=lambda w:float(w.get('xMin')))
    if not center_words or not locality_words:raise ValueError('displaced_center_or_locality_missing')
    center_name=' '.join(w.text or '' for w in center_words)
    locality=' '.join(w.text or '' for w in locality_words)
    all_words=sorted((w for w in words if y-3<float(w.get('yMin'))<min(next_y-3,y+25)),key=lambda w:(round(float(w.get('yMin')),1),float(w.get('xMin'))))
    output.append(DisplacedAward(name,positions[0],center.text or '',center_name,locality,' '.join(w.text or '' for w in all_words)))
  if not output:raise ValueError('displaced_empty_extraction')
  return output
