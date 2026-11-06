# 손글씨 비교·오인식 저감 — 0.9.2

2026-11-07, 한글 손글씨 6페이지·영문 손글씨 16줄·인쇄 대조군 2개를 같은 입력과 정답으로 비교했다. **영문 고정 평가 8줄의 오류율은 43.48%에서 15.72%로 줄었다. 한글 고정 평가 4페이지는 28.08%로 같았다.** 한글 손글씨 전체를 해결했다고 해석하지 않는다.

기본 OCR은 계속 macOS Apple Vision이다. 비교에 사용한 EasyOCR·Tesseract·별도 손글씨 모델들은 프로그램의 필수 의존성에 추가하지 않았다. 개선된 기본 프로그램·CLI·스킬은 같은 엔진을 사용한다.

## 실제 프로그램의 전후 결과

CER은 NFC 정규화 후 공백만 제거한 Unicode 코드포인트 편집 거리/정답 글자 수다. 대소문자·문장부호·숫자를 유지하며 **낮을수록 좋다**. 삽입도 포함하므로 `100-CER`을 정확도라고 부르지 않는다.

| 언어·용도 | 표본 | 정답 글자 수 | 0.9.1 오류 수/CER | 0.9.2 오류 수/CER |
|---|---:|---:|---:|---:|
| 한글 손글씨 개발 자료 | 2페이지 | 755 | 59 / 7.81% | 57 / 7.55% |
| 한글 손글씨 고정 평가 | 4페이지 | 1,282 | 360 / 28.08% | 360 / 28.08% |
| 영문 손글씨 개발 자료 | 8줄 | 325 | 86 / 26.46% | 29 / 8.92% |
| 영문 손글씨 고정 평가 | 8줄 | 299 | 130 / 43.48% | 47 / 15.72% |
| 인쇄 대조군 | 2개 | 904 | 20 / 2.21% | 19 / 2.10% |

24개 입력별로 개선 15개·동일 9개·악화 0개였다. 언어·페이지/줄의 난이도가 다르므로 이를 합쳐 일반적인 손글씨 정확도로 광고하지 않는다. `ko-02`에서는 영역 재인식이 원래 문장 뒤에 글자를 더하던 오류 2개를 막았다. 한글 고정 평가의 글자 인식 자체에는 개선이 확인되지 않았다.

## 다른 OCR과 같은 자료 비교

다음은 고정 평가 부분만 집계했다. 모든 정답·입력은 동일하며, 같은 자료의 실패도 점수에서 빼지 않았다.

| 엔진·설정 | 한글 4페이지 CER | 영문 8줄 CER |
|---|---:|---:|
| Source MD 0.9.1 · Apple Vision 기본/영역 재인식 | 28.08% | 43.48% |
| Source MD 0.9.2 · 기본 보완 포함 | 28.08% | 15.72% |
| Tesseract 5.5.4 · tessdata_best, kor+eng | 92.82% | 86.29% |
| EasyOCR 1.7.2 · ko+en, CPU, greedy | 55.15% | 66.22% |
| ko-hand-ocr 0.5.0 · 자체 사진 분할 | 86.74% | 별도 집계하지 않음 |
| korean-handwriting-rec · 같은 Vision 줄 영역 | 36.43% | 한국어 모델 비교만 수행 |

Tesseract는 OEM 1, 페이지 PSM 6, 이미 잘린 영문 줄 PSM 7이다. EasyOCR는 CPU 4스레드이며 이미 준비한 모델로 실행했다. Apple Vision은 macOS 27.2 arm64, revision 3, accurate다. 시스템 모델은 OS에 종속된다.

추가 한국어 모델도 고정 설정으로 시험했다. ko-hand-ocr은 같은 Vision 영역을 썼을 때 91.50%, 자체 페이지 분할을 썼을 때 86.74%였다. 기본 디코더와 홀자모 보존 설정도 확인했다. korean-handwriting-rec의 두 이미지 후보가 일치할 때만 기존 Vision 결과를 바꾸는 시험은 28.86%로, 기존 28.08%보다 나빴다. 이 비교에서는 두 모델 모두 기본 엔진으로 채택하지 않았다. 해당 모델들의 다른 자료·용도 성능에 대한 결론은 아니다.

시간은 동시 실행·초기화·캐시 영향을 받으므로 이 시험으로 속도 순위를 정하지 않았다. 외부 OCR/생성형 AI API에 입력 이미지를 보내지 않았고 모델을 추가 학습하지 않았다.

## 수정한 동작

1. 줄 재인식의 여백은 인식 맥락으로만 사용한다. 후보의 좌표를 실제 배율·테두리에서 원본으로 역변환하고, 원래 줄 밖에 중심이 있는 글자 조각을 선택하지 않는다.
2. 같은 문장을 그대로 포함하면서 앞뒤에 글자를 더한 잘라내기 후보는 자동 반영하지 않는다. 여러 번 같은 결과가 나와도 같은 이웃 조각을 반복 인식한 것일 수 있다. 처음 줄과 제안 후보는 검토용 JSON에 남는다.
3. 불확실하고 짧고 가로로 긴 Latin 줄은 영어 전용 인식의 원본·높이 128·높이 192 이미지 후보 3개를 비교한다. 이 후보들은 사전 보정을 끈다. 후보 간 정규화 편집 거리 합이 가장 작은 **실제 후보 하나**를 선택하며 새로운 문장을 합성하지 않는다. 원본 이미지 좌표로 돌려놓고 후속 재인식에도 선택 언어를 전달한다.
4. 원래 인식·모든 후보·변환·선택·불일치·실패를 `initial_recognition`, `latin_line_retry`, `refinement`에 보관한다. 후보 실패/요청·시간 상한은 처음 인식을 보존하고 미완료로 보고한다. 일치·신뢰도·후보 선택을 정확도 인증으로 승격하지 않는다.

한글·숫자만 있는 줄, 확실한 영문, 큰 페이지에는 영문 줄 보완을 적용하지 않는다. 아무 글자도 인식하지 못한 입력에서 문장을 추측해 만들지도 않는다. 영문 평가 `en-1300`은 여전히 미인식으로 남아 CER 100%다. 원본과 후보를 보고 필요한 부분을 직접 교정해야 한다.

## 자료·정답·평가의 범위

- 한국어는 [교사 전사 정답이 공개된 연구 자료](https://github.com/jkf87/korean-handwriting-ocr-benchmark)의 8개 이미지다. 눈으로 확인되는 인쇄 책 페이지 `03`과 출처가 인쇄물로 분류한 `05`는 인쇄 대조군으로 분리했다. 실제 손글씨는 `01,02,07,08,09,10`의 6페이지다. 개발 `01,02`, 고정 평가 `07,08,09,10`이다.
- 영어는 [IAM-line](https://huggingface.co/datasets/Teklia/IAM-line)의 test split에서 개발 행 `0,200,...,1400`, 평가 행 `100,300,...,1500`을 고정했다. 손글씨 글꼴로 만든 그림은 실제 필기로 세지 않았다.
- 원출처 `ground_truth`만 정답으로 사용했다. 원출처의 기존 OCR 출력으로 변환하거나, 정답을 인식 함수에 전달하거나, 특정 단어 치환 규칙을 만들지 않았다.
- 전체 이미지에 보이는 인쇄된 `No.`·수첩 표식·교사 메모 등이 정답 주석에 없을 수 있다. 주석의 맞춤법 정규화·누락·읽기 순서 차이도 엄격한 전체 페이지 CER에 섞인다. 점수를 높이기 위해 원문 내용을 지우지 않았다.
- 작은 진단 표본이며 필자별 독립 분리를 확인한 시험은 아니다. 학습 자료 중복도 배제하지 못한다. 문서/줄의 길이·난이도가 달라 문자수 가중 CER과 입력 개별 결과를 함께 공개한다.
- 첫 보완 정책은 개발 자료에서 정해 고정 평가를 실행했다. 이후 개발 사례에서 발견한 글자 덧붙임 안전장치 회귀에 같은 평가 입력을 재사용했다. 최종 수치를 새로 봉인한 통계적 검증이나 모든 문서의 정확도 인증으로 설명하지 않는다.
- 제3자 이미지·정답·전체 인식문은 저장소/배포본에 포함하지 않는다. [원출처·분할·해시 목록](../examples/handwriting-inputs/sources.json)과 [입력별 점수·가설 해시](HANDWRITING_RESULTS.json)를 제공한다. 자료 이용 조건은 각 원출처를 따른다.

독립 스킬 실행에서 원본 사본·이미지 픽셀·등록 파일 해시·실제 MD·OCR 후보·validate를 재조회했다. 독립 채점자가 원자료 전체를 DP와 Myers bitvector 두 알고리즘으로 재계산해 NFC/NFD 거리 96개를 대조했고, 기존 문자수·오류수 48행과 차이가 없었다. NFD 자모 지표도 JSON에 별도로 보관한다. 수치가 일관됨과 오인식이 없음은 다른 판정이다.

## 재현

기본 프로그램 설치 후, 새 출력 폴더를 사용한다. 자료 준비에는 원출처 접근을 위한 인터넷이 필요하다.

```sh
sh install.sh --dev
uv run --frozen python scripts/benchmark_handwriting.py prepare --out outputs/handwriting-data
uv run --frozen python scripts/benchmark_handwriting.py run --data outputs/handwriting-data --out outputs/handwriting-current --engine program
```

위 준비·실행 경로로 24개 입력을 다시 내려받고 SHA-256을 대조해 실제 변환했다. 실행기의 Python 가상환경 링크를 풀어 기본 Python으로 잘못 바꾸지 않는 회귀도 확인했다. 과거 버전과 비교하려면 같은 스크립트에 별도 설치한 이전 버전의 `--program-python /absolute/path/to/old/.venv/bin/python`을 지정한다.

원시 Vision은 `--engine vision`, Tesseract는 `--engine tesseract --tessdata /absolute/path/to/tessdata_best`로 실행한다. EasyOCR는 별도 시험 환경에 `easyocr==1.7.2`와 Source MD를 설치하고 모델을 명시적으로 준비한 뒤 `--engine easyocr --models /absolute/path/to/models`로 실행한다. 인식 중 자동 모델 다운로드를 하지 않는다. 이 비교 도구용 추가 패키지는 기본 프로그램 설치 조건이 아니다.

데이터 원문이 바뀌면 해시 불일치로 중단한다. 새 점수에 맞춰 정답·기준선을 자동 갱신하지 않는다. 전체 인식문이 들어 있는 `outputs`를 공개 저장소에 추가하지 않는다.

## 추가 모델 출처

- [ko-hand-ocr 코드](https://github.com/jysvai/ko-hand-ocr), [모델·판본](https://huggingface.co/localdeel/ko-hand-ocr). 단일 모델, CPU 4스레드, beam 5, 128 토큰 상한. 사진 분할은 원 패키지의 고정 알고리즘과 최대 64줄, 인식 묶음 4개다.
- [korean-handwriting-rec](https://github.com/VerifiedIdiot/korean-handwriting-rec). ONNX CPU 2스레드, 같은 Vision 줄 상자에서 1픽셀 여백, 높이 48로 비율 보존, BGR 정규화·CTC 디코딩. 추가 합의 시험은 흰 테두리 4픽셀 변형과의 일치 여부만 사용했다. 모델 파일 해시·실행 버전은 수치 JSON에 기록했다.

두 모델의 가중치·학습 자료는 배포본에 포함하지 않는다.

추가 모델 비교도 `scripts/benchmark_handwriting_extra.py`로 같은 8개 한국어 입력에서 재실행했다. 별도 연구 환경에 ko-hand-ocr 0.5.0 또는 numpy·onnxruntime을 설치하고, 위 출처의 모델을 직접 준비한다. `--baseline`은 같은 입력에서 생성한 프로그램 번들 폴더이며 `--model`은 준비한 모델 디렉터리다. 자동 다운로드·학습은 하지 않는다.

```sh
python scripts/benchmark_handwriting_extra.py --data outputs/handwriting-data --baseline outputs/handwriting-old --engine ko-hand --model /absolute/path/to/ko-hand-model --out outputs/ko-hand-comparison
python scripts/benchmark_handwriting_extra.py --data outputs/handwriting-data --baseline outputs/handwriting-current --engine korean-ctc --model /absolute/path/to/korean-ctc-model --out outputs/korean-ctc-comparison
```

119개 회귀 테스트와 새 가상환경의 설치 배포본 검사 20개가 통과했다. 기존 표 4개의 56개 셀은 값·행열·병합·빈칸이 모두 일치했고, 비표 대조군 28개에서 거짓 표가 검출되지 않았다. 이 표 시험은 고정된 시험 자료의 결과다.
