import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from PIL import Image, ImageDraw, ImageFont


def benchmark_module():
    path = Path(__file__).resolve().parents[1]/'scripts/benchmark_handwriting.py'
    spec = importlib.util.spec_from_file_location('handwriting_benchmark', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_preserves_korean_codepoints_numbers_and_letter_case():
    score = benchmark_module().score
    assert score('한글', '한 글')['exact']
    result = score('한글', '한굴')
    assert result['edits'] == 1 and result['cer'] == .5
    assert result['jamo_edits'] == 1 and result['jamo_characters'] == 6
    assert score('ABC-01', 'abc-01')['edits'] == 3
    assert score('1,250.00', '1,250.0')['edits'] == 1


@pytest.mark.skipif(sys.platform != 'darwin', reason='Real program requires macOS')
def test_benchmark_uses_requested_virtual_environment_from_another_cwd(tmp_path):
    module = benchmark_module()
    data = tmp_path/'data';data.mkdir()
    image = Image.new('RGB', (1300, 200), 'white')
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 55)
    ImageDraw.Draw(image).text((35, 65), 'BENCHMARK 84729', fill='black', font=font)
    path = data/'sample.png';image.save(path)
    (data/'manifest.json').write_text(json.dumps([{'id':'sample', 'language':'en', 'kind':'printed_control',
        'split':'test', 'path':'sample.png', 'text':'BENCHMARK 84729', 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]))
    result = module.run(SimpleNamespace(data=data, out=tmp_path/'result', engine='program',
        program_python=Path(sys.executable), models=None, tessdata=None))
    assert result['groups']['en/printed_control/test']['failed'] == 0
    assert json.loads((tmp_path/'result/results.json').read_text())[0]['exact']
