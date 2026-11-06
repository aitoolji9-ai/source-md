# 원문결 · Source MD

파일·공개 웹페이지의 원문, 순서, 표, 코드, 이미지와 출처를 Markdown으로 추출하는 로컬 프로그램입니다. 사용자 화면·CLI·Codex/Hermes 스킬이 같은 엔진을 사용합니다. 버전 **0.9.0**.

영문 프로젝트명은 **Source MD**, GitHub 저장소 이름은 `source-md`입니다. 설치 패키지·스킬 이름은 `source-preserve-md`, 실행 명령은 `preserve-md`를 사용합니다.

[프로그램 다운로드](https://github.com/aitoolji9-ai/source-md/releases/tag/v0.9.0) · [필수 환경·설치 검사](docs/INSTALLATION_GATE.md) · [스킬 설치](#스킬로-설치)

PDF·한글·워드·엑셀·파워포인트·웹페이지를 다음 작업에 재사용하려는 사람에게 적합합니다. 프로그램 화면으로 직접 사용하거나, 설치한 스킬을 Codex·Hermes에서 호출할 수 있습니다.

외부 생성형 AI API를 호출하지 않습니다. 이미지 문자는 macOS Apple Vision의 로컬 OCR로 읽습니다. OCR 자체는 머신러닝을 사용하며, `--ocr off`로 끌 수 있습니다. 요약·번역으로 원문을 대체하지 않습니다.

MD만으로 모든 글꼴·배치·도형·대화형 동작을 복제할 수는 없습니다. 원본 바이트와 SHA-256, 구조 JSON, 이미지/패키지/XML, 원본 대조 화면을 함께 보관합니다. 자동 결과의 `partial`과 저장 무결성 통과는 의미·시각 정확도 인증과 구분합니다.

## 설치와 실행

아래는 **처음 설치하는 macOS 사용자 기준**입니다. 0.9.0부터 기본 로컬 OCR을 필수로 하므로 **Linux·Windows에서는 설치를 중단합니다.** 최초 설치에는 네트워크가 필요합니다. 0.8.0의 Linux 검증 기록은 이전 버전의 결과입니다.

### 0. 필수 환경 — 미충족 시 설치 중단

- macOS, 64비트 Apple Silicon 또는 Intel Mac
- Swift/Xcode Command Line Tools
- Python 3.12 이상: 설치기가 uv로 Python 3.12를 준비합니다.
- 한국어·영어 Apple Vision OCR: 실제 시험 이미지의 인식까지 통과해야 합니다.
- 설치 위치의 여유 공간 1GiB 이상: Python·의존성·캐시·임시파일을 위한 설치 정책입니다. 문서 변환 결과를 저장할 공간은 별도로 필요합니다.

`install.sh`가 먼저 검사하고 **모든 필수 항목이 통과한 뒤에만** 프로젝트 환경과 패키지를 설치합니다. 실패하면 종료 코드 2와 부족한 항목/해결 방법을 표시합니다. 검사를 건너뛰는 설치 옵션은 제공하지 않습니다. `doctor`의 도구 존재 확인과 실제 OCR을 실행하는 `preflight`는 구분합니다.

### 1. 처음 한 번 준비하기

**응용 프로그램 → 유틸리티 → 터미널**을 엽니다. Git과 개발 도구가 없다면 다음 명령을 실행하고 설치 창의 안내를 마칩니다. 이미 설치했다면 건너뜁니다.

```sh
xcode-select --install
```

uv가 없다면 [uv 공식 설치 명령](https://docs.astral.sh/uv/getting-started/installation/)을 실행합니다.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치 후 **터미널을 닫았다가 새로 열어** 다음 단계로 진행합니다. Git과 uv가 이미 준비된 환경은 바로 다음 단계부터 시작하면 됩니다.

### 2. 프로그램 설치하고 열기

새로 설치할 위치에서 아래 명령을 한 줄씩 실행합니다. `install.sh`가 필수 환경을 검사하고, 통과하면 Python과 필요한 패키지를 준비합니다.

```sh
git clone https://github.com/aitoolji9-ai/source-md.git
cd source-md
sh install.sh
uv run --frozen preserve-md preflight
uv run --frozen preserve-md doctor
uv run --frozen preserve-md serve --open
```

`doctor`는 필요한 도구의 준비 상태를 보여주며, 마지막 명령이 프로그램 화면을 엽니다. 자동으로 열리지 않으면 브라우저에서 `http://127.0.0.1:8766`에 접속합니다.

다음부터는 설치 폴더의 **`시작.command`를 더블클릭**하면 됩니다. 터미널에서 다시 실행하려면 설치 폴더로 이동한 뒤 다음을 실행합니다.

```sh
uv run --frozen preserve-md serve --open
```

프로그램을 사용하는 동안 실행한 터미널을 유지합니다. 종료하려면 그 터미널에서 `Ctrl+C`를 누릅니다. 이 서버는 로컬 사용자용입니다.

### ZIP으로 받는 경우

[다운로드 페이지](https://github.com/aitoolji9-ai/source-md/releases/tag/v0.9.0)에서 **`source-md-0.9.0-repository.zip`**을 받아 압축을 풉니다. 터미널에서 압축을 푼 프로젝트 폴더로 이동한 뒤 압축을 푼 `source-md` 폴더에서 `sh install.sh`부터 실행하면 됩니다. 0.9.0 공식 배포는 사전 검사를 거치는 소스 ZIP/소스 배포본입니다. 설치 단계 검사를 실행할 수 없는 미리 빌드된 wheel은 공식 다운로드로 제공하지 않습니다.

### 추가 기능 준비

| 사용할 기능 | 필요한 준비 |
|---|---|
| 이미지·스캔 문서 글자 OCR | macOS와 Xcode Command Line Tools. 첫 인식 시 포함된 Swift 소스를 로컬 컴파일합니다. |
| 구형 DOC·XLS·PPT | [LibreOffice](https://www.libreoffice.org/download/download-libreoffice/) 설치. 자동으로 찾지 못하면 `SOURCE_PRESERVE_SOFFICE`에 실행 파일 경로를 지정합니다. |
| 동적 웹페이지·탭 수집 | Chrome 설치 또는 아래 Chromium 설치 명령 |

```sh
uv run --frozen python -m playwright install chromium
```

필수 환경·설치 거부 검증 범위는 [검증 문서](docs/INSTALLATION_GATE.md)를 확인하세요. 개인 브라우저의 로그인 정보는 가져오지 않습니다.

## 사용

### 화면에서 사용하기

1. **파일** 탭에서 파일을 선택하거나 끌어다 놓습니다. 웹페이지는 **웹페이지 URL** 탭에 공개 주소를 입력합니다.
2. **추출 옵션**을 확인합니다. 이미지·스캔 문서는 **이미지 속 글자도 로컬 OCR로 추출**, 탭·지연 로딩 웹페이지는 **브라우저로 로딩한 뒤 수집**과 **탭 자동 수집**을 사용합니다.
3. **추출 시작**을 누릅니다.
4. **읽기 화면**, **Markdown 원문**, **검증 보고서**에서 결과를 확인합니다.
5. **MD 받기** 또는 **전체 묶음 받기**로 저장합니다. 이미지·원본·출처까지 재사용하려면 **전체 묶음**을 권장합니다.

이미지 글자가 잘못 읽혔다면 **원본 대조·OCR 교정**에서 원본과 대조해 수정합니다. 변경 항목의 JSON을 내려받은 뒤 프로그램의 **교정 JSON 적용**으로 새 묶음을 만듭니다. 원본과 이전 인식은 보존합니다.

입력 URL 한 페이지의 표준/일부 비표준 탭·프레임·스크롤 변화를 수집합니다. 전체 사이트를 크롤링하지 않습니다. 무한 목록은 상한에서 멈추고 부분 결과로 보고합니다. 새 출력 폴더만 사용하며 원본을 덮어쓰지 않습니다.

### 터미널에서 사용하기

프로젝트 폴더에서 실행합니다. `/경로/문서.pdf`와 결과 폴더 이름은 실제 경로로 바꿉니다.

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

**프로그램을 먼저 설치한 뒤**, 터미널에서 설치한 프로젝트 폴더로 이동합니다. 프로그램이 실행 중인 터미널은 명령 입력을 받지 않으므로 **새 터미널 창**을 사용하거나, 기존 창에서 `Ctrl+C`로 프로그램을 종료한 뒤 설치하세요. 홈 폴더에 복제했다면 `cd ~/source-md`로 이동합니다. 사용하는 도구에 맞는 명령을 실행합니다. Codex·Hermes 자체 설치는 별도입니다.

### Codex

```sh
uv run --frozen preserve-md install-skill --target ~/.codex/skills/source-preserve-md
python3 ~/.codex/skills/source-preserve-md/scripts/run.py doctor
```

### Hermes

기본 환경의 스킬 폴더를 사용하는 예입니다.

```sh
uv run --frozen preserve-md install-skill --target ~/.hermes/skills/source-preserve-md
python3 ~/.hermes/skills/source-preserve-md/scripts/run.py doctor
```

별도 프로필을 사용한다면 `--target`을 해당 프로필의 실제 스킬 폴더로 바꿉니다. 기본 스킬 위치와 프로필 동작은 [Hermes 공식 안내](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)를 참고하세요.

### 스킬 사용 예시

설치한 호스트의 스킬 목록에서 `source-preserve-md`를 확인하고 다음처럼 요청합니다.

> 마크다운 추출 스킬로 이 PDF를 원문과 표 구조를 보존해서 추출해줘.

> 마크다운 추출 스킬로 이 URL의 동적 탭까지 수집해줘.

두 호스트 모두 같은 `SKILL.md`와 엔진 실행기를 사용합니다. **기존 스킬 폴더나 심볼릭 링크는 덮어쓰지 않습니다.** 이미 존재한다는 오류가 나오면 기존 설치를 확인하세요. 시험용 설치는 호스트가 읽는 스킬 폴더 밖의 새로운 폴더를 `--target`으로 지정해 기존 설치와 구분할 수 있습니다. 스킬 대화에 AI가 참여하더라도 변환 엔진은 생성형 모델을 호출하지 않습니다.

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

[공개 저장소에서 내려받아 설치한 검증 결과](docs/PUBLISHED_VERIFICATION.md)와 [GitHub Actions](https://github.com/aitoolji9-ai/source-md/actions/workflows/ci.yml)를 확인할 수 있습니다.

```sh
sh install.sh --dev
uv run --frozen pytest -q -m 'not browser'
uv build
uv run --frozen python scripts/verify_distribution.py --wheel dist/source_preserve_md-0.9.0-py3-none-macosx_11_0_universal2.whl --out outputs/새_배포검증
```

검사 항목과 실제 실행 결과는 [VERIFICATION](docs/VERIFICATION.md), 독립 공유 저장소·ZIP 생성과 GitHub 반영 절차는 [SHARING](docs/SHARING.md), 경계는 [SECURITY](SECURITY.md)에 있습니다. `.github/workflows/ci.yml`은 업로드 후 실행할 자동 검사이며 로컬 검증과 구분합니다.

이 프로젝트의 원본 코드는 **AGPL-3.0-only**로 공유합니다. [LICENSE](LICENSE), [의존성·표본 고지](THIRD_PARTY_NOTICES.md)를 함께 보관하세요. 사용자 입력 문서의 권리는 해당 원저작자에게 있습니다. 외부 런타임과 개인 문서는 배포 묶음에 포함하지 않습니다.
