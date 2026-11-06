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


def test_line_retry_cannot_append_ink_from_neighbouring_source_line():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB', (300, 200), 'white')
    original = '원래 첫번째 문장'
    observations = [{'text': original, 'confidence': .5, 'bbox': [.1, .1, .7, .15]}]
    def recognize_region(*args):
        return {'observations': [
            {'text': original, 'bbox': [.1, .3, .6, .2]},
            {'text': '이웃행', 'bbox': [.1, .8, .2, .05]},
        ]}
    result = refine_layout(image, observations, recognize_region)
    assert result['text'] == original
    # Raw candidates stay available even though the neighbouring row is excluded.
    assert result['line_refinements'][0]['passes'][0]['observations'][1]['text'] == '이웃행'


def test_line_crop_agreement_cannot_extend_an_unchanged_original_sentence():
    from source_preserve_md.ocr_refine import refine_layout
    original = '첫번째 문장입니다'
    image = Image.new('RGB', (300, 200), 'white')
    observations = [{'text': original, 'confidence': .5, 'bbox': [.1,.1,.7,.15]}]
    def repeat(*args):
        return {'observations': [{'text': original+' 이웃행', 'bbox': [.1,.3,.7,.2]}]}
    result = refine_layout(image, observations, repeat)
    assert result['text'] == original
    assert result['line_refinements'][0]['reason'] == 'candidate_extends_unchanged_source_text'


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


def test_korean_page_context_can_confirm_a_disputed_region_without_rewriting_source():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB', (900, 600), 'white')
    initial = {'text': '오늘은출분히기다린다', 'confidence': .5, 'bbox': [.1,.2,.8,.12]}
    corrected = '오늘은충분히기다린다'
    responses = []
    def call(prepared, correction):
        if prepared.size == image.size:
            response = {'observations': [{**initial, 'text': corrected}]}
        else:
            # Both primary crop passes agree, but the alternate crop is wrong.
            text = corrected if len(responses) < 2 else initial['text']
            response = {'observations': [{'text': text}]}
        responses.append(response)
        return response
    result = refine_layout(image, [initial], call)
    assert result['text'] == corrected
    assert initial['text'] == '오늘은출분히기다린다'
    assert result['changed_lines'] == 1
    assert result['page_context']['response']['observations'][0]['text'] == corrected
    entry = result['line_refinements'][0]
    assert entry['status'] == 'page_context_agreement_changed'
    assert entry['passes'][0]['observations'][0]['text'] == corrected
    assert entry['alternate_scale_pass']['observations'][0]['text'] == initial['text']


def test_korean_page_dictionary_alone_cannot_override_disagreeing_uncorrected_views():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB', (900, 600), 'white')
    initial = {'text': '오늘은출분히기다린다', 'confidence': .5, 'bbox': [.1,.2,.8,.12]}
    corrected = '오늘은충분히기다린다'
    def call(prepared, correction):
        return {'observations': [{**initial, 'text': corrected if correction else initial['text']}]}
    result = refine_layout(image, [initial], call)
    assert result['text'] == initial['text']


def test_korean_context_failure_retains_initial_and_is_recorded():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB', (900, 600), 'white')
    initial = {'text': '오늘은출분히기다린다', 'confidence': .5, 'bbox': [.1,.2,.8,.12]}
    corrected = '오늘은충분히기다린다'
    calls = 0
    def call(prepared, correction):
        nonlocal calls
        if prepared.size == image.size:
            raise RuntimeError('OCR request limit reached')
        calls += 1
        return {'observations': [{'text': corrected if calls < 3 else initial['text']}]}
    result = refine_layout(image, [initial], call)
    assert result['text'] == initial['text']
    assert result['page_context']['status'] == 'failed'
    assert any(i['stage'] == 'page_context' for i in result['issues'])


def test_disputed_english_region_does_not_request_korean_page_context():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB', (900, 600), 'white')
    initial = {'text': 'Original English', 'confidence': .5, 'bbox': [.1,.2,.8,.12]}
    calls = []
    def call(prepared, correction):
        calls.append(prepared.size)
        assert prepared.size != image.size
        return {'observations': [{'text': 'Different English' if correction else initial['text']}]}
    result = refine_layout(image, [initial], call)
    assert result['text'] == initial['text'] and len(calls) == 2


@pytest.mark.parametrize('original,proposal,context_box,duplicate_context', [
    ('현재기온은13도입니다', '현재기온은18도입니다', [.1,.2,.8,.12], False),
    ('오늘은출분히기다린다', '추가오늘은출분히기다린다', [.1,.2,.8,.12], False),
    ('오늘은출분히기다린다', '오늘은충분히기다린다', [.1,.7,.8,.12], False),
    ('오늘은출분히기다린다', '오늘은충분히기다린다', [.1,.2,.8,.12], True),
])
def test_page_context_does_not_change_numbers_append_or_choose_ambiguous_lines(original, proposal, context_box, duplicate_context):
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB', (900, 600), 'white')
    initial = {'text': original, 'confidence': .5, 'bbox': [.1,.2,.8,.12]}
    crops = 0
    def call(prepared, correction):
        nonlocal crops
        if prepared.size == image.size:
            observations = [{'text': proposal, 'bbox': context_box}]
            if duplicate_context:observations += observations
            return {'observations': observations}
        crops += 1
        return {'observations': [{'text': proposal if crops < 3 else original}]}
    result = refine_layout(image, [initial], call)
    assert result['text'] == original


def test_korean_glyph_consensus_can_fix_one_syllable_despite_different_line_endings():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB', (900, 600), 'white')
    initial = {'text': '바투른  글씨를 기록한다!', 'confidence': .5, 'bbox': [.1,.2,.8,.12]}
    page = '바두른 글씨를 기록한다?'
    crop = '바두른 글씨를 기록한다'
    def call(prepared, correction):
        if prepared.size == image.size:return {'observations': [{**initial, 'text': page}]}
        return {'observations': [{'text': crop+'.' if correction else crop}]}
    result = refine_layout(image, [initial], call)
    assert result['text'] == '바두른  글씨를 기록한다!'
    entry = result['line_refinements'][0]
    assert entry['status'] == 'page_context_span_agreement_changed'
    assert entry['span_selections'][0]['before'] == '투'
    assert entry['span_selections'][0]['after'] == '두'
    assert initial['text'] == '바투른  글씨를 기록한다!'
    assert result['changed_lines'] == 1


def test_dictionary_only_korean_span_cannot_override_both_uncorrected_crops():
    from source_preserve_md.ocr_refine import refine_layout
    image = Image.new('RGB', (900, 600), 'white')
    initial = {'text': '바투른  글씨를 기록한다!', 'confidence': .5, 'bbox': [.1,.2,.8,.12]}
    def call(prepared, correction):
        text = '바두른 글씨를 기록한다?' if correction else '바투른 글씨를 기록한다'
        return {'observations': [{**initial, 'text': text}] if prepared.size == image.size else [{'text': text}]}
    assert refine_layout(image, [initial], call)['text'] == initial['text']


def test_two_glyph_substitution_preserves_spaces_between_original_glyphs():
    from source_preserve_md.ocr_refine import _apply_korean_spans
    text, selections = _apply_korean_spans('오늘은 출  분하게 씁니다 13!', {(3,5,'충분')})
    assert text == '오늘은 충  분하게 씁니다 13!'
    assert selections[0]['before'] == '출분' and selections[0]['after'] == '충분'
