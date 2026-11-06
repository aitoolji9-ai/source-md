"""Fetch hash-pinned research inputs and compare local OCR without sending images.

Third-party images/transcriptions and full hypotheses belong in ignored outputs,
not in the public repository. Runtime engine imports are optional and explicit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def compact(text, form='NFC'):
    return ''.join(unicodedata.normalize(form, text).split())


def distance(a, b):
    previous = list(range(len(b)+1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(row[-1]+1, previous[j]+1, previous[j-1]+(ca != cb)))
        previous = row
    return previous[-1]


def score(reference, hypothesis):
    a, b = compact(reference), compact(hypothesis)
    ja, jb = compact(reference, 'NFD'), compact(hypothesis, 'NFD')
    edits, jamo_edits = distance(a, b), distance(ja, jb)
    return {'characters': len(a), 'edits': edits, 'cer': edits/max(1, len(a)),
            'jamo_characters': len(ja), 'jamo_edits': jamo_edits,
            'jamo_cer': jamo_edits/max(1, len(ja)), 'exact': a == b}


def fetch(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('raw.githubusercontent.com', 'datasets-server.huggingface.co'):
        raise ValueError('Unexpected research source URL')
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'SourceMD-benchmark'}), timeout=45) as response:
                data = response.read(25*1024*1024+1)
            if len(data) > 25*1024*1024:
                raise ValueError('Research input exceeds download limit')
            return data
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1)


def prepare(catalog, output):
    from PIL import Image
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = json.loads(catalog.read_text())['cases']
    result = []
    for case in cases:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,80}', case['id']):
            raise ValueError('Unsafe research case identifier')
        if case['source'] == 'korean-research':
            base = 'https://raw.githubusercontent.com/jkf87/korean-handwriting-ocr-benchmark/'+case['source_commit']+'/'
            image_bytes = fetch(base+quote(case['image_path']))
            truth_bytes = fetch(base+quote(case['truth_path']))
            if hashlib.sha256(truth_bytes).hexdigest() != case['source_truth_sha256']:
                raise ValueError('Reference source changed: '+case['id'])
            text = truth_bytes.decode('utf-8')
        else:
            url = 'https://datasets-server.huggingface.co/rows?dataset=Teklia%2FIAM-line&config=default&split=test&offset='+str(case['row'])+'&length=1'
            row = json.loads(fetch(url))['rows'][0]['row']
            image_bytes, text = fetch(row['image']['src']), row['text']
        if hashlib.sha256(image_bytes).hexdigest() != case['source_sha256'] or hashlib.sha256(text.encode()).hexdigest() != case['reference_sha256']:
            raise ValueError('Research source drifted; do not silently update baseline: '+case['id'])
        path = output/(case['id']+'.png')
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.width*image.height > 20_000_000:
                raise ValueError('Excessive research image dimensions')
            image.convert('RGB').save(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != case['sha256']:
            raise ValueError('PNG differs from frozen Pillow 13 corpus: '+case['id'])
        result.append({**case, 'path': path.name, 'text': text})
        print('prepared', case['id'], flush=True)
    (output/'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return {'prepared': len(result), 'output': str(output), 'external_ocr_calls': 0}


def visual_text(observations):
    rows = []
    for o in sorted(observations, key=lambda o: (o['bbox'][1]+o['bbox'][3]/2, o['bbox'][0])):
        y, h = o['bbox'][1]+o['bbox'][3]/2, o['bbox'][3]
        matches = [r for r in rows if abs(y-r['y']) <= max(.004, .45*min(h, r['h']))]
        if matches:
            min(matches, key=lambda r: abs(y-r['y']))['items'].append(o)
        else:
            rows.append({'y': y, 'h': h, 'items': [o]})
    return '\n'.join(' '.join(o['text'] for o in sorted(r['items'], key=lambda o: o['bbox'][0])) for r in sorted(rows, key=lambda r: r['y']))


def recognize_tesseract(path, sample, tessdata):
    from PIL import Image
    if tessdata is None:
        raise ValueError('--tessdata must point to prepared tessdata_best kor/eng models')
    psm = '7' if sample['kind'].endswith('_line') else '6'
    command = ['tesseract', str(path), 'stdout', '--tessdata-dir', str(tessdata.resolve()), '-l', 'kor+eng', '--oem', '1', '--psm', psm, '-c', 'tessedit_create_tsv=1']
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=90, env={**os.environ, 'OMP_THREAD_LIMIT': '4'})
    if not result.stdout.startswith('level\t'):
        raise ValueError('Expected literal Tesseract TSV')
    groups = {}
    for w in csv.DictReader(io.StringIO(result.stdout), delimiter='\t', quoting=csv.QUOTE_NONE):
        if w['level'] == '5' and w.get('text', '').strip():
            groups.setdefault(tuple(w[k] for k in ('page_num', 'block_num', 'par_num', 'line_num')), []).append(w)
    with Image.open(path) as image:
        width, height = image.size
    obs = []
    for words in groups.values():
        x, y = min(int(w['left']) for w in words), min(int(w['top']) for w in words)
        right = max(int(w['left'])+int(w['width']) for w in words)
        bottom = max(int(w['top'])+int(w['height']) for w in words)
        obs.append({'text': ' '.join(w['text'] for w in words), 'bbox': [x/width, y/height, (right-x)/width, (bottom-y)/height]})
    return visual_text(obs), {'psm': psm, 'languages': 'kor+eng', 'tsv': result.stdout, 'stderr': result.stderr}


def run(args):
    data = args.data.resolve()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    samples = json.loads((data/'manifest.json').read_text())
    easy = None
    if args.engine == 'easyocr':
        import torch
        import easyocr
        if args.models is None:
            raise ValueError('--models is required; downloads are not performed by inference')
        torch.set_num_threads(4)
        easy = easyocr.Reader(['ko', 'en'], gpu=False, model_storage_directory=str(args.models.resolve()), download_enabled=False, verbose=False)
    records = []
    for sample in samples:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,80}', sample['id']):
            raise ValueError('Unsafe research case identifier')
        path = (data/sample['path']).resolve()
        if not path.is_relative_to(data) or hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']:
            raise ValueError('Input bytes changed: '+sample['id'])
        start = time.monotonic()
        status, detail, text = 'completed', {}, ''
        try:
            if args.engine == 'program':
                folder = output/sample['id']
                # Resolving the venv executable symlink would silently launch
                # the base interpreter, losing the requested environment.
                result = subprocess.run([str(args.program_python.expanduser().absolute()), '-m', 'source_preserve_md', 'convert', str(path), '--out', str(folder)], cwd=output, capture_output=True, text=True, timeout=240)
                detail = {'cli_exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
                if not (folder/'structure.json').is_file():
                    raise RuntimeError('Program did not produce structure.json')
                structure = json.loads((folder/'structure.json').read_text())
                text = '\n'.join(u['metadata'].get('ocr_text', '') for u in structure['units'])
                status = 'completed' if result.returncode == 0 else 'incomplete'
            elif args.engine == 'vision':
                from source_preserve_md.ocr import recognize
                detail = recognize(path)
                text = visual_text(detail['observations'])
            elif args.engine == 'tesseract':
                text, detail = recognize_tesseract(path, sample, args.tessdata)
            else:
                from PIL import Image
                with Image.open(path) as image:
                    width, height = image.size
                obs = []
                for points, value, confidence in easy.readtext(str(path), detail=1, paragraph=False, workers=0):
                    xs, ys = [p[0] for p in points], [p[1] for p in points]
                    obs.append({'text': value, 'confidence': float(confidence), 'bbox': [min(xs)/width, min(ys)/height, (max(xs)-min(xs))/width, (max(ys)-min(ys))/height]})
                text, detail = visual_text(obs), {'observations': obs}
        except Exception as error:
            status, detail = 'failed', {'error': str(error)[:1000], 'prior_details': detail}
        record = {k: sample[k] for k in ('id', 'language', 'kind', 'split', 'sha256')}
        record.update(status=status, text=text, seconds=time.monotonic()-start, details=detail, **score(sample['text'], text))
        records.append(record)
        (output/'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n')
        print(sample['id'], status, record['edits'], record['characters'], flush=True)
    groups = {}
    for record in records:
        key = '/'.join(record[k] for k in ('language', 'kind', 'split'))
        group = groups.setdefault(key, {'n': 0, 'edits': 0, 'characters': 0, 'failed': 0, 'incomplete': 0})
        group['n'] += 1
        for k in ('edits', 'characters'):
            group[k] += record[k]
        group['failed'] += record['status'] == 'failed'
        group['incomplete'] += record['status'] == 'incomplete'
    for group in groups.values():
        group['cer'] = group['edits']/max(1, group['characters'])
    summary = {'engine': args.engine, 'inputs': len(records), 'groups': groups, 'external_ocr_calls': 0,
               'scope': 'Small fixed corpus; repeated inputs are not independent writers. Full-page scores include annotation coverage/order effects. Failure counts remain separate.'}
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--catalog', type=Path, default=ROOT/'examples/handwriting-inputs/sources.json')
    prep.add_argument('--out', type=Path, required=True)
    benchmark = commands.add_parser('run')
    benchmark.add_argument('--data', type=Path, required=True)
    benchmark.add_argument('--out', type=Path, required=True)
    benchmark.add_argument('--engine', choices=['program', 'vision', 'tesseract', 'easyocr'], default='program')
    benchmark.add_argument('--program-python', type=Path, default=Path(sys.executable))
    benchmark.add_argument('--models', type=Path)
    benchmark.add_argument('--tessdata', type=Path)
    args = parser.parse_args()
    result = prepare(args.catalog, args.out) if args.command == 'prepare' else run(args)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
