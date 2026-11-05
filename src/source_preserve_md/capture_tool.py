"""Build an offline bookmarklet helper; no network or AI service dependency."""
import html
from pathlib import Path
from urllib.parse import quote


def capture_script():
    web=Path(__file__).parent/'web'
    return '('+(web/'dom-capture.js').read_text()+')('+(web/'tab-collector.js').read_text()+');'


def write_capture_tool(output):
    script=capture_script()
    bookmarklet='javascript:'+quote(script,safe='')
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>원문결 DOM 저장 도우미</title>
<style>body{max-width:760px;margin:50px auto;padding:20px;font:17px/1.7 system-ui}a{display:inline-block;padding:12px 20px;background:#31766a;color:white}textarea{width:100%;height:120px}</style>
<h1>열린 웹 탭을 MD로 보존하기</h1><p>아래 링크를 브라우저 북마크 막대로 끌어다 놓으세요. 추출할 페이지를 연 뒤 해당 북마크를 누르면 저장 도우미가 나타납니다.</p>
<a href="'''+html.escape(bookmarklet,quote=True)+'''">원문결 DOM 저장</a>
<ol><li>페이지의 원본 HTML을 파일로 저장합니다. 같은 파일을 도우미와 변환기에 사용하세요.</li><li>도우미에서 원본 HTML을 선택합니다.</li><li>탭 자동 수집을 누르면 표준 탭을 순서대로 전환해 상태를 저장합니다. 사용자 정의 탭은 필요한 상태를 직접 추가할 수 있습니다.</li><li>DOM JSON 받기를 누릅니다. 원본 HTML과 JSON을 프로그램에 함께 넣어 변환합니다.</li></ol>
<p>각 상태의 현재 DOM만 저장합니다. 접근 가능한 iframe과 유한 스크롤의 상태도 보관합니다. 다른 출처의 iframe은 브라우저 경계에 따라 제외되며 공개 URL 프로그램 수집으로 처리할 수 있습니다. canvas 픽셀·끝없는 목록 전체·모든 상태 조합의 완결은 인증하지 않습니다. 원본 해시는 파일 짝과 무결성 확인용이며 사이트 출처 인증은 아닙니다. 비밀번호·로그인 상태가 포함될 수 있는 DOM은 저장 전 확인하세요.</p>
<p>페이지의 보안 정책으로 북마크 실행이 막히면 우회하지 말고 허용된 브라우저 도구로 DOM을 저장하세요. 아래는 동일한 로컬 도우미 코드입니다.</p><textarea readonly>'''+html.escape(script)+'''</textarea></html>'''
    path=Path(output).expanduser().resolve()
    with path.open('x',encoding='utf-8') as stream:stream.write(page)
    return str(path)
