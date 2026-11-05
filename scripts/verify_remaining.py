"""Exercise reported gaps with known-answer files, without changing the engine.

Results distinguish output integrity from useful Markdown fidelity. Unsupported
or intentionally bounded modes can fail an end-user criterion without being an
unexpected parser crash. No browser sessions or external accounts are used here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymupdf
from bs4 import BeautifulSoup
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont

from source_preserve_md.engine import PARSER, PRIMARY, validate_bundle


PROJECT = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT / 'skills/source-preserve-md/scripts/run.py'
VALUES = [['Item','Count','Note'],['Alpha','17',''],['Beta','','Done'],['','','']]


def normalized(value):
    return ''.join(unicodedata.normalize('NFC',value or '').split())


def fingerprint():
    return {str(p.relative_to(PROJECT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((PROJECT/'src').rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def raster_table(lines=True):
    image = Image.new('RGB',(960,600),'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',34)
    if lines:
        for x in (60,340,620,900): draw.line((x,100,x,500),fill=(170,170,170),width=2)
        for y in (100,200,300,400,500): draw.line((60,y,900,y),fill=(170,170,170),width=2)
    for r,row in enumerate(VALUES):
        for c,value in enumerate(row): draw.text((80+c*280,130+r*100),value,fill='black',font=font)
    return image


def tables_from_md(md):
    soup = BeautifulSoup(PARSER.render(md),'html.parser')
    return [[[cell.get_text() for cell in tr.find_all(['td','th'])] for tr in table.find_all('tr')]
            for table in soup.find_all('table')]


def same_matrix(a,b):
    return len(a)==len(b) and all(len(x)==len(y) and all(normalized(p)==normalized(q) for p,q in zip(x,y)) for x,y in zip(a,b))


def check_footnote_links(bundle):
    soup=BeautifulSoup(PARSER.render(bundle['markdown']),'html.parser')
    body='\n'.join(u['markdown'] for u in bundle['structure']['units'] if u.get('source_locator')=='body')
    body_soup=BeautifulSoup(PARSER.render(body),'html.parser')
    links=[a.get('href') for a in body_soup.find_all('a') if a.get('href','').startswith('#')]
    resolved=[target for target in links if soup.find(id=unquote(target[1:])) is not None]
    text_preserved='FOOTNOTE EVIDENCE' in soup.get_text()
    return {'passed':text_preserved and bool(resolved),'footnote_text_preserved':text_preserved,
            'expected':'본문 표식에서 해당 각주로 이동하는 연결','actual_body_reference_links':links,
            'resolved_body_reference_links':resolved,'criterion':'각주 본문 보존과 본문-각주 연결을 함께 확인'}


def check_grid(bundle):
    tables = tables_from_md(bundle['markdown'])
    return {'passed':len(tables)==1 and same_matrix(tables[0],VALUES),
            'expected':VALUES,'actual':tables,'criterion':'4행×3열과 빈칸 5개를 동일 셀 위치로 복원'}


def run(output,reference_inputs=None,dom_snapshot=None):
    output.mkdir(parents=True,exist_ok=False)
    inputs = output/'inputs';inputs.mkdir()
    before = fingerprint()
    rows = []

    def exercise(name,label,source,check,*,ocr='off',scope='known-answer fixture',limitation='',extra_args=()):
        folder=output/name
        try:
            if reference_inputs is not None:
                source=reference_inputs/source.relative_to(inputs)
            p=subprocess.run([sys.executable,str(WRAPPER),'convert',str(source.resolve()),'--ocr',ocr,'--out',str(folder.resolve()),*extra_args],capture_output=True,text=True,timeout=210)
            (output/(name+'-cli.json')).write_text(p.stdout)
            result=json.loads(p.stdout)
            if not (folder/PRIMARY).is_file(): raise RuntimeError(result)
            bundle={'folder':folder,'result':result,'markdown':(folder/PRIMARY).read_text(),
                    'structure':json.loads((folder/'structure.json').read_text())}
            finding=check(bundle)
            integrity=validate_bundle(folder)
            row={'case':name,'label':label,'scope':scope,'limitation':limitation,
                 'status':'pass' if finding['passed'] else 'fail','finding':finding,
                 'cli_exit':p.returncode,'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                 'output':str(folder),'integrity_passed':integrity['passed'],
                 'conversion_mode':'observed-dom' if extra_args else 'static/direct',
                 'machine_checks':result.get('machine_checks'),
                 'warning_codes':[w['code'] for w in result.get('warnings',[])]}
        except Exception as error:
            row={'case':name,'label':label,'status':'error','source':str(source),'error':f'{type(error).__name__}: {error}'}
        rows.append(row)
        print(json.dumps({'case':name,'status':row['status'],'finding':row.get('finding'),'error':row.get('error')},ensure_ascii=False),flush=True)

    # Same content, independently specified table geometry, then degraded inputs.
    image=raster_table();path=inputs/'ruled.png';image.save(path)
    exercise('ruled-control','직선 격자표 기준 사례',path,check_grid,ocr='auto')
    path=inputs/'borderless.png';raster_table(False).save(path)
    def borderless_check(b):
        actual=tables_from_md(b['markdown']);expected=VALUES[:3]
        # A trailing all-empty borderless row is indistinguishable from margin;
        # only the three visibly populated rows are part of this criterion.
        return {'passed':len(actual)==1 and same_matrix(actual[0],expected),'expected':expected,'actual':actual,
                'criterion':'시각적으로 식별 가능한 3행×3열과 내부 빈칸 2개를 복원; 여백 속 빈 행은 추정하지 않음'}
    exercise('borderless','선 없는 이미지 표',path,borderless_check,ocr='auto')
    path=inputs/'skewed.png';image.rotate(5,expand=True,resample=Image.Resampling.BICUBIC,fillcolor='white').save(path)
    exercise('skewed','5도 기울어진 이미지 표',path,check_grid,ocr='auto')
    broken=image.copy();draw=ImageDraw.Draw(broken)
    for y in (100,200,300,400,500): draw.rectangle((400,y-2,600,y+3),fill='white')
    path=inputs/'broken.png';broken.save(path)
    exercise('broken-lines','가로선이 끊긴 이미지 표',path,check_grid,ocr='auto')

    doc=pymupdf.open()
    for index,data in enumerate(([['Alpha','17',''],['Beta','','Done']],[['Gamma','23',''],['Delta','','End']])):
        page=doc.new_page(width=600,height=420)
        page.insert_text((40,40),'TABLE CONTINUED' if index else 'TABLE START')
        for x in (40,210,380,550): page.draw_line((x,80),(x,260))
        for y in (80,140,200,260): page.draw_line((40,y),(550,y))
        for r,row in enumerate([VALUES[0],*data]):
            for c,value in enumerate(row):page.insert_text((50+c*170,110+r*60),value,fontsize=16)
    path=inputs/'continued.pdf';doc.save(path);doc.close()
    expected=[VALUES[0],['Alpha','17',''],['Beta','','Done'],['Gamma','23',''],['Delta','','End']]
    def continued(b):
        actual=tables_from_md(b['markdown'])
        return {'passed':len(actual)==1 and same_matrix(actual[0],expected),'expected':expected,'actual':actual,'criterion':'명시적인 연속 표를 중복 머리글 없이 하나의 표로 연결'}
    exercise('continued-table','여러 페이지의 연속 표',path,continued)

    # Visual order differs deliberately from PDF producer-object order.
    doc=pymupdf.open();page=doc.new_page(width=700,height=450)
    for text,point in [('RIGHT ONE',(390,120)),('RIGHT TWO',(390,170)),('LEFT ONE',(40,120)),('LEFT TWO',(40,170))]:
        page.insert_text(point,text,fontsize=20)
    path=inputs/'two-columns.pdf';doc.save(path);doc.close()
    order=['LEFT ONE','LEFT TWO','RIGHT ONE','RIGHT TWO']
    def reading_order(b):
        text='\n'.join(u['text'] for u in b['structure']['units'])
        actual=sorted((word for word in order if word in text),key=lambda word:text.index(word))
        return {'passed':actual==order,'expected':order,'actual':actual,'criterion':'왼쪽 열을 먼저 끝까지 읽고 오른쪽 열로 이동'}
    exercise('native-reading-order','텍스트 PDF의 2단 읽기 순서',path,reading_order)
    image=Image.new('RGB',(1400,900),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',40)
    for text,point in [('RIGHT ONE',(780,240)),('RIGHT TWO',(780,340)),('LEFT ONE',(80,240)),('LEFT TWO',(80,340))]:draw.text(point,text,font=font,fill='black')
    path=inputs/'two-columns.png';image.save(path)
    exercise('raster-reading-order','이미지의 2단 읽기 순서',path,reading_order,ocr='auto')

    doc=Document();p=doc.add_paragraph('FRACTION: ')
    math=OxmlElement('m:oMath');fraction=OxmlElement('m:f')
    for tag,text in [('m:num','1'),('m:den','2')]:
        part=OxmlElement(tag);run_node=OxmlElement('m:r');t=OxmlElement('m:t');t.text=text;run_node.append(t);part.append(run_node);fraction.append(part)
    math.append(fraction);p._p.append(math)
    path=inputs/'fraction.docx';doc.save(path)
    def fraction_check(b):
        native='\n'.join(u['markdown'] for u in b['structure']['units'])
        return {'passed':any(token in native for token in ('1/2',r'\frac{1}{2}','<mfrac')),'expected':'읽을 수 있는 1/2 또는 동등한 수식 표기','actual':BeautifulSoup(PARSER.render(native),'html.parser').get_text(),'criterion':'분자와 분모 관계 보존'}
    exercise('word-math','Word 분수 수식',path,fraction_check)

    doc=Document();doc.add_paragraph('Alpha',style='List Number');doc.add_paragraph('Beta',style='List Number')
    path=inputs/'numbered.docx';doc.save(path)
    def numbering(b):
        soup=BeautifulSoup(PARSER.render(b['markdown']),'html.parser')
        lists=soup.find_all('ol')
        return {'passed':bool(lists) and [x.get_text() for x in lists[0].find_all('li')]==['Alpha','Beta'],
                'expected':['1. Alpha','2. Beta'],'actual_ordered_lists':len(lists),'criterion':'자동 번호 목록의 순서와 번호 보존'}
    exercise('word-numbering','Word 자동 번호',path,numbering)

    doc=Document();p=doc.add_paragraph('BODY WITH FOOTNOTE ')
    run_node=OxmlElement('w:r');reference=OxmlElement('w:footnoteReference');reference.set(qn('w:id'),'1');run_node.append(reference);p._p.append(run_node)
    base=inputs/'footnote-base.docx';doc.save(base)
    footnotes=b'<?xml version="1.0" encoding="UTF-8"?><w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1"><w:p><w:r><w:t>FOOTNOTE EVIDENCE</w:t></w:r></w:p></w:footnote></w:footnotes>'
    path=inputs/'footnote.docx'
    with zipfile.ZipFile(base) as src,zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data=src.read(name)
            if name=='[Content_Types].xml':data=data.replace(b'</Types>',b'<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/></Types>')
            if name=='word/_rels/document.xml.rels':data=data.replace(b'</Relationships>',b'<Relationship Id="rIdFootnoteAudit" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/></Relationships>')
            dst.writestr(name,data)
        dst.writestr('word/footnotes.xml',footnotes)
    exercise('word-footnotes','Word 각주 연결',path,check_footnote_links)

    book=Workbook();sheet=book.active;sheet['A1']=.125;sheet['A1'].number_format='0.0%';sheet['B1']=1234.5;sheet['B1'].number_format='"$"#,##0.00'
    path=inputs/'display.xlsx';book.save(path)
    def display_check(b):
        tables=tables_from_md(b['markdown']);actual=tables[0][0] if tables else []
        return {'passed':actual==['12.5%','$1,234.50'],'expected':['12.5%','$1,234.50'],'actual':actual,'criterion':'숫자 원시값과 화면 표시값을 구분해 표시'}
    exercise('excel-display','Excel 백분율·통화 표시',path,display_check)

    path=inputs/'flow.svg';path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120"><rect x="10" y="10" width="100" height="80"/><text x="20" y="50" fill="white">START</text><line x1="110" y1="50" x2="280" y2="50" stroke="black"/><rect x="280" y="10" width="100" height="80"/><text x="290" y="50" fill="white">FINISH</text></svg>')
    def graph_check(b):
        soup=BeautifulSoup(PARSER.render(b['markdown']),'html.parser')
        for pre in soup.find_all('pre'):pre.decompose()
        visible=soup.get_text()
        return {'passed':bool(soup.find('img')) or ('START' in visible and 'FINISH' in visible),
                'expected':'도식 이미지 또는 읽을 수 있는 START—FINISH 연결','source_code_preserved':'<svg' in b['markdown'],'criterion':'코드 보관과 사람이 읽는 도식 재사용 구분'}
    exercise('svg-flow','SVG 흐름도 재사용',path,graph_check)

    path=inputs/'unloaded.html';path.write_text('<html><body><h1>Page</h1><p id="late">Loading</p><button role="tab">Second</button><script>document.querySelector("button").onclick=()=>document.getElementById("late").textContent="TAB_TWO_CONTENT_84729";</script></body></html>')
    def late_content(b):
        present='TAB_TWO_CONTENT_84729' in BeautifulSoup(PARSER.render(b['markdown']),'html.parser').get_text()
        return {'passed':present,'expected':'탭을 열어야 생성되는 본문','actual_marker_present':present,'criterion':'탭 클릭 뒤 실제 생성된 TAB_TWO_CONTENT_84729 본문이 MD 읽기 화면에 존재하는지 확인'}
    exercise('static-unloaded-web','미로딩 탭 본문',path,late_content,
             extra_args=('--dom-snapshot',str(dom_snapshot)) if dom_snapshot else (),
             limitation='정적 원본만의 수집 결과와 구별. DOM 제공 시 실제 관측 상태를 추가 입력하며 원본 해시·기대 본문은 동일. 미관측 상태의 전체성 인증 아님.')

    path=inputs/'sample.odt'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('mimetype','application/vnd.oasis.opendocument.text')
        z.writestr('content.xml','<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2"><office:body><office:text><text:p>ODF_TEXT_84729</text:p></office:text></office:body></office:document-content>')
        z.writestr('META-INF/manifest.xml','<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>')
    def odf_body(b):
        present='ODF_TEXT_84729' in BeautifulSoup(PARSER.render(b['markdown']),'html.parser').get_text()
        return {'passed':present,'expected':'ODF_TEXT_84729','actual_marker_present':present,'criterion':'원본 보관뿐 아니라 본문 추출; MD 이스케이프는 렌더 후 비교'}
    exercise('odf-body','ODF 본문 변환',path,odf_body)

    local=inputs/'linked';(local/'assets').mkdir(parents=True)
    Image.new('RGB',(24,24),'green').save(local/'assets/picture.png')
    path=local/'note.md';path.write_text('# Linked image\n\n![diagram](assets/picture.png)\n')
    def linked_md(b):
        target=b['folder']/'assets/picture.png'
        return {'passed':target.is_file(),'markdown_byte_copy':(b['folder']/PRIMARY).read_bytes()==path.read_bytes(),
                'expected':'MD가 참조하는 assets/picture.png도 결과 묶음에 존재','actual_asset_present':target.is_file(),'criterion':'MD 바이트 보존과 연결 자산 보존 구분'}
    exercise('markdown-assets','입력 MD의 연결 이미지',path,linked_md)
    path=local/'page.html';path.write_text('<html><body><h1>Linked image</h1><img src="assets/picture.png" alt="diagram"></body></html>')
    def portable(b):
        destination=output/'relocated-html';shutil.copytree(b['folder'],destination)
        imgs=BeautifulSoup(PARSER.render(b['markdown']),'html.parser').find_all('img')
        local_paths=[destination/unquote(urlparse(i.get('src','')).path) for i in imgs]
        return {'passed':bool(local_paths) and all(p.is_file() for p in local_paths) and validate_bundle(destination)['passed'],
                'expected':'전체 묶음 이동 후 이미지 연결 유지','image_count':len(imgs),'existing_images':sum(p.is_file() for p in local_paths),'criterion':'HTML 변환 묶음의 실제 경로 이식성'}
    exercise('portable-html-assets','HTML 묶음 이동의 이미지 연결',path,portable)

    path=inputs/'linked.epub'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('mimetype','application/epub+zip')
        z.writestr('META-INF/container.xml','<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="EPUB/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('EPUB/book.opf','<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book">audit</dc:identifier><dc:title>Link audit</dc:title><dc:language>en</dc:language></metadata><manifest><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/><item id="two" href="two.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
        z.writestr('EPUB/one.xhtml','<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter one</h1><a href="two.xhtml#target">Go next</a></body></html>')
        z.writestr('EPUB/two.xhtml','<html xmlns="http://www.w3.org/1999/xhtml"><body><h1 id="target">Chapter two</h1></body></html>')
    def chapter_link(b):
        soup=BeautifulSoup(PARSER.render(b['markdown']),'html.parser')
        a=next(a for a in soup.find_all('a') if a.get_text()=='Go next');href=a['href'];parsed=urlparse(href)
        resolves=(not parsed.path and soup.find(id=parsed.fragment) is not None) or (bool(parsed.path) and (b['folder']/unquote(parsed.path)).is_file())
        return {'passed':resolves,'expected':'변환된 MD 또는 보관 자산의 유효한 장간 링크','actual_href':href,'target_resolves':resolves,'criterion':'EPUB 장간 링크 재연결'}
    exercise('epub-links','EPUB 장간 링크',path,chapter_link,scope='two-chapter EPUB package fixture; a full EPUB conformance validation is not included')

    after=fingerprint()
    summary={'date':'2026-11-05','cases':len(rows),'passed':sum(r['status']=='pass' for r in rows),
             'failed':sum(r['status']=='fail' for r in rows),'errors':sum(r['status']=='error' for r in rows),
             'production_source_unchanged':before==after,'source_hashes':before,'reference_inputs':str(reference_inputs) if reference_inputs else None,'results':rows,
             'untested':['실제 손글씨·세로쓰기·실제 촬영 문서 전체','HWP·구형 DOC/XLS/PPT·실제 HWPX/RTF 복잡한 본문','SmartArt·복잡한 도표 의미','실제 웹사이트 탭·가상 스크롤 전체성·로그인 본문','Obsidian 실제 앱 UI에서 열기'],
             'scope':'Known-answer boundary fixtures. Integrity pass does not imply table, formula, reading-order, asset or semantic fidelity.'}
    (output/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ['cases','passed','failed','errors','production_source_unchanged']},ensure_ascii=False))
    return 2 if summary['failed'] or summary['errors'] else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--reference-inputs',type=Path,help='이전 입력을 그대로 재사용해 수정 전후를 비교')
    parser.add_argument('--dom-snapshot',type=Path,help='미로딩 탭 사례에만 실제 브라우저 관측 DOM을 추가 입력')
    args=parser.parse_args()
    raise SystemExit(run(args.out.resolve(),args.reference_inputs.resolve() if args.reference_inputs else None,args.dom_snapshot.resolve() if args.dom_snapshot else None))
