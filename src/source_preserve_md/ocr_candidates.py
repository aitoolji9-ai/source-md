"""Bounded local OCR alternatives; never rewrite text using a vocabulary/model.

The selected result is one whole OCR response, not a synthesized transcription.
All original responses, transforms and disagreements remain reviewable.
"""
from __future__ import annotations

import copy
import math
import unicodedata
from PIL import Image, ImageOps


def compact(value):
    return ''.join(unicodedata.normalize('NFC', value).split())


def edit_distance(a, b):
    previous = list(range(len(b)+1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(ca != cb)))
        previous = current
    return previous[-1]


def retry_latin_line(image, initial, call):
    observations = initial.get('observations', [])
    text = compact(''.join(o.get('text', '') for o in observations))
    letters = [c for c in text if c.isalpha()]
    if (image.height > 256 or image.width < image.height*4
            or len(letters) < 2 or any(not c.isascii() for c in letters)
            or len(text) > 512 or not any(o.get('confidence', 0) < .8 for o in observations)):
        return initial
    report = {'method': 'medoid_of_uncorrected_english_views', 'language': 'en-US',
              'dictionary_correction': False, 'candidates': [], 'issues': [],
              'status': 'keep_initial', 'accuracy_verified': False}
    original = copy.deepcopy(initial)
    output = dict(initial)
    output['initial_recognition'] = original
    output['latin_line_retry'] = report
    for height in (None, 128, 192):
        border = 0 if height is None else 32
        prepared = image
        if height is not None:
            scale = min(height/image.height, 4000/image.width)
            prepared = image.resize((max(1, round(image.width*scale)), max(1, round(image.height*scale))), Image.Resampling.LANCZOS)
        inner_width, inner_height = prepared.size
        if border:
            prepared = ImageOps.expand(prepared, border=border, fill='white')
        try:
            response = call(prepared, 'en-US', False)
            projected = []
            for o in response.get('observations', []):
                bbox = o.get('bbox', [])
                if len(bbox) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bbox):
                    raise ValueError('Invalid OCR candidate coordinates')
                x, y, w, h = bbox
                x0, y0 = (x*prepared.width-border)/inner_width, (y*prepared.height-border)/inner_height
                x1, y1 = ((x+w)*prepared.width-border)/inner_width, ((y+h)*prepared.height-border)/inner_height
                a, b, c, d = max(0, x0), max(0, y0), min(1, x1), min(1, y1)
                if c > a and d > b:
                    projected.append({**o, 'bbox': [a, b, c-a, d-b]})
            candidate_text = compact(''.join(o.get('text', '') for o in projected))
            if not candidate_text or len(candidate_text) > 512:
                raise ValueError('Empty or excessive OCR candidate text')
            report['candidates'].append({'height': height, 'border': border,
                'prepared_size': list(prepared.size), 'response': response,
                'source_observations': projected, 'normalized_text': candidate_text})
        except Exception as error:
            report['issues'].append(str(error)[:500])
            return output
    candidates = report['candidates']
    texts = [c['normalized_text'] for c in candidates]
    scores = [sum(edit_distance(a, b)/max(len(a), len(b), 1) for b in texts) for a in texts]
    selected = min(range(len(candidates)), key=lambda n: (scores[n], n))
    output['observations'] = candidates[selected]['source_observations']
    output['languages'] = ['en-US']
    output['recognition_language'] = 'en-US'
    report.update(status='selected', selected_index=selected, distances=scores,
                  candidates_agree=len(set(texts)) == 1)
    return output
