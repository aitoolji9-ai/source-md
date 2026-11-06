"""Re-recognize image regions and reconstruct visible ruled tables.

Two local OCR passes must agree before replacing an existing line. This is an
agreement rule, not an accuracy guarantee. Both candidates and originals remain.
"""
from __future__ import annotations

import unicodedata
from PIL import Image, ImageChops, ImageFilter, ImageOps

from .html_extract import escape
from .raster_layout import detect_tables, borderless_tables, order_blocks, table_markdown


def compact(value):
    return ''.join(unicodedata.normalize('NFC', value).split())


def _text(response):
    return '\n'.join(o['text'] for o in response.get('observations', []) if o.get('text','').strip())


def _crop(image, box, *, inset=0):
    x0,y0,x1,y1 = box
    box = [max(0,round(x0)+inset),max(0,round(y0)+inset),
           min(image.width,round(x1)-inset),min(image.height,round(y1)-inset)]
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError('잘못된 OCR 영역')
    original = image.crop(tuple(box)).convert('RGB')
    scale = min(3, 3000/max(original.width,1), 1200/max(original.height,1))
    resized = original.resize((max(1,round(original.width*scale)),max(1,round(original.height*scale))),Image.Resampling.LANCZOS)
    return ImageOps.expand(resized,border=24,fill='white'), original, box


def _has_ink(image):
    gray = image.convert('L')
    histogram = gray.histogram()
    background = max(range(256),key=lambda n:histogram[n])
    local = gray.filter(ImageFilter.BoxBlur(max(4, min(16, min(gray.size)/6))))
    if background < 100:
        contrast = ImageChops.subtract(gray, local)
        mask = contrast.point(lambda p:255 if p > 25 else 0)
    else:
        contrast = ImageChops.subtract(local, gray)
        mask = contrast.point(lambda p:255 if p > 20 else 0)
    # The cell crop already excludes four pixels of the detected grid. A blurred
    # intersection can leave a one-pixel fringe at its edge; do not turn that
    # into invented "unrecognized" content. Source/crop/OCR pixels stay intact.
    if min(mask.size) > 2:
        mask = mask.crop((1, 1, mask.width-1, mask.height-1))
    return mask.getbbox() is not None


def _alternate(image):
    scale = min(2,3000/max(image.width,1),1200/max(image.height,1))
    crop = image.resize((max(1,round(image.width*scale)),max(1,round(image.height*scale))),Image.Resampling.BICUBIC)
    return ImageOps.expand(crop,border=24,fill='white')


def refine_layout(image: Image.Image, observations: list[dict], call, *, skip_table_boxes=()) -> dict:
    W,H = image.size
    report = {'tables':[], 'line_refinements':[], 'issues':[],
              'reading_order_method':'recursive_whitespace_xy_cut', 'reading_order_verified':False}
    try:
        tables = detect_tables(image)
        if not tables:tables=borderless_tables(image,observations)
    except Exception as error:
        tables = []
        report['issues'].append({'stage':'grid_detection','error':str(error)})
    def inside(box, outer):
        x0,y0,x1,y1 = box
        a,b,c,d = outer
        return a <= (x0+x1)/2 <= c and b <= (y0+y1)/2 <= d
    tables = [t for t in tables if not any(inside(t['bbox'],box) for box in skip_table_boxes)]
    for table in tables:
        table['header_semantics_verified'] = False
        for cell in table['cells']:
            r = {'text':'', 'status':'pending', 'passes':[]}
            cell.update(r)
            try:
                prepared, original, box = _crop(image,cell['bbox'],inset=4)
                cell['crop_bbox'] = box
                if not _has_ink(original):
                    cell['status'] = 'no_ink_detected'
                    continue
                first, second = call(prepared,False), call(prepared,True)
                cell['passes'] = [first,second]
                a,b = _text(first),_text(second)
                page_lines = []
                for observation in observations:
                    x,y,w,h = observation['bbox']
                    px,py,pw,ph = x*W,y*H,w*W,h*H
                    c0,r0,c1,r1 = cell['bbox']
                    if c0-2 <= px and r0-2 <= py and px+pw <= c1+2 and py+ph <= r1+2:
                        page_lines.append(observation)
                page_lines.sort(key=lambda o:(o['bbox'][1],o['bbox'][0]))
                page_text = '\n'.join(o['text'] for o in page_lines)
                cell['page_candidate'] = page_text
                if a and compact(a)==compact(b):
                    cell.update(text=a,status='ocr_passes_agree')
                elif a or b:
                    cell.update(text=a or b,status='ocr_disagreement')
                else:
                    cell.update(text='[미인식]',status='unrecognized_ink')
                selected = cell['text']
                if page_text and compact(selected)!=compact(page_text):
                    if compact(selected).casefold()==compact(page_text).casefold():
                        cell.update(text=page_text,status='case_disagreement_keep_page')
                    elif compact(page_text) in (compact(a),compact(b)):
                        cell.update(text=page_text,status='ocr_page_agree')
                    else:
                        third = call(_alternate(original),False)
                        cell['alternate_scale_pass'] = third
                        other = _text(third)
                        if other and compact(other)==compact(page_text):
                            cell.update(text=page_text,status='ocr_page_agree')
                        elif other and compact(other)==compact(a)==compact(b):
                            cell.update(text=a,status='ocr_multiscale_agree')
                        else:
                            cell.update(text=page_text,status='disagreement_keep_page')
                    if cell['status']=='disagreement_keep_page' and page_lines:
                        # A whole-cell crop may remain ambiguous. Retry each
                        # observed text line with its own surrounding pixels.
                        tight_raw, tight_corrected, details = [],[],[]
                        for observation in page_lines:
                            x,y,w,h = observation['bbox']
                            observed_box = [max(0,x*W-5),max(0,y*H-5),min(W,(x+w)*W+5),min(H,(y+h)*H+5)]
                            tight,_,_= _crop(image,observed_box)
                            p,q = call(tight,False),call(tight,True)
                            details.append({'bbox':observed_box,'passes':[p,q]})
                            tight_raw.append(_text(p));tight_corrected.append(_text(q))
                        cell['tight_line_passes'] = details
                        p,q = '\n'.join(tight_raw),'\n'.join(tight_corrected)
                        if p and compact(p)==compact(q) and compact(p) in (compact(a),compact(b)):
                            cell.update(text=p,status='ocr_independent_crop_agree')
            except Exception as error:
                cell.update(text=cell.get('text') or '[미검사]',status='failed',error=str(error))
                report['issues'].append({'stage':'cell_ocr','bbox':cell['bbox'],'error':str(error)})
        table['values'] = [[None for _ in range(table['column_count'])] for _ in range(table['row_count'])]
        for cell in table['cells']:
            table['values'][cell['row']][cell['column']] = cell['text']
        table['blank_cells'] = sum(c['status']=='no_ink_detected' for c in table['cells'])
        good = ('no_ink_detected','ocr_passes_agree','ocr_page_agree','ocr_multiscale_agree','ocr_independent_crop_agree')
        table['unresolved_cells'] = sum(c['status'] not in good for c in table['cells'])
        table['review_cells'] = [{'row':c['row']+1,'column':c['column']+1,'status':c['status']} for c in table['cells'] if c['status'] not in good]
        report['tables'].append(table)
    blocks = []
    for index,line in enumerate(observations):
        if line.get('duplicate_native') or not line.get('text','').strip():
            continue
        x,y,w,h = line['bbox']
        box = [x*W,y*H,(x+w)*W,(y+h)*H]
        if any(inside(box,t['bbox']) for t in tables):
            continue
        selected = line['text']
        # Region retries help short Korean glyphs and long page-wide lines alike.
        if any('\uac00' <= c <= '\ud7a3' for c in selected) or line.get('confidence',0)<.8:
            entry = {'index':index,'original_text':selected,'bbox':box,'status':'pending'}
            report['line_refinements'].append(entry)
            try:
                padded = [max(0,box[0]-5),max(0,box[1]-5),min(W,box[2]+5),min(H,box[3]+5)]
                prepared,original,crop_box = _crop(image,padded)
                a,b = call(prepared,False),call(prepared,True)
                entry.update(passes=[a,b],crop_bbox=crop_box)
                first,second = _text(a),_text(b)
                if first and compact(first)==compact(second):
                    if compact(first)!=compact(line['text']):
                        third = call(_alternate(original),False)
                        entry['alternate_scale_pass'] = third
                        if compact(_text(third))==compact(first) and len(compact(line['text'])) >= 5:
                            selected = first
                            entry['status'] = 'agreement_changed'
                        else:
                            entry['status'] = 'disagreement_keep_original'
                            if len(compact(line['text'])) < 5:
                                entry['reason'] = 'short_isolated_text_requires_page_context'
                    else:
                        selected = first
                        entry['status'] = 'agreement_unchanged'
                else:
                    entry['status'] = 'disagreement_keep_original'
                entry['selected_text'] = selected
            except Exception as error:
                entry.update(status='failed_keep_original',error=str(error),selected_text=selected)
                report['issues'].append({'stage':'line_ocr','bbox':box,'error':str(error)})
        blocks.append({'kind':'text','text':selected,'bbox':box,'source_observation':index})
    for index,table in enumerate(tables):
        blocks.append({'kind':'table','table_index':index,'bbox':table['bbox']})
    ordered = order_blocks(blocks)
    report['blocks'] = ordered
    report.update(markdown='',text='',
                  changed_lines=sum(r['status']=='agreement_changed' for r in report['line_refinements']),
                  disagreements=sum(r['status']=='disagreement_keep_original' for r in report['line_refinements'])+sum(t['unresolved_cells'] for t in tables))
    render_report(report)
    return report


def render_report(report):
    """Render the recorded blocks, also used by explicit human corrections."""
    markdown,texts=[],[]
    tables=report['tables']
    for block in report['blocks']:
        if block['kind']=='text':
            markdown.append(escape(block['text']).replace('\n','  \n'))
            texts.append(block['text'])
        else:
            table=tables[block['table_index']]
            rendered=f"**[이미지 표: {table['row_count']}행 × {table['column_count']}열 · OCR 원본 대조 필요]**\n\n"+table_markdown(table)
            if table.get('review_cells'):
                rendered+='\n\n> 재인식 불일치 — 원본 확인: '+', '.join(f"{c['row']}행 {c['column']}열" for c in table['review_cells'])
            markdown.append(rendered)
            texts.append('\n'.join('\t'.join(c or '' for c in row) for row in table['values']))
    report.update(markdown='\n\n'.join(markdown),text='\n'.join(texts))
