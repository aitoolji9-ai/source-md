"""Conservative ruled-grid detection and whitespace reading order using Pillow.

No document-specific coordinates or vocabulary; no neural layout model. Raster
geometry is inferred and remains reviewable alongside the unmodified source.
"""
from __future__ import annotations

import re
import statistics
import unicodedata

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .html_extract import escape


def _segments(mask: Image.Image, minimum: int):
    width, height = mask.size
    data = mask.tobytes()
    pattern = re.compile(b"\xff{" + str(minimum).encode() + b",}")
    bands = []
    previous = []
    def root(n):
        while bands[n][4] != n:
            bands[n][4] = bands[bands[n][4]][4]
            n = bands[n][4]
        return n
    for y in range(height):
        current = []
        for match in pattern.finditer(data[y*width:(y+1)*width]):
            start, end = match.span()
            # Blur/illumination changes a stroke's width between pixel rows.
            # Join overlapping runs on adjacent rows, retaining the whole band
            # so its centre, rather than a fringe, identifies the grid axis.
            touching = {root(n) for a, b, n in previous if start < b and a < end}
            if touching:
                n = min(touching)
                for other in touching - {n}:
                    band = bands[other]
                    bands[n][0] = min(bands[n][0], band[0])
                    bands[n][1] = min(bands[n][1], band[1])
                    bands[n][2] = max(bands[n][2], band[2])
                    bands[n][3] = max(bands[n][3], band[3])
                    band[4] = n
                bands[n][0] = min(bands[n][0], start)
                bands[n][2] = max(bands[n][2], end)
                bands[n][3] = y
            else:
                n = len(bands)
                bands.append([start, y, end, y, n])
            current.append((start, end, n))
        previous = current
    return [[a, round((top+bottom)/2), b]
            for n, (a, top, b, bottom, parent) in enumerate(bands) if parent == n]


def _clusters(values, tolerance=4):
    groups = []
    for value in sorted(values):
        if groups and value-groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(statistics.mean(group)) for group in groups]


def detect_tables(image: Image.Image) -> list[dict]:
    gray = image.convert("L")
    width, height = gray.size
    # A shaded page can be darker than a fixed global ink threshold. Compare
    # each pixel with its local background instead, so smooth illumination is
    # removed while narrow grid strokes remain. This mask is for geometry only;
    # OCR and saved originals keep the source pixels and coordinates unchanged.
    radius = max(8, min(32, round(min(width, height) / 30)))
    background = gray.filter(ImageFilter.BoxBlur(radius))
    mask = ImageChops.subtract(background, gray).point(lambda p: 255 if p > 12 else 0)
    horizontal = _segments(mask, max(35, int(width*.07)))
    vertical = _segments(mask.transpose(Image.Transpose.TRANSPOSE), max(35, int(height*.035)))
    # Repair short breaks inside a row stroke. Gaps ending on column boundaries
    # may be intentional merged cells and are not filled automatically.
    repaired=[];joined=[]
    axes=[s[1] for s in vertical]
    for y in _clusters(s[1] for s in horizontal):
        fragments=sorted((s for s in horizontal if abs(s[1]-y)<=3),key=lambda s:s[0])
        current=None
        for segment in fragments:
            if current is None:current=list(segment);continue
            gap=segment[0]-current[2]
            aligned=any(abs(x-current[2])<=5 for x in axes) and any(abs(x-segment[0])<=5 for x in axes)
            if 0 < gap <= width*.3 and not aligned:
                repaired.append([current[2],y,segment[0],y])
                ImageDraw.Draw(mask).line((current[2],y,segment[0],y),fill=255,width=3)
                current=[current[0],y,max(current[2],segment[2])]
            elif gap<=0:
                current[2]=max(current[2],segment[2])
            else:
                joined.append(current);current=list(segment)
        if current is not None:joined.append(current)
    horizontal=joined
    # Bound pathological inputs (e.g. dense line art) before building intersections.
    if len(horizontal)+len(vertical) > 2000:
        raise ValueError("래스터 선 검출 상한 초과")
    parent = list(range(len(horizontal)+len(vertical)))
    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    def union(a, b):
        parent[find(b)] = find(a)
    for h, (x0, y, x1) in enumerate(horizontal):
        for v, (y0, x, y1) in enumerate(vertical):
            if x0-4 <= x <= x1+4 and y0-4 <= y <= y1+4:
                union(h, len(horizontal)+v)
    components = {}
    for n in range(len(parent)):
        components.setdefault(find(n), []).append(n)
    pixels = mask.load()
    def boundary(axis, position, begin, end):
        if end-begin < 6:
            return False
        values = range(begin+3, end-2)
        count = 0
        total = 0
        for t in values:
            total += 1
            if axis == "v":
                count += any(pixels[x, min(height-1, t)] for x in range(max(0,position-3), min(width,position+3)))
            else:
                count += any(pixels[min(width-1,t), y] for y in range(max(0,position-3), min(height,position+3)))
        return bool(total and count/total >= .8)
    tables = []
    for nodes in components.values():
        hs = [horizontal[n] for n in nodes if n < len(horizontal)]
        vs = [vertical[n-len(horizontal)] for n in nodes if n >= len(horizontal)]
        xs, ys = _clusters(s[1] for s in vs), _clusters(s[1] for s in hs)
        # Single boxes and instruction cards are not grids.
        if len(xs) < 3 or len(ys) < 3:
            continue
        rows, cols = len(ys)-1, len(xs)-1
        if rows*cols > 20000 or min(b-a for a,b in zip(xs,xs[1:])) < 8 or min(b-a for a,b in zip(ys,ys[1:])) < 8:
            continue
        if not (boundary("h",ys[0],xs[0],xs[-1]) and boundary("h",ys[-1],xs[0],xs[-1])
                and boundary("v",xs[0],ys[0],ys[-1]) and boundary("v",xs[-1],ys[0],ys[-1])):
            continue
        cells_parent = list(range(rows*cols))
        def root(n):
            while cells_parent[n] != n:
                cells_parent[n] = cells_parent[cells_parent[n]]
                n = cells_parent[n]
            return n
        for row in range(rows):
            for col in range(cols):
                n = row*cols+col
                if col+1 < cols and not boundary("v",xs[col+1],ys[row],ys[row+1]):
                    cells_parent[root(n+1)] = root(n)
                if row+1 < rows and not boundary("h",ys[row+1],xs[col],xs[col+1]):
                    cells_parent[root(n+cols)] = root(n)
        groups = {}
        for n in range(rows*cols):
            groups.setdefault(root(n), []).append(divmod(n,cols))
        cells = []
        valid = True
        for positions in groups.values():
            r0, r1 = min(r for r,c in positions), max(r for r,c in positions)+1
            c0, c1 = min(c for r,c in positions), max(c for r,c in positions)+1
            if len(positions) != (r1-r0)*(c1-c0):
                valid = False
                break
            cells.append({"row":r0, "column":c0, "rowspan":r1-r0, "colspan":c1-c0,
                          "bbox":[xs[c0],ys[r0],xs[c1],ys[r1]]})
        if valid:
            tables.append({"bbox":[xs[0],ys[0],xs[-1],ys[-1]], "row_count":rows, "column_count":cols,
                           "cells":sorted(cells,key=lambda c:(c["row"],c["column"])),
                           "method":"raster_connected_ruled_lines", "geometry_verified":False,
                           "line_detection":{"background":"local_box_mean", "radius":radius,
                                             "minimum_contrast":12, "axes":"connected_stroke_band_centres"},
                           "repaired_strokes":[s for s in repaired if xs[0]<=s[0]<=xs[-1] and ys[0]<=s[1]<=ys[-1]]})
    return sorted(tables,key=lambda t:(t["bbox"][1],t["bbox"][0]))


def deskew_for_grid(image):
    if detect_tables(image):return image,{'angle':0}
    small=image.convert('L');small.thumbnail((1000,1000))
    mask=small.point(lambda p:255 if p<210 else 0)
    def score(im):
        data=im.tobytes();w,h=im.size
        return sum(data[y*w:(y+1)*w].count(255)**2 for y in range(h))
    baseline=score(mask);best=(baseline,0)
    for step in range(-14,15):
        angle=step/2
        if angle:
            value=score(mask.rotate(angle,resample=Image.Resampling.NEAREST,expand=True,fillcolor=0))
            if value>best[0]:best=(value,angle)
    if best[1] and best[0]>baseline*1.15:
        aligned=image.rotate(best[1],resample=Image.Resampling.BICUBIC,expand=True,fillcolor='white')
        if detect_tables(aligned):
            return aligned,{'angle':best[1],'original_size':list(image.size),'aligned_size':list(aligned.size),'method':'bounded_projection_search_confirmed_by_grid','verified':False}
    return image,{'angle':0}


def borderless_tables(image,observations):
    """Infer a sparse, repeated 3+ column alignment; never invent empty rows."""
    W,H=image.size
    lines=[]
    for observation in observations:
        if observation.get('duplicate_native') or not observation.get('text','').strip():continue
        x,y,w,h=observation['bbox']
        lines.append({'bbox':[x*W,y*H,(x+w)*W,(y+h)*H],'text':observation['text']})
    if len(lines)<6:return []
    typical=statistics.median(b['bbox'][3]-b['bbox'][1] for b in lines)
    # Long prose rows cannot safely be treated as cells.
    short=[b for b in lines if b['bbox'][2]-b['bbox'][0] < W*.3]
    columns=_clusters((round(b['bbox'][0]) for b in short),max(8,typical*.7))
    columns=[x for x in columns if sum(abs(b['bbox'][0]-x)<=max(8,typical*.7) for b in short)>=2]
    if not 3<=len(columns)<=12:return []
    spacing=min(b-a for a,b in zip(columns,columns[1:]))
    if spacing<typical*2:return []
    short=[b for b in short if min(abs(b['bbox'][0]-x) for x in columns)<=typical and b['bbox'][2]-b['bbox'][0]<spacing*.65]
    centers=_clusters((round((b['bbox'][1]+b['bbox'][3])/2) for b in short),max(6,typical*.55))
    rows=[]
    for y in centers:
        cells={}
        for b in short:
            if abs((b['bbox'][1]+b['bbox'][3])/2-y)<=typical*.7:
                c=min(range(len(columns)),key=lambda c:abs(columns[c]-b['bbox'][0]))
                if c in cells:return []
                cells[c]=b
        if len(cells)>=2:rows.append((y,cells))
    if len(rows)<3 or len(rows[0][1])!=len(columns):return []
    xcuts=[max(0,columns[0]-typical*.5)]+[(a+b)/2 for a,b in zip(columns,columns[1:])]+[min(W,columns[-1]+spacing*.8)]
    ys=[r[0] for r in rows]
    ycuts=[max(0,ys[0]-typical)]+[(a+b)/2 for a,b in zip(ys,ys[1:])]+[min(H,ys[-1]+typical)]
    cells=[]
    for r,(_,present) in enumerate(rows):
        for c in range(len(columns)):
            cells.append({'row':r,'column':c,'rowspan':1,'colspan':1,'bbox':[xcuts[c],ycuts[r],xcuts[c+1],ycuts[r+1]],'alignment_text':present.get(c,{}).get('text','')})
    return [{'bbox':[xcuts[0],ycuts[0],xcuts[-1],ycuts[-1]],'row_count':len(rows),'column_count':len(columns),'cells':cells,'method':'repeated_text_alignment','geometry_verified':False}]


def order_blocks(blocks: list[dict]) -> list[dict]:
    """Recursive whitespace cuts; wide sections separate column groups."""
    if len(blocks) < 2:
        return blocks
    typical = statistics.median(max(1,b["bbox"][3]-b["bbox"][1]) for b in blocks if b.get("kind") != "table") if any(b.get("kind") != "table" for b in blocks) else 20
    # A very wide continuous gutter identifies columns before the smaller gaps
    # between their lines. Spanning headings naturally prevent this first cut.
    intervals=sorted((b['bbox'][0],b['bbox'][2]) for b in blocks)
    end=intervals[0][1];wide=[]
    extent=max(b['bbox'][2] for b in blocks)-min(b['bbox'][0] for b in blocks)
    for a,z in intervals[1:]:
        if a-end >= max(typical*3,extent*.1):wide.append((a-end,(a+end)/2))
        end=max(end,z)
    if wide:
        cut=max(wide)[1]
        left=[b for b in blocks if b['bbox'][2]<=cut];right=[b for b in blocks if b['bbox'][0]>=cut]
        if len(left)>=2 and len(right)>=2 and len(left)+len(right)==len(blocks):
            return order_blocks(left)+order_blocks(right)
    for axis, threshold in ((1,max(12,typical*.9)),(0,max(12,typical*.5))):
        intervals = sorted((b["bbox"][axis],b["bbox"][axis+2]) for b in blocks)
        end = intervals[0][1]
        gaps = []
        for a,z in intervals[1:]:
            if a-end >= threshold:
                gaps.append((a-end,(a+end)/2))
            end = max(end,z)
        if gaps:
            cut = max(gaps)[1]
            left = [b for b in blocks if b["bbox"][axis+2] <= cut]
            right = [b for b in blocks if b["bbox"][axis] >= cut]
            if left and right and len(left)+len(right)==len(blocks):
                return order_blocks(left)+order_blocks(right)
    return sorted(blocks,key=lambda b:(b["bbox"][1],b["bbox"][0]))


def table_markdown(table: dict) -> str:
    cells = table["cells"]
    if any(c["rowspan"] > 1 or c["colspan"] > 1 for c in cells):
        import html
        rows = []
        for row in range(table["row_count"]):
            body = [f'<td rowspan="{c["rowspan"]}" colspan="{c["colspan"]}">' + html.escape(c.get("text","")).replace("\n","<br>") + '</td>' for c in cells if c["row"]==row]
            rows.append('<tr>\n'+'\n'.join(body)+'\n</tr>')
        return '<table>\n'+'\n'.join(rows)+'\n</table>'
    matrix = [["" for _ in range(table["column_count"])] for _ in range(table["row_count"])]
    for c in cells:
        matrix[c["row"]][c["column"]] = escape(c.get("text","")).replace("\n","<br>")
    def width(value):
        return sum(2 if unicodedata.east_asian_width(c) in ("W","F") else 1 for c in unicodedata.normalize("NFC",value) if not unicodedata.combining(c))
    widths = [min(80,max(3,max(width(row[c]) for row in matrix))) for c in range(table["column_count"])]
    def pipe(row):
        return '| '+' | '.join(value+' '*max(0,widths[c]-width(value)) for c,value in enumerate(row))+' |'
    return '\n'.join([pipe(matrix[0]),'| '+' | '.join('-'*w for w in widths)+' |',*[pipe(row) for row in matrix[1:]]])
