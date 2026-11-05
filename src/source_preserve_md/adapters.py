from __future__ import annotations

import csv
import contextlib
import html
import io
import json
import posixpath
import re
import unicodedata
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from charset_normalizer import from_bytes
from defusedxml import ElementTree as ET

from .html_extract import escape, extract_html
from .model import Assets, ConversionError, Extraction, MAX_EXPANDED, MAX_UNITS, fence, md_url
from .network import ResourceLoader


TEXT_EXTENSIONS = {"txt", "text", "log", "json", "jsonl", "ndjson", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf", "sql", "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "c", "h", "cpp", "hpp", "cs", "go", "rs", "sh", "zsh", "bash", "css", "scss", "sass", "vue", "svelte", "rb", "php", "swift", "kt", "kts", "r", "dart", "tex", "bib", "rst", "adoc", "srt", "vtt"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "bmp", "ico", "avif"}


def decode(data: bytes, encoding: str = "") -> tuple[str, str]:
    if encoding:
        return data.decode(encoding, errors="strict"), encoding
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8-sig"), "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        best = from_bytes(data).best()
        if best is None:
            raise ConversionError("문자 인코딩을 판별할 수 없습니다. --encoding 옵션을 지정해 주세요.")
        return str(best), best.encoding


def checked_zip(data: bytes) -> zipfile.ZipFile:
    z = zipfile.ZipFile(io.BytesIO(data))
    items = z.infolist()
    if len(items) > 15000 or sum(i.file_size for i in items) > MAX_EXPANDED:
        z.close()
        raise ConversionError("압축 내부 항목 수 또는 해제 크기 상한을 초과했습니다.")
    for item in items:
        p = PurePosixPath(item.filename.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts or item.flag_bits & 1:
            z.close()
            raise ConversionError("위험한 압축 경로 또는 암호화된 압축 항목입니다.")
    return z


def detect(data: bytes, filename: str, content_type: str = "") -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK"):
        with checked_zip(data) as z:
            names = set(z.namelist())
            for name, fmt in (("word/document.xml", "docx"), ("ppt/presentation.xml", "pptx"), ("xl/workbook.xml", "xlsx")):
                if name in names:
                    return fmt
            if "META-INF/container.xml" in names:
                return "epub"
            if 'mimetype' in names and 'content.xml' in names and z.read('mimetype').startswith(b'application/vnd.oasis.opendocument.'):
                return 'odf'
            if "Contents/content.hpf" in names or any(n.startswith("Contents/section") and n.endswith(".xml") for n in names):
                return "hwpx"
        return "unsupported"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        from .legacy import inspect_ole
        return inspect_ole(data)[0]
    if data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*", b"BM")) or data[8:12] == b"WEBP":
        return "image"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in ("mhtml", "mht") or b"multipart/related" in data[:2048].lower():
        return "mhtml"
    if data.lstrip().startswith(b"{\\rtf"):
        return "rtf"
    if ext in ("md", "markdown", "mdown"):
        return "markdown"
    if ext in ("html", "htm", "xhtml") or "text/html" in content_type or re.search(br"<!doctype\s+html|<html[\s>]|<body[\s>]", data[:8192], re.I):
        return "html"
    if ext in ("csv", "tsv"):
        return ext
    if ext == "svg":
        return "svg"
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in ("pdf", "docx", "pptx", "xlsx", "xlsm", "epub", "hwpx"):
        raise ConversionError("파일 확장명과 실제 파일 구조가 일치하지 않습니다. 손상·암호화 여부를 확인해 주세요.")
    if not data or b"\x00" not in data[:8192]:
        try:
            sample = data.decode("utf-8")
            if not sample or sum(c.isprintable() or c in "\r\n\t" for c in sample) / len(sample) > .95:
                return "text"
        except UnicodeDecodeError:
            pass
    return "unsupported"


def text_extract(data: bytes, filename: str, result: Extraction, encoding: str) -> None:
    text, used = decode(data, encoding)
    result.metadata["encoding"] = used
    if used not in ("utf-8", "utf-8-sig", "utf-16") and not encoding:
        result.warn("encoding_detected", f"{used}로 인코딩을 자동 추정했습니다. 원본 표시와 대조하거나 인코딩을 지정해 주세요.")
    if result.format == "markdown":
        result.original_markdown = data
        result.unit("원본 Markdown", "document", text, text, comparison="byte_copy")
        result.warn("markdown_external_assets", "MD 바이트를 그대로 복사했습니다. 원문이 참조하는 외부·상대 자산은 자동 수집하지 않습니다.")
    else:
        language = Path(filename).suffix.lstrip(".")
        unit = result.unit("원문", "document", fence(text, language), text, comparison="code_exact")
        unit.code_blocks = [text]
    if not data:
        result.warn("empty_source", "원본 파일이 비어 있습니다.")


def csv_extract(data: bytes, result: Extraction, assets: Assets, encoding: str) -> None:
    text, used = decode(data, encoding)
    result.metadata["encoding"] = used
    reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t" if result.format == "tsv" else ",")
    rows = []
    total_cells = 0
    for row in reader:
        total_cells += len(row)
        if len(rows) >= 100000 or total_cells > 200000:
            raise ConversionError("CSV 행 수 또는 200,000셀 상한을 초과했습니다.")
        rows.append(row)
    asset = assets.json("table-cells.json", {"rows": rows, "encoding": used})
    body = "<table>\n" + "\n".join("<tr>" + "".join("<td>" + html.escape(c).replace("\n", "<br>") + "</td>" for c in row) + "</tr>" for row in rows) + "\n</table>"
    result.unit("원본 표", "document", body + f"\n\n[셀 배열 JSON](<{md_url(asset)}>)", "".join(c for row in rows for c in row), rows=len(rows), cells=sum(map(len, rows)), comparison="table_cells", cell_data=asset)


def archive_parts(z: zipfile.ZipFile, assets: Assets) -> dict[str, str]:
    refs = {}
    for info in z.infolist():
        if info.is_dir():
            continue
        # Preserve every member as inert bytes, using hashed safe basenames.
        refs[info.filename] = assets.add(z.read(info), Path(info.filename).name, info.filename)
    assets.json("package-parts.json", refs)
    return refs


def chart_cache_data(xml: bytes) -> dict:
    """Preserve chart titles, formulas, cached labels/values and multi-level order.

    Reads the original XML; does not infer missing caches or recalculate formulas.
    The entire original chart part remains in package assets as well.
    """
    root = ET.fromstring(xml)
    ns = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    result = {"titles": ["".join(n.text or "" for n in title.findall(".//a:t", ns)) for title in root.findall(".//c:title", ns)], "series": []}
    for series in root.findall(".//c:ser", ns):
        row = {}
        for source in series:
            name = source.tag.rsplit("}", 1)[-1]
            if name not in ("tx", "cat", "val", "xVal", "yVal", "bubbleSize"):
                continue
            values = {"formulas": [n.text or "" for n in source.findall(".//c:f", ns)],
                      "format_codes": [n.text or "" for n in source.findall(".//c:formatCode", ns)]}
            levels = source.findall(".//c:lvl", ns)
            def points(node):
                return [{"index": p.get("idx"), "value": p.findtext("c:v", default="", namespaces=ns)} for p in node.findall(".//c:pt", ns)]
            if levels:
                values["levels"] = [points(level) for level in levels]
            else:
                values["points"] = points(source)
            literal = source.find("c:v", ns)
            if literal is not None:
                values["literal"] = literal.text or ""
            row[name] = values
        result["series"].append(row)
    return result


def chart_markdown(data: dict, asset: str) -> str:
    """A readable lossless view of scalar caches; detailed JSON stays linked."""
    lines = ["**[원본 차트의 제목·참조·저장 캐시]**"]
    lines.extend(escape(title) for title in data.get("titles", []) if title)
    if data.get("source_part"):
        lines.append("원본 위치: " + escape(data["source_part"]))
    rows, formulas = [], []
    names = {"cat": "범주", "val": "값", "xVal": "X 값", "yVal": "Y 값", "bubbleSize": "버블 크기", "tx": "계열명"}
    for index, series in enumerate(data.get("series", []), 1):
        for kind, value in series.items():
            if not isinstance(value, dict):
                continue
            role = names.get(kind, kind)
            for formula in value.get("formulas", []):
                formulas.append(f"계열 {index} / {role}: {formula}")
            groups = value.get("levels")
            if groups is None:
                groups = [value.get("points", [])]
            for level, points in enumerate(groups, 1):
                for point in points:
                    rows.append([str(index), role, str(level) if "levels" in value else "", str(point.get("index", "")), str(point.get("value", ""))])
            if "literal" in value:
                rows.append([str(index), role, "", "", str(value["literal"])])
    if rows:
        table = '<table><thead><tr><th>계열</th><th>자료</th><th>원본 수준</th><th>인덱스</th><th>원본 값</th></tr></thead><tbody>'
        table += "".join("<tr>" + "".join("<td>" + html.escape(c).replace("\n", "<br>") + "</td>" for c in row) + "</tr>" for row in rows)
        lines.append(table + "</tbody></table>")
    else:
        lines.append("표시할 저장 캐시가 없습니다. 원본 참조 수식·패키지를 보존했으며 값을 추정하거나 재계산하지 않았습니다.")
    if formulas:
        lines.append(fence("\n".join(formulas), "text"))
    lines.append(f"[차트 전체 구조 JSON](<{md_url(asset)}>)")
    return "\n\n".join(lines)


def package_chart_parts(refs: dict, assets: Assets, part: str) -> list[str]:
    """Follow only in-package drawing/chart relationships, preserving their order."""
    seen, found = set(), []
    def visit(current):
        if current in seen:
            return
        seen.add(current)
        directory, filename = posixpath.split(current)
        relpart = posixpath.join(directory, "_rels", filename + ".rels")
        if relpart not in refs:
            return
        tree = ET.fromstring((assets.root / refs[relpart]).read_bytes())
        for rel in tree:
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(directory, target))
            if target not in refs:
                continue
            kind = rel.get("Type", "").rsplit("/", 1)[-1]
            if kind == "chart":
                if target not in found:
                    found.append(target)
            elif kind == "drawing":
                visit(target)
    visit(part)
    return found


def docx_extract(data: bytes, result: Extraction, assets: Assets) -> None:
    from docx import Document
    from .office_semantics import omml_text

    with checked_zip(data) as z:
        refs = archive_parts(z, assets)
        names = set(z.namelist())
    doc = Document(io.BytesIO(data))
    result.metadata["core_properties"] = {k: str(getattr(doc.core_properties, k)) for k in ("title", "author", "created", "modified", "subject", "keywords")}
    loader = ResourceLoader(assets, download=False)
    rels = doc.part.rels
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    counters={}
    try:
        numbering=doc.part.numbering_part.element
    except (KeyError,NotImplementedError):
        numbering=None

    def list_spec(p):
        props=p.find('./'+ns+'pPr/'+ns+'numPr')
        style_node=p.find('./'+ns+'pPr/'+ns+'pStyle')
        style_id=style_node.get(ns+'val','') if style_node is not None else ''
        style=next((s for s in doc.styles if s.style_id==style_id),None)
        seen=set()
        while props is None and style is not None and style.style_id not in seen:
            seen.add(style.style_id);props=style.element.find('./'+ns+'pPr/'+ns+'numPr');style=style.base_style
        if props is None or numbering is None:return None
        num_node=props.find(ns+'numId');level_node=props.find(ns+'ilvl')
        if num_node is None:return None
        num_id=num_node.get(ns+'val');level=level_node.get(ns+'val','0') if level_node is not None else '0'
        num=next((n for n in numbering.findall(ns+'num') if n.get(ns+'numId')==num_id),None)
        if num is None:return None
        abstract_id=num.find(ns+'abstractNumId')
        abstract=next((n for n in numbering.findall(ns+'abstractNum') if abstract_id is not None and n.get(ns+'abstractNumId')==abstract_id.get(ns+'val')),None)
        lvl=next((n for n in abstract.findall(ns+'lvl') if n.get(ns+'ilvl')==level),None) if abstract is not None else None
        if lvl is None:return None
        fmt=lvl.find(ns+'numFmt');fmt=fmt.get(ns+'val','decimal') if fmt is not None else 'decimal'
        start=lvl.find(ns+'start');start=int(start.get(ns+'val','1')) if start is not None else 1
        override=next((n for n in num.findall(ns+'lvlOverride') if n.get(ns+'ilvl')==level),None)
        if override is not None:
            value=override.find(ns+'startOverride')
            if value is not None:start=int(value.get(ns+'val','1'))
        key=(num_id,level);current=counters.get(key,start-1)+1;counters[key]=current
        if level!='0':result.warn('word_nested_numbering','중첩 목록 수준을 별도 목록으로 보존했습니다. 복합 번호 연결은 원본과 대조해 주세요.')
        types={'decimal':'1','lowerLetter':'a','upperLetter':'A','lowerRoman':'i','upperRoman':'I'}
        if fmt not in (*types,'bullet'):
            result.warn('word_numbering_format','지원하지 않는 자동 번호 형식 '+fmt+'은 기본 순서 목록과 원본 정의를 보존했습니다.')
        return ('ul' if fmt=='bullet' else 'ol',num_id,level,current,types.get(fmt,'1'))

    def inline(node) -> str:
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in ('oMath','oMathPara'):
            value=omml_text(node)
            result.warn('office_math_converted','Office 수식을 읽을 수 있는 수식 표기로 변환하고 원본 XML을 함께 보존했습니다.')
            supported={'oMath','oMathPara','r','t','f','num','den','e','sSup','sSub','sSubSup','sup','sub','rad','deg','m','mr','nary','limLow','limUpp','lim','func','fName','d','acc','bar','groupChr','eqArr','box','borderBox'}
            unknown={n.tag.rsplit('}',1)[-1] for n in node.iter() if n.tag.startswith('{http://schemas.openxmlformats.org/officeDocument/2006/math}') and n.tag.rsplit('}',1)[-1] not in supported and not n.tag.rsplit('}',1)[-1].endswith('Pr') and not any(p.tag.rsplit('}',1)[-1].endswith('Pr') for p in n.iterancestors() if p is not node)}
            formula=assets.add(__import__('lxml.etree',fromlist=['tostring']).tostring(node), 'office-equation.xml','office-math')
            result.metadata.setdefault('equations',[]).append({'linear_math':value,'source_xml':formula,'unsupported_tags':sorted(unknown)})
            if unknown:result.warn('office_math_partial','일부 수식 구조는 원본 XML 대조가 필요합니다: '+', '.join(sorted(unknown)))
            return '<code>'+html.escape(value)+'</code>'
        if tag=='commentRangeStart':
            ident=re.sub(r'[^\w-]','_',node.get(ns+'id',''))
            return f'<a id="comment-ref-{ident}" href="#comment-{ident}">'
        if tag=='commentRangeEnd':return '</a>'
        if tag=='commentReference':return ''
        if tag in ('footnoteReference','endnoteReference'):
            kind={'footnoteReference':'footnote','endnoteReference':'endnote'}[tag]
            ident=re.sub(r'[^\w-]','_',node.get(ns+'id',''))
            return f'<sup><a id="{kind}-ref-{ident}" href="#{kind}-{ident}">{ident}</a></sup>'
        if tag in ("t", "delText", "instrText"):
            if tag == "instrText":
                result.warn("field_code", "필드 명령과 표시값은 패키지 XML에 함께 보존했습니다. 필드를 실행하지 않습니다.")
                return ""
            return html.escape(node.text or "")
        if tag == "tab":
            return "&#9;"
        if tag in ("br", "cr"):
            return "<br>"
        if tag == "blip":
            rid = node.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            if rid and rid in rels:
                part = rels[rid].target_part
                local = assets.add(part.blob, Path(str(part.partname)).name, str(part.partname))
                return f'<img src="{local}">'
        if tag in ("rPr", "pPr", "tblPr", "tcPr", "sectPr"):
            return ""
        content = "".join(inline(c) for c in node)
        if tag == "r":
            props = node.find(ns + "rPr")
            if props is not None:
                for prop, wrapper in (("b", "strong"), ("i", "em"), ("strike", "del")):
                    value = props.find(ns + prop)
                    if value is not None and value.get(ns + "val", "1") not in ("0", "false", "off"):
                        content = f"<{wrapper}>{content}</{wrapper}>"
        if tag == "hyperlink":
            rid = node.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = str(rels[rid].target_ref) if rid in rels else "#" + node.get(ns + "anchor", "")
            content = f'<a href="{html.escape(target, quote=True)}">{content}</a>'
        if tag in ("del", "ins"):
            result.warn("tracked_changes", "변경 추적의 삭제·삽입을 함께 보존했습니다. 확정본 판정은 하지 않습니다.")
            content = f"<{tag}>{content}</{tag}>"
        return content

    def paragraph(p) -> str:
        pstyle = p.find("./" + ns + "pPr/" + ns + "pStyle")
        style_id = pstyle.get(ns + "val", "") if pstyle is not None else ""
        style = next((s for s in doc.styles if s.style_id == style_id), None)
        name = style.name if style is not None else style_id
        heading = re.search(r"heading\s*(\d)", name, re.I)
        tag = f"h{min(int(heading[1]), 6)}" if heading else "p"
        if p.find("./" + ns + "pPr/" + ns + "numPr") is not None or name.lower().startswith("list"):
            result.warn("word_numbering", "Word 목록의 번호·들여쓰기 정의는 XML에 보존했습니다. 화면의 자동 번호를 완전히 재현하지 않습니다.")
        return f"<{tag}>" + inline(p) + f"</{tag}>"

    def table(tbl) -> str:
        rows: list[list[dict]] = []
        for tr in tbl.findall(ns + "tr"):
            cells = []
            for tc in tr.findall(ns + "tc"):
                props = tc.find(ns + "tcPr")
                span = props.find(ns + "gridSpan") if props is not None else None
                merge = props.find(ns + "vMerge") if props is not None else None
                cells.append({"span": int(span.get(ns + "val", "1")) if span is not None else 1,
                              "merge": merge.get(ns + "val", "continue") if merge is not None else "",
                              "body": blocks(tc), "rowspan": 1})
            rows.append(cells)
        active: dict[int, dict] = {}
        for row in rows:
            col = 0
            for cell in row:
                if cell["merge"] == "continue" and col in active:
                    active[col]["rowspan"] += 1
                    cell["skip"] = True
                elif cell["merge"] == "restart":
                    active[col] = cell
                else:
                    for c in range(col, col + cell["span"]):
                        active.pop(c, None)
                col += cell["span"]
        return "<table>" + "".join("<tr>" + "".join(f'<td colspan="{c["span"]}" rowspan="{c["rowspan"]}">{c["body"]}</td>' for c in row if not c.get("skip")) + "</tr>" for row in rows) + "</table>"

    def blocks(parent) -> str:
        output = []
        previous=None
        for child in parent:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "p":
                spec=list_spec(child)
                if spec:
                    kind,num_id,level,start,marker=spec
                    key=(kind,num_id,level)
                    body='<li>'+inline(child)+'</li>'
                    if previous==key:
                        output[-1]=output[-1][:-len(kind)-3]+body+f'</{kind}>'
                    else:
                        attrs=f' start="{start}"'+(f' type="{marker}"' if marker!='1' else '') if kind=='ol' else ''
                        output.append(f'<{kind}{attrs}>'+body+f'</{kind}>')
                    previous=key
                else:
                    output.append(paragraph(child));previous=None
            elif name == "tbl":
                output.append(table(child));previous=None
            elif name not in ("sectPr", "tcPr"):
                output.append(inline(child));previous=None
        return "".join(output)

    # Assets emitted above already point to package-relative paths; no external loading.
    loader.cache.update({ref: ref for ref in refs.values()})
    loader.cache.update({record["path"]: record["path"] for record in assets.records})
    extract_html(blocks(doc.element.body), result, assets, loader, "body")
    result.units[-1].metadata["source_part"] = "word/document.xml"
    for section_index, section in enumerate(doc.sections, 1):
        for which in ("header", "footer", "first_page_header", "first_page_footer", "even_page_header", "even_page_footer"):
            part = getattr(section, which)
            if part.is_linked_to_previous:
                continue
            text = "\n".join(p.text for p in part.paragraphs)
            images = any(n.tag.rsplit("}", 1)[-1] in ("blip", "imagedata") for n in part._element.iter())
            if text or part.tables or images:
                # Header/footer relationship IDs belong to that part, not document.xml.
                rels = part.part.rels
                extract_html(blocks(part._element), result, assets, loader, f"section-{section_index}/{which}")
                result.units[-1].metadata["source_part"] = str(part.part.partname).lstrip("/")
    for name in sorted(names):
        if name.startswith("word/") and Path(name).name in ("footnotes.xml", "endnotes.xml", "comments.xml"):
            raw = (assets.root / refs[name]).read_bytes()
            tree = ET.fromstring(raw)
            for child in tree:
                texts = "".join(n.text or "" for n in child.iter() if n.tag.rsplit("}", 1)[-1] in ("t", "delText"))
                if texts:
                    kind={'footnotes.xml':'footnote','endnotes.xml':'endnote','comments.xml':'comment'}[Path(name).name]
                    ident=re.sub(r'[^\w-]','_',child.get(ns+'id',''))
                    note=f'<a id="{kind}-{ident}"></a>\n\n'+fence(texts,'text')+f'\n\n[본문으로 돌아가기](#{kind}-ref-{ident})'
                    unit=result.unit(Path(name).stem, name + "#" + str(child.attrib), note, texts, comparison="code_exact", raw_xml=refs[name])
                    unit.code_blocks=[texts]
            result.warn("note_layout", "각주·미주·주석과 본문 표식의 링크를 보존했습니다. 쪽의 표시 위치는 원본과 대조해 주세요.")
    result.warn("office_layout", "Word 본문 흐름과 패키지를 보존했습니다. 페이지 배치·부동 개체·스타일 상속·시각 의미는 자동 검증하지 않습니다.")


def pdf_tables(page, result: Extraction, assets: Assets, locator: str) -> tuple[str, list[dict]]:
    """Detect ruled tables geometrically, preserving blank/merged cells without OCR."""
    try:
        # Suppress the library's promotional layout-package message, not errors.
        with contextlib.redirect_stdout(io.StringIO()):
            finder = page.find_tables(strategy="lines_strict")
        tables = []
        parts = []
        for index, table in enumerate(finder.tables, 1):
            matrix = table.extract()
            boxes = [[list(box) if box is not None else None for box in row.cells] for row in table.rows]
            record = {"index": index, "bbox": list(table.bbox), "row_count": table.row_count,
                      "column_count": table.col_count, "cells": matrix, "cell_boxes": boxes,
                      "method": "vector_lines_strict", "ocr_used": False}
            if table.row_count * table.col_count > 200000:
                result.warn("pdf_table_limit", "검출된 표가 200,000셀 상한을 초과해 셀 변환을 생략했습니다.", locator)
                continue
            local = assets.json(f"{locator.replace(':', '-')}-table-{index}.json", record, locator)
            record["asset"] = local
            tables.append(record)
            xcuts = sorted({round(x, 2) for row in boxes for b in row if b for x in (b[0], b[2])})
            ycuts = sorted({round(y, 2) for row in boxes for b in row if b for y in (b[1], b[3])})
            rows = []
            plain_grid = all(b is not None for row in boxes for b in row)
            for row_index, cells in enumerate(matrix):
                rendered = []
                for col_index, value in enumerate(cells):
                    box = boxes[row_index][col_index]
                    if box is None:
                        # The detector uses None for covered/merged positions;
                        # original coordinates remain in JSON for review.
                        continue
                    colspan = max(1, sum(box[0] - .1 <= x < box[2] - .1 for x in xcuts))
                    rowspan = max(1, sum(box[1] - .1 <= y < box[3] - .1 for y in ycuts))
                    plain_grid = plain_grid and colspan == 1 and rowspan == 1
                    content = html.escape(value or "").replace("\n", "<br>")
                    rendered.append(f'<td colspan="{colspan}" rowspan="{rowspan}">{content}</td>')
                rows.append("<tr>" + "".join(rendered) + "</tr>")
            if plain_grid:
                cells = [[escape(value or "").replace("\r\n", "\n").replace("\n", "<br>") for value in row] for row in matrix]
                def display_width(value):
                    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in unicodedata.normalize("NFC", value) if not unicodedata.combining(c))
                widths = [min(80, max(3, max(display_width(row[c]) for row in cells))) for c in range(table.col_count)]
                def pipe_row(values):
                    return "| " + " | ".join(value + " " * max(0, widths[c] - display_width(value)) for c, value in enumerate(values)) + " |"
                rendered_table = "\n".join([pipe_row(cells[0]), "| " + " | ".join("-" * width for width in widths) + " |", *[pipe_row(row) for row in cells[1:]]])
                record["render_format"] = "markdown_table"
            else:
                rendered_table = "<table>\n" + "\n".join(rows) + "\n</table>"
                record["render_format"] = "html_merged_table"
            parts.append(f"**[검출 표 {index}: {table.row_count}행 × {table.col_count}열]**\n\n" + rendered_table + f"\n\n[표 셀·좌표 JSON](<{md_url(local)}>)")
        if tables:
            result.warn("pdf_table_geometry", "테두리 선으로 검출한 표의 셀·빈칸·병합 좌표를 추가했습니다. 텍스트층과 일부 내용이 중복되며 검출 완전성·시각 의미는 원본과 대조해야 합니다.", locator)
        return "\n\n".join(parts), tables
    except Exception as e:
        result.warn("pdf_table_detection_failed", f"표 검출 실패: {type(e).__name__}. 원문 텍스트·페이지 렌더·원본은 보존했습니다.", locator)
        return "", []


def pdf_extract(data: bytes, result: Extraction, assets: Assets) -> None:
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if doc.needs_pass:
            raise ConversionError("암호화된 PDF입니다. 열람 가능한 사본을 입력해 주세요.")
        if len(doc) > MAX_UNITS:
            raise ConversionError("PDF 페이지 수 상한을 초과했습니다.")
        result.metadata.update({"pages": len(doc), "pdf_metadata": doc.metadata, "toc": doc.get_toc()})
        for index, page in enumerate(doc):
            label = f"페이지 {index+1}"
            locator = f"page:{index+1}"
            raw = page.get_text("dict", sort=False)
            raw_asset = assets.json(f"page-{index+1:04d}-layout.json", raw, locator)
            # PDF producer order, not an invented reading order. Coordinates remain available.
            text = page.get_text("text", sort=False)
            from .raster_layout import order_blocks
            line_blocks=[]
            for block in raw.get('blocks',[]):
                for line in block.get('lines',[]):
                    line_blocks.append({'bbox':list(line['bbox']),'text':''.join(s['text'] for s in line['spans'])})
            ordered_text='\n'.join(b['text'] for b in order_blocks(line_blocks))
            scale = min(1.5, 2400 / max(page.rect.width, page.rect.height))
            preview = assets.add(page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png"), f"page-{index+1:04d}.png", locator)
            markdown = f"![{label} 원본 렌더](<{md_url(preview)}>)"
            table_md, tables = pdf_tables(page, result, assets, locator)
            if table_md:
                markdown += "\n\n### [원본 표의 셀 구조 — 선 검출]\n\n" + table_md
            if ordered_text and not tables and ordered_text.strip()!=text.strip():
                markdown += '\n\n### [배치 기반 읽는 순서 — 원본 대조 필요]\n\n'+'\n\n'.join(escape(b['text']) for b in order_blocks(line_blocks))
            markdown += "\n\n### [원본 텍스트층 — 대조용]\n\n" + fence(text, "text")
            unit = result.unit(label, locator, markdown, ordered_text or text, comparison="code_exact", layout=raw_asset, page_image=preview, width=page.rect.width, height=page.rect.height,producer_order_text=text,reading_order_method='whitespace_xy_cut')
            unit.metadata.update(table_count=len(tables), tables=tables)
            unit.code_blocks = [text]
            unit.metadata["links"] = page.get_links()
            unit.metadata["annotations"] = [annot.info for annot in (page.annots() or [])]
            unit.metadata["widgets"] = [{"name": w.field_name, "value": w.field_value, "type": w.field_type_string} for w in (page.widgets() or [])]
            for image_no, image in enumerate(page.get_images(full=True), 1):
                try:
                    extracted = doc.extract_image(image[0])
                    assets.add(extracted["image"], f"page-{index+1}-image-{image_no}.{extracted['ext']}", locator)
                except Exception:
                    result.warn("pdf_image", "PDF의 이미지 자산 하나를 별도로 추출하지 못했습니다. 페이지 렌더와 원본에는 남아 있습니다.", locator)
            if not text.strip():
                result.warn("image_only_page", "텍스트 레이어가 없는 페이지입니다. AI/OCR을 사용하지 않아 이미지 속 글자는 텍스트로 추출하지 않았습니다.", locator)
        for index in range(doc.embfile_count()):
            info = doc.embfile_info(index)
            assets.add(doc.embfile_get(index), info.get("filename", f"attachment-{index}.bin"), "pdf-attachment")
    from .pdf_continuation import combine_continued_tables
    combine_continued_tables(result,assets)
    result.warn("pdf_structure", "PDF 텍스트·좌표·페이지 렌더·이미지와 선으로 검출한 표를 보존했습니다. 선 없는 표·다단 읽기 순서·도표·글꼴의 의미 구조는 자동 복원하지 않습니다.")


def xlsx_extract(data: bytes, result: Extraction, assets: Assets) -> None:
    from openpyxl import load_workbook
    from .office_semantics import excel_display

    with checked_zip(data) as z:
        refs = archive_parts(z, assets)
    workbook = load_workbook(io.BytesIO(data), data_only=False, read_only=False, keep_links=False, rich_text=True)
    cached = load_workbook(io.BytesIO(data), data_only=True, read_only=True, keep_links=False)
    try:
        result.metadata["sheet_order"] = workbook.sheetnames
        book = ET.fromstring((assets.root / refs["xl/workbook.xml"]).read_bytes())
        relroot = ET.fromstring((assets.root / refs["xl/_rels/workbook.xml.rels"]).read_bytes())
        reltargets = {r.get("Id"): r.get("Target", "") for r in relroot}
        sheet_parts = {}
        for node in book.iter():
            if node.tag.rsplit("}", 1)[-1] == "sheet":
                rid = node.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                target = reltargets.get(rid, "")
                sheet_parts[node.get("name")] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        for sheet in (workbook[name] for name in workbook.sheetnames):
            chart_parts = package_chart_parts(refs, assets, sheet_parts.get(sheet.title, ""))
            charts = [{"source_part": part, **chart_cache_data((assets.root / refs[part]).read_bytes())} for part in chart_parts]
            chart_md = ""
            chart_asset = None
            if charts:
                chart_asset = assets.json(f"sheet-{sheet.title}-charts.json", charts, "sheet:" + sheet.title)
                chart_md = "\n\n" + "\n\n".join(chart_markdown(chart, chart_asset) for chart in charts)
            if not hasattr(sheet, "iter_rows"):
                # Chartsheets are real workbook tabs, not worksheets with no cells.
                result.unit(sheet.title, "sheet:" + sheet.title, chart_md or "차트 전용 시트입니다. 원본 패키지에서 개체를 확인해 주세요.",
                            sheet_type="chartsheet", state=sheet.sheet_state, chart_data=chart_asset, charts=len(charts), source_part=sheet_parts.get(sheet.title))
                result.warn("chartsheet_visual", "차트 전용 시트의 순서·차트 제목·참조 수식·저장 캐시를 보존했습니다. 시각 배치와 캐시가 없는 값은 복원하지 않았습니다.", "sheet:" + sheet.title)
                continue
            if sheet.max_row * sheet.max_column > 200000:
                raise ConversionError(f"시트 {sheet.title}의 셀 영역이 200,000셀 상한을 초과했습니다.")
            rows, cells, formula_notes = [], [], []
            for row in sheet.iter_rows():
                html_cells = []
                for cell in row:
                    value = cell.value
                    formula = value if cell.data_type == "f" else None
                    stored = cached[sheet.title][cell.coordinate].value if formula else None
                    record = {"coordinate": cell.coordinate, "value": value, "data_type": cell.data_type,
                              "formula": formula, "cached_value": stored, "number_format": cell.number_format,
                              "style_id": cell.style_id, "comment": {"text": cell.comment.text, "author": cell.comment.author} if getattr(cell, "comment", None) else None,
                              "hyperlink": cell.hyperlink.target if getattr(cell, "hyperlink", None) else None}
                    rendered,supported=excel_display(stored if formula and stored is not None else value,cell.number_format)
                    record.update(display_value=rendered,display_format_supported=supported)
                    cells.append(record)
                    if not supported:result.warn('sheet_format_partial','일부 숫자 표시 형식은 원시 값으로 보존했습니다. 셀의 number_format과 원본을 대조해 주세요.','sheet:'+sheet.title)
                    if formula:
                        formula_notes.append(f"{cell.coordinate}: {formula}; 저장된 캐시={stored!r}")
                    html_cells.append("<td>" + html.escape(rendered).replace("\n", "<br>") + "</td>")
                rows.append("<tr>" + "".join(html_cells) + "</tr>")
            metadata = {"name": sheet.title, "state": sheet.sheet_state, "max_row": sheet.max_row, "max_column": sheet.max_column,
                        "merged_ranges": [str(r) for r in sheet.merged_cells.ranges],
                        "hidden_rows": [i for i, d in sheet.row_dimensions.items() if d.hidden],
                        "hidden_columns": [i for i, d in sheet.column_dimensions.items() if d.hidden],
                        "cells": cells, "charts": len(sheet._charts), "images": len(sheet._images)}
            cell_asset = assets.json(f"sheet-{sheet.title}-cells.json", metadata, "sheet:" + sheet.title)
            body = "<table>\n" + "\n".join(rows) + "\n</table>"
            if formula_notes:
                body += "\n\n수식·저장된 캐시:\n\n" + fence("\n".join(formula_notes), "text")
            if metadata["merged_ranges"]:
                body += "\n\n병합 범위: " + escape(", ".join(metadata["merged_ranges"]))
            body += f"\n\n[셀·서식·숨김·병합 정보](<{md_url(cell_asset)}>)"
            body += chart_md
            result.unit(sheet.title, "sheet:" + sheet.title, body, "".join(str(c["value"]) for c in cells if c["value"] is not None), comparison="spreadsheet_cells", cell_data=cell_asset, state=sheet.sheet_state, sheet_type="worksheet", chart_data=chart_asset)
            for image in sheet._images:
                assets.add(image._data(), "sheet-image." + image.format, "sheet:" + sheet.title)
        if any("vbaProject" in name for name in refs):
            result.warn("macros_preserved", "매크로를 원본 패키지 자산으로 보존했으며 실행하지 않았습니다.")
    finally:
        cached.close()
        workbook.close()
    result.warn("sheet_display", "수식·저장 캐시·원시 값·지원 숫자 표시 형식을 보존했습니다. 미지원 날짜/복합 서식·재계산·차트 의미·조건부서식의 시각 결과는 원본 대조가 필요합니다.")


def pptx_extract(data: bytes, result: Extraction, assets: Assets) -> None:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    with checked_zip(data) as z:
        refs = archive_parts(z, assets)
    deck = Presentation(io.BytesIO(data))
    result.metadata.update({"slides": len(deck.slides), "width": deck.slide_width, "height": deck.slide_height})

    def shape_extract(shape, elements: list[dict], prefix: str) -> str:
        locator = prefix + f"/shape:{shape.shape_id}"
        record = {"id": shape.shape_id, "name": shape.name, "type": int(shape.shape_type), "left": shape.left, "top": shape.top, "width": shape.width, "height": shape.height, "text": shape.text if shape.has_text_frame else ""}
        elements.append(record)
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            return "\n\n".join(shape_extract(child, elements, locator) for child in shape.shapes)
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            local = assets.add(shape.image.blob, "slide-image." + shape.image.ext, locator)
            return f"![{escape(shape.name)}](<{md_url(local)}>)"
        if shape.has_table:
            rows = []
            record["table"] = []
            for row in shape.table.rows:
                cells = []
                for c in row.cells:
                    record["table"].append({"text": c.text, "merge_origin": c.is_merge_origin, "spanned": c.is_spanned, "rowspan": c.span_height, "colspan": c.span_width})
                    if c.is_spanned:
                        continue
                    cells.append(f'<td rowspan="{c.span_height}" colspan="{c.span_width}">' + html.escape(c.text).replace("\n", "<br>") + "</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            return "<table>" + "".join(rows) + "</table>"
        if shape.has_chart:
            series = []
            for s in shape.chart.series:
                try:
                    series.append({"name": s.name, "values": list(s.values)})
                except Exception:
                    result.warn("chart_series", "차트 시리즈 일부를 파싱하지 못했습니다. 원본 XML은 보존했습니다.", locator)
            chart_data = chart_cache_data(shape.chart.part.blob)
            chart_data["parsed_series"] = series
            local = assets.json("chart-data.json", chart_data, locator)
            record["chart"] = chart_data
            return chart_markdown(chart_data, local)
        if shape.has_text_frame:
            record["paragraphs"] = [{"text": p.text, "level": p.level, "runs": [{"text": r.text, "bold": r.font.bold, "italic": r.font.italic, "size": r.font.size} for r in p.runs]} for p in shape.text_frame.paragraphs]
            return fence(shape.text, "text")
        result.warn("slide_object", f"텍스트·표·이미지 이외 개체 {shape.name}의 XML·좌표를 보존했습니다. 시각 의미는 미추출입니다.", locator)
        return ""

    for index, slide in enumerate(deck.slides, 1):
        locator = f"slide:{index}"
        elements: list[dict] = []
        content = "\n\n".join(shape_extract(shape, elements, locator) for shape in slide.shapes)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text
            if notes:
                content += "\n\n발표자 메모:\n\n" + fence(notes, "text")
        raw = assets.json(f"slide-{index:04d}-structure.json", elements, locator)
        result.unit(f"슬라이드 {index}", locator, content, "\n".join(e["text"] for e in elements if e["text"]), comparison="slide_objects", structure=raw, hidden=slide._element.get("show") == "0")
    result.warn("slide_layout", "슬라이드 관계 순서·개체 순서·좌표·텍스트·메모·이미지·표·차트 값을 보존했습니다. 배치·애니메이션·SmartArt·상속 요소의 시각 결과는 미검증입니다.")


def image_extract(data: bytes, result: Extraction, assets: Assets) -> None:
    from PIL import Image, ImageSequence

    with Image.open(io.BytesIO(data)) as image:
        result.metadata.update({"image_format": image.format, "width": image.width, "height": image.height, "frames": getattr(image, "n_frames", 1), "info": {k: str(v)[:500] for k, v in image.info.items()}})
        if getattr(image, "n_frames", 1) > MAX_UNITS or image.width * image.height > 80000000:
            raise ConversionError("이미지 프레임 수 또는 픽셀 수 상한을 초과했습니다.")
        for index, frame in enumerate(ImageSequence.Iterator(image), 1):
            buf = io.BytesIO()
            frame.convert("RGBA").save(buf, "PNG")
            local = assets.add(buf.getvalue(), f"frame-{index:04d}.png", f"frame:{index}")
            result.unit(f"프레임 {index}", f"frame:{index}", f"![원본 이미지 프레임 {index}](<{md_url(local)}>)", image=local, duration=frame.info.get("duration"))
    result.warn("image_text_unextracted", "이미지와 프레임은 보존했습니다. AI/OCR을 사용하지 않아 이미지 내부 문자·표·도형 관계는 텍스트로 추출하지 않았습니다.")


def epub_extract(data: bytes, result: Extraction, assets: Assets) -> None:
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse,unquote
    with checked_zip(data) as z:
        refs = archive_parts(z, assets)
        container = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = next((n.attrib["full-path"] for n in container.iter() if n.tag.endswith("rootfile")), None)
        if not rootfile or rootfile not in refs:
            raise ConversionError("EPUB의 OPF 문서를 찾지 못했습니다.")
        opf = ET.fromstring(z.read(rootfile))
        items = {n.attrib["id"]: n.attrib for n in opf.iter() if n.tag.endswith("}item")}
        spine = [n.attrib["idref"] for n in opf.iter() if n.tag.endswith("}itemref")]
        result.metadata["spine"] = spine
        spine_parts={}
        for index,item_id in enumerate(spine,1):
            if item_id in items:
                import posixpath
                spine_parts[posixpath.normpath(str(PurePosixPath(rootfile).parent/items[item_id]['href']))]=index
        for index, item_id in enumerate(spine, 1):
            item = items.get(item_id)
            if not item:
                result.warn("epub_missing_item", f"EPUB spine 항목 {item_id}가 manifest에 없습니다.")
                continue
            import posixpath
            part = posixpath.normpath(str(PurePosixPath(rootfile).parent / item["href"]))
            if part not in refs:
                result.warn("epub_missing_part", f"EPUB 본문 {part}를 찾지 못했습니다.")
                continue
            loader = ResourceLoader(assets, download=False)
            for target, local in refs.items():
                relative = posixpath.relpath(target, posixpath.dirname(part))
                loader.cache[relative] = local
            text, _ = decode(z.read(part))
            soup=BeautifulSoup(text,'html.parser')
            for node in soup.find_all(id=True):node['id']=f'epub-{index}-'+node['id']
            if soup.body:soup.body['id']=f'epub-{index}-start'
            for link in soup.find_all('a',href=True):
                parsed=urlparse(link['href'])
                if parsed.scheme or parsed.netloc:continue
                target=posixpath.normpath(posixpath.join(posixpath.dirname(part),unquote(parsed.path))) if parsed.path else part
                if target in spine_parts:
                    link['href']=f'#epub-{spine_parts[target]}-'+(unquote(parsed.fragment) if parsed.fragment else 'start')
                elif parsed.path in loader.cache:
                    link['href']=loader.cache[parsed.path]+('#'+parsed.fragment if parsed.fragment else '')
                else:result.warn('epub_link_unresolved','일부 EPUB 내부 링크의 대상을 다시 연결하지 못했습니다.',part)
            extract_html(str(soup), result, assets, loader, f"spine:{index}/{part}")
    result.warn("epub_navigation", "EPUB spine 순서·장간 앵커와 패키지를 보존했습니다. 고정 레이아웃·글꼴·복잡한 각주 왕복 링크는 원본과 대조해 주세요.")


def mhtml_extract(data: bytes, result: Extraction, assets: Assets) -> None:
    message = BytesParser(policy=policy.default).parsebytes(data)
    parts = list(message.walk())
    loader = ResourceLoader(assets, download=False)
    html_parts = []
    for index, part in enumerate(parts):
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        location = str(part.get("Content-Location", ""))
        cid = str(part.get("Content-ID", "")).strip("<>")
        local = assets.add(payload, part.get_filename() or f"mime-{index}." + part.get_content_subtype(), location)
        if location:
            loader.cache[location] = local
        if cid:
            loader.cache["cid:" + cid] = local
        if part.get_content_type() == "text/html":
            html_parts.append((payload, part.get_content_charset(), location))
    for index, (payload, charset, location) in enumerate(html_parts, 1):
        try:
            text, _ = decode(payload, charset or "")
        except (UnicodeDecodeError, LookupError):
            text, _ = decode(payload)
            result.warn("mime_encoding", "MIME 선언 인코딩으로 읽지 못해 인코딩을 재판별했습니다.")
        extract_html(text, result, assets, loader, f"mime-html:{index}", location)
    if not html_parts:
        result.warn("mhtml_no_html", "MHTML에서 HTML 본문을 찾지 못했습니다.")


def hwpx_extract(data: bytes, result: Extraction, assets: Assets) -> None:
    with checked_zip(data) as z:
        refs = archive_parts(z, assets)
        sections = sorted((n for n in refs if re.fullmatch(r"Contents/section\d+\.xml", n)), key=lambda n: int(re.search(r"section(\d+)", n)[1]))
        for name in sections:
            tree = ET.fromstring(z.read(name))
            pieces = []
            def walk(node):
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "t":
                    pieces.append(node.text or "")
                for child in node:
                    walk(child)
                if tag == "p":
                    pieces.append("\n")
            walk(tree)
            text = "".join(pieces)
            result.unit(name, name, fence(text, "text"), text, comparison="code_exact", raw_xml=refs[name])
    result.warn("hwpx_partial", "HWPX 섹션 텍스트와 모든 패키지 자산을 보존했습니다. 중첩 문단·표·수식·쪽 배치의 의미 구조는 부분지원입니다.")


def extract(data: bytes, filename: str, assets: Assets, *, encoding: str = "", content_type: str = "", source_path: Path | None = None,
            base_url: str = "", download: bool = True, selector: str = "") -> Extraction:
    fmt = detect(data, filename, content_type)
    result = Extraction(Path(filename).stem or "문서", fmt)
    suffix = Path(filename).suffix.lower().lstrip(".")
    compatible = {"image": IMAGE_EXTENSIONS, "markdown": {"md", "markdown", "mdown"}, "html": {"html", "htm", "xhtml"}, "xlsx": {"xlsx", "xlsm", "xltx", "xltm"}, "mhtml": {"mhtml", "mht"}, "odf":{'odt','ods','odp','odg','ott','ots','otp'}, "text": TEXT_EXTENSIONS | {""}}
    if suffix and suffix not in compatible.get(fmt, {fmt}) and fmt not in ("unsupported", "legacy_binary"):
        result.warn("extension_mismatch", f"확장명 .{suffix}와 달리 실제 구조는 {fmt}로 판별했습니다.")
    if fmt in ("text", "markdown"):
        text_extract(data, filename, result, encoding)
    elif fmt in ("csv", "tsv"):
        csv_extract(data, result, assets, encoding)
    elif fmt == "html":
        text, used = decode(data, encoding)
        result.metadata["encoding"] = used
        extract_html(text, result, assets, ResourceLoader(assets, source_path, base_url, download), base_url=base_url, selector=selector)
    elif fmt == "pdf":
        pdf_extract(data, result, assets)
    elif fmt == "docx":
        docx_extract(data, result, assets)
    elif fmt == "xlsx":
        xlsx_extract(data, result, assets)
    elif fmt == "pptx":
        pptx_extract(data, result, assets)
    elif fmt == "image":
        image_extract(data, result, assets)
    elif fmt == "epub":
        epub_extract(data, result, assets)
    elif fmt == "mhtml":
        mhtml_extract(data, result, assets)
    elif fmt == "hwpx":
        hwpx_extract(data, result, assets)
    elif fmt in ('hwp','doc','xls','ppt'):
        from .legacy import extract_legacy
        extract_legacy(data,result,assets)
    elif fmt == 'odf':
        from .odf_extract import extract_odf
        extract_odf(data,result,assets)
    elif fmt == "rtf":
        from striprtf.striprtf import rtf_to_text
        text, used = decode(data, encoding)
        plain = rtf_to_text(text)
        result.unit("RTF 텍스트", "document", fence(plain, "text"), plain, comparison="code_exact")
        result.warn("rtf_partial", "RTF 텍스트만 추출했습니다. 글꼴·이미지·표·개체 구조는 원본에 보존했습니다.")
    elif fmt == "svg":
        text, used = decode(data, encoding)
        ET.fromstring(text)  # Reject DTD/entities, never execute SVG scripts.
        result.unit("SVG 원문", "document", fence(text, "xml"), text, comparison="code_exact")
        try:
            import pymupdf
            tree=ET.fromstring(data)
            removed=False
            for parent in tree.iter():
                for node in list(parent):
                    if node.tag.rsplit('}',1)[-1] in ('script','foreignObject','style'):
                        parent.remove(node);removed=True
                for key,value in list(parent.attrib.items()):
                    local=key.rsplit('}',1)[-1]
                    if local.lower().startswith('on') or ('href' in local and not value.startswith('#')) or ('url(' in value and not re.fullmatch(r'url\(#[\w.-]+\)',value)):
                        del parent.attrib[key];removed=True
            with pymupdf.open(stream=ET.tostring(tree),filetype='svg') as svg:
                page=svg[0];scale=min(2,2400/max(page.rect.width,page.rect.height))
                png=assets.add(page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False).tobytes('png'),'svg-preview.png','svg-render')
            result.units[0].markdown='![SVG 도식 렌더](<'+md_url(png)+'>)\n\n'+result.units[0].markdown
            if removed:result.warn('svg_sanitized','SVG 렌더에서 실행 요소·외부 참조를 제외했습니다. 원본 SVG 코드는 보존했습니다.')
        except Exception as e:
            result.warn('svg_render_failed','SVG 렌더에 실패해 코드·원본을 보존했습니다: '+type(e).__name__)
        result.warn("svg_visual", "SVG 코드와 안전화한 페이지 렌더를 보존했습니다. 도형의 의미 관계는 자동 인증하지 않습니다.")
    else:
        result.unit("원본 보관", "document", "이 형식은 본문 변환을 지원하지 않습니다. 원본 파일을 보존했습니다.")
        result.warn("unsupported_format", "본문 미변환 형식입니다. 지원하지 않는 구형 변형·음성·영상·임의 바이너리는 원본 보관만 지원합니다.")
    return result

