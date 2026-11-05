#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if ! command -v uv >/dev/null 2>&1; then
  print 'uv 실행 파일이 필요합니다. README.md의 설치 절차를 확인해 주세요.'
  read -r '?Enter를 누르면 닫습니다. '
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  uv sync --frozen --python 3.12
fi
exec uv run --frozen --offline preserve-md serve --open

