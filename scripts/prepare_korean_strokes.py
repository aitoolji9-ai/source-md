"""Prepare hash-pinned real pen-stroke evaluation images (optional pyarrow).

This prepares research inputs only. No font generation, model training, OCR
requests, reference-guided cleanup, or upload of local files is performed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def render_strokes(strokes):
    from PIL import Image, ImageDraw
    points = [(x,y) for stroke in strokes for x,y in zip(stroke['x'],stroke['y'])]
    if not points or not all(math.isfinite(v) for p in points for v in p):
        raise ValueError('Invalid captured pen coordinates')
    xs,ys = zip(*points)
    left,top,right,bottom = min(xs),min(ys),max(xs),max(ys)
    scale = min(96/max(bottom-top,.01),12000/max(right-left,.01))
    width,height = math.ceil((right-left)*scale)+64,math.ceil((bottom-top)*scale)+64
    image = Image.new('RGB',(width,height),'white')
    draw = ImageDraw.Draw(image)
    for stroke in strokes:
        if len(stroke['x']) != len(stroke['y']):
            raise ValueError('Unequal pen coordinate arrays')
        points = [(round((x-left)*scale)+32,round((y-top)*scale)+32) for x,y in zip(stroke['x'],stroke['y'])]
        if len(points)>1:
            draw.line(points,fill='black',width=2,joint='curve')
        elif points:
            x,y = points[0];draw.ellipse((x-1,y-1,x+1,y+1),fill='black')
    return image


def prepare(out, source=None):
    catalog = json.loads((ROOT/'examples/handwriting-inputs/korean-strokes.json').read_text())
    if source:
        data = source.read_bytes()
    else:
        with urlopen(Request(catalog['url'],headers={'User-Agent':'SourceMD-research-inputs'}),timeout=60) as response:
            data = response.read(32*1024*1024+1)
    if len(data) != catalog['size'] or hashlib.sha256(data).hexdigest() != catalog['sha256']:
        raise ValueError('Pinned recordings changed; do not update evaluation labels automatically')
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError('Research preparation requires optional pyarrow; see KOREAN_HANDWRITING_FIX.md') from error
    rows = pq.read_table(pa.BufferReader(data)).to_pylist()
    out = out.resolve();out.mkdir(parents=True,exist_ok=False)
    manifest = []
    for case in catalog['cases']:
        offset = case['source_offset']
        if case['id'] != f'strokes-{offset:04d}':
            raise ValueError('Unsafe or inconsistent case identifier')
        row = rows[offset]
        if row['recording_id'] != case['source_recording_id'] or row['writer_id'] != case['source_writer_id']:
            raise ValueError('Recording provenance changed')
        if hashlib.sha256(row['text'].encode()).hexdigest() != case['reference_sha256']:
            raise ValueError('Reference transcript changed')
        path = out/(case['id']+'.png')
        render_strokes(row['strokes']).save(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != case['sha256']:
            raise ValueError('Rasterized pen strokes changed')
        manifest.append({'id':case['id'],'language':'ko','kind':'real_handwriting_stroke_line',
            'split':'fresh_test_'+case['cohort'],'path':path.name,'text':row['text'],'sha256':case['sha256'],
            'source_recording_id':row['recording_id'],'source_writer_id':row['writer_id']})
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return {'inputs':len(manifest),'writers':catalog['writers'],'attribution':catalog['attribution']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,help='Optional already downloaded pinned recordings.parquet')
    p.add_argument('--out',type=Path,required=True)
    args = p.parse_args()
    print(json.dumps(prepare(args.out,args.source),ensure_ascii=False))
