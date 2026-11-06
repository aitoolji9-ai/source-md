#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODE=${1:-install}
case "$MODE" in install|--check-only|--dev) ;; *) printf '%s\n' '사용법: sh install.sh [--check-only|--dev]' >&2; exit 2;; esac
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'Source MD 설치 중단: 기본 로컬 OCR에 macOS가 필수입니다. Linux·Windows에는 설치하지 않습니다.' >&2
    exit 2
fi
if ! command -v swiftc >/dev/null 2>&1; then
    printf '%s\n' 'Source MD 설치 중단: Swift 개발 도구가 없습니다. xcode-select --install을 완료하세요.' >&2
    exit 2
fi
if ! SOURCE_MD_TOOLCHAIN=$(xcode-select -p 2>/dev/null) || [ ! -d "$SOURCE_MD_TOOLCHAIN" ] || ! xcrun --find swiftc >/dev/null 2>&1; then
    printf '%s\n' 'Source MD 설치 중단: 선택된 Xcode 개발 도구 또는 Swift 컴파일러가 없습니다. xcode-select --install을 완료하세요.' >&2
    exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'Source MD 설치 중단: uv가 없습니다. README의 uv 설치 단계를 완료하세요.' >&2
    exit 2
fi
cd "$ROOT"
# No project environment/dependencies before the native prerequisite probe passes.
uv run --no-project --python 3.12 "$ROOT/src/source_preserve_md/preflight.py" --directory "$ROOT"
if [ "$MODE" = --check-only ]; then exit 0; fi
if [ "$MODE" = --dev ]; then
    exec uv sync --frozen --python 3.12 --extra dev
fi
exec uv sync --frozen --python 3.12
