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
> **이 안내는 `underwrite==0.1.1`을 대상으로 합니다.** CLI와 Python 작업 흐름은 체크아웃 없이
> 사용할 수 있습니다. 이전 `0.0.1`에는 이 작업 흐름이 없고 `pins refresh`에
> 별도 주의 사항이 있습니다. [설치](#설치)를 참고하세요.

개발본의 capabilities에 `promptfoo.eval-output|auto`가 있으면
`underwrite audit-promptfoo FILE --json`으로 SummaryV3를 읽을 수 있습니다.
생성 도구 버전 옵션은 필요 없습니다. 이 자동 기본값은 배포된 0.1.1 이후의 기능이며,
[입력 안내](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#audit-promptfoo-counts)를 참고하세요.

## 빠른 시작

**Python 3.12 이상**과 [uv](https://docs.astral.sh/uv/)가 필요합니다. 아래의 선택형
합성 예제 다운로드에는 `curl`을 사용합니다.
파일 기반 CLI 작업은 Linux와 macOS의 CPython 3.12에서 테스트했습니다.
일반 아티팩트 읽기를 포함해 Unix/POSIX 파일 API가 필요합니다.

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

직접 만든 완료된 Promptfoo 내보내기 파일은 로컬 경로와 지원되는 원천 버전을 지정하세요.
예: `underwrite audit-promptfoo /path/to/export.json --version 0.123.0 --json`.
위 다운로드 파일은 구성한 합성 예제이며, 생성 도구의 원본 내보내기 파일은 아닙니다.

<details>
<summary><strong>네이티브 측정 → 수락 분류 흐름 실행하기</strong></summary>

아래 정책과 fixture는 GitHub 체크아웃에 있으며 설치된 패키지에는 포함되지
않습니다. 먼저 체크아웃을 준비한 뒤 합성 이진 사건 예제를 실행합니다.

```sh
git clone https://github.com/ntts9990/underwrite.git
cd underwrite
uv sync --frozen --all-groups

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

코딩 에이전트에 설치된 CLI와 완료된 로컬 내보내기 파일을 제공하세요. 에이전트는 사용 가능한
경로를 찾고 실행한 뒤 JSON을 답변에 활용할 수 있습니다.

```sh
underwrite --help
underwrite capabilities --json
```

Python에서는 `underwrite.instrument.ingest.accounting.audit_promptfoo_file(Path(...))`로
같은 결과를 딕셔너리로 받을 수 있습니다. 생성 도구별 필드를 해석하기 전에 정확히
지원되는 프로필을 선택하세요.

터미널을 사용할 수 있는 에이전트에게 다음과 같이 요청할 수 있습니다.

> 설치된 `underwrite` 0.1.1 CLI로
> `/absolute/path/to/completed-promptfoo-export.json`을 Promptfoo 0.123.0
> 내보내기 파일로 검토하세요. `capabilities --json`과
> `audit-promptfoo --version 0.123.0`을 사용하세요. `declared_relation`, `producer_stats`,
> `row_tallies`, `raw_hash`, `limitations`와 다음으로 할 만한 작업을 보고하세요.
> 생성 도구를 다시 실행하거나 종료 코드 `0`을 일치 또는 승인으로 해석하지 마세요.

선택 설치형 [underwrite-review 스킬](https://github.com/ntts9990/underwrite/blob/main/skills/underwrite-review/SKILL.md)은 Codex와
Claude Code에서 재사용할 수 있는 지침을 제공합니다.
[설정과 사용 안내](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#optional-artifact-review-skill)에 따라 사용하며,
스킬은 PyPI 패키지가 아니라 저장소에 있습니다. 스킬이 CLI를 설치하거나
백그라운드 서비스를 추가하지는 않습니다.
직접 만든 자동화에서도 같은 CLI와 가져와 사용할 수 있는 Python API를 이용할 수 있습니다.

## 지원 형식

지원 범위는 **아래의 구체적인 로컬 페이로드 프로필**입니다. 각 도구가 제공하는 모든
API, 내보내기 형식, 버전을 지원한다는 뜻은 아닙니다. 설치된 capabilities를 확인하세요.
이전 계약을 재현할 때는 명시적인 선택자를 사용합니다.

| 입력 | 지원 프로필 | 시작 경로 |
| --- | --- | --- |
| **goppi** | 완료된 baseline exchange manifest v2; paired handoff는 별도 경로 | 설명용 공통 JSON은 [`audit-baseline`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#audit-a-completed-goppi-baseline-as-common-evidence) · 네이티브 측정용 관측은 [`project-baseline`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#project-a-completed-goppi-baseline-into-event-observations) · [`review-handoff`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#review-a-completed-paired-handoff-in-one-call) |
| **Langfuse** | 배포된 `0.1.1`: `4.35.0` 서버 blob 배열. 개발본의 자동 입력: 호환되는 blob 배열 또는 공식 CLI 목록 캡처. | [`audit-langfuse`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-langfuse-score-kinds-and-associations), 선택형 내용 최소화 행 인덱스 제공 |
| **DeepEval** | 일반 TestRun JSON, `4.1.1` 또는 `4.2.8` | [`audit-deepeval`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-deepeval-scores-and-evaluator-errors) |
| **Promptfoo** | Eval output JSON, `0.123.0` 또는 `0.124.1` | [`audit-promptfoo`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#source-checkout-pilot-audit-promptfoo-counts) |
| **외부 공통 결과** | 생성 도구의 `evidence_result.v1` JSON | [`inspect-result`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#inspect-an-external-common-result)로 계약과 가용성 집계를 검사하고, 선택적으로 근거 항목의 `next_action`과 선언된 메트릭 결합 상태별 개수를 집계 |
| **네이티브 근거** | `underwrite.evidence-bundle` / `v1` | `ingest` → `measure` → `accept` |
| **Inspect AI** | `inspect.eval-log` / `0.3.263` | `ingest` |
| **OpenInference** | `openinference.traces` / `0.1.38` | `ingest` |
| **OTLP JSON** | `otlp-json.traces` / `0.160.0` | `ingest` |

이미 로컬에 있는 완료된 내보내기 파일은 설치된 CLI로 바로 검사할 수 있습니다.
보유한 파일의 원천 프로필을 정확히 선택하세요.

```sh
underwrite audit-promptfoo /path/to/promptfoo-output.json --version 0.124.1 --json
underwrite audit-deepeval /path/to/deepeval-test-run.json --version 4.2.8 --json
underwrite audit-langfuse --observations /path/to/blob-observations.json \
  --scores /path/to/blob-scores.json --json
underwrite audit-baseline --exchange /path/to/goppi-baseline-exchange --json
```

DeepEval 집계 검사의 기본값은 `4.1.1`입니다. `4.2.8` 프로필을 쓰려면
`--version 4.2.8`을 지정하세요. 배포된 `0.1.1`의 Promptfoo 기본값은
`0.123.0`이며, 그 버전에서 `0.124.1`에는 `--version 0.124.1`을 지정합니다.
배포된 Langfuse 입력의 기본값은 4.35.0 서버 blob export입니다. 개발본에서
자동 Langfuse 입력을 광고하면 호환되는 blob 배열이나
[공식 CLI 목록 캡처](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#read-official-langfuse-cli-captures-offline)를
`--profile` 없이 읽습니다. 선택된 reader는 생성 도구의 실제 배포 버전을 입증하지
않습니다. 명시적인 blob/CLI 프로필은 이전 v1 출력을 유지합니다. 통제된 CLI fixture는
로컬 형식 처리를 보여 줄 뿐, 실제 서버와의 상호 운용성이나 내보내기의 완전성을
증명하지 않습니다.
검사한 범위와 fixture의 출처는
[아티팩트 요구사항](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#get-the-right-producer-artifact)과
[생성 도구 검증](https://github.com/ntts9990/underwrite/blob/main/EVIDENCE_REVIEW.md)을 참고하세요.

범용 `ingest` / `project`는 지원되는 내보내기를 관측으로 정규화합니다.
외부 관측은 현재 `measure` 경로로 이어지지 않습니다. goppi는 명시적인 exchange
어댑터를 사용합니다. `audit-baseline`은 정책이나 방향성 판정 없이 설명용
`evidence_result.v1`을 만들고, `project-baseline`은 네이티브 사건 관측을 만들 수 있습니다.
`prepare-event-policy`는 소비자 설정을 결합하면서 통계 가정은 알 수 없는 상태로 둡니다.
이미 생성된 `evidence_result.v1` 파일은 `inspect-result`로 설치된 계약과 항목 수를
검사할 수 있습니다. 이 명령은 생성 도구의 원본 파일이나 묶음을 확인하거나 측정·수락
권한을 부여하지 않습니다.
선택형 조치 뷰의 `next_action`은 근거 보완을 위한 후속 조치이며, 에이전트가 실행한
도구 호출이나 작업 경로를 뜻하지 않습니다.

## 다시 찾아볼 수 있는 결과 보관

분석과 선택한 원본 바이트를 함께 보관하려면 `records`를 사용하세요.
완료된 로컬 Promptfoo 내보내기 파일이 있다면:

```sh
underwrite records audit-promptfoo /path/to/promptfoo-output.json \
  --version 0.123.0 --root ./review-store --id review-001 --json

underwrite records read-json \
  --root ./review-store --id review-001 \
  --name analysis/accounting.json --json
```

캡처할 때마다 새 ID를 사용하세요. 기존 기록은 덮어쓰지 않습니다.
`records verify`는 저장된 데이터의 무결성을 검사합니다. `records measure`는
네이티브 측정을 캡처하고, `records replay-measure`는 검증된 저장 입력을 현재 엔진으로
다시 계산합니다. 원래 생성 도구를 실행하지 않습니다.

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
