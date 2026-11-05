import io
import json
import hashlib

import pytest
from bs4 import BeautifulSoup

from source_preserve_md.engine import PRIMARY, PARSER, convert, validate_bundle
from source_preserve_md.model import ConversionError
from source_preserve_md.server import create_app

SOURCE=b'<html><body><p>Loading</p><script>throw Error("MUST_NOT_RUN")</script></body></html>'


def snapshot():
    return {'schema_version':1,'source_sha256':hashlib.sha256(SOURCE).hexdigest(),
            'source_url':'https://example.com/page','frames':[
                {'state':'initial','label':'First','html':'<body><p>Loading</p></body>'},
                {'state':'tab-second','label':'Second','html':'<body><h2>Second</h2><p>ACTUAL TAB CONTENT</p><script>BAD_SCRIPT_TEXT</script></body>'}]}


def test_observed_states_preserve_source_and_provenance(tmp_path):
    out=tmp_path/'out'
    result=convert('original.html',out,data=SOURCE,dom_snapshot=snapshot(),ocr='off',download_assets=False)
    structure=json.loads((out/'structure.json').read_text())
    text=BeautifulSoup(PARSER.render((out/PRIMARY).read_text()),'html.parser').get_text()
    assert 'ACTUAL TAB CONTENT' in text and 'BAD_SCRIPT_TEXT' not in text
    assert (out/'source/original.html').read_bytes()==SOURCE
    assert [u['metadata']['browser_state'] for u in structure['units']]==['initial','tab-second']
    assert [u['source_locator'] for u in structure['units']]==['view:0/frame:0','view:1/frame:0']
    assert len(structure['metadata']['dom_capture']['states'])==2
    assert result['machine_checks']=={'passed':2,'total':2}
    assert validate_bundle(out)['passed']


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(source_sha256='0'*64),
    lambda d:d.update(frames=[]),
    lambda d:d['frames'][0].update(frame_index='bad'),
    lambda d:d.update(source_url=['bad']),
])
def test_invalid_snapshot_rejected_before_output(tmp_path,mutation):
    data=snapshot();mutation(data);out=tmp_path/'out'
    with pytest.raises(ConversionError):convert('original.html',out,data=SOURCE,dom_snapshot=data,ocr='off')
    assert not out.exists()


def test_snapshot_rejects_dynamic_and_non_html(tmp_path):
    with pytest.raises(ConversionError):
        convert('original.html',tmp_path/'dynamic',data=SOURCE,dom_snapshot=snapshot(),dynamic=True)
    nonhtml=b'hello';d=snapshot();d['source_sha256']=hashlib.sha256(nonhtml).hexdigest()
    with pytest.raises(ConversionError):convert('note.txt',tmp_path/'text',data=nonhtml,dom_snapshot=d,ocr='off')


def test_api_multipart_snapshot_roundtrip_and_wrong_source(tmp_path):
    app=create_app(tmp_path);client=app.test_client();token=client.get('/api/config').json['token']
    def submit(src):
        return client.post('/api/convert',headers={'X-Local-Token':token},data={
            'file':(io.BytesIO(src),'original.html'),'dom_snapshot':(io.BytesIO(json.dumps(snapshot()).encode()),'dom.json'),
            'ocr':'off','download_assets':'false'})
    response=submit(SOURCE)
    assert response.status_code==200,response.json
    job=client.get('/api/jobs/'+response.json['id']).json
    assert 'ACTUAL TAB CONTENT' in job['preview'] and job['integrity']['passed']
    assert submit(b'<html>DIFFERENT</html>').status_code==422


def test_state_local_anchors_and_selector_keep_their_targets(tmp_path):
    data=snapshot()
    for frame in data['frames']:
        frame['html']='<body><article id="panel"><a href="#note">Go note</a><p id="note">NOTE</p></article></body>'
    out=tmp_path/'out'
    convert('original.html',out,data=SOURCE,dom_snapshot=data,selector='#panel',ocr='off')
    rendered=BeautifulSoup(PARSER.render((out/PRIMARY).read_text()),'html.parser')
    ids=[node['id'] for node in rendered.find_all(id=True)]
    assert len(ids)==len(set(ids))
    links=[a['href'] for a in rendered.find_all('a') if a.get_text()=='Go note']
    assert links==['#view-0-note','#view-1-note']
    assert all(rendered.find(id=target[1:]) for target in links)
