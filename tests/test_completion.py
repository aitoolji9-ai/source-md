import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from bs4 import BeautifulSoup
from lxml import etree

from source_preserve_md.engine import convert,PRIMARY,PARSER,validate_bundle
from source_preserve_md.model import ConversionError

FIXTURES=Path(__file__).resolve().parents[1]/'examples/legacy-inputs'


def test_hwp_tables_are_extracted_and_source_is_preserved(tmp_path):
    source=FIXTURES/'table.hwp';out=tmp_path/'out';result=convert(source,out,ocr='off')
    soup=BeautifulSoup(PARSER.render((out/PRIMARY).read_text()),'html.parser')
    table=soup.find('table')
    assert result['format']=='hwp' and table is not None
    assert len(table.find_all('tr'))==2 and len(table.find_all('td'))==4
    assert (out/'source/table.hwp').read_bytes()==source.read_bytes()
    assert validate_bundle(out)['passed']


def test_hwp_password_is_refused_and_source_preserved(tmp_path):
    source=FIXTURES/'password-12345.hwp';out=tmp_path/'out'
    with pytest.raises(ConversionError,match='암호'):
        convert(source,out,ocr='off')
    assert (out/'source/password-12345.hwp').read_bytes()==source.read_bytes()
    assert (out/'error.json').exists() and not (out/PRIMARY).exists()


def test_math_sum_and_nested_fraction_keep_their_relationships():
    from source_preserve_md.office_semantics import omml_text
    node=etree.fromstring(b'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:nary><m:naryPr><m:chr m:val="&#8721;"/></m:naryPr><m:sub><m:r><m:t>i=1</m:t></m:r></m:sub><m:sup><m:r><m:t>n</m:t></m:r></m:sup><m:e><m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:sSup><m:e><m:r><m:t>i</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:den></m:f></m:e></m:nary></m:oMath>')
    assert omml_text(node)==r'\sum_{i=1}^{n} \frac{1}{i^{2}}'


def fake_ocr(path,**kwargs):
    return {'engine':'fixture','width':300,'height':100,'observations':[{'text':'wrng','bbox':[.1,.1,.7,.2],'confidence':1}],'languages':['en-US']}


def image_bytes():
    stream=io.BytesIO();Image.new('RGB',(300,100),'white').save(stream,'PNG');return stream.getvalue()


def test_source_bound_ocr_correction_preserves_previous_data(tmp_path,monkeypatch):
    monkeypatch.setattr('source_preserve_md.ocr.recognize',fake_ocr)
    raw=image_bytes();out=tmp_path/'out'
    correction={'schema_version':1,'source_sha256':hashlib.sha256(raw).hexdigest(),'actions':[{'target':'frame:1','kind':'line','line':0,'expected':'wrng','replacement':'right'}]}
    result=convert('source.png',out,data=raw,ocr_corrections=correction)
    structure=json.loads((out/'structure.json').read_text())
    unit=next(u for u in structure['units'] if u['metadata'].get('ocr_data'))
    assert unit['metadata']['ocr_text']=='right'
    trace=unit['metadata']['human_corrections']
    assert json.loads((out/trace['previous_ocr_asset']).read_text())['accepted_text']=='wrng'
    assert json.loads((out/unit['metadata']['ocr_data']).read_text())['accepted_text']=='right'
    assert (out/'04_원본대조.html').is_file() and validate_bundle(out)['passed']


def test_wrong_correction_source_rejected_before_output(tmp_path):
    correction={'schema_version':1,'source_sha256':'0'*64,'actions':[{'target':'frame:1','kind':'line','line':0,'expected':'a','replacement':'b'}]}
    with pytest.raises(ConversionError,match='해시'):convert('source.png',tmp_path/'out',data=image_bytes(),ocr_corrections=correction)
    assert not (tmp_path/'out').exists()


def test_correction_of_existing_bundle_never_reruns_ocr(tmp_path,monkeypatch):
    from source_preserve_md.engine import correct_bundle
    monkeypatch.setattr('source_preserve_md.ocr.recognize',fake_ocr)
    raw=image_bytes();original=tmp_path/'original';convert('source.png',original,data=raw)
    before=(original/PRIMARY).read_bytes()
    monkeypatch.setattr('source_preserve_md.ocr.recognize',lambda *a,**k:pytest.fail('OCR must not rerun'))
    action={'schema_version':1,'source_sha256':hashlib.sha256(raw).hexdigest(),'actions':[{'target':'frame:1','kind':'line','line':0,'expected':'wrng','replacement':'right'}]}
    result=correct_bundle(original,tmp_path/'corrected',action)
    assert result['ocr_rerun'] is False and result['integrity']['passed']
    assert (original/PRIMARY).read_bytes()==before
    structure=json.loads((tmp_path/'corrected/structure.json').read_text())
    assert any(u['metadata'].get('ocr_text')=='right' for u in structure['units'])


def test_review_and_correction_use_the_real_local_api(tmp_path,monkeypatch):
    from source_preserve_md.server import create_app
    monkeypatch.setattr('source_preserve_md.ocr.recognize',fake_ocr)
    app=create_app(tmp_path);client=app.test_client();token=client.get('/api/config').json['token'];raw=image_bytes()
    created=client.post('/api/convert',headers={'X-Local-Token':token},data={'file':(io.BytesIO(raw),'source.png')}).json
    review=client.get('/api/jobs/'+created['id']+'/review')
    assert review.status_code==200 and b'/static/review.js' in review.data
    assert b'/preview-asset/assets/' in review.data and '원문 대조'.encode() in review.data
    action={'schema_version':1,'source_sha256':hashlib.sha256(raw).hexdigest(),'actions':[{'target':'frame:1','kind':'line','line':0,'expected':'wrng','replacement':'right'}]}
    corrected=client.post('/api/jobs/'+created['id']+'/correct',json=action,headers={'X-Local-Token':token})
    assert corrected.status_code==200,corrected.json
    assert corrected.json['ocr_rerun'] is False and corrected.json['integrity']['passed']


def test_hwp_footnotes_and_endnotes_have_text_and_return_links(tmp_path):
    result=convert(FIXTURES/'footnote-endnote.hwp',tmp_path/'out',ocr='off')
    data=json.loads((tmp_path/'out/structure.json').read_text());notes=data['metadata']['hwp_notes']
    assert len(notes)==4 and all(n['reference_linked'] for n in notes)
    rendered=BeautifulSoup(PARSER.render((tmp_path/'out'/PRIMARY).read_text()),'html.parser')
    assert '각주두번째입니다.' in ''.join(rendered.get_text().split())
    assert '미주두번째입니다.' in ''.join(rendered.get_text().split())
    assert all(rendered.find(id=n['id']) and rendered.find(id=n['id']+'-ref') for n in notes)
    assert result['machine_checks']['passed']==result['machine_checks']['total']


def test_legacy_profile_disables_macros_links_and_active_content(tmp_path):
    from source_preserve_md.legacy import office_profile
    from defusedxml import ElementTree as ET
    profile=tmp_path/'profile';office_profile(profile);root=ET.fromstring((profile/'registrymodifications.xcu').read_bytes())
    paths={n.get('{http://openoffice.org/2001/registry}path'):n for n in root}
    ns='{http://openoffice.org/2001/registry}'
    props={p.get(ns+'name'):p.findtext('value') for p in paths['/org.openoffice.Office.Common/Security/Scripting']}
    assert props['DisableMacrosExecution']==props['DisableActiveContent']=='true'
    for component in ('Writer','Calc'):assert paths['/org.openoffice.Office.'+component+'/Content/Update'][0].findtext('value')=='0'


def test_older_bundle_review_is_read_only(tmp_path,monkeypatch):
    from source_preserve_md.server import create_app
    monkeypatch.setattr('source_preserve_md.ocr.recognize',fake_ocr)
    app=create_app(tmp_path);client=app.test_client();token=client.get('/api/config').json['token']
    result=client.post('/api/convert',headers={'X-Local-Token':token},data={'file':(io.BytesIO(image_bytes()),'source.png')}).json
    folder=Path(result['output']);manifest=json.loads((folder/'manifest.json').read_text());manifest['artifacts'].pop('04_원본대조.html')
    (folder/'04_원본대조.html').unlink();(folder/'manifest.json').write_text(json.dumps(manifest))
    before=(folder/'manifest.json').read_bytes()
    assert client.get('/api/jobs/'+result['id']+'/review').status_code==200
    assert not (folder/'04_원본대조.html').exists() and (folder/'manifest.json').read_bytes()==before


def test_review_asset_paths_cannot_escape_bundle(tmp_path):
    from source_preserve_md.review import local_file
    outside=tmp_path/'outside.json';outside.write_text('{}');bundle=tmp_path/'bundle';bundle.mkdir()
    with pytest.raises(ConversionError):local_file(bundle,'../outside.json')


def test_math_group_symbol_and_braces_are_not_reinterpreted():
    from source_preserve_md.office_semantics import omml_text
    group=etree.fromstring('<m:groupChr xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:groupChrPr><m:chr m:val="⏜"/><m:pos m:val="top"/></m:groupChrPr><m:e><m:r><m:t>x</m:t></m:r></m:e></m:groupChr>'.encode())
    assert omml_text(group)==r'\overset{⏜}{x}'
    delimiter=etree.fromstring(b'<m:d xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:dPr><m:begChr m:val="{"/><m:endChr m:val="}"/></m:dPr><m:e><m:r><m:t>x</m:t></m:r></m:e></m:d>')
    assert omml_text(delimiter)==r'\left\{x\right\}'
