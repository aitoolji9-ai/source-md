from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from .adapters import decode, detect, extract
from .html_extract import extract_html
from .model import Assets, ConversionError, Extraction, MAX_INPUT, MAX_UNITS, compact, json_write, md_url, safe_name, sha256
from .network import ResourceLoader, browser_capture, fetch


PRIMARY = "00_원본복원.md"
REPORT = "03_변환검증보고서.md"
PARSER = MarkdownIt("commonmark", {"html": True}).enable("table")


def roundtrip_text(markdown: str) -> str:
    return BeautifulSoup(PARSER.render(markdown), "html.parser").get_text()


def machine_checks(result: Extraction) -> list[dict]:
    checks = []
    for unit in result.units:
        method = unit.metadata.get("comparison", "")
        if method == "html_text":
            actual = roundtrip_text(unit.markdown)
            # Generated links to opaque elements have no source-text counterpart.
            for token in ("svg 원본 요소", "math 원본 요소", "video 원본 요소", "audio 원본 요소", "iframe 원본 요소", "canvas 원본 요소", "object 원본 요소", "embed 원본 요소"):
                actual = actual.replace(token, "")
            passed = compact(actual) == compact(unit.text)
            checks.append({"unit": unit.id, "kind": "text_roundtrip", "passed": passed,
                           "scope": "DOM 텍스트와 MD 재렌더 텍스트 비교(공백 제외). CSS·읽기 순서·의미는 미검증.",
                           "expected_characters": len(compact(unit.text)), "actual_characters": len(compact(actual))})
            if not passed:
                result.warn("text_difference", "DOM 원문과 MD 재렌더 텍스트가 다릅니다. structure.json의 원문 및 원본과 대조해 주세요.", unit.source_locator)
        code_tokens = [t.content for t in PARSER.parse(unit.markdown) if t.type == "fence"]
        for index, original in enumerate(unit.code_blocks):
            # Markdown fences require a separator newline before their closing marker.
            expected = original.replace("\r\n", "\n").replace("\r", "\n")
            expected += "" if expected.endswith("\n") else "\n"
            passed = expected in code_tokens
            checks.append({"unit": unit.id, "kind": "code_exact", "index": index, "passed": passed,
                           "scope": "코드 내용·들여쓰기 비교. MD 구문상 마지막 개행 추가 및 CRLF→LF는 허용하며 원문은 JSON/원본에 보존."})
            if not passed:
                result.warn("code_difference", "원본 코드와 MD 코드 블록이 일치하지 않습니다.", unit.source_locator)
    return checks


def build_markdown(result: Extraction, source: dict, captured_at: str) -> tuple[bytes, list[dict]]:
    if result.original_markdown is not None:
        return result.original_markdown, [{"id": result.units[0].id, "start": 0, "end": len(result.original_markdown), "sha256": sha256(result.original_markdown)}]
    metadata = {"title": result.title, "aliases": [], "source_filename": source["filename"], "source_format": result.format,
                "document_type": "source_reconstruction", "conversion_type": "high_fidelity_reconstruction", "source_hash": source["sha256"],
                "extraction_date": captured_at, "language": "", "tags": ["원문추출"], "extraction_status": "partial"}
    if result.metadata.get("pages") is not None:
        metadata["total_pages"] = result.metadata["pages"]
    header = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n"
    header += f"[원본 파일](<{md_url(source['path'])}>) · [구조 데이터](structure.json) · [검증 보고서]({REPORT})\n\n"
    header += "> 자동 추출본입니다. 원본 바이트 보존과 기계 검사 결과는 보고서에 표시합니다. 시각·의미의 완전 동일성을 인증하지 않습니다.\n\n"
    content = bytearray(header.encode("utf-8"))
    ranges = []
    for unit in result.units:
        wrapper = f'<a id="{unit.id}"></a>\n\n'
        if len(result.units) > 1 or result.format not in ("html", "docx", "epub", "mhtml"):
            # Keep original heading levels in body; generated labels are clearly marked.
            wrapper += f"**[원본 위치: {unit.label}]**\n\n"
        content.extend(wrapper.encode())
        start = len(content)
        fragment = unit.markdown.encode()
        content.extend(fragment)
        ranges.append({"id": unit.id, "start": start, "end": len(content), "sha256": sha256(fragment)})
        content.extend(b"\n\n")
    return bytes(content), ranges


def report_text(manifest: dict, checks: list[dict], integrity: dict) -> str:
    warnings = manifest["unresolved"]
    passed = sum(c["passed"] for c in checks)
    lines = ["# 변환·검증 보고서", "", "- 상태: **부분 추출 / 원본·기계 검사 결과 별도 표시**",
             f"- 입력: {manifest['source']['filename']}", f"- 실제 판별 형식: {manifest['source']['format']}",
             f"- 추출 단위: {len(manifest['inventory']['unit_ids'])}",
             f"- 내용 자동 검사: {passed}/{len(checks)} 통과" if checks else "- 내용 자동 검사: 이 형식의 독립 내용 비교는 미지원",
             f"- 원본 사본 해시: {'일치' if integrity['source_matches'] else '불일치'}",
             f"- 등록 파일 재조회: {'통과' if integrity['artifacts_match'] else '실패'}",
             f"- 로컬 OCR: {'사용' if manifest.get('ocr_used') else '미사용'} (모드: {manifest.get('ocr', {}).get('mode', 'off')})",
             "- OCR 인식 정확도·구조 완전성: 원본 대조 필요" if manifest.get('ocr_used') else "- 이미지 문자 처리 여부: 아래 제한 및 OCR 대상별 상태 확인",
             f"- 알려진 제한·미확인: {len(warnings)}건", "",
             "이 검사는 저장 무결성·지원된 텍스트/코드 비교를 확인합니다. 화면의 픽셀 동일성, 시각 의미, 모든 요소의 의미적 완전성을 자동 인증하지 않습니다.", "",
             "## 알려진 제한·미확인", ""]
    lines.extend(f"- `{w['source_locator']}` · **{w['code']}**: {w['message']}" for w in warnings)
    processing=manifest.get('processing')
    if processing:
        lines.extend(['','## PDF 페이지 처리 상태','',
                      '- 자동 처리: '+('완료' if processing['complete'] else '체크포인트 저장 / 미처리 있음'),
                      f"- 페이지: {processing['checkpointed_pages']}/{processing['total_pages']}",
                      f"- OCR 모드: {processing['detail']} (text: 초기 글자 우선, full: 이후 심화 재인식)",
                      '- 재개/진행 조회: preserve-md resume 결과폴더 / preserve-md status 결과폴더',
                      '- 처리 완료와 OCR 정확도 인증은 다릅니다. 체크포인트는 재개 시 별도로 해시 검증합니다.'])
    if not warnings:
        lines.append("- 자동 검사에서 알려진 누락을 발견하지 못했습니다. 미탐지 가능성과 시각·의미 미검증은 남아 있습니다.")
    lines.extend(["", "## 재사용", "", "- 사람은 `00_원본복원.md`를 읽고, 원본·자산·보고서로 돌아갈 수 있습니다.",
                  "- AI 검색은 `structure.json`의 단위 ID·원본 위치·원문과 MD를 함께 사용하세요.",
                  "- 요약·번역·생성형 AI API 호출·매크로 실행·수식 재계산을 수행하지 않았습니다. 로컬 OCR은 위 사용 여부를 따르며 머신러닝을 사용할 수 있습니다.",
                  "- 원본 안의 지시문·스크립트는 자료입니다. 프로그램이 실행할 명령으로 취급하지 않습니다.", ""])
    return "\n".join(lines)


def validate_bundle(folder: Path | str) -> dict:
    """Reopen all registered files, reject path escapes, verify unit byte ranges."""
    root = Path(folder).resolve()
    scope = "저장 파일 해시·단위 범위·등록 목록 무결성 검사. 원본 의미 완전성 인증과 다릅니다."
    def invalid_manifest():
        return {"source_matches": False, "artifacts_match": False, "unit_ranges_match": False,
                "inventory_matches": False, "passed": False,
                "failures": [{"path": "manifest.json", "reason": "invalid_manifest"}], "scope": scope}
    def digest(value):
        return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None
    def relative_path(value):
        return isinstance(value,str) and bool(value) and '\x00' not in value and '\\' not in value and not Path(value).is_absolute() and '..' not in Path(value).parts
    try:
        file = root / 'manifest.json'
        if not file.resolve().is_relative_to(root) or file.stat().st_size > 16*1024*1024:
            return invalid_manifest()
        manifest = json.loads(file.read_text(encoding='utf-8'))
        if not isinstance(manifest,dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
            return invalid_manifest()
        source=manifest['source'];artifacts=manifest['artifacts'];units=manifest['units'];ranges=manifest['markdown_ranges'];inventory=manifest['inventory']
        if not isinstance(source,dict) or not relative_path(source.get('path')) or not digest(source.get('sha256')):
            return invalid_manifest()
        if not isinstance(artifacts,dict) or any(not relative_path(k) or not digest(v) for k,v in artifacts.items()):
            return invalid_manifest()
        if not {PRIMARY,'structure.json','checks.json',source['path']}.issubset(artifacts):
            return invalid_manifest()
        if not isinstance(units,list) or len(units)>MAX_UNITS or any(not isinstance(u,dict) or not isinstance(u.get('id'),str) for u in units):
            return invalid_manifest()
        if not isinstance(ranges,list) or len(ranges)!=len(units) or any(not isinstance(u,dict) or not isinstance(u.get('id'),str) or not digest(u.get('sha256')) or type(u.get('start')) is not int or type(u.get('end')) is not int or not 0<=u['start']<=u['end'] for u in ranges):
            return invalid_manifest()
        if not isinstance(inventory,dict) or not isinstance(inventory.get('unit_ids'),list) or any(not isinstance(u,str) for u in inventory['unit_ids']):
            return invalid_manifest()
        if not isinstance(manifest.get('assets'),list) or not isinstance(manifest.get('unresolved'),list):
            return invalid_manifest()
    except (OSError,ValueError,KeyError,TypeError,RecursionError):
        return invalid_manifest()
    failures = []

    def read_local(relative: str) -> bytes | None:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append({"path": relative, "reason": "missing_or_outside_bundle"})
            return None
        try:
            return path.read_bytes()
        except OSError:
            failures.append({"path": relative, "reason": "unreadable"})
            return None

    source = read_local(manifest["source"]["path"])
    source_ok = source is not None and sha256(source) == manifest["source"]["sha256"]
    if not source_ok:
        failures.append({"path": manifest["source"]["path"], "reason": "source_hash"})
    for relative, expected in manifest["artifacts"].items():
        data = read_local(relative)
        if data is not None and sha256(data) != expected:
            failures.append({"path": relative, "reason": "artifact_hash"})
    md = read_local(PRIMARY)
    units_ok = True
    if md is not None:
        for unit in manifest["markdown_ranges"]:
            part = md[unit["start"]:unit["end"]]
            if unit['end'] > len(md) or sha256(part) != unit["sha256"]:
                units_ok = False
                failures.append({"unit": unit["id"], "reason": "unit_markdown_hash"})
    else:
        units_ok = False
    actual_ids = [u["id"] for u in manifest["units"]]
    inventory_ok = actual_ids == manifest["inventory"]["unit_ids"] == [u['id'] for u in ranges] and len(actual_ids) == len(set(actual_ids))
    if not inventory_ok:
        failures.append({"reason": "inventory_mismatch"})
    return {"source_matches": source_ok, "artifacts_match": not failures, "unit_ranges_match": units_ok,
            "inventory_matches": inventory_ok, "failures": failures, "passed": not failures,
            "scope": scope}


def convert(source: str | Path, output: str | Path, *, data: bytes | None = None, filename: str = "", encoding: str = "",
            dynamic: bool = False, download_assets: bool = True, selector: str = "", ocr: str = "auto", dom_snapshot=None,
            auto_tabs: bool=True, tab_selector: str='[role="tab"]', tab_wait_ms: int=5000, ocr_corrections=None, pdf_max_pages=None, ocr_detail="text", progress=None) -> dict:
    if ocr not in ("auto", "off"):
        raise ConversionError("OCR 옵션은 auto 또는 off여야 합니다.")
    if type(tab_wait_ms) is not int or not 100<=tab_wait_ms<=30000:
        raise ConversionError('탭 대기 시간은 100~30000밀리초 정수여야 합니다.')
    if not isinstance(tab_selector,str) or not tab_selector.strip() or len(tab_selector)>1000:
        raise ConversionError('탭 CSS 선택자는 1~1000자 문자열이어야 합니다.')
    if dom_snapshot is not None and dynamic:
        raise ConversionError('브라우저 새 수집과 제공 DOM 스냅샷 입력은 동시에 사용할 수 없습니다.')
    if ocr_corrections is not None and ocr=='off':
        raise ConversionError('OCR 교정 입력은 OCR auto 모드와 함께 사용하세요.')
    raw_source = str(source)
    is_url = raw_source.startswith(("http://", "https://"))
    source_path = None
    content_type, final_url = "", ""
    if is_url:
        if data is not None:
            raise ConversionError("URL과 파일 바이트를 동시에 지정할 수 없습니다.")
        data, final_url, content_type = fetch(raw_source)
        filename = safe_name(filename or unquote(urlparse(final_url).path).rsplit("/", 1)[-1] or "webpage.html")
        if "text/html" in content_type and not Path(filename).suffix:
            filename += ".html"
    elif data is None:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file() or source_path.stat().st_size > MAX_INPUT:
            raise ConversionError("입력 파일이 없거나 100MB 상한을 초과했습니다.")
        data = source_path.read_bytes()
        filename = filename or source_path.name
    filename = safe_name(filename or Path(raw_source).name)
    if len(data) > MAX_INPUT:
        raise ConversionError("입력 파일이 100MB 상한을 초과했습니다.")
    if dynamic and not is_url:
        raise ConversionError("동적 수집은 공개 URL에만 사용할 수 있습니다. 내려받은 HTML 스크립트는 실행하지 않습니다.")
    snapshot_data = None
    corrections_data=None
    if ocr_corrections is not None:
        from .review import load_corrections
        corrections_data,corrections_payload=load_corrections(ocr_corrections,data)
    if dom_snapshot is not None:
        from .dom_snapshot import load_snapshot
        if is_url or detect(data,filename,content_type)!='html':
            raise ConversionError('DOM 스냅샷은 저장한 원본 HTML 파일과 함께 입력해야 합니다.')
        snapshot_data,snapshot_payload=load_snapshot(dom_snapshot,data)
    if (data.startswith(b'%PDF') or Path(filename).suffix.lower()=='.pdf') and ocr=='auto' and ocr_corrections is None:
        from .pdf_job import start_pdf
        return start_pdf(data,filename,output,source_path=source_path,url=final_url or None,
                         max_pages=pdf_max_pages,detail=ocr_detail,progress=progress)
    if pdf_max_pages is not None:
        raise ConversionError('페이지 체크포인트는 PDF OCR auto 변환에서 사용하세요.')
    root = Path(output).expanduser().resolve()
    # Existing output is never replaced. No source modification.
    root.mkdir(parents=True, exist_ok=False)
    (root / "source").mkdir()
    source_relative = "source/" + filename
    (root / source_relative).write_bytes(data)
    assets = Assets(root)
    captured_at = datetime.now(timezone.utc).isoformat()
    source_info = {"path": source_relative, "filename": filename, "sha256": sha256(data), "size": len(data), "url": final_url or None}
    try:
        result = extract(data, filename, assets, encoding=encoding, content_type=content_type, source_path=source_path,
                         base_url=final_url, download=download_assets, selector='' if snapshot_data is not None or dynamic else selector)
        if snapshot_data is not None:
            from .dom_snapshot import apply_snapshot
            result=apply_snapshot(result,assets,snapshot_data,snapshot_payload,download=download_assets,selector=selector)
        elif dynamic and result.format == "html":
            from .dom_snapshot import apply_snapshot,load_snapshot
            frames, notices = browser_capture(final_url, assets,auto_tabs=auto_tabs,tab_selector=tab_selector,tab_wait_ms=tab_wait_ms)
            snapshot={'schema_version':1,'source_sha256':sha256(data),'source_url':final_url,'captured_at':captured_at,
                      'frames':[dict(frame,frame_index=frame.get('frame_index',frame.get('index',0))) for frame in frames]}
            if frames and frames[0].get('automatic_tabs'):snapshot['automatic_tabs']=frames[0]['automatic_tabs']
            parsed,payload=load_snapshot(snapshot,data)
            result=apply_snapshot(result,assets,parsed,payload,download=download_assets,selector=selector,
                                  mode='automatic_tabs' if auto_tabs else 'browser_initial',main_selector_only=True)
            if frames and frames[0].get('capture_report'):
                result.metadata['dom_capture']['capture_report']=frames[0]['capture_report']
            for notice in notices:
                result.warn("dynamic_capture", notice)
        elif dynamic:
            result.warn("dynamic_not_html", "입력이 HTML이 아니어서 브라우저 동적 수집은 적용하지 않았습니다.")
        if result.format == "markdown":
            from .markdown_assets import preserve_markdown_assets
            preserve_markdown_assets(result, assets, source_path)
        if is_url:
            result.warn("url_scope", "입력 URL 한 페이지와 직접 자산만 수집했습니다. 링크된 다른 페이지·사이트 전체·영상 본문은 수집 범위에 포함하지 않습니다.")
        if not result.units:
            result.warn("no_units", "추출된 본문 단위가 없습니다.")
        from .ocr import augment_ocr
        ocr_summary = augment_ocr(result, assets, ocr, data)
        if corrections_data is not None:
            from .review import apply_corrections
            apply_corrections(result,assets,corrections_data,corrections_payload)
        return finish_bundle(result,assets,source_info,captured_at,ocr_summary,source_path=source_path)
    except Exception as error:
        json_write(root / "error.json", {"status": "failed", "error": str(error), "source": source_info, "source_preserved": True})
        if isinstance(error, ConversionError):
            raise
        raise ConversionError(f"변환 실패: {type(error).__name__}: {error}. 원본은 {root / source_relative}에 보존했습니다.") from error



def finish_bundle(result, assets, source_info, captured_at, ocr_summary, *, source_path=None):
    root=assets.root
    checks = machine_checks(result)
    source_info["format"] = result.format
    from .review import write_review
    write_review(result,assets,source_info,checks)
    markdown, ranges = build_markdown(result, source_info, captured_at)
    (root / PRIMARY).write_bytes(markdown)
    json_write(root / "structure.json", result.structure())
    json_write(root / "checks.json", {"checks": checks, "ai_calls": 0, "generative_ai_calls": 0,
                                     "ocr_used": ocr_summary["used"], "ocr": ocr_summary,
                                     "meaning_reviewed": False, "visual_reviewed": False})
    unit_ids = [u.id for u in result.units]
    from . import __version__
    manifest = {"schema_version": 1, "producer": "source-preserve-md/" + __version__, "source": source_info,
                "extraction_date": captured_at, "inventory": {"unit_ids": unit_ids, "basis": "형식별 파서가 읽은 상위 페이지·시트·슬라이드·본문 목록. 미탐지 하위 요소 가능성은 보고서 참조."},
                "units": [{"id": u.id, "anchor": u.id, "source_locator": u.source_locator, "extraction": "partial",
                           "checks": {stage: "pending" for stage in ("scope", "text", "visual", "structure", "utility")}} for u in result.units],
                "markdown_ranges": ranges, "assets": assets.records, "unresolved": result.warnings,
                "status": "partial", "processing": result.metadata.get("pdf_processing"), "ai_calls": 0, "generative_ai_calls": 0,
                "ocr_used": ocr_summary["used"], "ocr": ocr_summary, "artifacts": {}}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in ("manifest.json", "integrity.json", REPORT) and not (result.metadata.get("pdf_processing") and (path.relative_to(root).parts[0]=="checkpoint" or (path.parent==root and path.name in ("pdf-job.json",".pdf-job.lock")))):
            manifest["artifacts"][path.relative_to(root).as_posix()] = sha256(path.read_bytes())
    json_write(root / "manifest.json", manifest)
    integrity = validate_bundle(root)
    (root / REPORT).write_text(report_text(manifest, checks, integrity), encoding="utf-8")
    manifest["artifacts"][REPORT] = sha256((root / REPORT).read_bytes())
    json_write(root / "manifest.json", manifest)
    integrity = validate_bundle(root)
    json_write(root / "integrity.json", integrity)
    if source_path and sha256(source_path.read_bytes()) != source_info["sha256"]:
        raise ConversionError("변환 중 입력 파일이 바뀌었습니다. 보관한 원본은 남겼으며 현재 입력으로 다시 실행해 주세요.")
    return {"output": str(root), "markdown": str(root / PRIMARY), "format": result.format, "title": result.title,
            "units": len(unit_ids), "warnings": result.warnings, "integrity": integrity,
            "machine_checks": {"passed": sum(c["passed"] for c in checks), "total": len(checks)}, "status": "partial",
            "ai_calls": 0, "generative_ai_calls": 0, "ocr_used": ocr_summary["used"], "ocr": ocr_summary,
            **({"processing":result.metadata["pdf_processing"]} if "pdf_processing" in result.metadata else {})}

def new_output(parent: Path | str, name: str) -> Path:
    return Path(parent) / (datetime.now().strftime("%Y%m%d-%H%M%S-") + safe_name(Path(name).stem)[:60] + "-" + uuid.uuid4().hex[:8])


def correct_bundle(folder,output,corrections):
    """Apply reviewed changes to an immutable extraction, without rerunning OCR."""
    import shutil
    from .model import Unit
    from .review import load_corrections,apply_corrections,write_review
    original=Path(folder).expanduser().resolve()
    if not validate_bundle(original)['passed']:raise ConversionError('원본 추출 묶음의 무결성이 일치하지 않습니다.')
    manifest=json.loads((original/'manifest.json').read_text())
    source=(original/manifest['source']['path']).read_bytes()
    data,payload=load_corrections(corrections,source)
    structure=json.loads((original/'structure.json').read_text())
    result=Extraction(structure['title'],structure['format'],metadata=structure['metadata'],warnings=list(manifest['unresolved']))
    result.units=[Unit(**unit) for unit in structure['units']]
    root=Path(output).expanduser().resolve();root.mkdir(parents=True,exist_ok=False)
    for relative in manifest['artifacts']:
        target=(root/relative).resolve()
        if not target.is_relative_to(root):raise ConversionError('묶음 경로가 출력 폴더 밖입니다.')
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original/relative,target)
    assets=Assets(root);assets.records=list(manifest['assets'])
    assets.total_bytes=sum(p.stat().st_size for p in (root/'assets').rglob('*') if p.is_file())
    apply_corrections(result,assets,data,payload)
    checks=machine_checks(result);captured=datetime.now(timezone.utc).isoformat()
    write_review(result,assets,manifest['source'],checks)
    markdown,ranges=build_markdown(result,manifest['source'],captured)
    (root/PRIMARY).write_bytes(markdown);json_write(root/'structure.json',result.structure())
    summary=result.metadata.get('ocr',manifest.get('ocr',{}))
    json_write(root/'checks.json',{'checks':checks,'ai_calls':0,'generative_ai_calls':0,'ocr_used':manifest['ocr_used'],'ocr':summary,'meaning_reviewed':False,'visual_reviewed':False,'ocr_rerun':False})
    manifest.update(extraction_date=captured,markdown_ranges=ranges,assets=assets.records,unresolved=result.warnings,ocr=summary,
                    derived_from={'manifest_sha256':sha256((original/'manifest.json').read_bytes()),'operation':'human_ocr_correction','ocr_rerun':False},artifacts={})
    for path in root.rglob('*'):
        if path.is_file() and path.name not in ('manifest.json','integrity.json',REPORT):manifest['artifacts'][path.relative_to(root).as_posix()]=sha256(path.read_bytes())
    json_write(root/'manifest.json',manifest);integrity=validate_bundle(root)
    (root/REPORT).write_text(report_text(manifest,checks,integrity),encoding='utf-8')
    manifest['artifacts'][REPORT]=sha256((root/REPORT).read_bytes());json_write(root/'manifest.json',manifest)
    integrity=validate_bundle(root);json_write(root/'integrity.json',integrity)
    return {'output':str(root),'markdown':str(root/PRIMARY),'format':result.format,'units':len(result.units),'integrity':integrity,'machine_checks':{'passed':sum(c['passed'] for c in checks),'total':len(checks)},'status':'partial','ocr_rerun':False,'corrections':len(data['actions'])}

