import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup

from source_preserve_md.engine import PARSER, PRIMARY, convert, validate_bundle


def test_blurred_stroke_with_varying_width_is_one_line():
    from source_preserve_md.raster_layout import _segments
    mask = Image.new('L', (200, 40), 0)
    draw = ImageDraw.Draw(mask)
    draw.line((50, 8, 150, 8), fill=255)
    draw.rectangle((10, 9, 190, 13), fill=255)
    draw.line((50, 14, 150, 14), fill=255)
    assert _segments(mask, 35) == [[10, 11, 191]]


def test_shadowed_blurred_merged_grid_preserves_exact_cell_spans():
    from source_preserve_md.raster_layout import detect_tables
    fixture = Path(__file__).parent / 'fixtures' / 'shadowed-merged-table.png'
    truth = json.loads(fixture.with_suffix('.json').read_text())
    with Image.open(fixture) as image:
        original_pixels = image.tobytes()
        tables = detect_tables(image)
        assert image.tobytes() == original_pixels
    assert len(tables) == 1
    table = tables[0]
    assert (table['row_count'], table['column_count']) == (truth['rows'], truth['columns'])
    fields = ('row', 'column', 'rowspan', 'colspan')
    assert {tuple(c[k] for k in fields) for c in table['cells']} == {
        tuple(c[k] for k in fields) for c in truth['cells']
    }


def test_shaded_blank_cell_ignores_grid_fringe_but_keeps_small_ink():
    from source_preserve_md.raster_layout import detect_tables
    from source_preserve_md.ocr_refine import _crop, _has_ink
    fixture = Path(__file__).parent / 'fixtures' / 'shadowed-merged-table.png'
    with Image.open(fixture) as image:
        table = detect_tables(image)[0]
        cell = next(c for c in table['cells'] if c['row'] == 2 and c['column'] == 3)
        _, original, _ = _crop(image, cell['bbox'], inset=4)
    before = original.tobytes()
    assert not _has_ink(original)
    assert original.tobytes() == before
    ImageDraw.Draw(original).point((2, 2), fill='black')
    assert _has_ink(original)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Actual Apple Vision OCR')
def test_actual_shadowed_merged_table_md_keeps_values_spans_and_blank(tmp_path):
    fixture = Path(__file__).parent / 'fixtures' / 'shadowed-merged-table.png'
    truth = json.loads(fixture.with_suffix('.json').read_text())
    output = tmp_path / 'shadowed-merged'
    result = convert(fixture, output)
    tables = BeautifulSoup(PARSER.render((output / PRIMARY).read_text()), 'html.parser').find_all('table')
    assert len(tables) == 1
    occupied, actual = set(), {}
    for row_index, row in enumerate(tables[0].find_all('tr')):
        column = 0
        for cell in row.find_all(['td', 'th'], recursive=False):
            while (row_index, column) in occupied:
                column += 1
            rs, cs = int(cell.get('rowspan', 1)), int(cell.get('colspan', 1))
            actual[(row_index, column, rs, cs)] = ''.join(cell.get_text().split())
            occupied.update((r, c) for r in range(row_index, row_index+rs) for c in range(column, column+cs))
            column += cs
    fields = ('row', 'column', 'rowspan', 'colspan')
    expected = {tuple(c[k] for k in fields): ''.join(c['text'].split()) for c in truth['cells']}
    assert actual == expected
    assert sum(not v for v in actual.values()) == 1
    assert result['ocr_used'] and result['status'] == 'partial'
    assert validate_bundle(output)['passed']


def grid_image():
    image = Image.new("RGB", (960, 600), "white")
    draw = ImageDraw.Draw(image)
    for x in (60, 340, 620, 900):
        draw.line((x, 100, x, 500), fill=(170, 170, 170), width=2)
    for y in (100, 200, 300, 400, 500):
        draw.line((60, y, 900, y), fill=(170, 170, 170), width=2)
    return image


def test_detect_grid_preserves_blank_cells_and_ignores_cards():
    from source_preserve_md.raster_layout import detect_tables
    image = grid_image()
    d = ImageDraw.Draw(image)
    d.rectangle((10, 10, 200, 60), outline="black", width=2)
    d.rectangle((220, 10, 410, 60), outline="black", width=2)
    tables = detect_tables(image)
    assert len(tables) == 1
    table = tables[0]
    assert (table["row_count"], table["column_count"]) == (4, 3)
    assert len(table["cells"]) == 12
    assert all(c["rowspan"] == c["colspan"] == 1 for c in table["cells"])


def test_detect_merged_cells_without_inventing_boundary():
    from source_preserve_md.raster_layout import detect_tables
    image = grid_image()
    ImageDraw.Draw(image).rectangle((338, 102, 342, 198), fill="white")
    tables = detect_tables(image)
    assert len(tables) == 1
    first = next(c for c in tables[0]["cells"] if c["row"] == c["column"] == 0)
    assert first["colspan"] == 2 and first["rowspan"] == 1
    assert len(tables[0]["cells"]) == 11


def test_layout_keeps_columns_together_between_spanning_sections():
    from source_preserve_md.raster_layout import order_blocks
    blocks = [
        {"text": "heading", "bbox": [0, 0, 900, 30]},
        {"text": "left 1", "bbox": [0, 80, 350, 100]},
        {"text": "right 1", "bbox": [500, 80, 900, 100]},
        {"text": "left 2", "bbox": [0, 110, 350, 130]},
        {"text": "right 2", "bbox": [500, 110, 900, 130]},
        {"text": "footer", "bbox": [0, 200, 900, 230]},
    ]
    assert [b["text"] for b in order_blocks(blocks)] == ["heading", "left 1", "left 2", "right 1", "right 2", "footer"]


def test_merged_empty_grid_is_preserved_in_rendered_markdown(tmp_path, monkeypatch):
    from source_preserve_md import ocr
    monkeypatch.setattr(ocr,'recognize',lambda *a,**kw:{'engine':'test-double','observations':[]})
    image = grid_image()
    ImageDraw.Draw(image).rectangle((338,102,342,198),fill='white')
    source = tmp_path/'merged.png';image.save(source)
    output = tmp_path/'merged'
    convert(source,output)
    table = BeautifulSoup(PARSER.render((output/PRIMARY).read_text()),'html.parser').find('table')
    assert table is not None
    assert table.find('td')['colspan']=='2'
    assert len(table.find_all('tr'))==4 and len(table.find_all('td'))==11
    assert validate_bundle(output)['passed']


def test_retry_agreement_cannot_override_conflicting_pixel_scale():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB',(800,200),'white')
    observations = [{'text':'원래 글자','confidence':.5,'bbox':[.1,.2,.5,.2]}]
    responses = iter(['다른 글자','다른 글자','원래 글자'])
    def recognize_region(*args):
        return {'observations':[{'text':next(responses)}]}
    result = refine_layout(image,observations,recognize_region)
    assert result['text']=='원래 글자'
    assert result['line_refinements'][0]['status']=='disagreement_keep_original'
    assert result['disagreements']==1


def test_short_isolated_word_keeps_page_context_when_retries_change_it():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB',(800,200),'white')
    observations = [{'text':'셉니다','confidence':.5,'bbox':[.1,.2,.5,.2]}]
    def isolated(*args):
        return {'observations':[{'text':'삽니다'}]}
    result = refine_layout(image,observations,isolated)
    assert result['text']=='셉니다'
    assert result['line_refinements'][0]['status']=='disagreement_keep_original'
    assert result['disagreements']==1


@pytest.mark.skipif(sys.platform != "darwin", reason="Actual macOS OCR")
def test_actual_image_table_renders_original_rows_columns_and_blanks(tmp_path):
    image = grid_image()
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
    expected = [["Item", "Count", "Note"], ["Alpha", "17", ""], ["Beta", "", "Done"], ["", "", ""]]
    draw = ImageDraw.Draw(image)
    for r, row in enumerate(expected):
        for c, text in enumerate(row):
            draw.text((80+c*280, 130+r*100), text, fill="black", font=font)
    source = tmp_path/"table.png"
    image.save(source)
    out = tmp_path/"converted"
    result = convert(source, out)
    soup = BeautifulSoup(PARSER.render((out/PRIMARY).read_text()), "html.parser")
    tables = soup.find_all("table")
    assert len(tables) == 1
    actual = [[c.get_text().strip() for c in row.find_all(["td", "th"])] for row in tables[0].find_all("tr")]
    assert actual == expected
    assert sum(c == "" for row in actual for c in row) == 5
    structure = json.loads((out/"structure.json").read_text())
    assert len(structure["units"][0]["metadata"]["ocr_tables"]) == 1
    assert result["status"] == "partial" and validate_bundle(out)["passed"]
