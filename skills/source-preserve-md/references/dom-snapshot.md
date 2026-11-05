# 탭 클릭 뒤 생성되는 DOM 보존

사용자가 원하는 본문이 초기 HTML에 없는 경우에 읽는다. 원본 HTML은 보관하고 실제 열린 상태들의 DOM을 추가 입력한다. 본문을 AI로 만들거나 스크립트 문자열에서 정답을 옮겨 넣지 않는다.

## 사람이 AI 없이 사용

```text
python3 scripts/run.py capture-tool --out DOM저장도우미.html
```

생성한 HTML을 브라우저로 열고 “원문결 DOM 저장” 링크를 북마크 막대로 끌어다 놓는다. 추출할 페이지에서 해당 북마크를 실행한다. 페이지 보안 정책이 막으면 우회하지 않고 허용된 DOM 내보내기를 사용한다.

1. 페이지 원본 HTML 파일을 저장하고 도우미에서 그 파일을 선택한다. 변환에도 같은 파일을 사용한다.
2. **탭 자동 수집**을 누른다. 표준·일부 비표준 탭과 접근 가능한 iframe을 탐색하고 스크롤에서 바뀌는 상태도 저장한다. 자동으로 인식하지 못하는 탭은 직접 열어 이름을 정해 상태를 추가할 수 있다.
3. DOM JSON을 내보낸다. 프로그램의 추출 옵션에서 원본 HTML 한 파일과 DOM JSON을 선택하거나 CLI로 변환한다.

```text
python3 scripts/run.py convert 원본.html --dom-snapshot 원본.html.dom.json --out 새_결과폴더
python3 scripts/run.py validate 새_결과폴더
```

도우미는 입력 원본의 SHA-256과 최대 64개 상태를 로컬 JSON으로 내려받으며 외부 전송·AI 호출을 하지 않는다. 탭 자동 수집은 최대 24회·전체 45초, 기본 탭별 5초 상한이다. 도우미는 같은 출처 iframe에 접근하며, 공개 URL 프로그램 수집은 공개된 다른 출처 프레임도 별도로 처리한다. iframe 깊이 3, 스크롤 영역 8개·영역당 32회, 총 64상태/40MB 상한이다. canvas 픽셀·미관측 탭·모든 탭 조합·끝없는 목록의 완결은 자동 인증하지 않는다. 비활성·외부 이동·form 제출 제어는 제외한다. 도우미를 실행할 페이지·열 탭은 사용자가 정한다.

## 공개 URL에서 원본부터 자동 수집

```text
python3 scripts/run.py convert 공개URL --dynamic --out 새_결과폴더
python3 scripts/run.py convert 공개URL --dynamic --tab-selector '[role="tab"]' --tab-wait-ms 10000 --out 새_결과폴더
```

프로그램의 동적 수집을 선택하면 탭 자동 수집이 기본으로 켜진다. `--no-auto-tabs`로 끄면 초기 로딩만 수집한다. 기존 공개 주소·GET/HEAD·WebSocket 차단·로그인 정보 미가져오기 경계를 유지한다. 내려받은 로컬 HTML 스크립트를 실행하는 옵션이 아니다. 비표준 선택자는 실제 탭 구조에만 지정한다.

`dom_capture.automatic_tabs.outcomes`와 `assets/*automatic-tabs.json`의 captured/already-selected/scroll-end/frame-recovered/skipped/timeout-or-unchanged/frame-error/scroll-limit/error/limit를 구분한다. 시간 초과·오류·상한 초과는 `automatic_tab_incomplete`와 CLI 종료 2로 남기며 저장된 원본·부분 결과는 보존한다. DOM 변화·안정 시간과 aria-busy로 본문 로딩을 기다리므로 서버 상태 전체의 완료 인증과 다르다.

## 에이전트 사용

현재 허용된 브라우저 도구로 실제 본문이 나타난 뒤 DOM을 관측한다. 브라우저 조작 권한·전용 도구 제약을 따른다. JSON을 직접 구성할 때도 html은 관측한 DOM 그대로 사용하고 원본 해시는 실제 입력 바이트로 계산한다. 다음 스키마이며 HTML 바이트와 짝이 다르면 출력 생성 전에 거부된다.

```json
{
  "schema_version": 1,
  "source_sha256": "원본 HTML 바이트의 SHA-256",
  "source_url": "관측한 페이지 URL",
  "captured_at": "ISO 8601 시각",
  "frames": [
    {"state": "initial", "label": "첫 화면", "url": "관측 URL", "frame_index": 0, "html": "실제 DOM"},
    {"state": "tab-second", "label": "둘째 탭", "url": "관측 URL", "frame_index": 0, "html": "클릭·로딩 뒤 실제 DOM"}
  ]
}
```

## 검증

- `structure.json.metadata.dom_capture`의 입력 해시·상태 수·DOM 자산 해시를 읽는다. 원본 HTML·정적 추출 JSON·제공 스냅샷·상태별 원본 DOM을 함께 보관한다.
- MD에는 상태를 순서대로 별도 단위로 넣는다. 원본 ID와 상태별 앵커 대응은 `unit.metadata.anchor_map`에 기록한다. CSS 선택자는 관측 DOM의 원래 ID에 적용한다.
- 원래 실패한 본문이 실제 MD 렌더 텍스트에 있는지 대조한다. 스크립트 코드 보관만으로 본문 보존을 인정하지 않는다. 저장 무결성 검사와 DOM 텍스트 왕복 검사는 별개로 읽는다.
- 스냅샷 해시는 파일 짝·무결성을 확인한다. 관측자의 신뢰·사이트 출처·미관측 탭의 전체성을 인증하지 않는다. 자동 `partial` 상태와 CSS·시각 검토 경고를 유지한다.
