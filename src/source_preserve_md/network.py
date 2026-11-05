from __future__ import annotations

import base64
import ipaddress
import mimetypes
import socket
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .model import Assets, ConversionError, MAX_INPUT, safe_name


def public_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ConversionError("인증정보 없는 http/https 공개 URL을 입력해 주세요.")
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise ConversionError("로컬·사설·예약 주소는 URL 수집 대상에서 제외됩니다. 로컬 파일로 넣어 주세요.")
    except (OSError, ValueError) as e:
        raise ConversionError(f"URL 호스트를 확인할 수 없습니다: {p.hostname}") from e


def fetch(url: str, limit: int = MAX_INPUT) -> tuple[bytes, str, str]:
    """Bounded GETs only; validate each redirect before following it."""
    deadline = time.monotonic() + 60
    with httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": "SourcePreserveMD/0.1 (+local archival tool)"}) as client:
        for _ in range(6):
            public_url(url)
            with client.stream("GET", url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if not response.headers.get("location"):
                        raise ConversionError("리디렉션 목적지가 없습니다.")
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code in (401, 403, 429):
                    raise ConversionError(f"서버가 접근을 제한했습니다(HTTP {response.status_code}). 로그인·차단을 우회하지 않습니다. 열람 가능한 원본 파일을 넣어 주세요.")
                if response.status_code >= 400:
                    raise ConversionError(f"웹 수집 실패: HTTP {response.status_code}")
                chunks, count = [], 0
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > limit or time.monotonic() > deadline:
                        raise ConversionError("다운로드 크기 또는 시간 상한을 초과했습니다.")
                    chunks.append(chunk)
                return b"".join(chunks), str(response.url), response.headers.get("content-type", "")
    raise ConversionError("리디렉션 횟수 상한을 초과했습니다.")


class ResourceLoader:
    def __init__(self, assets: Assets, source_path: Path | None = None, base_url: str = "", download: bool = True):
        self.assets, self.source_path, self.base_url, self.download = assets, source_path, base_url, download
        self.cache: dict[str, str] = {}
        self.errors: list[dict] = []
        self.total = 0
        self.started = time.monotonic()

    def load(self, src: str) -> str:
        if src in self.cache:
            return self.cache[src]
        if time.monotonic() - self.started > 120:
            raise ConversionError("자산 수집 시간 상한(120초)에 도달했습니다.")
        if not src:
            raise ConversionError("이미지 주소가 없습니다.")
        if len(self.cache) >= 200:
            raise ConversionError("자산 200개 상한에 도달했습니다.")
        if src.startswith("data:"):
            header, payload = src.split(",", 1)
            if not header.startswith("data:image/"):
                raise ConversionError("이미지 이외의 data URL은 자동 추출하지 않습니다.")
            data = base64.b64decode(payload, validate=True) if ";base64" in header else unquote(payload).encode()
            mime = header[5:].split(";")[0]
            name = "image" + (mimetypes.guess_extension(mime) or ".bin")
        else:
            target = urljoin(self.base_url, src) if self.base_url else src
            parsed = urlparse(target)
            if parsed.scheme in ("http", "https"):
                if not self.download:
                    raise ConversionError("원격 자산 다운로드 옵션이 꺼져 있습니다.")
                data, final_url, mime = fetch(target, 20 * 1024 * 1024)
                name = safe_name(unquote(urlparse(final_url).path))
                if not Path(name).suffix:
                    name += mimetypes.guess_extension(mime.split(";")[0]) or ".bin"
            elif not parsed.scheme and self.source_path:
                parent = self.source_path.parent.resolve()
                candidate = (parent / unquote(parsed.path)).resolve()
                if not candidate.is_relative_to(parent) or not candidate.is_file():
                    raise ConversionError("원본 폴더 밖이거나 존재하지 않는 자산입니다.")
                if candidate.stat().st_size > 20 * 1024 * 1024:
                    raise ConversionError("자산이 20MB 상한을 초과했습니다.")
                data, name = candidate.read_bytes(), candidate.name
            else:
                raise ConversionError("이 자산 주소는 자동 다운로드하지 않습니다.")
        if len(data) > 20 * 1024 * 1024 or self.total + len(data) > 100 * 1024 * 1024:
            raise ConversionError("자산 크기 상한을 초과했습니다.")
        self.total += len(data)
        result = self.assets.add(data, name, src)
        self.cache[src] = result
        return result


def browser_capture(url: str, assets: Assets, *, auto_tabs: bool=True, tab_selector: str='[role="tab"]', tab_wait_ms: int=5000) -> tuple[list[dict], list[str]]:
    """Clean browser profile, bounded scrolling. No imported cookies or credentials."""
    from playwright.sync_api import sync_playwright

    public_url(url)
    warnings: list[str] = []
    frames: list[dict] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                raise ConversionError("동적 수집용 Chrome/Chromium을 찾지 못했습니다. 정적 모드를 쓰거나 `uv run playwright install chromium`을 실행해 주세요.") from e
        try:
            context = browser.new_context(accept_downloads=False, service_workers="block", viewport={"width": 1440, "height": 1080})
            memo: set[str] = set()

            def route_handler(route):
                req = route.request
                try:
                    if req.method not in ("GET", "HEAD"):
                        raise ConversionError("읽기 이외의 요청 차단")
                    host = urlparse(req.url).netloc
                    if host not in memo:
                        public_url(req.url)
                        memo.add(host)
                    route.continue_()
                except Exception:
                    warnings.append("브라우저의 비공개 주소 또는 읽기 이외 요청을 차단했습니다.")
                    route.abort()

            context.route("**/*", route_handler)
            context.route_web_socket("**/*", lambda ws: ws.close())
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if response and response.status >= 400:
                raise ConversionError(f"동적 페이지 접근 실패: HTTP {response.status}")
            page.wait_for_timeout(800)
            page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
            last_height, stable = 0, 0
            for _ in range(12):
                height = page.evaluate("document.documentElement.scrollHeight")
                stable = stable + 1 if height == last_height else 0
                if stable >= 2:
                    break
                page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                page.wait_for_timeout(350)
                last_height = height
            page.evaluate("window.scrollTo(0, 0)")
            if not auto_tabs and page.locator('[role="tab"]').count():
                warnings.append('탭 자동 수집을 꺼 두어 초기 상태만 수집했습니다.')
            for index, frame in enumerate(page.frames[:64]):
                try:
                    frames.append({"html": frame.content(), "url": frame.url, "index": index,
                                   'frame_index':index,'state':'initial','label':'Initial' if index==0 else f'Initial frame {index}'})
                except Exception:
                    warnings.append(f"프레임 {index} 본문을 수집하지 못했습니다.")
            if len(page.frames)>64:warnings.append('프레임 64개 상한으로 나머지 프레임은 제외했습니다.')
            if auto_tabs:
                script=(Path(__file__).parent/'web/tab-collector.js').read_text()
                deadline=time.monotonic()+45
                collected=page.evaluate(script,{'tabSelector':tab_selector,'tabWaitMs':tab_wait_ms,'restore':False})
                if not collected.get('states'):raise ConversionError('자동 탭 수집의 초기 DOM을 확보하지 못했습니다: '+str(collected.get('outcomes')))
                frames=[dict(state,index=state.get('frame_index',0)) for state in collected['states']]
                # A clean browser can read public cross-origin frames through
                # its supported frame API; the bookmark helper obeys the page's
                # same-origin boundary and records inaccessible frames instead.
                known={frame['url'] for frame in frames if frame.get('frame_index',0)!=0}
                for child in page.frames[1:9]:
                    if child.url in known:continue
                    if time.monotonic()>=deadline or len(frames)>=64:
                        collected['outcomes'].append({'status':'limit','message':'iframe/전체 수집 상한'});break
                    try:
                        public_url(child.url)
                        other=child.evaluate(script,{'tabSelector':tab_selector,'tabWaitMs':tab_wait_ms,'restore':False,'includeFrames':False,'totalMs':max(100,int((deadline-time.monotonic())*1000))})
                        index=max(f.get('frame_index',0) for f in frames)+1
                        frames.extend(dict(state,index=index,frame_index=index,label=f'Frame {index} / '+state['label']) for state in other['states'])
                        collected['outcomes'].extend(dict(item,frame_index=index) for item in other['outcomes'])
                        known.add(child.url)
                        for item in collected['outcomes']:
                            if item.get('status')=='frame-inaccessible' and item.get('url')==child.url:item['status']='frame-recovered'
                    except Exception as error:
                        collected['outcomes'].append({'status':'frame-error','url':child.url,'message':str(error)[:300]})
                if len(frames)>64:
                    frames=frames[:64];warnings.append('DOM 상태/프레임 64개 상한으로 나머지 상태는 제외했습니다.')
                report={k:v for k,v in collected.items() if k!='states'}
                report['saved_state_frames']=len(frames)
                report_asset=assets.json('automatic-tabs.json',report,url)
                frames[0]['capture_report']=report_asset
                frames[0]['automatic_tabs']=report
                for item in collected.get('outcomes',[]):
                    if item['status'] not in ('captured','already-selected','scroll-end','frame-recovered'):
                        warnings.append('자동 탭 확인 항목: '+str(item))
                warnings.append('탭 자동 수집은 최상위 문서의 식별 가능한 탭을 유한 전환합니다. iframe 내부 탭·모든 상태 조합·가상 목록 전체성은 별도 대조가 필요합니다.')
            try:
                assets.add(page.screenshot(full_page=True, timeout=10000), "webpage.png", url)
            except Exception:
                warnings.append("전체 화면 캡처를 만들지 못했습니다.")
            warnings.append("현재 로딩한 DOM과 프레임을 수집했습니다. 가상 스크롤·무한 목록·탭 전체성 및 화면 의미는 자동 검증하지 않았습니다.")
        finally:
            browser.close()
    return frames, list(dict.fromkeys(warnings))

