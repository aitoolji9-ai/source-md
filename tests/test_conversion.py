import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from source_preserve_md.adapters import checked_zip, detect
from source_preserve_md.engine import PRIMARY, convert, roundtrip_text, validate_bundle
from source_preserve_md.model import ConversionError, sha256


def conversion(tmp_path, name, data, **kwargs):
    # These fixtures verify the direct-extraction contract. OCR has separate tests.
    kwargs.setdefault("ocr", "off")
    source = tmp_path / name
    source.write_bytes(data)
    output = tmp_path / "result"
    result = convert(source, output, **kwargs)
    assert source.read_bytes() == data
    assert (output / "source" / name).read_bytes() == data
    assert validate_bundle(output)["passed"]
    assert result["ai_calls"] == 0
    return result, output, (output / PRIMARY).read_bytes()


def test_markdown_is_byte_copy_and_tampering_detected(tmp_path):
    data = b"\xef\xbb\xbf# Exact\r\n\r\n- [a](x)\r\n```py\r\nprint('hi')\r\n```\r\n"
    _, output, md = conversion(tmp_path, "exact.md", data)
    assert md == data
    (output / PRIMARY).write_bytes(md + b"changed")
    result = validate_bundle(output)
    assert not result["passed"]
    assert any(f.get("reason") == "artifact_hash" for f in result["failures"])


def test_code_backticks_and_korean_encoding(tmp_path):
    text = "# 제목\r\n  공백\t```\r\n````\r\n$() `literal` <script>\r\n"
    result, _, md = conversion(tmp_path, "원본.txt", text.encode("cp949"), encoding="cp949")
    assert "`````txt" in md.decode()
    assert result["machine_checks"]["passed"] == result["machine_checks"]["total"] == 1


def test_html_structure_merged_table_code_literal_and_lazy_image(tmp_path):
    from PIL import Image
    image = Image.new("RGB", (4, 4), "green")
    buf = io.BytesIO(); image.save(buf, "PNG")
    image_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    code = 'const a = `x`;\n  ```\n  __SOURCE_TOKEN_0001__ & < >\n'
    import html
    data = f'''<!doctype html><html><head><title>보존 시험</title></head><body>
    <h1>큰 제목</h1><h3>작은 제목</h3><p>문장 <strong>강조</strong>_원문 *별표* [괄호] &amp; &lt;기호&gt;.</p>
    <ol start="3"><li>첫 항목<ul><li>안쪽</li></ul></li><li>다음</li></ol>
    <blockquote><p>인용문</p></blockquote><pre><code class="language-js">{html.escape(code)}</code></pre>
    <table><tr><th rowspan="2">병합</th><td>A|B<br>C</td></tr><tr><td></td></tr></table>
    <img data-src="{image_url}" alt="도표"><details><summary>접힌 제목</summary><p>상세</p></details>
    <script>throw new Error('not executed')</script></body></html>'''.encode()
    result, output, md = conversion(tmp_path, "page.html", data, download_assets=False)
    body = md.decode()
    assert 'rowspan="2"' in body
    assert "### 작은 제목" in body
    assert "3. 첫 항목" in body
    assert "assets/" in body
    assert "not executed" not in body
    checks = json.loads((output / "checks.json").read_text())["checks"]
    assert checks and all(c["passed"] for c in checks), checks
    structure = json.loads((output / "structure.json").read_text())
    assert structure["units"][0]["code_blocks"] == [code]
    assert any(w["code"] == "inert_elements" for w in result["warnings"])


def test_local_image_folder_boundary_and_srcset(tmp_path):
    from PIL import Image
    image = tmp_path / "image.png"; Image.new("RGB", (2, 2)).save(image)
    content = b'<h1>Title</h1><img srcset="image.png 1x" alt="local"><img src="../outside.png" alt="outside">'
    result, output, md = conversion(tmp_path, "local.html", content, download_assets=False)
    assert b'![local](<assets/' in md
    assert any(w["code"] == "image_unavailable" for w in result["warnings"])
    assert len(list((output / "assets").glob("*-image.png"))) == 1


def test_csv_empty_cells_embedded_newline_and_symbols(tmp_path):
    data = '이름,값,비고\r\n"A|B",,"줄1\n줄2"\r\n"<script>",0,"**원문**"\r\n'.encode()
    _, output, md = conversion(tmp_path, "표.csv", data)
    structure = json.loads((output / "structure.json").read_text())
    cells = json.loads((output / structure["units"][0]["metadata"]["cell_data"]).read_text())["rows"]
    assert cells[1] == ["A|B", "", "줄1\n줄2"]
    assert "&lt;script&gt;" in md.decode()
    assert "<td></td>" in md.decode()


def test_docx_body_order_images_table_headings(tmp_path):
    from docx import Document
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (3, 3)).save(buf, "PNG"); buf.seek(0)
    doc = Document(); doc.add_heading("원래 제목", 1); doc.add_paragraph("앞 문단")
    table = doc.add_table(rows=2, cols=2); table.cell(0, 0).text = "병합 값"; table.cell(0, 0).merge(table.cell(1, 0)); table.cell(0, 1).text = "표 내용"
    doc.add_paragraph("뒤 문단"); doc.add_picture(buf)
    document = io.BytesIO(); doc.save(document)
    result, output, md = conversion(tmp_path, "test.docx", document.getvalue())
    text = md.decode()
    assert text.index("앞 문단") < text.index("표 내용") < text.index("뒤 문단")
    assert "# 원래 제목" in text
    assert 'rowspan="2"' in text
    assert b'![](<assets/' in md
    assert result["machine_checks"]["passed"] == result["machine_checks"]["total"]


def test_spreadsheet_raw_values_formulas_merge_hidden_comments(tmp_path):
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    wb = Workbook(); ws = wb.active; ws.title = "원본"
    ws["A1"] = "시작"; ws["B2"] = 0; ws["C2"] = "=B2+1"; ws["D3"] = "끝"
    ws["A1"].comment = Comment("주석 원문", "작성자"); ws.merge_cells("A4:B4"); ws["A4"] = "병합"
    ws.row_dimensions[3].hidden = True; wb.create_sheet("숨김").sheet_state = "hidden"
    stream = io.BytesIO(); wb.save(stream)
    result, output, md = conversion(tmp_path, "cells.xlsx", stream.getvalue())
    structure = json.loads((output / "structure.json").read_text())
    sheet = json.loads((output / structure["units"][0]["metadata"]["cell_data"]).read_text())
    mapping = {c["coordinate"]: c for c in sheet["cells"]}
    assert mapping["B2"]["value"] == 0 and mapping["B1"]["value"] is None
    assert mapping["C2"]["formula"] == "=B2+1" and mapping["C2"]["cached_value"] is None
    assert mapping["A1"]["comment"]["text"] == "주석 원문"
    assert sheet["merged_ranges"] == ["A4:B4"] and sheet["hidden_rows"] == [3]
    assert structure["units"][1]["metadata"]["state"] == "hidden"
    assert result["units"] == 2


def test_pptx_slide_order_notes_and_group_metadata(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches
    deck = Presentation()
    for text in ("첫 슬라이드", "둘째 슬라이드"):
        slide = deck.slides.add_slide(deck.slide_layouts[6]); shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)); shape.text = text
        slide.notes_slide.notes_text_frame.text = "메모: " + text
    stream = io.BytesIO(); deck.save(stream)
    result, output, md = conversion(tmp_path, "deck.pptx", stream.getvalue())
    text = md.decode()
    assert text.index("첫 슬라이드") < text.index("둘째 슬라이드")
    assert "메모: 첫 슬라이드" in text
    assert result["units"] == 2
    assert any(w["code"] == "slide_layout" for w in result["warnings"])


def test_pdf_pages_render_text_and_image_only_warning(tmp_path):
    import pymupdf
    doc = pymupdf.open(); page = doc.new_page(); page.insert_text((40, 40), "Exact 10.5 kg & code")
    doc.new_page(); data = doc.tobytes(); doc.close()
    result, output, md = conversion(tmp_path, "source.pdf", data)
    assert result["units"] == 2 and b"Exact 10.5 kg & code" in md
    assert len(list((output / "assets").glob("*-page-*.png"))) == 2
    assert any(w["code"] == "image_only_page" and w["source_locator"] == "page:2" for w in result["warnings"])


def test_animated_image_frames_and_no_ocr(tmp_path):
    from PIL import Image
    stream = io.BytesIO(); Image.new("RGB", (4, 4), "red").save(stream, "GIF", save_all=True, append_images=[Image.new("RGB", (4, 4), "blue")], duration=50)
    result, output, _ = conversion(tmp_path, "animation.gif", stream.getvalue())
    assert result["units"] == 2
    assert any(w["code"] == "image_text_unextracted" for w in result["warnings"])


def test_epub_uses_spine_order_not_filename_sort(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>')
        z.writestr("OPS/content.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="z" href="z.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="z"/><itemref idref="a"/></spine></package>')
        z.writestr("OPS/z.xhtml", "<html><body><h1>FIRST</h1></body></html>")
        z.writestr("OPS/a.xhtml", "<html><body><h1>SECOND</h1></body></html>")
    result, _, md = conversion(tmp_path, "book.epub", stream.getvalue())
    assert md.index(b"FIRST") < md.index(b"SECOND")
    assert result["units"] == 2


def test_hwpx_and_rtf_are_explicitly_partial(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("Contents/section0.xml", '<section xmlns:h="urn:h"><h:p><h:run><h:t>한글 본문</h:t></h:run></h:p></section>')
    result, _, md = conversion(tmp_path, "한글.hwpx", stream.getvalue())
    assert "한글 본문" in md.decode()
    assert any(w["code"] == "hwpx_partial" for w in result["warnings"])


def test_mhtml_embedded_image(tmp_path):
    from email.message import EmailMessage
    message = EmailMessage(); message.make_related()
    part = EmailMessage(); part.set_content('<html><body><p>MIME 본문</p><img src="cid:img" alt="내부"></body></html>', subtype="html", charset="utf-8"); message.attach(part)
    image = EmailMessage(); image.set_content(b"PNG-bytes", maintype="image", subtype="png"); image["Content-ID"] = "<img>"; message.attach(image)
    result, output, md = conversion(tmp_path, "saved.mhtml", message.as_bytes())
    assert "MIME 본문" in md.decode()
    assert "![내부](<assets/" in md.decode()
    assert not any(w["code"] == "image_unavailable" for w in result["warnings"])


def test_unknown_binary_preserved_without_fake_success(tmp_path):
    result, _, md = conversion(tmp_path, "anything.bin", b"\x00\x01\x02binary")
    assert result["format"] == "unsupported"
    assert result["status"] == "partial"
    assert any(w["code"] == "unsupported_format" for w in result["warnings"])


def test_zip_traversal_and_wrong_magic_are_rejected(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("../escape", "no")
    with pytest.raises(ConversionError):
        detect(stream.getvalue(), "bad.docx")
    with pytest.raises(ConversionError):
        detect(b"not-a-pdf", "bad.pdf")


def test_no_overwrite_and_no_network_for_local_text(tmp_path, monkeypatch):
    import httpx
    def forbidden(*args, **kwargs):
        raise AssertionError("local text must not use network")
    monkeypatch.setattr(httpx, "Client", forbidden)
    _, output, _ = conversion(tmp_path, "offline.txt", b"local source")
    with pytest.raises(FileExistsError):
        convert(tmp_path / "offline.txt", output)


def test_manifest_path_escape_is_detected(tmp_path):
    _, output, _ = conversion(tmp_path, "plain.txt", b"source")
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["artifacts"]["../../outside"] = "0" * 64
    (output / "manifest.json").write_text(json.dumps(manifest))
    assert not validate_bundle(output)["passed"]


def test_xlsx_chart_sheet_keeps_workbook_order_and_chart_categories(tmp_path):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    wb = Workbook(); sheet = wb.active; sheet.title = "Data"
    sheet.append(["City", "Count"]); sheet.append(["Seoul", 7]); sheet.append(["Busan", 9])
    chart = BarChart(); chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=3), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=3))
    chart.title = "By city"
    chart_sheet = wb.create_chartsheet("Chart"); chart_sheet.add_chart(chart)
    wb._sheets = [chart_sheet, sheet]
    stream = io.BytesIO(); wb.save(stream)
    result, output, md = conversion(tmp_path, "charts.xlsx", stream.getvalue())
    structure = json.loads((output / "structure.json").read_text())
    assert [unit["label"] for unit in structure["units"]] == ["Chart", "Data"]
    assert structure["units"][0]["metadata"]["sheet_type"] == "chartsheet"
    assert "By city" in md.decode()
    assert "Data!$A$2:$A$3" in md.decode() or "'Data'!$A$2:$A$3" in md.decode()


def test_pptx_chart_retains_category_names_series_values_and_title(tmp_path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    deck = Presentation(); slide = deck.slides.add_slide(deck.slide_layouts[6])
    data = CategoryChartData(); data.categories = ["Seoul", "Busan"]
    data.add_series("Cars", (7, 9))
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(5), Inches(3), data).chart
    chart.has_title = True; chart.chart_title.text_frame.text = "By city"
    stream = io.BytesIO(); deck.save(stream)
    _, output, md = conversion(tmp_path, "categories.pptx", stream.getvalue())
    for text in ("Seoul", "Busan", "Cars", "By city"):
        assert text in md.decode()
    structure = json.loads((output / "structure.json").read_text())
    record = json.loads((output / structure["units"][0]["metadata"]["structure"]).read_text())
    assert "Seoul" in json.dumps(record, ensure_ascii=False)


def test_html_original_anchors_and_nonstandard_list_numbers_are_readable(tmp_path):
    data = b'<h2 id="original-anchor">Different heading</h2><p><a href="#original-anchor">Jump</a></p><ol start="8" reversed><li>Eight</li><li value="3">Three</li></ol>'
    _, _, md = conversion(tmp_path, "anchors.html", data, download_assets=False)
    from bs4 import BeautifulSoup
    from source_preserve_md.engine import PARSER
    soup = BeautifulSoup(PARSER.render(md.decode()), "html.parser")
    assert soup.find(id="original-anchor") is not None
    assert soup.find("ol").has_attr("reversed")
    assert soup.find("li", value="3") is not None


def test_docx_image_only_header_uses_header_relationships(tmp_path):
    from docx import Document
    from PIL import Image
    from bs4 import BeautifulSoup
    from source_preserve_md.engine import PARSER
    images = []
    for color in ("red", "blue"):
        image = io.BytesIO(); Image.new("RGB", (5, 5), color).save(image, "PNG"); images.append(image.getvalue())
    doc = Document()
    doc.sections[0].header.paragraphs[0].add_run().add_picture(io.BytesIO(images[0]))
    doc.add_picture(io.BytesIO(images[1]))
    stream = io.BytesIO(); doc.save(stream)
    _, output, md = conversion(tmp_path, "image-header.docx", stream.getvalue())
    soup = BeautifulSoup(PARSER.render(md.decode()), "html.parser")
    actual = [sha256((output / image["src"]).read_bytes()) for image in soup.find_all("img")]
    assert len(actual) == 2
    assert set(actual) == set(map(sha256, images))


def test_pdf_ruled_table_preserves_blank_cells_and_ignores_background(tmp_path):
    import pymupdf
    from bs4 import BeautifulSoup
    from source_preserve_md.engine import PARSER
    doc = pymupdf.open(); page = doc.new_page(width=500, height=500)
    page.draw_rect(pymupdf.Rect(20, 20, 480, 480), color=None, fill=(.95, .95, .95))
    page.insert_text((50, 60), "Before table")
    for x in [50, 170, 290, 410]:
        page.draw_line((x, 120), (x, 280), color=(0, 0, 0))
    for y in [120, 160, 200, 240, 280]:
        page.draw_line((50, y), (410, y), color=(0, 0, 0))
    for x, label in zip([60, 180, 300], ["Name", "Length", "Sound"]):
        page.insert_text((x, 145), label)
    for y, label in zip([185, 225, 265], ["1", "2", "Median"]):
        page.insert_text((60, y), label)
    page.insert_text((50, 330), "After table")
    data = doc.tobytes(); doc.close()
    result, output, md = conversion(tmp_path, "ruled.pdf", data)
    soup = BeautifulSoup(PARSER.render(md.decode()), "html.parser")
    tables = soup.find_all("table")
    assert len(tables) == 1
    rows = tables[0].find_all("tr")
    assert len(rows) == 4 and all(len(row.find_all(["td", "th"], recursive=False)) == 3 for row in rows)
    assert rows[1].find_all("td")[1].get_text() == ""
    structure = json.loads((output / "structure.json").read_text())
    assert structure["units"][0]["metadata"]["table_count"] == 1
    assert "Before table" in md.decode() and "After table" in md.decode()
    # A human opening the MD source should see one pipe-delimited line per row,
    # without a run of HTML cell tags for this ordinary, unmerged table.
    assert len([line for line in md.decode().splitlines() if line.startswith("|")]) == 5
    assert "<table>" not in md.decode()

