"""Local OCR supplement. Originals and direct extraction remain separately traceable."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import math
from pathlib import Path

from .model import Assets, ConversionError, Extraction, md_url, sha256

_BUILD_LOCK = threading.Lock()
MAX_TARGETS = 100
MAX_SECONDS = 180
MAX_EDGE = 4096
MAX_REQUESTS = 256


def _binary() -> Path:
    if sys.platform != "darwin" or not shutil.which("swiftc"):
        raise RuntimeError("Apple Vision OCR에는 macOS와 Swift 컴파일러가 필요합니다.")
    source = Path(__file__).with_name("vision_ocr.swift")
    cache = Path.home() / "Library/Caches/source-preserve-md"
    cache.mkdir(parents=True, exist_ok=True)
    binary = cache / ("vision-ocr-" + sha256(source.read_bytes())[:20])
    with _BUILD_LOCK:
        if not binary.is_file():
            fd, temp = tempfile.mkstemp(prefix="vision-build-", dir=cache)
            os.close(fd)
            try:
                completed = subprocess.run(["swiftc", "-O", str(source), "-o", temp], capture_output=True, timeout=90)
                if completed.returncode:
                    raise RuntimeError("OCR 컴파일 실패: " + completed.stderr.decode(errors="replace")[:500])
                os.replace(temp, binary)
            finally:
                Path(temp).unlink(missing_ok=True)
    return binary


def recognize(path: Path, *, timeout: float = 30, correction: bool = False, language: str = '') -> dict:
    if language not in ('', 'ko-KR', 'en-US'):
        raise ValueError('Unsupported OCR language')
    binary = _binary()
    command = [str(binary), str(path)] + (["--language-correction"] if correction else [])
    if language:
        command.append('--language='+language)
    completed = subprocess.run(command, capture_output=True, timeout=timeout)
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode(errors="replace")[:500] or "OCR 실행 실패")
    data = json.loads(completed.stdout)
    if not isinstance(data.get("observations"), list):
        raise RuntimeError("잘못된 OCR 응답")
    return data


def _normalized(text: str) -> str:
    # Signs, punctuation and case carry source meaning; only whitespace may vary.
    return "".join(c for c in unicodedata.normalize("NFC", text) if not c.isspace())


def is_native_duplicate(line: dict, native: list[dict]) -> bool:
    """Only suppress matching text at overlapping coordinates, never distant repeats."""
    x0, y0, x1, y1 = line["bbox"]
    area = max(0, x1 - x0) * max(0, y1 - y0)
    overlapping = []
    for item in native:
        a, b, c, d = item["bbox"]
        intersection = max(0, min(x1, c) - max(x0, a)) * max(0, min(y1, d) - max(y0, b))
        smaller = min(area, max(0, c-a) * max(0, d-b))
        if smaller > 0 and intersection / smaller >= .5:
            overlapping.append(item["text"])
    value = _normalized(line["text"])
    return bool(value and overlapping and value == _normalized("".join(overlapping)))


def augment_ocr(result: Extraction, assets: Assets, mode: str, source_data: bytes) -> dict:
    if mode not in ("auto", "off"):
        raise ConversionError("OCR 옵션은 auto 또는 off여야 합니다.")
    summary = {"mode": mode, "engine": "apple-vision", "used": False, "attempted": False,
               "targets": [], "accuracy_verified": False, "external_api_calls": 0, "requests":0,
               "limits": {"targets": MAX_TARGETS, "requests":MAX_REQUESTS, "seconds": MAX_SECONDS, "max_image_edge": MAX_EDGE}}
    result.metadata["ocr"] = summary
    started = time.monotonic()
    completed_count = 0

    def run_ocr(path, *, correction=False, language=''):
        remaining = MAX_SECONDS-(time.monotonic()-started)
        if summary["requests"] >= MAX_REQUESTS or remaining <= 0:
            raise RuntimeError("OCR 재인식 요청 수 또는 시간 상한 도달")
        summary["requests"] += 1
        return recognize(path, timeout=min(30,max(1,remaining)),correction=correction,language=language)

    def process(png: bytes | None, locator: str, unit, *, native=None, rect=None, source_image=""):
        nonlocal completed_count
        target = {"source_locator": locator, "source_image": source_image, "status": "pending"}
        summary["targets"].append(target)
        if mode == "off":
            target["status"] = "disabled"
            result.warn("ocr_disabled", "이미지/무텍스트 영역의 글자는 OCR을 꺼서 미추출입니다.", locator)
            return
        if completed_count >= MAX_TARGETS or time.monotonic() - started >= MAX_SECONDS:
            target["status"] = "skipped_limit"
            result.warn("ocr_limit", "OCR 대상 수 또는 처리 시간 상한에 도달했습니다. 남은 영역은 미검사입니다.", locator)
            return
        completed_count += 1
        try:
            if png is None:
                raise ValueError("OCR 입력 이미지가 없습니다.")
            from PIL import Image
            from .raster_layout import deskew_for_grid
            deskew={'angle':0}
            if not native:
                with Image.open(io.BytesIO(png)) as initial_image:
                    aligned,deskew=deskew_for_grid(initial_image.convert('RGB'))
                if deskew['angle']:
                    stream=io.BytesIO();aligned.save(stream,'PNG');png=stream.getvalue()
                    original_source_image=source_image
                    source_image=assets.add(png,locator.replace(':','-')+'-deskew.png',locator)
                    target.update(original_source_image=original_source_image,source_image=source_image,deskew=deskew)
            with tempfile.TemporaryDirectory(prefix="source-preserve-ocr-") as temp:
                path = Path(temp) / "input.png"
                path.write_bytes(png)
                summary["attempted"] = True
                raw = run_ocr(path)
                from .ocr_candidates import retry_latin_line
                with Image.open(path) as original:
                    line_image = original.convert('RGB')
                def candidate_call(prepared, language, correction):
                    prepared.save(path, 'PNG')
                    return run_ocr(path, language=language, correction=correction)
                raw = retry_latin_line(line_image, raw, candidate_call)
            summary["used"] = True
            raw["source_locator"] = locator
            raw["source_image"] = source_image
            raw["coordinate_system"] = "normalized top-left x,y,width,height"
            raw['deskew']=deskew
            raw["preprocessing"] = "PDF page render or EXIF-oriented raster; scaled to max edge 4096; raster alpha composited on white"
            accepted = []
            duplicates = 0
            for line in raw["observations"]:
                if not line.get("text", "").strip():
                    continue
                x, y, w, h = line["bbox"]
                if rect:
                    x0, y0, x1, y1 = rect
                    if deskew['angle']:
                        ow,oh=deskew['original_size'];nw,nh=deskew['aligned_size']
                        angle=math.radians(deskew['angle']);cs,sn=math.cos(angle),math.sin(angle)
                        corners=[]
                        for px,py in [(x*nw,y*nh),((x+w)*nw,y*nh),(x*nw,(y+h)*nh),((x+w)*nw,(y+h)*nh)]:
                            dx,dy=px-nw/2,py-nh/2
                            corners.append((cs*dx-sn*dy+ow/2,sn*dx+cs*dy+oh/2))
                        a,b=min(p[0] for p in corners)/ow,min(p[1] for p in corners)/oh
                        c,d=max(p[0] for p in corners)/ow,max(p[1] for p in corners)/oh
                        x,y,w,h=a,b,c-a,d-b
                    source_box = [x0+x*(x1-x0), y0+y*(y1-y0), x0+(x+w)*(x1-x0), y0+(y+h)*(y1-y0)]
                    line["source_bbox"] = source_box
                    if is_native_duplicate({"text": line["text"], "bbox": source_box}, native or []):
                        line["duplicate_native"] = True
                        duplicates += 1
                        continue
                accepted.append(line)
            from PIL import Image
            from .ocr_refine import refine_layout
            with Image.open(io.BytesIO(png)) as original_image:
                image = original_image.convert("RGB")
            skip_boxes = []
            if rect and unit:
                x0,y0,x1,y1 = rect
                for table in unit.metadata.get("tables",[]):
                    a,b,c,d = table["bbox"]
                    skip_boxes.append([(a-x0)/(x1-x0)*image.width,(b-y0)/(y1-y0)*image.height,
                                       (c-x0)/(x1-x0)*image.width,(d-y0)/(y1-y0)*image.height])
            with tempfile.TemporaryDirectory(prefix="source-preserve-refine-") as temp:
                retry_path = Path(temp)/"region.png"
                def retry(crop, correction):
                    crop.save(retry_path,"PNG")
                    return run_ocr(retry_path,correction=correction,language=raw.get('recognition_language',''))
                refined = refine_layout(image,raw["observations"],retry,skip_table_boxes=skip_boxes)
            raw["refinement"] = refined
            raw["initial_accepted_text"] = "\n".join(line["text"] for line in accepted)
            raw["accepted_text"] = refined["text"]
            raw["duplicate_native_count"] = duplicates
            raw_asset = assets.json(locator.replace(":", "-") + "-ocr.json", raw, locator)
            target.update(status="recognized" if accepted else "no_additional_text_detected",
                          observations=len(raw["observations"]), added_lines=len(accepted),
                          native_duplicates=duplicates, data=raw_asset, engine=raw["engine"],
                          low_confidence_lines=sum(line.get("confidence", 0) < .8 for line in accepted),
                          reconstructed_tables=len(refined["tables"]), changed_lines=refined["changed_lines"],
                          refinement_disagreements=refined["disagreements"], refinement_issues=len(refined["issues"]),
                          languages=raw.get("languages", []), revision=raw.get("revision"))
            if raw["accepted_text"]:
                body = "\n\n### [이미지 문자 — 로컬 OCR, 원본 대조 필요]\n\n"
                body += refined["markdown"]
                body += f"\n\n[OCR 글자·좌표·신뢰도](<{md_url(raw_asset)}>)"
                if unit is None:
                    unit = result.unit("이미지 문자: " + locator, locator, body, raw["accepted_text"], comparison="ocr_transcription")
                else:
                    unit.markdown += body
                    unit.metadata["direct_text"] = unit.text
                    unit.text = (unit.text + "\n" + raw["accepted_text"]).strip()
                unit.metadata["ocr_data"] = raw_asset
                unit.metadata["ocr_text"] = raw["accepted_text"]
                unit.metadata["ocr_tables"] = refined["tables"]
                unit.metadata["ocr_reading_order"] = refined["reading_order_method"]
                result.warn("ocr_review_required", "OCR 문자·좌표·신뢰도를 보존했습니다. 오인식 가능성과 문단/표 구조·읽는 순서는 원본 대조가 필요합니다.", locator)
            else:
                result.warn("ocr_no_additional_text", "OCR에서 추가 글자가 검출되지 않았습니다. 이미지에 글자가 없다는 인증은 아닙니다.", locator)
            if refined["disagreements"]:
                result.warn("ocr_disagreement", "재인식 결과가 엇갈리는 줄/셀이 있습니다. 첫 인식과 후보를 JSON에 보존했으며 원본 검토가 필요합니다.", locator)
            latin = raw.get('latin_line_retry', {})
            if latin.get('status') == 'selected' and not latin['candidates_agree']:
                result.warn('ocr_disagreement', '영문 줄 재인식 후보가 다릅니다. 후보·선택 근거·처음 인식을 JSON에 보존했으며 원본 대조가 필요합니다.', locator)
            if latin.get('issues'):
                code = 'ocr_limit' if summary['requests'] >= MAX_REQUESTS or time.monotonic()-started >= MAX_SECONDS else 'ocr_refinement_failed'
                result.warn(code, '영문 줄 재인식을 완료하지 못해 처음 인식을 보존했습니다.', locator)
            if refined["issues"]:
                if summary["requests"] >= MAX_REQUESTS or time.monotonic()-started >= MAX_SECONDS:
                    result.warn("ocr_limit", "영역/셀 재인식 상한에 도달해 일부 구조가 미검사입니다.", locator)
                else:
                    result.warn("ocr_refinement_failed", "일부 영역/셀 재인식 또는 선 검출이 실패했습니다. 최초 인식과 대상별 실패 기록을 보존했습니다.", locator)
        except Exception as error:
            target.update(status="failed", error=f"{type(error).__name__}: {error}")
            result.warn("ocr_failed", "로컬 OCR 실패로 이 영역의 이미지 문자는 미검사입니다: " + str(error)[:300], locator)

    if result.format == "pdf":
        import pymupdf
        with pymupdf.open(stream=source_data, filetype="pdf") as doc:
            for index, page in enumerate(doc):
                unit = result.units[index]
                # Inspect each page, including mixed pages with an existing text layer.
                image_info = page.get_image_info()
                image_count = len(image_info)
                unit.metadata["raster_image_count"] = image_count
                needs_ocr = image_count > 0 or not unit.text.strip()
                unit.metadata["ocr_candidate"] = needs_ocr
                if not needs_ocr:
                    continue
                native = []
                for block in page.get_text("dict").get("blocks", []):
                    for line in block.get("lines", []):
                        native.append({"bbox": list(line["bbox"]), "text": "".join(s["text"] for s in line["spans"])})
                png, local = None, unit.metadata.get("page_image", "")
                if mode != "off" and completed_count < MAX_TARGETS and time.monotonic()-started < MAX_SECONDS:
                    scale = min(300/72, MAX_EDGE/max(page.rect.width, page.rect.height))
                    # Avoid first resampling a dominant scan upwards and then
                    # cropping those interpolated pixels for OCR. Native text
                    # is already extracted independently at original precision.
                    dominant = []
                    for info in image_info:
                        box = pymupdf.Rect(info["bbox"])
                        transform = info.get("transform",(1,0,0,1,0,0))
                        if box.width > 0 and box.height > 0 and box.get_area() >= page.rect.get_area()*.2 and abs(transform[1]) < .01 and abs(transform[2]) < .01:
                            dominant.append((box.get_area(),min(info["width"]/box.width,info["height"]/box.height)))
                    if dominant:
                        scale = min(scale,max(dominant)[1])
                    unit.metadata["ocr_render_scale"] = scale
                    unit.metadata["ocr_render_basis"] = "bounded by dominant raster native pixel density" if dominant else "300dpi capped by max edge"
                    png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
                    local = assets.add(png, f"page-{index+1:04d}-ocr-input.png", unit.source_locator)
                process(png, unit.source_locator, unit, native=native, rect=list(page.rect), source_image=local)
    elif result.original_markdown is None and result.format not in ("unsupported", "legacy_binary"):
        from PIL import Image, ImageOps
        seen = set()
        units = list(result.units)
        for record in list(assets.records):
            path = assets.root / record["path"]
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif", ".avif", ".ico"):
                continue
            if record["sha256"] in seen:
                continue
            seen.add(record["sha256"])
            locator = record.get("source") or record["path"]
            if str(locator).lstrip("/").lower().startswith("docprops/thumbnail"):
                # Office package preview is not a separate visible document image.
                continue
            unit = next((u for u in units if u.metadata.get("image") == record["path"]), None)
            if mode == "off":
                process(None, locator, unit, source_image=record["path"])
                continue
            try:
                with Image.open(path) as image:
                    if image.width * image.height > 80000000:
                        raise ValueError("이미지 픽셀 수 상한 초과")
                    oriented = ImageOps.exif_transpose(image).convert("RGBA")
                    normalized = Image.new("RGB", oriented.size, "white")
                    normalized.paste(oriented, mask=oriented.getchannel("A"))
                    normalized.thumbnail((MAX_EDGE, MAX_EDGE))
                    stream = io.BytesIO()
                    normalized.save(stream, "PNG")
                process(stream.getvalue(), locator, unit, source_image=record["path"])
            except Exception as error:
                summary["targets"].append({"source_locator": locator, "status": "failed", "error": str(error)})
                result.warn("ocr_failed", "이미지 OCR 준비 실패: " + str(error)[:200], locator)
    if summary["used"]:
        # Replace stale 'OCR not used' notices emitted by direct-only adapters.
        result.warnings = [w for w in result.warnings if w["code"] not in ("image_only_page", "image_text_unextracted")]
        result.warn("ocr_structure_partial", "래스터 선으로 검출한 표와 공백 기반 읽기 순서를 보완했습니다. 선 없는 표·끊긴/기울어진 선·다단 배치·도형 관계·손글씨의 완전성은 미검증입니다.")
    return summary
