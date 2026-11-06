"""Optional local-model experiments on the same frozen Korean inputs.

No model downloads, training, reference-guided decoding, or production changes.
Install optional research dependencies and prepare model files explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import unicodedata

from benchmark_handwriting import compact, score


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--baseline', type=Path, required=True, help='0.9.1 program benchmark output with per-case bundles')
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--engine', choices=['ko-hand', 'korean-ctc'], required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    from PIL import Image, ImageOps
    data, baseline, model = args.data.resolve(), args.baseline.resolve(), args.model.resolve()
    out = args.out.resolve();out.mkdir(parents=True, exist_ok=False)
    samples = [s for s in json.loads((data/'manifest.json').read_text()) if s['language'] == 'ko']
    if args.engine == 'ko-hand':
        from kohandocr.reader import Reader
        from kohandocr import page
        if not (model/'model.safetensors').is_file():
            raise ValueError('Prepared safetensors model directory required')
        reader = Reader(model, device='cpu', threads=4)
        def read(crop):
            return reader.read([crop], letters=True)[0]
    else:
        import numpy as np
        import onnxruntime as ort
        options = ort.SessionOptions();options.intra_op_num_threads = 2;options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(model/'rec_korean_hw.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
        chars = ['', *(model/'ppocrv5_korean_dict.txt').read_text().splitlines(), ' ']
        def read(crop):
            width = max(8, min(4096, math.ceil(48*crop.width/crop.height)))
            pixels = np.asarray(crop.convert('RGB').resize((width,48), Image.Resampling.BILINEAR), dtype=np.float32)[:,:,::-1]/255
            tensor = ((pixels-.5)/.5).transpose(2,0,1)[None].copy()
            probabilities = session.run(None, {session.get_inputs()[0].name:tensor})[0][0]
            text, previous = [], 0
            for value in probabilities.argmax(-1):
                index = int(value)
                if index and index != previous:
                    text.append(chars[index])
                previous = index
            return unicodedata.normalize('NFC', ''.join(text))
    records = []
    for s in samples:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,80}', s['id']):
            raise ValueError('Unsafe case identifier')
        path = (data/s['path']).resolve()
        if not path.is_relative_to(data) or hashlib.sha256(path.read_bytes()).hexdigest() != s['sha256']:
            raise ValueError('Input bytes changed')
        start = time.monotonic()
        folder = baseline/s['id']
        structure = json.loads((folder/'structure.json').read_text())
        unit = next(u for u in structure['units'] if u['metadata'].get('ocr_data'))
        raw_path = (folder/unit['metadata']['ocr_data']).resolve()
        if not raw_path.is_relative_to(folder.resolve()):
            raise ValueError('External OCR asset')
        raw = json.loads(raw_path.read_text())
        image = Image.open(path).convert('RGB')
        crops, blocks = [], [b for b in raw['refinement']['blocks'] if b['kind']=='text']
        pad = 4 if args.engine == 'ko-hand' else 1
        for b in blocks:
            x,y,w,h = raw['observations'][b['source_observation']]['bbox']
            box = [max(0,math.floor(x*image.width)-pad),max(0,math.floor(y*image.height)-pad),
                   min(image.width,math.ceil((x+w)*image.width)+pad),min(image.height,math.ceil((y+h)*image.height)+pad)]
            # Ko-hand comparison used truncated left/top and rounded right/bottom.
            if args.engine == 'ko-hand':
                box = [max(0,int(x*image.width)-4),max(0,int(y*image.height)-4),
                       min(image.width,round((x+w)*image.width)+4),min(image.height,round((y+h)*image.height)+4)]
            crops.append(image.crop(box))
        if args.engine == 'ko-hand':
            texts = []
            for offset in range(0,len(crops),4):
                texts.extend(reader.read(crops[offset:offset+4], letters=True))
            prepared = page.prepare(path.read_bytes())
            own_crops = [Image.open(io.BytesIO(c['png'])).convert('RGB') for c in page.lines(prepared,max_lines=64)]
            own = []
            for offset in range(0,len(own_crops),4):
                own.extend(reader.read(own_crops[offset:offset+4], letters=True))
            variants = [('common-crops','\n'.join(texts)), ('own-page','\n'.join(own))]
            details = {'shared_crops':len(crops), 'own_crops':len(own_crops)}
        else:
            pure, fallback, pairs = [], [], []
            for crop,b in zip(crops,blocks):
                first, second = read(crop), read(ImageOps.expand(crop,border=4,fill='white'))
                accepted = any('\uac00'<=c<='\ud7a3' for c in b['text']) and bool(compact(first)) and compact(first)==compact(second)
                pure.append(first);fallback.append(first if accepted else b['text'])
                pairs.append({'source_index':b['source_observation'], 'passes':[first,second], 'selected':accepted})
            variants = [('recognizer-only','\n'.join(pure)), ('agreement-fallback','\n'.join(fallback))]
            details = {'shared_crops':len(crops), 'pairs':pairs}
        for method,text in variants:
            row = {k:s[k] for k in ('id','language','kind','split','sha256')}
            row.update(engine=args.engine, method=method, text=text, details=details,
                       seconds=time.monotonic()-start, **score(s['text'],text))
            records.append(row)
            print(s['id'],method,row['edits'],row['characters'],flush=True)
        (out/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    model_hashes = {f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in model.iterdir() if f.is_file() and f.suffix in ('.onnx','.txt','.json','.safetensors')}
    (out/'models.json').write_text(json.dumps(model_hashes,indent=2)+'\n')


if __name__ == '__main__':
    main()
