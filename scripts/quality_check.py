"""Explicit public corpus check; reads OOXML independently of extraction libraries.

Run: uv run --offline python scripts/quality_check.py --out outputs/quality-NEW
Public fixture downloads are GET-only, pinned to resolved Git commits and hashed.
This is a scoped content/structure audit, not a pixel/meaning identity certificate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from source_preserve_md.engine import convert, validate_bundle


ROOT = Path(__file__).resolve().parents[1]
MD = MarkdownIt("commonmark", {"html": True}).enable("table")
W = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
A = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
X = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
C = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
FIXTURES = [
    ("python-openxml/python-docx", "master", "tests/test_files/having-images.docx"),
    ("python-openxml/python-docx", "master", "features/steps/test_files/blk-paras-and-tables.docx"),
    ("python-openxml/python-docx", "master", "features/steps/test_files/par-hyperlinks.docx"),
    ("python-openxml/python-docx", "master", "features/steps/test_files/num-having-numbering-part.docx"),
    ("python-openxml/python-docx", "master", "features/steps/test_files/hdr-header-footer.docx"),
    ("python-openxml/python-docx", "master", "features/steps/test_files/comments-rich-para.docx"),
    ("scanny/python-pptx", "master", "features/steps/test_files/tbl-cell.pptx"),
    ("scanny/python-pptx", "master", "features/steps/test_files/shp-picture.pptx"),
    ("scanny/python-pptx", "master", "features/steps/test_files/shp-groupshape.pptx"),
    ("scanny/python-pptx", "master", "features/steps/test_files/prs-notes.pptx"),
    ("scanny/python-pptx", "master", "features/steps/test_files/cht-category-access.pptx"),
    ("pandas-dev/pandas", "main", "pandas/tests/io/data/excel/test1.xlsx"),
    ("pandas-dev/pandas", "main", "pandas/tests/io/data/excel/testmultiindex.xlsx"),
    ("pandas-dev/pandas", "main", "pandas/tests/io/data/excel/test_multisheet.xlsx"),
    ("pandas-dev/pandas", "main", "pandas/tests/io/data/excel/chartsheet.xlsx"),
    ("pandas-dev/pandas", "main", "pandas/tests/io/data/excel/test_types.xlsx"),
]


def compact(text):
    return re.sub(r"\s+", "", text or "")


def download_corpus(folder):
    cache = ROOT / "examples" / "quality-inputs"
    cache.mkdir(exist_ok=True)
    catalog = cache / "sources.json"
    if catalog.exists():
        sources = json.loads(catalog.read_text())
        from fetch_quality_inputs import fetch_catalog
        fetch_catalog(cache)
        return cache, sources
    commits = {}
    for repo, ref, _ in FIXTURES:
        if repo not in commits:
            response = httpx.get(f"https://api.github.com/repos/{repo}/commits/{ref}", timeout=20)
            response.raise_for_status()
            commits[repo] = response.json()["sha"]
    targets = [(f"https://raw.githubusercontent.com/{repo}/{commits[repo]}/{path}", Path(path).name,
                {"repo": repo, "commit": commits[repo], "path": path}) for repo, _, path in FIXTURES]
    targets += [("https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf", "w3c-dummy.pdf", {}),
                ("https://docs.python.org/3/tutorial/controlflow.html", "python-controlflow.html", {})]

    def get(item):
        url, filename, provenance = item
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        data = response.content
        assert len(data) <= 10 * 1024 * 1024
        (cache / filename).write_bytes(data)
        return {"url": str(response.url), "filename": filename, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **provenance}

    sources = list(ThreadPoolExecutor(4).map(get, targets))
    catalog.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n")
    return cache, sources


def plain(markdown):
    return BeautifulSoup(MD.render(markdown), "html.parser").get_text()


def source_relationships(z, part):
    directory, name = posixpath.split(part)
    rels = posixpath.join(directory, "_rels", name + ".rels")
    if rels not in z.namelist():
        return {}
    return {n.attrib["Id"]: n.attrib for n in ET.fromstring(z.read(rels))}


def check_ooxml(source, output):
    structure = json.loads((output / "structure.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    raw = (output / "00_원본복원.md").read_bytes()
    parts = {r["id"]: raw[r["start"]:r["end"]].decode() for r in manifest["markdown_ranges"]}
    units = structure["units"]
    checks = []

    def record(kind, passed, **details):
        checks.append({"kind": kind, "passed": bool(passed), **details})

    with zipfile.ZipFile(source) as z:
        if source.suffix == ".docx":
            tree = ET.fromstring(z.read("word/document.xml"))
            body = tree.find("w:body", W)
            unit = next(u for u in units if u["source_locator"] == "body")
            md = parts[unit["id"]]
            expected = "".join(n.text or "" for n in body.findall(".//w:t", W))
            record("docx_body_text_order", compact(expected) == compact(plain(md)), source_characters=len(compact(expected)), markdown_characters=len(compact(plain(md))))
            record("docx_table_count", len(body.findall(".//w:tbl", W)) == len(BeautifulSoup(MD.render(md), "html.parser").find_all("table")))
            rels = source_relationships(z, "word/document.xml")
            links = [rels[h.attrib[R + "id"]]["Target"] for h in body.findall(".//w:hyperlink", W) if R + "id" in h.attrib]
            actual_links = [a.get("href") for a in BeautifulSoup(MD.render(md), "html.parser").find_all("a")]
            record("docx_hyperlink_targets", Counter(links) <= Counter(actual_links), source_links=len(links))
            expected_images = []
            for b in body.findall(".//a:blip", A):
                rid = b.attrib.get(R + "embed")
                if rid in rels:
                    path = posixpath.normpath(posixpath.join("word", rels[rid]["Target"]))
                    expected_images.append(hashlib.sha256(z.read(path)).hexdigest())
            actual_images = [hashlib.sha256((output / img["src"]).read_bytes()).hexdigest() for img in BeautifulSoup(MD.render(md), "html.parser").find_all("img") if (output / img.get("src", "")).is_file()]
            record("docx_image_links_and_bytes", Counter(expected_images) == Counter(actual_images), source_images=len(expected_images))
            # Header/footer references are independent OOXML parts with their own
            # relationship IDs; image-only parts have no w:t text to trigger on.
            for kind in ("headerReference", "footerReference"):
                for reference in tree.findall(".//w:" + kind, W):
                    target = rels[reference.attrib[R + "id"]]["Target"]
                    part = posixpath.normpath(posixpath.join("word", target))
                    component = ET.fromstring(z.read(part))
                    component_units = [u for u in units if u["metadata"].get("source_part") == part]
                    expected_component_text = "".join(n.text or "" for n in component.findall(".//w:t", W))
                    component_rels = source_relationships(z, part)
                    expected_component_images = []
                    for b in component.findall(".//a:blip", A):
                        rid = b.attrib.get(R + "embed")
                        target_image = posixpath.normpath(posixpath.join(posixpath.dirname(part), component_rels[rid]["Target"]))
                        expected_component_images.append(hashlib.sha256(z.read(target_image)).hexdigest())
                    if expected_component_text or expected_component_images:
                        matched = False
                        for u in component_units:
                            component_md = parts[u["id"]]
                            component_soup = BeautifulSoup(MD.render(component_md), "html.parser")
                            actual_component_images = [hashlib.sha256((output / image["src"]).read_bytes()).hexdigest() for image in component_soup.find_all("img") if (output / image.get("src", "")).is_file()]
                            if compact(expected_component_text) == compact(component_soup.get_text()) and Counter(expected_component_images) == Counter(actual_component_images):
                                matched = True
                        record("docx_header_footer_text_images", matched, source_part=part, source_images=len(expected_component_images), output_units=len(component_units))
            numbered = len(body.findall(".//w:numPr", W))
            if numbered:
                record("docx_list_structure", bool(BeautifulSoup(MD.render(md), "html.parser").find(["ol", "ul"])), source_numbered_paragraphs=numbered)

        if source.suffix == ".pptx":
            tree = ET.fromstring(z.read("ppt/presentation.xml"))
            rels = source_relationships(z, "ppt/presentation.xml")
            slide_nodes = [n for n in tree.iter() if n.tag.endswith("}sldId")]
            record("pptx_slide_count", len(slide_nodes) == len(units), source_slides=len(slide_nodes), output_units=len(units))
            for index, node in enumerate(slide_nodes):
                part = posixpath.normpath(posixpath.join("ppt", rels[node.attrib[R + "id"]]["Target"]))
                slide = ET.fromstring(z.read(part))
                text = compact(plain(parts[units[index]["id"]]))
                paragraphs = ["".join(n.text or "" for n in p.findall(".//a:t", A)) for p in slide.findall(".//a:p", A)]
                position, missing = 0, []
                for paragraph in filter(None, map(compact, paragraphs)):
                    found = text.find(paragraph, position)
                    if found < 0:
                        missing.append(paragraph[:100])
                    else:
                        position = found + len(paragraph)
                record("pptx_paragraph_order", not missing, slide=index + 1, paragraphs=len(paragraphs), missing=missing)
                slide_rels = source_relationships(z, part)
                for chart in slide.findall(".//c:chart", C):
                    chart_part = posixpath.normpath(posixpath.join(posixpath.dirname(part), slide_rels[chart.attrib[R + "id"]]["Target"]))
                    chart_root = ET.fromstring(z.read(chart_part))
                    labels = [n.text or "" for n in chart_root.findall(".//c:cat//c:v", C)]
                    missing_labels = [label for label in labels if compact(label) not in text]
                    record("pptx_chart_category_labels", not missing_labels, slide=index + 1, labels=len(labels), missing=missing_labels[:10])

        if source.suffix == ".xlsx":
            workbook = ET.fromstring(z.read("xl/workbook.xml"))
            sheets = workbook.findall("x:sheets/x:sheet", X)
            record("xlsx_sheet_order_count", [n.attrib["name"] for n in sheets] == [u["label"] for u in units], source_sheets=[n.attrib["name"] for n in sheets], output_sheets=[u["label"] for u in units])
            rels = source_relationships(z, "xl/workbook.xml")
            strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                strings = ["".join(n.text or "" for n in si.findall(".//x:t", X)) for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
            for node in sheets:
                unit = next((u for u in units if u["label"] == node.attrib["name"]), None)
                if not unit or not unit["metadata"].get("cell_data"):
                    continue
                cell_data = json.loads((output / unit["metadata"]["cell_data"]).read_text())
                actual = {c["coordinate"]: c for c in cell_data["cells"]}
                target = rels[node.attrib[R + "id"]]["Target"]
                part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                sheet = ET.fromstring(z.read(part))
                missing = []
                for cell in sheet.findall(".//x:sheetData/x:row/x:c", X):
                    coord = cell.attrib["r"]
                    value = cell.find("x:v", X); formula = cell.find("x:f", X)
                    record_value = actual.get(coord)
                    if record_value is None:
                        missing.append(coord)
                        continue
                    if formula is not None and formula.text:
                        if record_value["formula"] != "=" + formula.text:
                            missing.append(coord + ":formula")
                    elif cell.attrib.get("t") == "s" and value is not None:
                        if record_value["value"] != strings[int(value.text)]:
                            missing.append(coord + ":string")
                    elif cell.attrib.get("t") == "inlineStr":
                        expected = "".join(n.text or "" for n in cell.findall(".//x:t", X))
                        if record_value["value"] != expected:
                            missing.append(coord + ":inline-string")
                    elif value is not None and isinstance(record_value["value"], (int, float)):
                        if float(record_value["value"]) != float(value.text):
                            missing.append(coord + ":number")
                record("xlsx_source_cells", not missing, sheet=node.attrib["name"], source_cells=len(sheet.findall(".//x:sheetData/x:row/x:c", X)), missing=missing[:15])
                expected_merges = [m.attrib["ref"] for m in sheet.findall("x:mergeCells/x:mergeCell", X)]
                record("xlsx_merge_ranges", set(expected_merges) == set(cell_data["merged_ranges"]), sheet=node.attrib["name"])
    return checks


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    cache, sources = download_corpus(output)
    rows = []
    for source in sources:
        path = cache / source["filename"]
        folder = output / path.stem
        try:
            # This corpus checks direct extraction against OOXML/DOM originals.
            # OCR has separate real-engine fixtures and accuracy limitations.
            kwargs = {"download_assets": False, "ocr": "off"}
            if path.suffix == ".html":
                kwargs["selector"] = 'div[role="main"]'
            result = convert(path, folder, **kwargs)
            checks = check_ooxml(path, folder) if path.suffix in (".docx", ".pptx", ".xlsx") else []
            internal = json.loads((folder / "checks.json").read_text())["checks"]
            checks += [{"kind": "engine_" + c["kind"], "passed": c["passed"]} for c in internal]
            checks.append({"kind": "original_bytes_and_artifact_integrity", "passed": validate_bundle(folder)["passed"]})
            if path.suffix == ".pdf":
                text = (folder / "00_원본복원.md").read_text()
                checks.append({"kind": "pdf_known_source_phrase", "passed": "Dummy PDF file" in text})
            if path.suffix == ".html":
                original = BeautifulSoup(path.read_bytes(), "html.parser").select_one('div[role="main"]')
                expected_ids = {node["id"] for node in original.find_all(id=True)}
                if original.get("id"):
                    expected_ids.add(original["id"])
                rendered = BeautifulSoup(MD.render((folder / "00_원본복원.md").read_text()), "html.parser")
                actual_ids = {node["id"] for node in rendered.find_all(id=True)}
                checks.append({"kind": "html_original_anchor_ids", "passed": expected_ids <= actual_ids, "original_ids": len(expected_ids), "missing": sorted(expected_ids - actual_ids)[:20]})
            rows.append({"filename": path.name, "source": source, "checks": checks, "passed": all(c["passed"] for c in checks), "warnings": result["warnings"], "output": str(folder)})
        except Exception as e:
            rows.append({"filename": path.name, "source": source, "passed": False, "error": f"{type(e).__name__}: {e}", "output": str(folder)})
    summary = {"documents": len(rows), "passed": sum(r["passed"] for r in rows), "failed": sum(not r["passed"] for r in rows), "scope": "실제 공개 표본의 텍스트·순서·표·링크·자산·셀/수식·목록/차트 검사. 픽셀/시각 의미 완전성 인증과 다름.", "results": rows}
    (output / "quality-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    for row in rows:
        print(json.dumps({"filename": row["filename"], "passed": row["passed"], "failures": [c for c in row.get("checks", []) if not c["passed"]], "error": row.get("error")}, ensure_ascii=False), flush=True)
    print(json.dumps({k: summary[k] for k in ("documents", "passed", "failed", "scope")}, ensure_ascii=False))
    return 0 if not summary["failed"] else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().out.resolve()))

