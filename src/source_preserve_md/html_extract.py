from __future__ import annotations

import html
import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .model import Assets, Extraction, fence, md_url
from .network import ResourceLoader


def escape(text: str) -> str:
    # Escaping punctuation prevents a literal source paragraph becoming a heading/list.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", text)


def inline_code(text: str) -> str:
    if not text:
        return "<code></code>"
    length = max((len(x) for x in re.findall(r"`+", text)), default=0) + 1
    marker = "`" * length
    pad = " " if text.startswith(("`", " ")) or text.endswith(("`", " ")) else ""
    return f"{marker}{pad}{text}{pad}{marker}"


def allowed_link(url: str) -> bool:
    return urlparse(url).scheme.lower() in ("", "http", "https", "mailto", "tel")


class HTMLRenderer:
    def __init__(self, result: Extraction, loader: ResourceLoader, base_url: str, preserve_fragments: bool=False):
        self.result, self.loader, self.base_url = result, loader, base_url
        self.preserve_fragments=preserve_fragments
        self.codes: list[str] = []
        self.opaque_assets: dict[int,tuple[str,str]] = {}

    def link(self, href: str) -> str:
        if self.preserve_fragments and href.startswith('#'):return href
        return urljoin(self.base_url,href) if self.base_url else href

    def children(self, node: Tag) -> str:
        return "".join(self.render(c) for c in node.children)

    def render(self, node) -> str:
        content = self._render(node)
        if isinstance(node, Tag) and node.get("id"):
            return f'<a id="{html.escape(str(node["id"]), quote=True)}"></a>' + content
        return content

    def _render(self, node) -> str:
        if isinstance(node, Comment):
            return ""
        if isinstance(node, NavigableString):
            return escape(re.sub(r"\s+", " ", str(node)))
        if not isinstance(node, Tag):
            return ""
        tag = node.name.lower()
        if tag == "pre":
            code = node.find("code")
            text = (code or node).get_text()
            language = ""
            for cls in (code or node).get("class", []):
                if cls.startswith(("language-", "lang-")):
                    language = cls.split("-", 1)[1]
            self.codes.append(text)
            return "\n\n" + fence(text, language) + "\n\n"
        if tag == "code":
            return inline_code(node.get_text())
        if tag == "br":
            return "  \n"
        if tag == "hr":
            return "\n\n---\n\n"
        if tag == "img":
            return self.image(node)
        if tag == "table":
            return "\n\n" + self.table(node) + "\n\n"
        if tag in ("ul", "ol"):
            if tag == "ol" and (node.has_attr("reversed") or node.has_attr("type") or node.find("li", value=True)):
                # Markdown's sequential markers cannot represent reversed lists,
                # alphabetic numbering or an explicit per-item value faithfully.
                return "\n\n" + self.table(node) + "\n\n"
            rows = []
            try:
                start = int(node.get("start", 1))
            except (ValueError, TypeError):
                start = 1
            for index, li in enumerate(node.find_all("li", recursive=False)):
                prefix = f"{start + index}. " if tag == "ol" else "- "
                content = self.children(li).strip()
                rows.append(prefix + content.replace("\n", "\n" + " " * len(prefix)))
            return "\n\n" + "\n".join(rows) + "\n\n"
        content = self.children(node)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return f"\n\n{'#' * int(tag[1])} {content.strip()}\n\n"
        if tag in ("strong", "b", "em", "i", "del", "s", "sup", "sub", "u", "mark"):
            # Inline HTML avoids CommonMark delimiter ambiguity near Korean text/punctuation.
            tag = {"b": "strong", "i": "em", "s": "del"}.get(tag, tag)
            return f"<{tag}>{content}</{tag}>"
        if tag == "a":
            url = self.link(node.get('href',''))
            if url and allowed_link(url):
                embedded=[child for child in node.find_all(True) if id(child) in self.opaque_assets]
                if embedded:
                    # Asset references are links too: nesting them inside a
                    # Markdown link makes its punctuation/URL visible as text.
                    label=escape(re.sub(r'\s+',' ',node.get_text()))
                    anchors=''.join(f'<a id="{html.escape(str(child["id"]),quote=True)}"></a>' for child in node.find_all(id=True))
                    refs=''.join(f'\n\n[{self.opaque_assets[id(child)][0]} 원본 요소](<{md_url(self.opaque_assets[id(child)][1])}>)\n\n' for child in embedded)
                    return anchors+f'[{label}](<{md_url(url)}>)'+refs
                return f"[{content}](<{md_url(url)}>)"
            if node.get("href"):
                self.result.warn("unsafe_link", "실행형 링크는 본문에 실행 가능한 형태로 옮기지 않고 DOM 기록에 보존했습니다.")
            return content
        if tag == "blockquote":
            return "\n\n" + "\n".join("> " + line for line in content.strip().splitlines()) + "\n\n"
        if tag in ("p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "figure", "figcaption", "dl", "dt", "dd", "details", "summary", "address", "form", "fieldset"):
            return "\n\n" + content.strip() + "\n\n"
        if tag == "input":
            self.result.warn("form_control", "입력 위젯 상태는 DOM 기록에 보존하며 MD에서 동작하지 않습니다.")
            return ""
        if tag in ("video", "audio", "iframe", "canvas", "svg", "math", "object", "embed"):
            asset = self.loader.assets.add(str(node).encode(), f"element-{tag}.html", self.base_url)
            self.opaque_assets[id(node)]=(tag,asset)
            self.result.warn("non_text_element", f"{tag} 요소 원문을 {asset}에 보존했습니다. 동작·이미지 문자·시각 의미는 텍스트로 복원하지 않았습니다.")
            return content + f"\n\n[{tag} 원본 요소](<{md_url(asset)}>)\n\n"
        return content

    def image(self, node: Tag) -> str:
        candidates = [node.get("src"), node.get("data-src"), node.get("data-original"), node.get("data-lazy-src")]
        srcset = node.get("srcset", "")
        picture = node.find_parent("picture")
        if not srcset and picture:
            source = picture.find("source", srcset=True)
            srcset = source.get("srcset", "") if source else ""
        if srcset:
            # Data URLs contain commas; they are handled only in src/data-src.
            candidates += [entry.strip().split()[0] for entry in srcset.split(",") if entry.strip()]
        last_error = "이미지 주소가 없습니다."
        for src in candidates:
            if not src:
                continue
            try:
                local = self.loader.load(src)
                node["data-preserved-src"] = local
                return f"![{escape(node.get('alt', ''))}](<{md_url(local)}>)"
            except Exception as e:
                last_error = str(e)
        self.result.warn("image_unavailable", f"이미지 저장 실패: {last_error}")
        src = next((s for s in candidates if s and allowed_link(s)), "")
        if src:
            src = urljoin(self.base_url, src) if self.base_url else src
            return f"![{escape(node.get('alt', ''))}](<{md_url(src)}>)"
        return escape(node.get("alt", "[이미지: 주소 미확인]"))

    def table(self, node: Tag) -> str:
        # HTML tables retain rowspan/colspan, multi-row headers and empty cells.
        allowed = {"table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "p", "div", "br", "strong", "b", "em", "i", "code", "pre", "sup", "sub", "del", "a", "img", "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}

        def safe(n):
            if isinstance(n, Comment):
                return ""
            if isinstance(n, NavigableString):
                # Physical blank lines end a CommonMark HTML block. Preserve
                # source whitespace as entities so indented cell text stays HTML.
                return html.escape(str(n)).replace('\n','&#10;').replace('\r','&#13;')
            if not isinstance(n, Tag):
                return ""
            name = n.name.lower()
            if name not in allowed:
                return "".join(safe(c) for c in n.children)
            attrs = {}
            for key in ("colspan", "rowspan", "scope", "start", "id", "value", "type", "reversed"):
                if n.has_attr(key):
                    attrs[key] = str(n[key])
            if name == "a" and allowed_link(n.get("href", "")):
                attrs["href"] = self.link(n.get('href',''))
            if name == "img":
                self.image(n)
                attrs["src"] = n.get("data-preserved-src", "")
                attrs["alt"] = n.get("alt", "")
            attributes = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in attrs.items())
            opening = f"<{name}{attributes}>"
            if name in ("br", "img"):
                return opening
            return opening + "".join(safe(c) for c in n.children) + f"</{name}>"

        return safe(node)


def extract_html(text: str, result: Extraction, assets: Assets, loader: ResourceLoader,
                 locator: str = "document", base_url: str = "", selector: str = "", anchor_prefix: str = '') -> None:
    soup = BeautifulSoup(text, "html.parser")
    if soup.title and not result.metadata.get("html_title"):
        result.title = soup.title.get_text(strip=True) or result.title
        result.metadata["html_title"] = result.title
    root = soup.select_one(selector) if selector else soup.body or soup
    if root is None:
        raise ValueError("지정한 CSS 선택자에 해당하는 본문이 없습니다.")
    if root.select('[role="tab"]') and root.find('script'):
        result.warn('web_runtime_required','탭과 스크립트가 있어 클릭 뒤 생성되는 본문은 현재 HTML에 없을 수 있습니다. 필요한 탭을 연 뒤의 DOM/저장 HTML과 대조해 주세요.',locator)
    excluded = []
    for element in root.find_all(["script", "style", "noscript", "head", "template"]):
        excluded.append({"tag": element.name, "text": element.get_text(), "html": str(element)})
        element.decompose()
    if excluded:
        assets.json("excluded-dom.json", excluded, locator)
        result.warn("inert_elements", "스크립트·스타일·대체/템플릿 요소는 원본과 별도 JSON에 보존하고 MD 실행 본문에서 제외했습니다.", locator)
    dom = []
    for index, tag in enumerate(root.find_all(True)):
        dom.append({"index": index, "tag": tag.name, "id": tag.get("id"), "attrs": dict(tag.attrs),
                    "direct_text": "".join(str(c) for c in tag.children if isinstance(c, NavigableString) and not isinstance(c, Comment))})
    dom_asset = assets.json("dom-structure.json", dom, locator)
    # Namespace observed states after selecting the original DOM selector.
    # Raw DOM and original IDs remain in the source/DOM assets.
    anchors={}
    if anchor_prefix:
        nodes=([root] if root.get('id') else [])+root.find_all(id=True)
        for node in nodes:
            original=str(node['id']);anchors[original]=anchor_prefix+original;node['id']=anchors[original]
        for link in root.find_all('a',href=True):
            target=link['href']
            if target.startswith('#') and unquote(target[1:]) in anchors:
                link['href']='#'+anchors[unquote(target[1:])]
    expected = root.get_text()
    renderer = HTMLRenderer(result, loader, base_url,preserve_fragments=bool(anchor_prefix))
    markdown = renderer.children(root).strip() + "\n"
    if root.get("id"):
        markdown = f'<a id="{html.escape(str(root["id"]), quote=True)}"></a>\n\n' + markdown
    unit = result.unit("본문" if locator == "document" else locator, locator, markdown, expected,
                       dom=dom_asset, comparison="html_text", title_hierarchy=[{"level": int(h.name[1]), "text": h.get_text()} for h in root.find_all(re.compile(r"^h[1-6]$"))],
                       tables=len(root.find_all("table")), images=len(root.find_all("img")), base_url=base_url)
    unit.code_blocks = renderer.codes
    if anchor_prefix:unit.metadata['anchor_map']=anchors
    result.warn("web_visual_unverified", "DOM 순서와 구조를 추출했습니다. CSS 배치·숨김 상태·픽셀 모양과 시각 의미의 동일성은 자동 검증하지 않습니다.", locator)
    if not expected.strip() and not root.find("img"):
        result.warn("empty_body", "추출 가능한 본문 텍스트가 없습니다. 앱 껍데기·로그인·스크립트 로딩 여부를 확인해 주세요.", locator)
    if root.select('input[type="password"]'):
        result.warn("possible_login", "비밀번호 입력란이 있어 로그인 화면일 가능성이 있습니다. 대상 본문 확보 여부를 확인해 주세요.", locator)

