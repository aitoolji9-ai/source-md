# 원문결 · Source Preserve MD

파일·공개 웹페이지의 원문, 순서, 표, 코드, 이미지와 출처를 Markdown으로 추출하는 로컬 프로그램입니다. 사용자 화면·CLI·Codex/Hermes 스킬이 같은 엔진을 사용합니다. 버전 **0.8.0**.

외부 생성형 AI API를 호출하지 않습니다. 이미지 문자는 macOS Apple Vision의 로컬 OCR로 읽습니다. OCR 자체는 머신러닝을 사용하며, `--ocr off`로 끌 수 있습니다. 요약·번역으로 원문을 대체하지 않습니다.

MD만으로 모든 글꼴·배치·도형·대화형 동작을 복제할 수는 없습니다. 원본 바이트와 SHA-256, 구조 JSON, 이미지/패키지/XML, 원본 대조 화면을 함께 보관합니다. 자동 결과의 `partial`과 저장 무결성 통과는 의미·시각 정확도 인증과 구분합니다.

## 설치와 실행

우선 검증 환경은 **macOS, Python 3.12**입니다. [uv](https://docs.astral.sh/uv/getting-started/installation/)를 설치하고 이 저장소를 내려받은 폴더에서 실행합니다. 최초 설치는 네트워크가 필요합니다.

```sh
uv sync --frozen --python 3.12
uv run --frozen preserve-md doctor
uv run --frozen preserve-md serve --open
```

이후에는 `시작.command`를 더블클릭해도 됩니다. 기본 화면은 `http://127.0.0.1:8766`입니다. 종료는 실행한 터미널에서 `Ctrl+C`입니다. 서버는 로컬용입니다.

선택 기능의 준비:

- 이미지 OCR: macOS의 Xcode Command Line Tools (`xcode-select --install`). 첫 인식 시 Swift 도구를 로컬 컴파일합니다.
- 구형 DOC/XLS/PPT: LibreOffice 설치. 자동으로 찾지 못하면 `SOURCE_PRESERVE_SOFFICE`에 실행 파일 경로를 지정합니다.
- 동적 URL: Chrome을 설치하거나 `uv run --frozen python -m playwright install chromium`을 실행합니다. 로그인 정보는 가져오지 않습니다.
- 다른 운영체제의 직접 추출은 의존성·형식에 따라 사용할 수 있지만 Apple Vision OCR은 macOS 전용입니다. 다른 OS에서의 실제 검증 상태는 [검증 문서](docs/VERIFICATION.md)를 확인하세요.

## 사용

파일을 올리거나 공개 URL 한 개를 입력합니다. 동적 수집은 표준/일부 비표준 탭·프레임·스크롤 변화를 자동 수집합니다. 전체 사이트를 크롤링하지 않습니다. 무한 목록은 상한에서 멈추고 부분 결과로 보고합니다. 새 출력 폴더만 사용하며 원본을 덮어쓰지 않습니다.

```sh
uv run --frozen --offline preserve-md convert '/경로/문서.pdf' --out outputs/새_문서
uv run --frozen --offline preserve-md convert '/경로/문서.hwp' --ocr off --out outputs/직접추출
uv run --frozen --offline preserve-md convert 'https://example.com/article' --dynamic --out outputs/새_웹문서
uv run --frozen --offline preserve-md validate outputs/새_문서
uv run --frozen --offline preserve-md formats
```

`uv --offline`은 패키지 관리 옵션입니다. URL·원격 이미지 수집은 해당 사이트에 접속합니다. 로컬 문서의 원격 자산 다운로드를 끄려면 `--no-assets`를 추가합니다. 명령 종료 0은 해당 명령의 생성/검사가 통과한 것이며, 전체 의미 정확도 인증이 아닙니다. 오류·불일치·미지원·일부 상한은 종료 2와 보고서로 남깁니다.

결과 폴더의 핵심 파일:

```text
00_원본복원.md             원문·순서·표·코드·상대 이미지 링크
03_변환검증보고서.md       검사·제한·미해결 항목
04_원본대조.html           원본 이미지/위치와 OCR 결과 대조·교정
source/                   원본 사본
assets/                   이미지·XML·표/수식·OCR 원시 데이터
structure.json            단위 순서·원문·출처 좌표·메타데이터
manifest.json             원본/산출물 해시·단위 목록
checks.json               수행한 내용 자동 검사
integrity.json            저장 무결성 재조회 결과
```

사람·AI가 출처와 이미지를 재사용하려면 **전체 묶음**을 보관하세요. MD 입력은 바이트 그대로 복사합니다. 원본 대조 화면에서 인식 줄·셀을 고친 뒤 JSON을 내려받아 프로그램의 “교정 JSON 적용”으로 새 묶음을 만듭니다. 원본과 이전 인식은 유지하며 OCR을 다시 실행하지 않습니다.

```sh
uv run --frozen --offline preserve-md correct 기존_묶음 --corrections ocr-corrections.json --out outputs/새_교정본
```

## 스킬로 설치

엔진을 설치한 후 호스트가 읽는 **새 스킬 폴더**를 지정합니다. 예를 들어 Codex는 다음과 같습니다.

```sh
uv run --frozen preserve-md install-skill --target ~/.codex/skills/source-preserve-md
python3 ~/.codex/skills/source-preserve-md/scripts/run.py doctor
```

Hermes도 해당 환경의 스킬 폴더를 `--target`으로 지정합니다. 같은 `SKILL.md`와 실행기가 설치됩니다. 호출명은 **“마크다운 추출 스킬로 이 파일을 추출해줘”**입니다. 기존 스킬은 덮어쓰지 않습니다. 스킬 대화에 AI가 참여하더라도 변환 엔진은 생성형 모델을 호출하지 않습니다.

체크아웃 안의 `skills/source-preserve-md`를 직접 사용할 수도 있습니다. 설치본의 `.engine.json`은 해당 컴퓨터의 Python 연결 정보이므로 공유하지 않습니다. 환경을 옮겼다면 `install-skill`로 다시 연결합니다. [공유·설치 상세](docs/SHARING.md)

## 지원 범위

| 입력 | 추출·보존 | 자동으로 복원하지 않는 부분 |
|---|---|---|
| TXT·소스코드·JSON·XML·YAML 등 | 코드 펜스 안에 원문, 인코딩 기록, 원본 바이트 | 구문 의미 해석, 코드 실행 |
| MD | 바이트 동일 복사·참조된 로컬 자산의 상대 경로 보존 | 원격 자산·원본 폴더 밖·업로드의 기준 경로 부재 |
| HTML·공개 URL | 제목 계층·DOM 순서·목록·강조·링크·코드·표·이미지·DOM JSON | CSS/픽셀·숨김의 시각 의미, 링크된 다른 본문 |
| 동적 URL | 깨끗한 Chrome/Chromium에서 details 펼침·유한 스크롤·표준/일부 비표준 탭·iframe·단계별 스크롤·상태별 DOM·캡처 | 로그인·모든 탭 조합·끝없는 목록의 완결 |
| CSV·TSV | 빈 셀·행별 배열·인용 개행·HTML 표 | 스프레드시트 자동 형변환 |
| PDF | 텍스트 레이어·좌표·링크·주석·입력 위젯·페이지 PNG·원 이미지·내장 첨부·테두리 선으로 검출한 표의 셀/빈칸/병합 좌표·스캔/혼합 페이지 로컬 OCR | 선 없는 표·검출되지 않은 셀·다단 읽기 순서·도형 관계·OCR 정확도 인증 |
| DOCX | 본문/표 순서·강조·병합표·이미지·머리/꼬리말·기본 번호 목록·기본 수식·주석/각주 링크·패키지 | 중첩/복합 번호 완전 재현·부동 개체·페이지 배치·일부 복합 수식 |
| PPTX | 관계 기준 슬라이드 순서·개체/그룹 좌표·텍스트·메모·표·이미지·차트 제목/다층 범주/참조 수식/캐시·패키지 | 슬라이드 렌더·SmartArt·애니메이션·캐시가 없는 차트 값·시각 의미·상속 개체 의미 |
| XLSX·XLSM | 탭 순서·원시 값·수식·캐시·기본 숫자/백분율/통화 표시·빈 셀·숨김·병합 범위·차트·패키지 | 날짜/복합 표시 형식·수식 재계산·차트/조건부서식 시각 의미; 병합은 범위 정보로 보완 |
| EPUB | spine 순서·XHTML 본문·이미지·장간 앵커 재연결·패키지 | 복잡한 각주 왕복 링크·고정 레이아웃·CSS/글꼴 |
| MHTML | MIME HTML/프레임·내장 이미지·파트 | 대화형 동작·원격 미포함 자산 |
| HWPX·RTF | 섹션/본문 텍스트, HWPX 전체 패키지 | 표·수식·페이지 구조 복원 |
| PNG·JPEG·GIF·WEBP·TIFF·BMP·ICO·AVIF | 이미지 프레임·원본·로컬 OCR·격자/일부 정렬 표·제한 범위의 기울기·선 끊김 보완 | 복잡한 선 없는 표·도형 의미·OCR 정확도 인증·애니메이션 시간 재현 |
| SVG | 안전 XML 파싱·코드 원문·실행/외부 참조를 제외한 PNG 렌더 | 스크립트·외부 참조·시각 의미 인증 |
| ODF | ODT 등의 본문·표·이미지·전체 패키지 | 스타일 기반 번호·복잡한 배치·계산/발표 의미 |
| HWP5 | 본문·표·이미지·각주/미주·모델 XML·내장 미리보기·스타일 HTML | 암호/배포용·미지원 개체·폰트/픽셀 동일성 |
| DOC·XLS·PPT | 격리 LibreOffice→OOXML→본문·표·이미지·수식/캐시·메모 추출, 원본과 중간 형식 보관 | 변환 손실·고급 서식·페이지 동일성 |
| 미지원 구형 변형·음성/영상·임의 바이너리 | 원본 바이트 보관·미지원 보고 | 본문 변환 |

입력 100MB, 패키지 해제 300MB/15,000항목, 상위 단위 1,500개, 시트/CSV 200,000셀, OCR 100대상/256요청/180초 등 상한이 있습니다. 동적 수집은 전체 45초·64상태·40MB, iframe 깊이 3·스크롤 영역 8개·영역당 32회입니다. 스캔/혼합 PDF·이미지·문서 내 래스터의 OCR 대상·실패·미검사·후보 불일치는 별도 기록합니다. 표가 보존돼도 선 없는 표·다단 읽기 순서·사진·손글씨 등은 원본 대조가 필요합니다.

## 검증·공유

```sh
uv sync --frozen --extra dev
uv run --frozen pytest -q -m 'not browser'
uv build
uv run --frozen python scripts/verify_distribution.py --wheel dist/source_preserve_md-0.8.0-py3-none-any.whl --out outputs/새_배포검증
```

검사 항목과 실제 실행 결과는 [VERIFICATION](docs/VERIFICATION.md), 독립 공유 저장소·ZIP 생성과 GitHub 반영 절차는 [SHARING](docs/SHARING.md), 경계는 [SECURITY](SECURITY.md)에 있습니다. `.github/workflows/ci.yml`은 업로드 후 실행할 자동 검사이며 로컬 검증과 구분합니다.

이 프로젝트의 원본 코드는 **AGPL-3.0-only**로 공유합니다. [LICENSE](LICENSE), [의존성·표본 고지](THIRD_PARTY_NOTICES.md)를 함께 보관하세요. 사용자 입력 문서의 권리는 해당 원저작자에게 있습니다. 외부 런타임과 개인 문서는 배포 묶음에 포함하지 않습니다.
