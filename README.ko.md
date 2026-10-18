<div align="center">

<h1><img src="https://raw.githubusercontent.com/ntts9990/underwrite/main/assets/brand/underwrite-logo.png" alt="underwrite" width="640"></h1>

### 평가 파일로 무엇을 말할 수 있는지 확인하세요.

불일치를 찾고, 빠진 근거를 드러내고, 다음에 확인할 일을 정합니다.

**로컬 CLI와 Python 라이브러리 · JSON 결과 · 서버나 모델 호출 없이**

[![CI](https://github.com/ntts9990/underwrite/actions/workflows/ci.yml/badge.svg)](https://github.com/ntts9990/underwrite/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/underwrite)](https://pypi.org/project/underwrite/)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue)](https://pypi.org/project/underwrite/)
[![라이선스: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/ntts9990/underwrite/blob/main/LICENSE)

**[직접 실행하기](#빠른-시작)** · [에이전트와 사용하기](#에이전트-사용) · [지원 형식](#지원-형식) · [문서](#문서)

[English](https://github.com/ntts9990/underwrite/blob/main/README.md) · **한국어**

</div>

평가 도구는 평가를 실행하고 결과 파일을 만듭니다. underwrite는 지원하는 로컬 파일을
읽어 파일에서 확인되는 내용, 아직 알 수 없는 점, 다음에 확인할 근거를 정리합니다.
예를 들어 Promptfoo가 보고한 집계와 제공된 결과 행이 일치하는지 확인합니다.
사람이나 코딩 에이전트는 JSON 결과를 보고 조사하고 결정합니다. 선택한 원본
파일과 결과를 함께 보관해 나중에 다시 확인할 수도 있습니다.

underwrite는 평가를 다시 실행하거나, 생성 도구의 판정을 독립적으로 증명하거나,
여러 도구의 결과를 하나의 품질 점수로 합치지 않습니다. 지원되는 로컬 파일을
검사하는 데 Docker, 대시보드, 제공자 인증 정보는 필요하지 않습니다.

> [!NOTE]
> **사용 경로를 선택하세요:** 배포된 기능은 [PyPI 0.1.1](#배포판--pypi-011),
> 새 자동 입력 처리와 실행 단계 비교는 [현재 main 체크아웃](#개발본--현재-main-체크아웃)을
> 사용합니다. 둘 다 현재 `0.1.1`로 표시되므로 `capabilities --json`으로
> 실행 파일이 실제로 지원하는 기능을 확인하세요. 이전 `0.0.1`의 핀 매니페스트
> 주의 사항은 [설치](#설치)에 있습니다.

## 빠른 시작

**Python 3.12 이상**과 [uv](https://docs.astral.sh/uv/)가 필요합니다. 아래의 선택형
합성 예제 다운로드에는 `curl`을 사용합니다.
파일 기반 CLI 작업은 Linux와 macOS의 CPython 3.12에서 테스트했습니다.
일반 아티팩트 읽기를 포함해 Unix/POSIX 파일 API가 필요합니다.

### 배포판 · PyPI 0.1.1

**1. 설치하고 파일을 검사합니다.** 앱만 설치해도 호환되는 `underwrite-core`가
함께 설치됩니다. 공개 합성 예제를 다운로드하세요. 체크아웃이나 API 키는 필요하지 않습니다.

```sh
uv tool install 'underwrite==0.1.1'
underwrite capabilities --json
example_dir=$(mktemp -d)
curl -fsSLo "$example_dir/promptfoo.json" \
  https://raw.githubusercontent.com/ntts9990/underwrite/cb913d39bce628b93dcd3b06fcc4ce36f9dc66af/fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.json
underwrite audit-promptfoo "$example_dir/promptfoo.json" \
  --version 0.123.0 --json
```

같은 `uv tool install` 명령으로 uv가 설치한 이전 `underwrite` 도구도
`--force` 없이 갱신됩니다. 셸에서 `underwrite`를 찾지 못하면
`uv tool update-shell`을 실행하고 새 셸을 여세요.

**2. 발견 사항을 읽습니다.** 생성 도구의 요약은 **결과 3개**와 **실패 1개**를
선언하지만, 파일에는 **행 2개**와 **실패 행 0개**가 있습니다. 명령은 어느 쪽이
잘못됐는지 추측하지 않고 차이를 보고합니다. 다음은 실제 출력에서 고른 필드입니다.

```json
{
  "schema": "promptfoo_accounting.v1",
  "declared_relation": "inconsistent",
  "producer_stats": {"errors": 1, "failures": 1, "successes": 1, "total": 3},
  "row_tallies": {"errors": 1, "failures": 0, "successes": 1, "total": 2},
  "deltas": {"errors": 0, "failures": -1, "successes": 0, "total": -1},
  "findings": [
    {"code": "CATEGORY_COUNT_MISMATCH", "location": "/results/stats/failures", "row_index": null}
  ],
  "limitations": [
    "PLANNED_DENOMINATOR_UNKNOWN",
    "CAUSE_UNKNOWN",
    "SOURCE_CLAIMS_UNVERIFIED",
    "NO_APPROVAL_AUTHORITY"
  ]
}
```

**3. 다음을 확인합니다.** 실행 기록·계획된 사례 목록과 내보내기 파일을
대조하세요. 그전에는 실행이 누락됐는지, 실패 행이 빠졌는지 단정할 수 없습니다.
`CAUSE_UNKNOWN`과 `PLANNED_DENOMINATOR_UNKNOWN`이 이 한계를 드러냅니다.
종료 코드 `0`은 보고서가 생성됐다는 뜻이지 집계가 일치하거나 원천 판정이
검증됐다는 뜻이 아닙니다. 입력과 분석을 함께 보관하려면
[로컬 기록으로 저장하세요](#다시-찾아볼-수-있는-결과-보관).

직접 만든 완료된 Promptfoo 내보내기 파일에는 로컬 경로와
[배포판이 지원하는 프로필](#지원-형식)을 사용하세요. 다운로드한 파일은 구성한
합성 예제이며, 생성 도구의 원본 내보내기 파일은 아닙니다.

### 개발본 · 현재 main 체크아웃

새 작업 흐름은 체크아웃에서 사용하세요. 배포판 설치와 구분되도록 해당 디렉터리
안에서 `uv run`으로 실행합니다. 이미 체크아웃이 있다면 저장소 루트에서 아래
`uv sync`부터 실행하세요.

```sh
git clone https://github.com/ntts9990/underwrite.git
cd underwrite
uv sync --frozen --all-groups
uv run --frozen --no-sync underwrite capabilities --json
uv run --frozen --no-sync underwrite audit-promptfoo \
  fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.json --json
```

위 배포판 예제와 같은 합성 집계인 선언 결과 3개와 행 2개를 대조합니다.
자동 reader는 `promptfoo_accounting.v2`를 만들고, 이 fixture에 선언된 `0.123.0`을
검증되지 않은 원천 주장으로 보존합니다. 버전 선언이 없는 입력에서는 버전을
알 수 없는 상태로 유지합니다. 이 예제에는 버전 옵션, 생성 도구 설치, Goppi,
제공자 호출이 필요하지 않습니다.
체크아웃 명령에는 항상 `uv run --frozen --no-sync underwrite` 접두사를 사용하세요.
다른 경로는 [개발본 추가 기능](#개발본--현재-main의-추가-기능)에 있습니다.

<details>
<summary><strong>네이티브 측정 → 수락 분류 흐름 실행하기</strong></summary>

위에서 체크아웃을 준비했다면 저장소 루트에서 실행하세요. 이 예제의 정책과
fixture는 저장소에 있으며 설치된 패키지에는 포함되지 않습니다.

```sh
mkdir -p output
uv run --frozen --no-sync underwrite ingest \
  --format underwrite.evidence-bundle --version v1 \
  --file fixtures/examples/event-measurement/bundle.json --json \
  > output/observation.json

uv run --frozen --no-sync underwrite measure \
  --observation output/observation.json \
  --policy fixtures/examples/event-acceptance/policy.json --json \
  > output/read-event.json

uv run --frozen --no-sync underwrite accept \
  --candidate fixtures/examples/event-acceptance/candidate-single.json \
  --claims fixtures/examples/event-acceptance/claims.json \
  --read output/read-event.json --json
```

이 예제는 합성 이진 사건의 비율을 측정하며, 일반 품질이나 지연 시간을 측정하지 않습니다.
`read.v2`는 명시적인 `measurement_policy.v3`을 사용하고, 보정 진단값은 판정에
투표하지 않습니다. 최종 분류는 `screened`이며, 사람의 검토가 필요하고
`merge_authorized`는 false로 유지됩니다. 필요한 주장마다 `--read`를 반복해 전달하세요.
[측정의 의미](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#measurement-meaning-and-migration)를 참고하세요.

</details>

## 왜 underwrite인가요?

| 평가 후 궁금한 점 | underwrite가 제공하는 것 |
| --- | --- |
| 요약과 제공된 기록이 일치하나요? | Promptfoo의 선언 집계와 행을 대조합니다. DeepEval의 점수·평가기 오류와 Langfuse 점수 연결 관계는 별도로 집계하며, 이들의 요약 집계까지 검증했다고 간주하지 않습니다. |
| 무엇이 아직 알려지지 않았나요? | 계획된 사례가 알려지지 않았거나 원천 주장을 검증하지 못했음을 드러냅니다. 알 수 없는 값을 0으로 세지 않습니다. |
| 이 근거로 측정할 수 있나요? | 별도의 네이티브 이진 사건 경로에서 선언한 사건과 정책에 따라 측정합니다. 외부 점수를 범용 품질 판정으로 쓰지 않습니다. |
| 나중에 결론을 다시 확인할 수 있나요? | 선택한 입력 바이트와 분석을 비공개 기록에 보관하고 나중에 검증·조회할 수 있습니다. |

## 작동 방식

- **평가 도구:** 평가를 실행하고 결과 파일을 내보냅니다.
- **underwrite:** 제공된 결과를 검사하고 확인한 내용과 부족한 근거를 JSON으로
  정리하며, 선택한 원본 파일과 결과를 함께 보관할 수 있습니다.
- **사람이나 에이전트:** 부족한 근거를 확인하고 다음 작업을 결정합니다.

각 명령은 선택한 입력 프로필이 지원하는 범위만 검사합니다. 원천 판정은 여전히
원천의 주장이고, underwrite는 변경을 적용하거나 병합 권한을 부여하지 않습니다.
별도의 네이티브 이진 사건 경로는 명시한 사건과 정책 아래에서만 측정하며, 임의의
외부 점수를 측정값으로 바꾸지 않습니다.

## 에이전트 사용

코딩 에이전트에 완료된 로컬 내보내기 파일과 선택한 사용 경로를 알려주세요.
배포판은 `underwrite`, 체크아웃은 해당 디렉터리의
`uv run --frozen --no-sync underwrite`로 `capabilities --json`과 명령의
`--help`를 확인하게 하세요. 패키지 버전만으로 지원 기능을 판단하지 않습니다.

Python에서는 선택한 환경의
`underwrite.instrument.ingest.accounting.audit_promptfoo_file(Path(...))`를
사용할 수 있습니다. 배포판에서는 입력이 지원 프로필과 일치하는지 확인하고,
문서의 기본값과 다를 때 프로필을 명시하세요. 현재 자동 입력 경로에서는
생성 도구의 버전을 추측할 필요가 없습니다.

터미널을 사용할 수 있는 에이전트에게 다음과 같이 요청할 수 있습니다.

> 선택한 Underwrite 사용 경로로
> `/absolute/path/to/completed-promptfoo-export.json`을 검토하세요.
> capabilities와 도움말을 확인하고 지원되는 집계 경로를 사용하세요.
> 자동 입력이 지원되면 그 경로를 사용하고, 그렇지 않으면 버전을 추측하지 말고
> 배포판이 지원하는 프로필임을 확인할 근거를 요구하세요.
> 집계 차이, 입력 식별 정보, 부족한 근거, 해석 한계와 다음 확인 작업을 보고하세요.
> 생성 도구를 다시 실행하거나 종료 코드 `0`을 일치 또는 승인으로 해석하지 마세요.

선택 설치형 [underwrite-review 스킬](https://github.com/ntts9990/underwrite/blob/main/skills/underwrite-review/SKILL.md)은 Codex와
Claude Code에서 재사용할 수 있는 지침을 제공합니다.
[설정과 사용 안내](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#optional-artifact-review-skill)에 따라 사용하며,
스킬은 PyPI 패키지가 아니라 저장소에 있습니다. 스킬이 CLI를 설치하거나
백그라운드 서비스를 추가하지는 않습니다.
직접 만든 자동화에서도 같은 CLI와 가져와 사용할 수 있는 Python API를 이용할 수 있습니다.

## 지원 형식

지원 범위는 선택된 로컬 페이로드이며, 각 도구의 모든 API·내보내기·버전을
뜻하지 않습니다. 선택한 실행 파일의 capabilities와 연결된 안내에서 정확한
입력 요구사항을 확인하세요.

### 배포판 지원 프로필 · PyPI 0.1.1

| 입력 | 지원 프로필 | 시작 경로 |
| --- | --- | --- |
| **Goppi** | 완료된 baseline exchange manifest v2; P7 paired handoff | [`audit-baseline`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#audit-a-completed-goppi-baseline-as-common-evidence) · [`project-baseline`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#project-a-completed-goppi-baseline-into-event-observations) · [`review-handoff`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#review-a-completed-paired-handoff-in-one-call) |
| **Langfuse** | `4.35.0` 서버 blob 배열 | [`audit-langfuse`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-langfuse-score-kinds-and-associations) |
| **DeepEval** | 일반 TestRun, `4.1.1`(기본값) 또는 명시적 `4.2.8` | [`audit-deepeval`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-deepeval-scores-and-evaluator-errors) |
| **Promptfoo** | Eval output, `0.123.0`(기본값) 또는 명시적 `0.124.1` | [`audit-promptfoo`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#audit-promptfoo-counts) |
| **외부 공통 결과** | `evidence_result.v1` | [`inspect-result`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#inspect-an-external-common-result) |
| **네이티브 근거** | `underwrite.evidence-bundle` / `v1` | `ingest` → `measure` → `accept` |
| **Inspect AI** | `inspect.eval-log` / `0.3.263` | `ingest` |
| **OpenInference** | `openinference.traces` / `0.1.38` | `ingest` |
| **OTLP JSON** | `otlp-json.traces` / `0.160.0` | `ingest` |

### 개발본 · 현재 main의 추가 기능

- **자동 입력:** 호환되는 Promptfoo SummaryV3, DeepEval TestRun, Langfuse blob
  배열 또는 [공식 CLI 목록 캡처](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#read-official-langfuse-cli-captures-offline)를
  생성 도구 버전·프로필 선택 없이 읽습니다. 페이로드에서 실제 배포 버전을
  확인할 수 없으면 알 수 없는 상태로 두며, 이전 계약 재현용 선택자는 유지합니다.
- **목적별 보기와 저장:** 선택된 DeepEval trace 구조, 생성 도구 버전에 독립적인
  `deepeval_metric_bindings.v2` 팩, Langfuse 행 인덱스 저장을 제공합니다.
  각 계약과 한계는 위 생성 도구별 안내를 따르세요.
- **공통 결과:** `inspect-result`가 `evidence_result.v2`도 받습니다.
- **Goppi 단계:** [`audit-trajectory`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#compare-a-goppi-paired-trajectory)는
  조기 중단된 실행을 포함해 paired+trajectory 내보내기의 도구 상태와 평가 결과를
  비교합니다. 이 선택형 경로는 오프라인 검증을 위해 설치된 Goppi가 필요합니다.
  제공자를 실행하거나 인과관계를 확립하거나 재시도 효과를 측정하지 않습니다.
  [`records audit-trajectory`](https://github.com/ntts9990/underwrite/blob/main/STORAGE.md#retain-a-goppi-paired-trajectory)로
  같은 입력과 결과를 보관하고, `records read-trajectory`로 원본 내보내기와
  Goppi 없이 다시 확인할 수 있습니다.

fixture 출처와 검사 범위는
[아티팩트 요구사항](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#get-the-right-producer-artifact)과
[생성 도구 검증](https://github.com/ntts9990/underwrite/blob/main/EVIDENCE_REVIEW.md)에 있습니다.
통제된 fixture는 실제 서버와의 상호 운용성이나 내보내기 완전성을 증명하지 않습니다.

배포된 0.1.1에서 이동한다면 [마이그레이션 안내](https://github.com/ntts9990/underwrite/blob/main/MIGRATION.md)에서
기본 JSON 계약 변경, 기존 선택자와 저장 기록의 유지 방법을 확인하세요.

범용 `ingest` / `project`는 지원되는 내보내기를 정규화하며, 외부 관측은 현재
`measure` 경로로 이어지지 않습니다. Goppi `audit-baseline`은 설명용 공통 근거를,
`project-baseline`은 네이티브 사건 관측을 만들 수 있습니다. `inspect-result`는
설치된 공통 결과 계약과 항목 수를 검사할 뿐, 원천의 진위를 검증하거나 측정·수락
경로로 연결하지 않습니다. 조치 보기는 실행된 도구가 아닌 기록된 다음 확인 작업을 셉니다.

## 다시 찾아볼 수 있는 결과 보관

분석과 선택한 원본 바이트를 함께 보관하려면 `records`를 사용하세요.
다음은 완료된 로컬 Promptfoo 내보내기 파일을 **배포판**으로 저장하는 예제입니다.

```sh
underwrite records audit-promptfoo /path/to/promptfoo-output.json \
  --version 0.123.0 --root ./review-store --id review-001 --json

underwrite records read-json \
  --root ./review-store --id review-001 \
  --name analysis/accounting.json --json
```

체크아웃에서는 두 명령 앞에 `uv run --frozen --no-sync`를 붙이고, 자동 입력에는
`--version`을 생략하세요. 캡처할 때마다 새 ID를 사용하며 기존 기록은 덮어쓰지 않습니다.
`records verify`는 저장된 데이터의 무결성을 검사합니다. `records measure`는
네이티브 측정을 캡처하고, `records replay-measure`는 검증된 저장 입력을 현재 엔진으로
다시 계산합니다. 원래 생성 도구를 실행하지 않습니다. 완료된 Goppi baseline v2 디렉터리는
`records audit-baseline`으로 다섯 파일과 설명용 결과를 함께 보관할 수 있습니다.
[Goppi 기록 안내](https://github.com/ntts9990/underwrite/blob/main/STORAGE.md#retain-a-completed-goppi-baseline)를 참고하세요.

기록에는 비공개 내용이 담길 수 있는 원본 입력이 보관됩니다. 비공개 로컬 POSIX
파일시스템과 백업 전략을 선택하세요. `./review-store`는 시연용 위치이며 백업이
아닙니다. [저장 계약과 복구](https://github.com/ntts9990/underwrite/blob/main/STORAGE.md)를 참고하세요.

## 결과가 의미하는 것

underwrite는 서로 다른 종류의 근거를 구분합니다.

| 결과 | 확인할 수 있는 것 |
| --- | --- |
| 원천 집계 검사 | 제공된 행, 점수, 선언 요약 사이의 관계 |
| 바이트 해시와 기록 검증 | 선택한 바이트의 동일성과 무결성. 독립적인 출처 증명은 아님 |
| 네이티브 측정 | 제공된 사건 정의, 모집단, 통계 가정 아래의 결과 |
| 수락 분류 | 선언된 주장과 read에 따른 정책 결과. 병합이나 배포 권한은 없음 |

`not_measured`, `indeterminate`, `abstained`는 서로 다른 상태로 유지됩니다.
빠진 정보는 명시적으로 드러납니다. 원천 점수가 자동으로 보정된 확률이 되는 것은 아니며,
명령 실행에 성공했다고 배포가 승인되는 것도 아닙니다.

## 설치

버전을 지정해 앱을 설치하면 호환되는 코어가 의존성으로 선택됩니다. 설치된 프로필은
`underwrite --help`와 `underwrite capabilities --json`으로 확인하세요.

```sh
uv tool install 'underwrite==0.1.1'
underwrite --help
underwrite capabilities --json
```

같은 `uv tool install` 명령으로 uv가 설치한 이전 도구도 `--force` 없이
갱신됩니다. 실행 파일이 `PATH`에 없다면 `uv tool update-shell`을 실행하고
새 셸을 여세요.

가져와 쓰는 Python API에는 Python 3.12 환경을 사용하세요.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'underwrite==0.1.1'
.venv/bin/python - <<'PY'
from pathlib import Path
from underwrite.instrument.ingest.accounting import audit_promptfoo_file

result = audit_promptfoo_file(Path("/path/to/promptfoo-output.json"), version="0.123.0")
print(result["declared_relation"])
PY
```

설치된 배포물에는 실행 코드와 스키마가 들어 있습니다. 안내 문서, 합성 fixture,
선택형 에이전트 스킬, 기여 도구는 GitHub 저장소에 있으며 해당 예제를 실행하려면
체크아웃이 필요합니다.

<details>
<summary>이전 0.0.1과 핀 매니페스트 주의 사항</summary>

이전 공개판 [앱](https://pypi.org/project/underwrite/0.0.1/)과
[코어](https://pypi.org/project/underwrite-core/0.0.1/)에는 `0.1.0`의 측정,
집계 검사, 기록, handoff, `capabilities` 작업 흐름이 포함되어 있지 않습니다.
설치된 명령은 `--help`로 확인하세요. `0.1.0` 앱 wheel과 `0.0.1` 코어를
함께 사용하지 마세요.

공개된 `0.0.1`의 `pins refresh`에는 신뢰할 수 없는 핀 매니페스트를 사용하지 마세요.
통제된 로컬 감사에서 조작된 매니페스트로 핀 등록부 밖의 쓰기 가능한 파일이
덮어써지는 것을 재현했습니다. `0.1.0` 코드는 변경 전에 해당 경로를
거부하지만, 이전 `0.0.1` 배포물에는 이 수정이 없습니다. 재현된 사례에는 조작된
매니페스트와 쓰기 가능한 대상 파일이 필요했습니다.

</details>

## CLI

각 옵션은 `underwrite <command> --help`로 확인하세요. 위 작업 흐름 외에도
`inspect`는 아티팩트 매니페스트를 검사하고, `audit-counts`는 기대 해시가 담긴
매니페스트를 기준으로 Promptfoo 집계를 검사합니다. `check`는 릴리스 근거의 연결을,
`absence`는 모집단의 존재 여부를, `boundary scan`은 설정된 판정 어휘를 검사합니다.
`drift check` / `pins refresh`는 제공된 핀 등록부를 다룹니다.
각 명령 계열의 결과와 오류는 [JSON 계약](https://github.com/ntts9990/underwrite/tree/main/contracts/)에 정의되어 있습니다.

<a id="개발-체크아웃의-근거-검사-파일럿"></a>

## 문서

다음 안내 문서와 예제는 실행용 wheel 바깥의 GitHub 저장소에 있습니다. 설치된
패키지에는 CLI와 Python API에 필요한 스키마가 포함됩니다.

| 알아볼 내용 | 안내 |
| --- | --- |
| 에이전트 실행, 입력 선택, JSON 해석 | [에이전트 사용](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md) |
| 선언, 쌍대 근거, goppi handoff 비교 | [근거 작업 흐름](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#source-checkout-pilot-compare-declarations) |
| 저장, 검증, 조회, 재계산 | [로컬 기록](https://github.com/ntts9990/underwrite/blob/main/STORAGE.md) |
| 생성 도구 fixture와 검증 범위 | [생성 도구 근거](https://github.com/ntts9990/underwrite/blob/main/EVIDENCE_REVIEW.md) |
| CLI와 순수 계산 코어 확장 | [기여 안내](https://github.com/ntts9990/underwrite/blob/main/CONTRIBUTING.md) · [아키텍처 규칙](https://github.com/ntts9990/underwrite/blob/main/AGENTS.md) |

## 기여

재현 가능한 아티팩트, 구체적인 작업 흐름, 수정안을 제안해 주세요.
[이슈](https://github.com/ntts9990/underwrite/issues)에 생성 도구의 버전과 기대하는
결과를 적고, 예제는 민감한 정보를 제거한 것만 공유하세요. 로컬 preflight와 PR 검사는
[CONTRIBUTING.md](https://github.com/ntts9990/underwrite/blob/main/CONTRIBUTING.md), 비공개 취약점 신고는
[SECURITY.md](https://github.com/ntts9990/underwrite/blob/main/SECURITY.md)를 참고하세요.

<details>
<summary>유지관리자 안내: 향후 PyPI 릴리스</summary>

OIDC 배포를 사용하기 전에 GitHub 환경 `pypi`를 만들고 `main`으로 제한합니다.
**각** PyPI 패키지에 GitHub Trusted Publisher를 설정합니다. 소유자는 `ntts9990`,
저장소는 `underwrite`, 워크플로는 `publish.yml`, 환경은 `pypi`입니다.
설정 후 GitHub Actions에서 `main`의 **Publish PyPI**를 수동 실행합니다.

워크플로는 두 패키지를 검증한 뒤 코어, 애플리케이션 순서로 배포합니다. 별도 배포
작업에만 OIDC 권한이 부여되며, 저장된 PyPI API 토큰은 필요하지 않습니다. 일부만
업로드됐다면 동일한 아티팩트로 재시도합니다. 이미 등록된 파일은 정확히 일치해야 하며,
아티팩트가 바뀌면 새 버전이 필요합니다.

공식 [uv 배포 가이드](https://docs.astral.sh/uv/guides/package/)와
[PyPI Trusted Publisher 가이드](https://docs.pypi.org/trusted-publishers/using-a-publisher/)를 참고하세요.

</details>

## 라이선스

[Apache-2.0](https://github.com/ntts9990/underwrite/blob/main/LICENSE). 적용한 정책 템플릿의 CC 라이선스는
[LICENSING.md](https://github.com/ntts9990/underwrite/blob/main/LICENSING.md)에 기록되어 있습니다.
