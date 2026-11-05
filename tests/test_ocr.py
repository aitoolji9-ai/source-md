import io
import json
import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

from source_preserve_md.engine import PRIMARY, convert, validate_bundle


def labelled_image(text="IMAGE ONLY 84729"):
    image = Image.new("RGB", (1400, 250), "white")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 60)
    except OSError:
        font = ImageFont.load_default(size=60)
    ImageDraw.Draw(image).text((40, 70), text, fill="black", font=font)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def fake_recognize(path, **kwargs):
    return {"engine": "test-double", "languages": ["en-US"], "revision": 3,
            "observations": [{"text": "IMAGE ONLY 84729", "confidence": .98, "bbox": [.05, .25, .8, .3]}]}


def test_mixed_pdf_extracts_image_text_and_keeps_native_text(tmp_path, monkeypatch):
    import pymupdf
    from source_preserve_md import ocr
    monkeypatch.setattr(ocr, "recognize", fake_recognize)
    doc = pymupdf.open()
    page = doc.new_page(width=700, height=450)
    page.insert_text((40, 40), "NATIVE TEXT 13579")
    page.insert_image(pymupdf.Rect(40, 120, 660, 231), stream=labelled_image())
    source = tmp_path / "mixed.pdf"
    source.write_bytes(doc.tobytes())
    doc.close()
    out = tmp_path / "mixed"
    result = convert(source, out, ocr="auto")
    md = (out / PRIMARY).read_text()
    assert "NATIVE TEXT 13579" in md and "IMAGE ONLY 84729" in md
    assert result["ocr_used"] is True
    assert result["status"] == "partial"
    assert validate_bundle(out)["passed"]
    structure = json.loads((out / "structure.json").read_text())
    target = structure["metadata"]["ocr"]["targets"][0]
    assert target["source_locator"] == "page:1"
    assert target["status"] == "recognized" and target["observations"] == 1


def test_ocr_off_and_engine_failure_are_explicit(tmp_path, monkeypatch):
    from source_preserve_md import ocr
    source = tmp_path / "image.png"
    source.write_bytes(labelled_image())
    def fail(*args, **kwargs):
        raise RuntimeError("engine unavailable")
    monkeypatch.setattr(ocr, "recognize", fail)
    off = convert(source, tmp_path / "off", ocr="off")
    assert off["ocr_used"] is False
    auto = convert(source, tmp_path / "failed", ocr="auto")
    assert auto["ocr_used"] is False
    assert any(w["code"] == "ocr_failed" for w in auto["warnings"])
    assert auto["status"] == "partial"
    assert validate_bundle(tmp_path / "failed")["passed"]


def test_native_overlap_filter_preserves_distinct_image_words():
    from source_preserve_md.ocr import is_native_duplicate
    native = [{"text": "NATIVE TEXT", "bbox": [0, 0, 200, 30]}]
    assert is_native_duplicate({"text": "NATIVE TEXT", "bbox": [0, 0, 200, 30]}, native)
    assert not is_native_duplicate({"text": "NATIVE TEXT", "bbox": [0, 100, 200, 130]}, native)
    assert not is_native_duplicate({"text": "NEW IMAGE WORDS", "bbox": [0, 0, 200, 30]}, native)
    assert not is_native_duplicate({"text":"-5", "bbox":[0,0,100,30]},[{"text":"5","bbox":[0,0,100,30]}])


def test_embedded_document_image_is_transcribed_and_limit_remains_unchecked(tmp_path, monkeypatch):
    from docx import Document
    from source_preserve_md import ocr
    monkeypatch.setattr(ocr, "recognize", fake_recognize)
    doc = Document()
    doc.add_paragraph("Native document paragraph")
    doc.add_picture(io.BytesIO(labelled_image()))
    source = tmp_path / "embedded.docx"
    doc.save(source)
    out = tmp_path / "embedded"
    result = convert(source, out)
    assert result["ocr_used"]
    md = (out / PRIMARY).read_text()
    assert "Native document paragraph" in md and "IMAGE ONLY 84729" in md
    assert validate_bundle(out)["passed"]
    monkeypatch.setattr(ocr, "MAX_TARGETS", 0)
    limited = convert(source, tmp_path / "limited")
    assert not limited["ocr_used"]
    assert limited["ocr"]["targets"][0]["status"] == "skipped_limit"
    assert any(w["code"] == "ocr_limit" for w in limited["warnings"])


def test_cli_reports_ocr_failure_as_incomplete(tmp_path, monkeypatch, capsys):
    from source_preserve_md import ocr
    from source_preserve_md.cli import main
    source = tmp_path / "image.png"
    source.write_bytes(labelled_image())
    def fail(*args, **kwargs):
        raise RuntimeError("OCR unavailable")
    monkeypatch.setattr(ocr, "recognize", fail)
    assert main(["convert", str(source), "--out", str(tmp_path/"failed-cli")]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["integrity"]["passed"] and data["status"] == "partial"
    assert data["ocr"]["targets"][0]["status"] == "failed"
    assert main(["convert", str(source), "--ocr", "off", "--out", str(tmp_path/"off-cli")]) == 0


def test_transparent_image_is_recognized_on_white_background(tmp_path, monkeypatch):
    from source_preserve_md import ocr
    image = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((10, 10), "TEXT", fill=(0, 0, 0, 255))
    source = tmp_path / "transparent.png"
    image.save(source)
    def check_input(path, **kwargs):
        with Image.open(path) as prepared:
            assert prepared.mode == "RGB"
            assert prepared.getpixel((299, 99)) == (255, 255, 255)
        return fake_recognize(path)
    monkeypatch.setattr(ocr, "recognize", check_input)
    result = convert(source, tmp_path / "transparent")
    assert result["ocr_used"]
    assert result["ocr"]["targets"][0]["status"] == "recognized"


def test_refinement_budget_preserves_initial_text_and_reports_unchecked(tmp_path, monkeypatch):
    from source_preserve_md import ocr
    def korean_result(*args, **kwargs):
        data = fake_recognize(None)
        data['observations'][0]['text'] = '검증 문구'
        return data
    monkeypatch.setattr(ocr,'recognize',korean_result)
    monkeypatch.setattr(ocr,'MAX_REQUESTS',1)
    source = tmp_path/'limited.png'
    source.write_bytes(labelled_image())
    output = tmp_path/'limited'
    result = convert(source,output)
    assert '검증 문구' in (output/PRIMARY).read_text()
    assert result['ocr']['requests']==1
    assert any(w['code']=='ocr_limit' for w in result['warnings'])
    assert result['status']=='partial' and validate_bundle(output)['passed']


@pytest.mark.skipif(sys.platform != "darwin", reason="Actual Apple Vision integration requires macOS")
def test_real_local_ocr_png_and_scanned_mixed_pdf(tmp_path):
    import pymupdf
    image = labelled_image()
    png = tmp_path / "source.png"
    png.write_bytes(image)
    result = convert(png, tmp_path / "png", ocr="auto")
    assert result["ocr_used"], result
    assert "IMAGE ONLY 84729" in (tmp_path / "png" / PRIMARY).read_text()
    doc = pymupdf.open()
    for mixed in (False, True):
        page = doc.new_page(width=700, height=450)
        if mixed:
            page.insert_text((40, 40), "NATIVE TEXT 13579")
        page.insert_image(pymupdf.Rect(40, 120, 660, 231), stream=image)
    pdf = tmp_path / "scan-and-mixed.pdf"
    pdf.write_bytes(doc.tobytes())
    doc.close()
    result = convert(pdf, tmp_path / "pdf", ocr="auto")
    structure = json.loads((tmp_path / "pdf" / "structure.json").read_text())
    assert result["ocr_used"]
    assert all("IMAGE ONLY 84729" in u["markdown"] for u in structure["units"])
    mixed = structure["units"][1]
    assert mixed["markdown"].count("NATIVE TEXT 13579") == 1
    assert validate_bundle(tmp_path / "pdf")["passed"]
