# 공개 저장소 설치 검증 — 2026-11-06

공개 저장소: [aitoolji9-ai/sourceweave](https://github.com/aitoolji9-ai/sourceweave).

## 공개 다운로드와 설치

인증 도우미·추가 인증 헤더를 사용하지 않는 HTTPS clone으로 저장소를 새 폴더에 내려받았다. `uv sync --frozen --extra dev --python 3.12`로 새 환경을 설치하고 회귀 **95건**을 통과했다. 기존 로컬 작업 폴더나 설치된 스킬을 GitHub 다운로드 대신 재사용하지 않았다.

내려받은 코드에서 wheel을 새로 만들고, 또 다른 환경에 wheel을 설치했다. 스킬 설치 및 다른 작업 폴더의 상대 경로 변환, HTML 표/코드, DOCX/XLSX/PPTX, HWP, LibreOffice 구형 문서, 실제 Apple Vision OCR·교정, Flask 업로드·읽기·ZIP 다운로드 등 **17개 경로**를 통과했다. 이 Mac에는 선택 런타임이 준비돼 있으며 최초 설치에는 네트워크가 필요하다.

## GitHub Actions

검증한 실행: [Verify shared distribution #42227051902](https://github.com/aitoolji9-ai/sourceweave/actions/runs/42227051902). 기준 커밋: `1b9f4a4c3ee586141a0d9212f54e9b52045d64aa`.

| 원격 잡 | 결과 |
|---|---|
| Ubuntu 테스트·빌드·wheel 새 환경 설치 | 통과 |
| macOS 테스트·빌드·wheel 새 환경 설치 | 통과 |
| Chromium 동적 렌더·details·iframe·쓰기 요청 차단 | 통과 |
| 잠금 의존성의 공개 취약점 조회 | 통과 |

최신 커밋의 상태는 [워크플로 실행 목록](https://github.com/aitoolji9-ai/sourceweave/actions/workflows/ci.yml)에서 확인한다. 문서/스킬 안내를 갱신한 커밋도 같은 검사를 실행한다. 선택 도구가 없는 CI 환경에서는 해당 실제 변환을 성공 수치에 합산하지 않으므로 wheel 검사 수는 환경별로 다르다.

## 재현

```sh
git clone https://github.com/aitoolji9-ai/sourceweave.git
cd sourceweave
uv sync --frozen --python 3.12
uv run --frozen preserve-md doctor
uv run --frozen preserve-md convert examples/구조보존_시험.html --ocr off --out outputs/first-run
uv run --frozen preserve-md validate outputs/first-run
uv run --frozen preserve-md install-skill --target ~/my-agent-skills/source-preserve-md
python3 ~/my-agent-skills/source-preserve-md/scripts/run.py formats
```

`--target`은 실제 호스트가 읽는 새 스킬 폴더로 바꾼다. 기본 웹 화면은 `uv run --frozen preserve-md serve --open`으로 연다. 상세 환경 준비와 상한은 [README](../README.md), [공유 안내](SHARING.md), [정확성 범위](VERIFICATION.md)를 따른다. 이 검사는 설치·명시된 동작 경로의 확인이며 모든 원문의 의미·시각·OCR 정확도 인증과 구분한다.
