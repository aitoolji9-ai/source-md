import io
import json

import pytest

from source_preserve_md.engine import convert,PRIMARY,validate_bundle
from source_preserve_md.model import ConversionError
from source_preserve_md.server import create_app

SOURCE=b'<html><body><p>Initial only</p></body></html>'


def test_dynamic_uses_each_collected_state_and_original_selector(tmp_path,monkeypatch):
    monkeypatch.setattr('source_preserve_md.engine.fetch',lambda url:(SOURCE,url,'text/html'))
    calls=[]
    def capture(url,assets,**kwargs):
        calls.append(kwargs)
        return [{'index':0,'frame_index':0,'state':'initial','label':'Initial','url':url,'html':'<body><article id="panel">FIRST</article></body>'},
                {'index':0,'frame_index':0,'state':'tab-1','label':'Second','url':url,'html':'<body><article id="panel">SECOND</article></body>'}],[]
    monkeypatch.setattr('source_preserve_md.engine.browser_capture',capture)
    out=tmp_path/'out';result=convert('https://example.com',out,dynamic=True,selector='#panel',ocr='off')
    assert calls==[{'auto_tabs':True,'tab_selector':'[role="tab"]','tab_wait_ms':5000}]
    data=json.loads((out/'structure.json').read_text())
    assert [u['text'] for u in data['units']]==['FIRST','SECOND']
    assert data['metadata']['dom_capture']['mode']=='automatic_tabs'
    assert (out/'source/webpage.html').read_bytes()==SOURCE
    assert result['machine_checks']=={'passed':2,'total':2} and validate_bundle(out)['passed']


@pytest.mark.parametrize('value',[0,99,30001,'bad',True])
def test_wait_bounds_rejected_before_output(tmp_path,value):
    out=tmp_path/'out'
    with pytest.raises(ConversionError):convert('page.html',out,data=SOURCE,tab_wait_ms=value,ocr='off')
    assert not out.exists()


def test_api_does_not_drop_auto_tab_controls(tmp_path,monkeypatch):
    calls=[]
    def fake(source,out,**kwargs):
        calls.append(kwargs);return {'output':str(out),'integrity':{'passed':True}}
    monkeypatch.setattr('source_preserve_md.server.convert',fake)
    client=create_app(tmp_path).test_client();token=client.get('/api/config').json['token']
    response=client.post('/api/convert',json={'url':'https://example.com','dynamic':True,'auto_tabs':False,'tab_selector':'.tabs button','tab_wait_ms':12000},headers={'X-Local-Token':token})
    assert response.status_code==200
    assert calls[0]['auto_tabs'] is False
    assert calls[0]['tab_selector']=='.tabs button' and calls[0]['tab_wait_ms']==12000


def test_svg_link_remains_a_link_without_nested_markdown(tmp_path):
    from bs4 import BeautifulSoup
    from source_preserve_md.engine import PARSER
    source=b'<html><body><a href="https://example.com"><svg><title id="icon-title">Home</title></svg> Site</a><p>Body</p></body></html>'
    out=tmp_path/'out';result=convert('icons.html',out,data=source,ocr='off')
    assert result['machine_checks']=={'passed':1,'total':1}
    soup=BeautifulSoup(PARSER.render((out/PRIMARY).read_text()),'html.parser')
    link=soup.find('a',href='https://example.com')
    assert link is not None and link.get_text()=='Home Site'
    assert soup.find(id='icon-title')
    assert any(a.get_text()=='svg 원본 요소' for a in soup.find_all('a'))


def test_incomplete_automatic_snapshot_is_not_silently_successful(tmp_path):
    import hashlib
    from source_preserve_md.cli import main
    source=tmp_path/'original.html';source.write_bytes(SOURCE)
    snapshot=tmp_path/'dom.json';snapshot.write_text(json.dumps({'schema_version':1,'source_sha256':hashlib.sha256(SOURCE).hexdigest(),
        'frames':[{'state':'initial','html':'<body>Body</body>'}],
        'automatic_tabs':{'outcomes':[{'status':'timeout-or-unchanged','label':'No effect'}]}}))
    out=tmp_path/'out'
    assert main(['convert',str(source),'--dom-snapshot',str(snapshot),'--ocr','off','--out',str(out)])==2
    assert validate_bundle(out)['passed']
    manifest=json.loads((out/'manifest.json').read_text())
    assert any(w['code']=='automatic_tab_incomplete' for w in manifest['unresolved'])


def test_html_table_indentation_does_not_turn_inline_html_into_code(tmp_path):
    source=b'<html><body><table>\n<tr>\n<td>\n<a href="https://example.com"><b>Browser</b>\n\n                                      - <b>Version</b>\n</a>\n</td>\n</tr>\n</table></body></html>'
    result=convert('table.html',tmp_path/'out',data=source,ocr='off')
    assert result['machine_checks']=={'passed':1,'total':1}
