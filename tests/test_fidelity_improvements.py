import json
from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document
from docx.oxml import OxmlElement
from openpyxl import Workbook
from PIL import Image

from source_preserve_md.engine import PARSER, PRIMARY, convert, validate_bundle


def test_word_fraction_and_number_list_are_readable(tmp_path):
    doc=Document();p=doc.add_paragraph('Fraction: ')
    math=OxmlElement('m:oMath');fraction=OxmlElement('m:f')
    for tag,text in [('m:num','1'),('m:den','2')]:
        part=OxmlElement(tag);run=OxmlElement('m:r');t=OxmlElement('m:t');t.text=text;run.append(t);part.append(run);fraction.append(part)
    math.append(fraction);p._p.append(math)
    doc.add_paragraph('Alpha',style='List Number');doc.add_paragraph('Beta',style='List Number')
    source=tmp_path/'source.docx';doc.save(source)
    output=tmp_path/'converted';convert(source,output,ocr='off')
    md=(output/PRIMARY).read_text();soup=BeautifulSoup(PARSER.render(md),'html.parser')
    assert '1/2' in soup.get_text() or r'\frac{1}{2}' in md
    assert [li.get_text() for li in soup.find('ol').find_all('li')]==['Alpha','Beta']
    assert validate_bundle(output)['passed']


def test_excel_display_and_raw_value_are_both_preserved(tmp_path):
    book=Workbook();ws=book.active
    ws['A1']=.125;ws['A1'].number_format='0.0%'
    ws['B1']=1234.5;ws['B1'].number_format='"$"#,##0.00'
    source=tmp_path/'source.xlsx';book.save(source)
    output=tmp_path/'converted';convert(source,output,ocr='off')
    soup=BeautifulSoup(PARSER.render((output/PRIMARY).read_text()),'html.parser')
    assert [c.get_text() for c in soup.find('tr').find_all('td')]==['12.5%','$1,234.50']
    s=json.loads((output/'structure.json').read_text())
    data=json.loads((output/s['units'][0]['metadata']['cell_data']).read_text())
    assert data['cells'][0]['value']==.125 and data['cells'][0]['display_value']=='12.5%'


def test_markdown_local_assets_are_copied_without_rewriting_source(tmp_path):
    source_dir=tmp_path/'input';(source_dir/'assets').mkdir(parents=True)
    image=source_dir/'assets/image.png';Image.new('RGB',(10,10),'green').save(image)
    source=source_dir/'note.md';raw=b'# Exact\r\n![local](assets/image.png)\r\n';source.write_bytes(raw)
    output=tmp_path/'converted';convert(source,output,ocr='off')
    assert (output/PRIMARY).read_bytes()==raw
    assert (output/'assets/image.png').read_bytes()==image.read_bytes()
    assert validate_bundle(output)['passed']


def test_wide_column_gap_precedes_gaps_between_lines():
    from source_preserve_md.raster_layout import order_blocks
    blocks=[{'text':text,'bbox':box} for text,box in [
        ('RIGHT ONE',[780,240,1030,280]),('RIGHT TWO',[780,340,1030,380]),
        ('LEFT ONE',[80,240,330,280]),('LEFT TWO',[80,340,330,380])]]
    assert [b['text'] for b in order_blocks(blocks)]==['LEFT ONE','LEFT TWO','RIGHT ONE','RIGHT TWO']


def test_native_pdf_order_preserves_producer_text_separately(tmp_path):
    import pymupdf
    doc=pymupdf.open();page=doc.new_page(width=700,height=450)
    for text,point in [('RIGHT ONE',(390,120)),('RIGHT TWO',(390,170)),('LEFT ONE',(40,120)),('LEFT TWO',(40,170))]:page.insert_text(point,text,fontsize=20)
    source=tmp_path/'source.pdf';doc.save(source);doc.close()
    output=tmp_path/'converted';convert(source,output,ocr='off')
    unit=json.loads((output/'structure.json').read_text())['units'][0]
    assert unit['text'].index('LEFT ONE') < unit['text'].index('LEFT TWO') < unit['text'].index('RIGHT ONE')
    assert unit['metadata']['producer_order_text'].index('RIGHT ONE') < unit['metadata']['producer_order_text'].index('LEFT ONE')


def test_broken_stroke_repair_does_not_close_intentional_rowspan():
    from PIL import ImageDraw
    from source_preserve_md.raster_layout import detect_tables
    image=Image.new('RGB',(960,600),'white');draw=ImageDraw.Draw(image)
    for x in (60,340,620,900):draw.line((x,100,x,500),fill=(170,170,170),width=2)
    for y in (100,200,300,400,500):draw.line((60,y,900,y),fill=(170,170,170),width=2)
    draw.rectangle((62,198,338,203),fill='white')
    tables=detect_tables(image)
    assert len(tables)==1
    top=next(c for c in tables[0]['cells'] if c['row']==c['column']==0)
    assert top['rowspan']==2


def test_markdown_asset_cannot_escape_original_folder(tmp_path):
    folder=tmp_path/'input';folder.mkdir()
    private=tmp_path/'outside.png';Image.new('RGB',(10,10),'red').save(private)
    source=folder/'note.md';source.write_text('![outside](../outside.png)\n[folder](.)\n')
    output=tmp_path/'converted';result=convert(source,output,ocr='off')
    assert (output/PRIMARY).read_bytes()==source.read_bytes()
    assert any(w['code']=='markdown_asset_boundary' for w in result['warnings'])
    assert not (output/'outside.png').exists()
