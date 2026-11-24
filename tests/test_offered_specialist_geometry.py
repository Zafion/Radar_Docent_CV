import json,collections,re,subprocess,xml.etree.ElementTree as ET,unicodedata,unittest
from pypdf import PdfReader
from app.services.offered_positions_parser import OfferedPositionsParserService
p=json.load(open('auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json'));e=next(x for x in p['entries'] if x['version_id']==47);path=e['local_sha_verified_paths'][0];rows=OfferedPositionsParserService()._parse_document({'file_path':path,'list_scope':'continua'});root=ET.fromstring(subprocess.run(['pdftotext','-bbox-layout',path,'-'],capture_output=True,check=True).stdout);ns={'x':'http://www.w3.org/1999/xhtml'};independent=[];pdf=PdfReader(path)
for page,pdfpage in zip(root.findall('.//x:page',ns),pdf.pages):
 lines=[];prev=[]
 def op(op,args,cm,tm):
  if op==b'm':prev[:]=[(float(args[0]),float(args[1]))]
  elif op==b'l' and prev:
   x,y=map(float,args);ox,oy=prev[0];prev[:]=[(x,y)]
   if abs(y-oy)<.2 and abs(x-ox)>200:lines.append(round(y,1))
 pdfpage.extract_text(visitor_operand_before=op);borders=sorted(set(lines),reverse=True)[1:];words=page.findall('.//x:word',ns);height=float(page.get('height'));anchors=sorted([((float(w.get('yMin'))+float(w.get('yMax')))/2,w.text) for w in words if 65<float(w.get('xMin'))<103 and re.fullmatch(r'\d{8}',w.text or '')]);assert len(anchors)+1==len(borders)
 for i,(y,code) in enumerate(anchors):
  high,low=borders[i:i+2];cols=collections.defaultdict(list)
  for w in words:
   x,z=float(w.get('xMin')),(float(w.get('yMin'))+float(w.get('yMax')))/2;t=w.text or ''
   if not low < height-z < high:continue
   field='identifier' if 40<x<70 else 'code' if 70<=x<103 else 'center' if 103<=x<260 else 'locality' if 260<=x<320 else 'specialty' if 320<=x<520 else 'period' if 520<=x<605 else 'hours' if 615<=x<635 and abs(z-y)<3 and re.fullmatch(r'\d{1,2}',t) else 'observations' if 605<=x<800 else None
   if field:cols[field].append((z,x,t))
  independent.append({k:' '.join(t for z,x,t in sorted(v)) for k,v in cols.items()})
def norm(x):return re.sub(r'\W','',unicodedata.normalize('NFKD',x or '').casefold())
diff=collections.Counter()
for a,b in zip(rows,independent):
 parts=a.observations.split(' | ');period=next((x.removeprefix('Període: ') for x in parts if x.startswith('Període: ')),None);obs=' '.join(x for x in parts if not x.startswith(('Lloc: ','Període: ')))
 for key,aa,bb in [('identifier',next(x.removeprefix('Lloc: ') for x in parts if x.startswith('Lloc: ')),b.get('identifier')),('center_code',a.center_code,b.get('code')),('center',a.center_name,b.get('center')),('locality',a.locality,b.get('locality')),('specialty',a.specialty_name,b.get('specialty')),('hours',a.hours_text,b.get('hours')),('period',period,b.get('period')),('observations',obs,b.get('observations'))]:
  if norm(aa)!=norm(bb):diff[key]+=1

class SpecialistGeometryTests(unittest.TestCase):
 def test_pdf_geometry_matches_independent_poppler(self):
  self.assertEqual(len(rows),56);self.assertEqual(len(independent),56);self.assertEqual(dict(diff),{});self.assertTrue(all(r.position_code is None and r.specialty_code is None for r in rows))
if __name__=='__main__':unittest.main()
