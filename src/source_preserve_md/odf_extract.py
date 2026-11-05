"""ODF text/table extraction; source package and styles remain available."""
import html
import posixpath

from defusedxml import ElementTree as ET

from .html_extract import extract_html
from .network import ResourceLoader


def extract_odf(data,result,assets):
    from .adapters import checked_zip,archive_parts
    with checked_zip(data) as z:
        refs=archive_parts(z,assets)
        tree=ET.fromstring(z.read('content.xml'))
    namespaces={'text':'urn:oasis:names:tc:opendocument:xmlns:text:1.0','table':'urn:oasis:names:tc:opendocument:xmlns:table:1.0','xlink':'http://www.w3.org/1999/xlink'}
    count=0
    def render(node):
        nonlocal count
        count+=1
        if count>200000:raise ValueError('ODF element limit')
        tag=node.tag.rsplit('}',1)[-1]
        content=html.escape(node.text or '')+''.join(render(c)+html.escape(c.tail or '') for c in node)
        if tag=='h':
            level=min(6,max(1,int(node.get('{'+namespaces['text']+'}outline-level','1'))))
            return f'<h{level}>'+content+f'</h{level}>'
        if tag=='p':return '<p>'+content+'</p>'
        if tag=='table':return '<table>'+content+'</table>'
        if tag=='table-row':
            repeat=int(node.get('{'+namespaces['table']+'}number-rows-repeated','1'))
            if repeat>1000 or repeat*content.count('<td')>200000:raise ValueError('ODF repeated row limit')
            return ('<tr>'+content+'</tr>')*repeat
        if tag=='table-cell':
            span=int(node.get('{'+namespaces['table']+'}number-columns-spanned','1'));rowspan=int(node.get('{'+namespaces['table']+'}number-rows-spanned','1'))
            repeat=int(node.get('{'+namespaces['table']+'}number-columns-repeated','1'))
            if max(repeat,span,rowspan)>1000:raise ValueError('ODF repeated cell limit')
            return (f'<td colspan="{span}" rowspan="{rowspan}">'+content+'</td>')*repeat
        if tag=='covered-table-cell':return ''
        if tag=='list':return '<ul>'+content+'</ul>'
        if tag=='list-item':return '<li>'+content+'</li>'
        if tag=='line-break':return '<br>'
        if tag=='s':return ' '*min(1000,int(node.get('{'+namespaces['text']+'}c','1')))
        if tag=='a':return '<a href="'+html.escape(node.get('{'+namespaces['xlink']+'}href',''),quote=True)+'">'+content+'</a>'
        if tag=='image':return '<img src="'+html.escape(node.get('{'+namespaces['xlink']+'}href',''),quote=True)+'">'
        return content
    body=next((n for n in tree.iter() if n.tag.rsplit('}',1)[-1]=='body'),tree)
    loader=ResourceLoader(assets,download=False)
    loader.cache.update(refs);loader.cache.update({'./'+k:v for k,v in refs.items()})
    extract_html(render(body),result,assets,loader)
    result.warn('odf_layout','ODF 본문·표·이미지와 원본 패키지를 보존했습니다. 스타일 기반 번호·페이지 배치·프레젠테이션 및 계산 의미는 부분 지원입니다.')
