from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import convert, correct_bundle, new_output, validate_bundle
from .model import ConversionError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="preserve-md", description="외부 생성형 AI 호출 없이 원문·구조·자산을 보존하는 로컬 Markdown 추출기")
    from . import __version__
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    conversion = commands.add_parser("convert", help="파일 또는 공개 웹 URL 변환")
    conversion.add_argument("sources", nargs="+")
    conversion.add_argument("--out", type=Path, default=Path("outputs"), help="한 입력은 새 결과 폴더, 여러 입력은 새 결과들의 상위 폴더")
    conversion.add_argument("--encoding", default="", help="예: utf-8, cp949 (생략 시 판별)")
    conversion.add_argument("--dynamic", action="store_true", help="깨끗한 브라우저로 공개 URL 로딩 후 수집")
    conversion.add_argument('--no-auto-tabs',action='store_true',help='동적 URL 수집에서 탭 자동 전환 끄기')
    conversion.add_argument('--tab-selector',default='[role="tab"]',help='자동으로 전환할 탭 CSS 선택자')
    conversion.add_argument('--tab-wait-ms',type=int,default=5000,help='각 탭 DOM 변화를 기다리는 상한(100~30000ms)')
    conversion.add_argument('--dom-snapshot',type=Path,help='원본 HTML 해시와 실제 관측 DOM 상태를 담은 JSON. 로컬 스크립트 실행 없음')
    conversion.add_argument('--ocr-corrections',type=Path,help='원본대조 화면에서 내보낸 원본 해시·좌표별 OCR 교정 JSON')
    conversion.add_argument("--no-assets", action="store_true", help="원격 이미지 다운로드 끄기")
    conversion.add_argument("--selector", default="", help="HTML의 추출 범위를 지정하는 CSS 선택자. 기본: body 전체")
    conversion.add_argument("--ocr", choices=("auto", "off"), default="auto", help="이미지 문자 로컬 OCR: auto(기본, macOS Vision) / off(직접 추출만)")
    conversion.add_argument('--pdf-max-pages',type=int,help='PDF 페이지 작업 N개 후 체크포인트에 멈춤; resume으로 이어하기')
    conversion.add_argument('--ocr-detail',choices=('text','full'),default='text',help='PDF: 기본 text는 전체 페이지 초기 OCR 우선, full은 이후 줄/셀 심화 재인식')
    inventory=commands.add_parser('inventory',help='지정 폴더 바로 아래의 원본 PDF 목록·쪽수·해시 조회(재귀/OCR 없음)')
    inventory.add_argument('folder',type=Path)
    status=commands.add_parser('status',help='PDF 체크포인트의 진행·실제 쓰기 잠금을 읽기 전용으로 조회')
    status.add_argument('folder',type=Path)
    resume=commands.add_parser('resume',help='원본 해시·페이지 체크포인트를 검증하고 PDF 미처리 부분만 재개')
    resume.add_argument('folder',type=Path)
    resume.add_argument('--max-pages',type=int)
    validation = commands.add_parser("validate", help="결과 묶음을 재조회해 저장 무결성 검사")
    validation.add_argument("folder", type=Path)
    serving = commands.add_parser("serve", help="로컬 사용자 화면 실행")
    serving.add_argument("--port", type=int, default=8766)
    serving.add_argument("--open", action="store_true", help="기본 브라우저 열기")
    serving.add_argument("--output-root", type=Path, default=Path("outputs"))
    commands.add_parser("formats", help="지원 범위 출력")
    commands.add_parser('doctor', help='로컬 의존성·스킬 파일 확인(설치/다운로드 없음)')
    commands.add_parser('preflight', help='필수 설치 환경과 실제 로컬 OCR 검사; 미충족 시 종료 2')
    skill = commands.add_parser('install-skill', help='같은 엔진을 호출하는 스킬을 새 폴더에 설치')
    skill.add_argument('--target', type=Path, required=True, help='호스트가 읽을 새 스킬 폴더; 기존 스킬 덮어쓰기 없음')
    capture=commands.add_parser('capture-tool',help='AI 없이 열린 웹 탭 DOM을 저장하는 오프라인 북마크 도우미 생성')
    capture.add_argument('--out',type=Path,required=True)
    correction=commands.add_parser('correct',help='기존 추출 묶음에 원본 대조 교정을 새 묶음으로 적용(OCR 재실행 없음)')
    correction.add_argument('folder',type=Path)
    correction.add_argument('--corrections',type=Path,required=True)
    correction.add_argument('--out',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'preflight':
            from .preflight import inspect_environment
            result=inspect_environment();print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result['passed'] else 2
        if args.command in ('convert','resume','serve','correct','capture-tool'):
            from .preflight import require_environment
            require_environment()
        def page_progress(event):
            print(json.dumps({'pdf_progress':event},ensure_ascii=False),file=__import__('sys').stderr,flush=True)
        if args.command=='inventory':
            from .pdf_job import inventory_pdf
            print(json.dumps(inventory_pdf(args.folder),ensure_ascii=False,indent=2))
            return 0
        if args.command=='status':
            from .pdf_job import status_pdf
            print(json.dumps(status_pdf(args.folder),ensure_ascii=False,indent=2))
            return 0
        if args.command=='resume':
            from .pdf_job import resume_pdf
            result=resume_pdf(args.folder,max_pages=args.max_pages,progress=page_progress)
            from .pdf_job import cli_summary
            print(json.dumps(cli_summary(result),ensure_ascii=False,indent=2))
            if not result['integrity']['passed'] or result['machine_checks']['passed']!=result['machine_checks']['total']:return 2
            if result['processing']['failed_pages'] or any(w['code'] in ('ocr_failed','ocr_limit','ocr_refinement_failed') for w in result['warnings']):return 2
            return 0 if result['processing']['complete'] else 3
        if args.command == 'doctor':
            from .integration import doctor
            print(json.dumps(doctor(), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'install-skill':
            from .integration import install_skill
            print(json.dumps(install_skill(args.target), ensure_ascii=False, indent=2))
            return 0
        if args.command=='correct':
            result=correct_bundle(args.folder,args.out,args.corrections)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result['integrity']['passed'] else 2
        if args.command=='capture-tool':
            from .capture_tool import write_capture_tool
            print(json.dumps({'capture_tool':write_capture_tool(args.out)},ensure_ascii=False))
            return 0
        if args.command == "convert":
            if (args.dom_snapshot or args.ocr_corrections) and len(args.sources)!=1:
                raise ConversionError('DOM 스냅샷/교정 JSON은 한 개의 원본과 짝지어 입력하세요.')
            failed = False
            checkpointed=False
            for source in args.sources:
                output = args.out if len(args.sources) == 1 else new_output(args.out, source)
                try:
                    result = convert(source, output, encoding=args.encoding, dynamic=args.dynamic, download_assets=not args.no_assets, selector=args.selector, ocr=args.ocr,dom_snapshot=args.dom_snapshot,
                                     auto_tabs=not args.no_auto_tabs,tab_selector=args.tab_selector,tab_wait_ms=args.tab_wait_ms,ocr_corrections=args.ocr_corrections,pdf_max_pages=args.pdf_max_pages,ocr_detail=args.ocr_detail,progress=page_progress)
                    if result.get("processing"):
                        from .pdf_job import cli_summary
                        print(json.dumps(cli_summary(result),ensure_ascii=False,indent=2))
                    else:
                        print(json.dumps(result, ensure_ascii=False, indent=2))
                    checkpointed |= bool(result.get("processing") and not result["processing"]["complete"])
                    failed |= not result["integrity"]["passed"] or any(notice["code"] in ("unsupported_format", "ocr_failed", "ocr_limit", "ocr_refinement_failed",'automatic_tab_incomplete','hwp_conversion_incomplete','hwp_note_reference_unresolved') for notice in result["warnings"])
                    failed |= result["machine_checks"]["passed"] != result["machine_checks"]["total"]
                except (ConversionError, FileExistsError) as e:
                    print(json.dumps({"source": source, "status": "failed", "error": str(e)}, ensure_ascii=False))
                    failed = True
            return 2 if failed else 3 if checkpointed else 0
        if args.command == "validate":
            result = validate_bundle(args.folder)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 2
        if args.command == "serve":
            from .server import serve
            serve(args.port, args.open, args.output_root)
        if args.command == "formats":
            print("직접 추출: MD/TXT/코드/JSON/XML/YAML/CSV/TSV/HTML/PDF/DOCX/PPTX/XLSX/XLSM/EPUB/MHTML\n로컬 변환: HWP5(본문·표·이미지·각주/미주), DOC/XLS/PPT(격리 LibreOffice→OOXML)\n부분 추출: ODF(본문·표·패키지), HWPX(텍스트·패키지), RTF(텍스트), SVG(코드·안전 렌더), 이미지(프레임·원본·로컬 OCR)\nOCR: macOS Apple Vision, 기본 auto. 격자선·일부 정렬 표, 제한 범위의 기울기/선 끊김 보완. --ocr off로 끌 수 있음\n본문 미지원: 암호/배포 HWP·미지원 구형 변형·음성·영상·임의 바이너리(원본 보관 또는 명시 실패)\n모든 형식: 시각·의미 완전성은 자동 인증하지 않음. 외부 생성형 AI API 호출 없음.")
        return 0
    except (ConversionError, OSError, ValueError, RuntimeError) as e:
        print(json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

