# 구조 프로그램 (첫 판)

작성: 2026-11-16. 코드 기준: `1d344a8`. 상태: **측정과 관문 정의. 이행 조각은 아직 시작하지 않았다.**

## 0. 이 문서의 위치

- 기능 조각마다 하는 리팩터링 패스([maintainer delivery loop](../../CONTRIBUTING.md#maintainer-delivery-loop) 4단계)와
  별도로, 전체 구조를 기능 작업과 함께 옮기는 프로그램이다.
- 담는 것: 현재 구조 측정, 목표 경계, 줄어들기만 하는 래칫 장부, 순서, 멈출 조건.
- owner 기준은 [CONTRIBUTING.md의 Implementation owners](../../CONTRIBUTING.md#implementation-owners)와
  ADR-019·020·021을 따른다. 이 문서는 그 기준을 다시 쓰지 않고, 측정과 순서를 맡는다.
- 진전은 재현 가능한 지표로 말한다. 시험 수나 지운 줄 수는 목표가 아니다.

## 1. 현재 구조 측정

비어 있지 않은 줄 수 기준이다. 명령은 2절의 재현 방법에 적었다.

### 1.1 패키지

| 패키지 | 계층 | 파일 | 줄 |
| --- | --- | --- | --- |
| `instrument` (`ingest` 셸 + `evidence` 순수) | 혼합 | 64 | 15,577 |
| `cli` | 셸 | 43 | 7,260 |
| `service` | 셸 | 4 | 1,320 |
| `measurement` | 순수 | 7 | 1,149 |
| `fleet` | 셸 | 4 | 816 |
| `acceptance` | 순수 | 6 | 734 |
| `measurement_contract` | 순수 공유 계약 | 3 | 446 |
| `adjudication` | 순수(예약) | 1 | 0(빈 `__init__.py`) |
| `underwrite_core` | 핵심 | 9 | 1,100 |

`src/underwrite` 합계는 135파일, 27,385줄이다.

### 1.2 큰 모듈 (상위 8)

| 모듈 | 줄 |
| --- | --- |
| `instrument/evidence/goppi_p7_handoff.py` | 1,865 |
| `service/records.py` | 894 |
| `instrument/ingest/goppi_pilot.py` | 773 |
| `instrument/evidence/goppi_baseline.py` | 752 |
| `instrument/evidence/goppi_trajectory.py` | 710 |
| `cli/main.py` | 640 |
| `instrument/evidence/paired_binary.py` | 585 |
| `instrument/evidence/paired_review.py` | 535 |

### 1.3 경계

- `.importlinter` 계약 3개(core는 앱을 import하지 않음, 순수 계층은 stdlib와 core만, 5단 계층).
  `lint-imports` 결과: 144파일, 의존 483개, **계약 3개 유지, 위반 0**. `ignore_imports`는 0개이고
  `tests/gates/test_layers.py`가 그 부재를 확인한다.
- 하위 패키지 사이 import 순환: **0**(패키지 단위). 하위 패키지 간 간선 13종, import 지점 171개.
  가장 무거운 간선은 `cli → instrument` 112개다. `instrument`를 하나로 본 값이라 `ingest`와 `evidence`를
  나눈 그래프는 S0에서 다시 잰다. 순환과 간선 수는 일회성 AST 스크립트로 잰 값이고, 재현 스크립트는
  S0에서 만든다.
- 순수 계층 AST 검사: `tests/gates/test_layers.py`와 `packages/underwrite-core/tests/test_purity.py`가
  시계, 파일, 네트워크, 프로세스, 시드 없는 난수, 제3자 import를 막는다. 금지 집합이 두 시험에서 같은지도
  확인한다.

### 1.4 owner가 흩어진 곳 (후보)

| 신호 | 측정 | 비고 |
| --- | --- | --- |
| 같은 이름의 사적 helper | `_text` 17모듈, `_object` 16, `_identifier` 9(`cli/record_*.py` 7, `instrument/evidence/langfuse_*.py` 2), `_contracts` 8, `_analysis_text` 8, `_error` 8 | 이름이 같다고 같은 owner는 아니다. 의미를 먼저 비교한다 |
| 오류 코드 literal | 정규식 추정 약 380–410개, 약 97모듈 | [에이전트 표면 프로그램](AGENT_SURFACE_PROGRAM.ko.md) A1과 같은 대상. 기준값은 A0 스크립트가 정함 |
| goppi 소비 모듈 | `evidence` 4개, `ingest` 5개. 경로·한도·decode helper를 모듈마다 둠 | 형식마다 의미가 다르므로 공통 helper만 후보 |
| `cli/record_*.py` | 7모듈이 `_identifier`·`_contracts`·`_analysis_text`·`_files`를 반복 | `cli/record_capture.py`가 이미 일부 owner |
| 쓰이지 않는 상수 | `tests/gates/test_layers.py:16-18`(`INIT_CHECK_SCRIPT`, `EXEMPTIONS_PATH`, `PURE_LAYER_EXEMPTION_KEY`), `tests/unit/shell/test_cli_drift.py:24-25`(`EXEMPTIONS_PATH`, `SCHEMA_PATH`). 이 가운데 `scripts/init_check.py`와 `rules/exemptions.json`은 파일도 없음. `test_cli_drift.py:425` 주석도 없는 `rules/exemptions.json`을 가리킴 | S1에서 지운다 |
| 예약된 빈 계층 | `adjudication`은 빈 패키지지만 의도적으로 순수 계층 관문에 고정됨(`test_layers.py:30`, `:104-105`, `:253-265`의 미래 모듈 검사) | 유지. 은퇴하려면 ADR |

## 2. 재현 방법

```sh
uv run --frozen --no-sync lint-imports
# 패키지별 파일·줄 수
for d in src/underwrite/*/ packages/underwrite-core/src/underwrite_core/; do
  printf '%s ' "$d"; find "$d" -name '*.py' -exec grep -c . /dev/null {} + | grep -v '^/dev/null' \
    | awk -F: '{s+=$2; n++} END {print n, s}'
done
```

순환, 간선, helper 이름 집계는 S0에서 `scripts/` 아래 한 owner 스크립트로 옮기고, preflight가 그 결과를
래칫 장부와 비교하게 한다. 그 전까지 이 표의 숫자는 일회성 측정이다.

## 3. 목표 경계

1. **지금의 계층을 유지한다.** core → 순수 공유 계약 → 순수 도메인 → 공유 I/O → 셸(`ingest`, `service`,
   `fleet`) → `cli` 방향만 허용한다. 새 계층이나 DI 틀은 만들지 않는다.
2. **producer 형식은 순수 판정과 셸 읽기를 나눈 쌍으로 둔다.** 지금처럼 `evidence/<형식>`과
   `ingest/<형식>`을 짝으로 두고, 형식 사이 공통 부분은 의미가 같은 것만 공유 owner로 옮긴다.
3. **명령 모듈은 얇게 둔다.** `cli/*`는 입력 선택, 서비스 호출, 결과 내보내기만 한다. 판정, 오류 의미,
   저장 규칙은 아래 계층 owner에 둔다.
4. **오류 의미와 결과 투영은 owner 하나다.** 에이전트 표면 프로그램 A1과 같은 작업이다.
5. **큰 모듈은 의미 단위로 나눈다.** 줄 수 상한 자체가 목표는 아니다. 한 모듈이 둘 이상의 변경 이유를
   가질 때만 나눈다.

## 4. 관문과 래칫 장부

### 4.1 관문으로 승격하는 시험

다음 시험을 이 프로그램의 관문으로 둔다. 모두 이미 full preflight에서 돈다.

- `tests/gates/test_layers.py`: `lint-imports` 통과, `ignore_imports` 0, 순수 계층 AST 검사,
  금지 집합 일치
- `packages/underwrite-core/tests/test_purity.py`: core 순수성
- `scripts/preflight.py`의 `lint-imports`와 `deptry` 단계

관문을 약하게 바꾸는 변경(계약 삭제, `ignore_imports` 추가, 금지 집합 축소)은 ADR 없이 하지 않는다.

### 4.2 줄어들기만 하는 래칫 장부

| 지표 | 기준값(`1d344a8`) | 방향 | 관문 |
| --- | --- | --- | --- |
| `ignore_imports` 항목 | 0 | 0 유지 | 있음(`test_layers.py`) |
| import-linter 위반 | 0 | 0 유지 | 있음 |
| 하위 패키지 순환 | 0 | 0 유지 | S0에서 추가 |
| 쓰이지 않는 상수(1.4절) | 5 | 0으로 | S1에서 0 확인. 일반 검사는 S0 측정에 포함 |
| registry 밖 오류 코드 literal | 약 380–410(A0에서 확정) | 줄어들기만 | 에이전트 표면 A1 |
| 같은 의미의 중복 helper | S0에서 의미 검토 뒤 확정 | 줄어들기만 | S2 |

장부 값은 `scripts/`의 측정 owner가 내고, 늘어나면 preflight가 실패한다. 줄었을 때만 기준값을 낮춘다.
예외는 커밋에 근거를 적고 이 표를 같은 PR에서 고친다.

## 5. 순서

| 조각 | 내용 | 끝나는 조건 |
| --- | --- | --- |
| **S0 측정 owner** | 1절 측정을 한 스크립트로 옮김. `ingest`/`evidence`를 나눈 간선 그래프. 장부 비교를 preflight 선택기 안에 둠 | 같은 커밋에서 같은 값. 장부가 늘면 실패 |
| **S1 죽은 것 정리** | `test_layers.py`·`test_cli_drift.py`의 쓰이지 않는 상수와 주석 삭제 | 장부의 쓰이지 않는 상수 0. 관문 시험 통과 |
| **S2 helper 의미 검토** | `_text`·`_object`·`_identifier` 등을 의미로 분류. 같은 의미는 기존 owner(`_json`, `cli/record_capture.py` 등)로 옮기고, 다른 의미는 이름으로 구분 | 장부의 중복 helper 감소. 기존 CLI 출력 바이트 불변 |
| **S3 오류 registry** | 에이전트 표면 A1과 한 조각으로 진행 | registry 밖 literal 감소, 관문 |
| **S4 큰 모듈** | `goppi_p7_handoff.py`처럼 변경 이유가 둘 이상인 모듈만 의미 단위로 나눔 | 모듈마다 변경 이유 하나. 시험 통과, 출력 불변 |

S0과 S1은 작고 독립적이라 먼저 한다. S2와 S3은 같은 파일을 많이 열므로 갈래 영향표(병렬 조각마다 고칠 파일, 겹침, 통합 순서를 적는 표. CONTRIBUTING의 maintainer delivery loop 3단계)에서 통합 순서를 정한다.

## 6. 동작 보존 계약

- 구조 이행은 CLI stdout/stderr, 종료 코드, 스키마, 저장 기록의 바이트를 바꾸지 않는다.
  바꿔야 하면 그 조각은 구조 조각이 아니라 기능 조각이고, 이행 경로를 따로 정한다.
- 큰 이동 전에는 이동 대상의 공개 진입점에 회귀 시험을 먼저 둔다.
- 순수 계층 시험은 의존성 없는 환경(`.venv-pure`)에서도 계속 통과해야 한다.

## 7. 멈출 조건

다음이면 그 조각을 멈추고 범위를 다시 정한다.

- 의미가 다른 두 개념을 한 owner로 합쳐야만 진행된다.
- 관문을 약하게 해야만 통과한다.
- 측정 지표가 나아지지 않거나, 사용자 경로와 유지 비용에 이득을 설명할 수 없다.
