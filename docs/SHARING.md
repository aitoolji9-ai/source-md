> 현재 설치 정책은 [0.9.0 필수 환경 검사](INSTALLATION_GATE.md)를 따른다. 아래 0.8.0 wheel 설치 예시는 이전 배포 방식의 기록이다. 새 사용자는 README의 `sh install.sh`로 설치한다.

# 설치·스킬 통합·공유

## 배포 구성

저장소 소스, `uv.lock`, `skills/source-preserve-md`, 테스트, 설치/검증 스크립트를 함께 공유한다. `uv build`는 wheel과 소스 배포본을 만든다. wheel에도 웹 화면·Swift OCR 소스·스킬이 포함된다. 처음 설치할 때는 Python/uv 및 패키지 다운로드가 필요하며, Chrome/Chromium·LibreOffice·Swift는 선택 기능에 필요한 외부 도구다. 실행 파일 하나로 모든 운영체제에서 즉시 실행되는 배포본은 아니다.

체크아웃:

```sh
uv sync --frozen --python 3.12
uv run --frozen preserve-md doctor
uv run --frozen preserve-md install-skill --target ~/my-agent-skills/source-preserve-md
python3 ~/my-agent-skills/source-preserve-md/scripts/run.py formats
```

wheel을 별도 환경에 설치할 때:

```sh
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python /경로/source_preserve_md-0.8.0-py3-none-any.whl
.venv/bin/preserve-md install-skill --target ~/my-agent-skills/source-preserve-md
```

다른 컴퓨터에서 같은 잠금 버전을 재현하려면 저장소의 `uv sync --frozen` 경로를 사용한다. 단독 wheel 설치는 선언된 호환 범위에서 의존성을 선택한다. 배포 검증 스크립트는 `uv.lock`의 버전으로 wheel을 설치해 확인한다.

`--target`은 각 호스트가 읽는 실제 스킬 디렉터리로 바꾼다. Codex의 기본 위치는 `~/.codex/skills/source-preserve-md`, Hermes는 해당 프로필/공통 스킬 설정의 위치를 사용한다. 기존 폴더·심볼릭 링크는 덮어쓰지 않으므로 업데이트할 때는 기존 사본을 보관하고 새 폴더에서 시험한다. 이미 열린 대화가 새 스킬을 읽었는지는 호스트의 재조회 동작으로 확인한다.

스킬 실행기는 호출 당시 작업 폴더를 유지한다. 상대 입력·출력도 그 폴더 기준이다. 체크아웃을 직접 사용하는 경우 상위 `.venv`를 찾고, 설치한 스킬은 `.engine.json`에 연결된 Python을 사용한다. `SOURCE_PRESERVE_MD_ROOT`를 지정하면 해당 체크아웃을 우선하며 잘못된 경로에서 다른 설치로 몰래 전환하지 않는다. `.engine.json`과 가상환경은 다른 컴퓨터로 복사하지 않는다.

## 공유용 저장소 만들기

```sh
uv run --frozen python scripts/export_repository.py --out ../source-preserve-md-share
```

새 폴더만 사용한다. 허용한 소스·스킬·문서·테스트만 복사하고 바이트 해시를 대조한다. 개인 변환 문서, `outputs`, `.env`, 가상환경, 캐시, 로컬 스킬 연결 정보, 이전 Git 기록은 포함하지 않는다. 공개 품질 표본은 출처·해시 목록만 포함하고, 명시적 요청으로 내려받는다. 포함된 HWP 표본의 출처와 라이선스는 `THIRD_PARTY_NOTICES.md`를 따른다.

새 공유 폴더에서 설치·검증을 끝낸 뒤 독립 Git 저장소를 만든다. 상위 폴더의 다른 프로젝트를 함께 스테이징하지 않는다. `git status`, 스테이징된 파일 목록, 비밀값 검사를 확인하고 커밋한다. 공개 여부와 대상 계정을 정한 후 GitHub 저장소를 생성하고 이 독립 저장소만 푸시한다. 저장소 이름은 `source-preserve-md`를 권장한다. 자동 검사 실행 여부·성공 상태는 GitHub에서 별도 재조회한다.

## 재검증

```sh
uv sync --frozen --extra dev
uv run --frozen pytest -q -m 'not browser'
uv run --frozen python scripts/fetch_quality_inputs.py --kind all
uv run --frozen python scripts/quality_check.py --out outputs/새_공개표본검증
uv build
uv run --frozen python scripts/verify_distribution.py --wheel dist/source_preserve_md-0.8.0-py3-none-any.whl --out outputs/새_설치검증 --require-office
```

`--require-office`는 LibreOffice가 없으면 실패한다. 생략하면 미설치 상태를 기록하고 해당 실제 변환을 통과 수치에 넣지 않는다. 공개 표본의 해시가 달라지면 원본이 바뀐 것이므로 실패 원인을 확인하며 비교 기준을 자동 완화하지 않는다. `doctor`는 의존성 존재 점검이며 인식·변환 실사용 검사를 대체하지 않는다.
