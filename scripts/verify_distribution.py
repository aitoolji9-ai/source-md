"""Install a built wheel into a new environment and exercise its public paths."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def verify(wheel: Path, out: Path, require_office=False):
    out=out.resolve();out.mkdir(parents=True,exist_ok=False)
    def run(command, *, stdin=None, timeout=180):
        result=subprocess.run([str(x) for x in command],input=stdin,cwd=out,capture_output=True,text=True,timeout=timeout)
        with (out/'commands.log').open('a',encoding='utf-8') as log:
            log.write(json.dumps({'command':[str(x) for x in command],'exit_code':result.returncode},ensure_ascii=False)+'\n'+result.stdout+result.stderr+'\n')
        if result.returncode:raise RuntimeError('Distribution check failed: '+result.stdout[-1000:]+result.stderr[-1000:])
        return result.stdout
    requirements=out/'requirements.txt'
    run(['uv','export','--project',ROOT,'--frozen','--no-dev','--no-emit-project','--format','requirements-txt','--output-file',requirements])
    run(['uv','venv',out/'environment','--python','3.12'])
    python=out/'environment'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    run(['uv','pip','install','--python',python,'-r',requirements,wheel.resolve()])
    # This code runs using the installed wheel, from outside the source tree.
    code=r'''
import io,json,os,shutil,subprocess,sys,zipfile
from pathlib import Path
from bs4 import BeautifulSoup
from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches
import source_preserve_md
from source_preserve_md.engine import PRIMARY,PARSER,convert,validate_bundle
from source_preserve_md.integration import install_skill,doctor
from source_preserve_md.server import create_app
from source_preserve_md.legacy import convert_office

config=json.load(sys.stdin);root=Path.cwd();package=Path(source_preserve_md.__file__).parent
assert 'site-packages' in str(package) and package.is_relative_to(root/'environment')
assert all((package/name).is_file() for name in ['vision_ocr.swift','web/index.html','web/app.js','web/tab-collector.js','web/review.js','web/review.css','skill/SKILL.md','skill/scripts/run.py'])
checks=[]
def check(name,value):
    assert value,name
    checks.append({'case':name,'passed':True})
skill=root/'host skills/source-preserve-md';install_skill(skill)
original=b'---\r\ntitle: Exact\r\n---\r\n\r\n# Literal\r\n'
(root/'input.md').write_bytes(original)
p=subprocess.run([sys.executable,str(skill/'scripts/run.py'),'convert','input.md','--out','from-skill','--ocr','off'],capture_output=True,text=True)
check('installed-skill-relative-path-and-byte-copy',p.returncode==0 and (root/'from-skill'/PRIMARY).read_bytes()==original and validate_bundle(root/'from-skill')['passed'])

html='<body><h1>표 순서</h1><table><tr><th>A</th><th>B</th></tr><tr><td>17</td><td></td></tr><tr><td>23</td><td>Done</td></tr></table><pre><code>literal &lt;x&gt;\n  two</code></pre></body>'
out=root/'html';result=convert('table.html',out,data=html.encode(),ocr='off')
soup=BeautifulSoup(PARSER.render((out/PRIMARY).read_text()),'html.parser')
check('html-table-rows-columns-blanks',[[c.get_text() for c in row.find_all(['th','td'])] for row in soup.table.find_all('tr')]==[['A','B'],['17',''],['23','Done']])
check('html-code',soup.code.get_text()=='literal <x>\n  two\n' and json.loads((out/'structure.json').read_text())['units'][0]['code_blocks']==['literal <x>\n  two'])

inputs=root/'inputs';inputs.mkdir();marker='SHARE_SOURCE_84729'
doc=Document();doc.add_paragraph(marker);table=doc.add_table(rows=2,cols=2)
for row,values in zip(table.rows,[['A','B'],['17','']]):
 for cell,text in zip(row.cells,values):cell.text=text
doc.save(inputs/'sample.docx')
book=Workbook();sheet=book.active;sheet.append([marker,'Value']);sheet.append(['Alpha',17]);sheet.append(['Beta',None]);book.save(inputs/'sample.xlsx')
slides=Presentation();slide=slides.slides.add_slide(slides.slide_layouts[6]);slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text_frame.text=marker;slides.save(inputs/'sample.pptx')
for fmt in ['docx','xlsx','pptx']:
 folder=root/fmt;convert(inputs/('sample.'+fmt),folder,ocr='off');structure=json.loads((folder/'structure.json').read_text())
 check('native-'+fmt,marker in ''.join(u['text'] for u in structure['units']) and validate_bundle(folder)['passed'])

legacy=[]
if doctor()['legacy_office']['executable']:
 legacy_dir=root/'legacy-inputs';legacy_dir.mkdir()
 for native,old in [('docx','doc'),('xlsx','xls'),('pptx','ppt')]:
  binary,_=convert_office(inputs/('sample.'+native),legacy_dir,old,root/('profile-'+old))
  folder=root/old;convert(binary,folder,ocr='off');structure=json.loads((folder/'structure.json').read_text())
  check('legacy-'+old,marker in ''.join(u['text'] for u in structure['units']) and validate_bundle(folder)['passed']);legacy.append(old)
elif config['require_office']:raise AssertionError('LibreOffice required for release verification')
for name in ['table.hwp','footnote-endnote.hwp']:
 folder=root/name;convert(Path(config['fixtures'])/name,folder,ocr='off');check('hwp-'+name,validate_bundle(folder)['passed'])

if sys.platform=='darwin':
 from PIL import Image,ImageDraw,ImageFont
 from source_preserve_md.engine import correct_bundle
 image=Image.new('RGB',(900,180),'white');draw=ImageDraw.Draw(image)
 font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',52)
 draw.text((30,50),'OCR INSTALL 84729',font=font,fill='black');image.save(inputs/'ocr.png')
 from source_preserve_md.ocr import recognize
 english=recognize(inputs/'ocr.png',language='en-US')
 check('installed-english-native-language-option','OCRINSTALL84729' in ''.join(''.join(o['text'] for o in english['observations']).split()))
 folder=root/'actual-ocr';converted=convert(inputs/'ocr.png',folder)
 structure=json.loads((folder/'structure.json').read_text());unit=next(u for u in structure['units'] if u['metadata'].get('ocr_data'))
 check('actual-installed-ocr',converted['ocr_used'] and 'OCRINSTALL84729' in ''.join(unit['metadata']['ocr_text'].split()))
 raw=json.loads((folder/unit['metadata']['ocr_data']).read_text());block=next(b for b in raw['refinement']['blocks'] if b['kind']=='text')
 action={'schema_version':1,'source_sha256':__import__('hashlib').sha256((inputs/'ocr.png').read_bytes()).hexdigest(),'actions':[{'kind':'line','target':raw['source_locator'],'line':block['source_observation'],'expected':block['text'],'replacement':block['text']+' CHECKED'}]}
 corrected=correct_bundle(folder,root/'corrected',action)
 check('installed-ocr-correction',corrected['ocr_rerun'] is False and validate_bundle(root/'corrected')['passed'] and 'CHECKED' in (root/'corrected'/PRIMARY).read_text() and 'CHECKED' not in (folder/PRIMARY).read_text())
 fixture=Path(config['table_fixture']);truth=json.loads(fixture.with_suffix('.json').read_text())
 folder=root/'shadowed-merged-table';convert(fixture,folder)
 tables=BeautifulSoup(PARSER.render((folder/PRIMARY).read_text()),'html.parser').find_all('table')
 check('installed-shadowed-merged-table-detected',len(tables)==1)
 occupied=set();actual={}
 for ri,row in enumerate(tables[0].find_all('tr')):
  ci=0
  for cell in row.find_all(['td','th'],recursive=False):
   while (ri,ci) in occupied:ci+=1
   rs,cs=int(cell.get('rowspan',1)),int(cell.get('colspan',1))
   actual[(ri,ci,rs,cs)]=''.join(cell.get_text().split())
   occupied.update((r,c) for r in range(ri,ri+rs) for c in range(ci,ci+cs));ci+=cs
 expected={tuple(c[k] for k in ['row','column','rowspan','colspan']):''.join(c['text'].split()) for c in truth['cells']}
 check('installed-shadowed-table-md-values-spans-blank',actual==expected and validate_bundle(folder)['passed'])

# Fresh installed PDF job, real OCR and the public installed-skill resume path.
if sys.platform=='darwin':
 import pymupdf
 pdf=pymupdf.open()
 for number in range(3):
  page=pdf.new_page(width=900,height=250)
  page.insert_image(pymupdf.Rect(0,0,900,180),filename=str(inputs/'ocr.png'))
  page.insert_link({'kind':pymupdf.LINK_URI,'from':pymupdf.Rect(0,200,100,240),'uri':'https://example.com/'})
 pdf.save(inputs/'resumable.pdf');pdf.close()
 folder=root/'pdf-resume'
 first=subprocess.run([sys.executable,str(skill/'scripts/run.py'),'convert',str(inputs/'resumable.pdf'),'--out',str(folder),'--pdf-max-pages','1'],capture_output=True,text=True)
 check('installed-pdf-checkpoint-exit3',first.returncode==3 and json.loads(first.stdout)['processing']['checkpointed_pages']==1 and validate_bundle(folder)['passed'])
 resumed=subprocess.run([sys.executable,str(skill/'scripts/run.py'),'resume',str(folder)],capture_output=True,text=True)
 state=json.loads((folder/'pdf-job.json').read_text());text=(folder/PRIMARY).read_text()
 check('installed-skill-pdf-resume-and-merge',resumed.returncode==0 and json.loads(resumed.stdout)['processing']['complete'] and validate_bundle(folder)['passed'] and text.count('OCR INSTALL 84729')==3)
 check('installed-pdf-reuses-completed-pages',[entry['page'] for entry in state['history']]==[1,2,3] and len(list((folder/'source').glob('*.pdf')))==1)
 status=subprocess.run([sys.executable,str(skill/'scripts/run.py'),'status',str(folder)],capture_output=True,text=True)
 check('installed-pdf-readonly-status',status.returncode==0 and json.loads(status.stdout)['complete'] and not json.loads(status.stdout)['writer_active'])

 batch_inputs=root/'batch-inputs';batch_inputs.mkdir()
 for name in ('01.pdf','02.pdf'):shutil.copyfile(inputs/'resumable.pdf',batch_inputs/name)
 batch_root=root/'batch-output'
 batch_first=subprocess.run([sys.executable,str(skill/'scripts/run.py'),'batch',str(batch_inputs),'--out',str(batch_root),'--max-pages','2'],capture_output=True,text=True)
 check('installed-batch-checkpoint-exit3',batch_first.returncode==3 and json.loads(batch_first.stdout)['checkpointed_pages']==2)
 checkpoint=(batch_root/'01/checkpoint/pages/0001.json').read_bytes()
 poison=root/'host-python';poison.mkdir();(poison/'source_preserve_md.py').write_text("raise RuntimeError('HOST PYTHONPATH LEAKED')")
 batch_resume=subprocess.run([sys.executable,str(skill/'scripts/run.py'),'resume',str(batch_root)],env={**os.environ,'PYTHONPATH':str(poison)},capture_output=True,text=True)
 batch=json.loads(batch_resume.stdout)
 check('installed-skill-batch-with-host-pythonpath',batch_resume.returncode==0 and batch['complete'] and batch['books_complete']==2 and batch['checkpointed_pages']==6 and checkpoint==(batch_root/'01/checkpoint/pages/0001.json').read_bytes())
 batch_status=subprocess.run([sys.executable,str(skill/'scripts/run.py'),'status',str(batch_root)],capture_output=True,text=True)
 check('installed-batch-merges-and-reuses-pages',json.loads(batch_status.stdout)['complete'] and all(validate_bundle(batch_root/name)['passed'] and [e['page'] for e in json.loads((batch_root/name/'pdf-job.json').read_text())['history']]==[1,2,3] for name in ['01','02']))

# Installed wheel's live Flask routes and download bytes, without source checkout imports.
app=create_app(root/'web-outputs');client=app.test_client();token=client.get('/api/config').json['token']
created=client.post('/api/convert',headers={'X-Local-Token':token},data={'file':(io.BytesIO(original),'input.md')})
check('web-upload',created.status_code==200)
job=created.json['id'];check('web-review',client.get('/api/jobs/'+job+'/review').status_code==200)
download=client.get('/api/jobs/'+job+'/zip')
with zipfile.ZipFile(io.BytesIO(download.data)) as z:check('web-download-byte-copy',z.read(PRIMARY)==original and z.read('source/input.md')==original)
check('web-static-assets',all(client.get('/static/'+name).status_code==200 for name in ['app.js','style.css','review.js','review.css','dom-capture.js']))
report={'version':source_preserve_md.__version__,'wheel_import':True,'checks':checks,'passed':len(checks),'failed':0,'legacy_office':legacy,'doctor':doctor()}
(root/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':len(checks),'failed':0,'version':source_preserve_md.__version__}))
'''
    run([python,'-c',code],stdin=json.dumps({'fixtures':str(ROOT/'examples/legacy-inputs'),'table_fixture':str(ROOT/'tests/fixtures/shadowed-merged-table.png'),'require_office':require_office}),timeout=300)
    return json.loads((out/'verification.json').read_text())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--wheel',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--require-office',action='store_true')
    a=p.parse_args();r=verify(a.wheel,a.out,a.require_office);print(json.dumps({'passed':r['passed'],'failed':r['failed'],'version':r['version']}))
