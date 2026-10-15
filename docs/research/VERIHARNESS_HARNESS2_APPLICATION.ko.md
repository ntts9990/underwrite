# VeriHarness와 Harness²의 Underwrite 적용 검토

검토일: 2026-10-16. 대상은 두 논문의 v1, 고정한 공식 코드와 공개 데이터,
Underwrite 0.1.0 소스다. 아래의 적용안은 후속 개발 제안이며 현재 지원 기능이나
새 릴리스 약속이 아니다. 마지막 절에는 Goppi에 전달할 텍스트가 있다.

## 1. 판단과 제품 방향

Underwrite가 집중할 역할은 **검증·개선 결과가 무엇을 근거로 성립하고,
어떤 조건에서 비교할 수 있으며, 무엇을 더 확인해야 하는지 구조화하는 것**이다.
실행·검사 선택·산출물 수정·하네스 개선은 해당 기능을 가진 생산 도구와
호출 에이전트가 맡는다. 다른 도구가 이미 잘 수행하는 기능을 다시 구현하지 않는다.

VeriHarness는 이미 주장·검사·근거·판정을 연결한 기록을 만든다. Harness²도
실행 이력, 선택 결과, 경험 라이브러리와 일부 보고 경고를 제공한다.
따라서 기록 저장이나 경고 재출력만으로 차별화됐다고 할 수 없다.
Underwrite의 추가 가치는 서로 다른 생산자의 기록을 소비할 때 **모집단,
지표 의미, 검사 대상 버전, 비용 범위와 미확인 상태가 섞이지 않는 것**에서
검증해야 한다.

첫 단계는 두 공개 어댑터의 동시 개발이 아니라, 고정된 VeriHarness 공개 인덱스로
하나의 오프라인 소비 과제를 검증하는 것이다. 기존 데이터 카드·스크립트와 비교해
해석 오류나 수작업을 줄이지 못하고 같은 숫자를 다시 세는 데 그치면 기능을 추가하지
않는다. Harness²의 비교 기록 소비와 복합 검증 기록 계약은 별도 근거가 생긴 뒤 판단한다.

사용자가 공유한 Goppi 측 연구도 반영했다. Goppi가 제안한 **첫 reviewer가 놓친 결함의
새 문맥 발견 실험**은 위 인덱스 소비 실험과 다른 질문이다. 서로의 선행 조건으로
묶지 않고, 기존 계약·fixture의 재사용 가능성과 소비에 필요한 최소 정보를 먼저 맞춘다.
그에 대한 답변과 수정된 전달문은 9절과 마지막 절에 있다.

## 2. 검토한 정본과 버전

| 자료 | 고정한 버전 | 용도 |
| --- | --- | --- |
| [VeriHarness 논문][V1] | arXiv `2610.00972v1`, 2026-10-01 제출 | 방법·실험·부록의 주장 |
| [VeriHarness 공식 코드][V2] | `0f10db9818920f8d79641b23d9311c4877f67702` | 실제 기록·검사·점수 처리 의미 |
| [VeriHarness 공개 데이터][V3] | `214c7081ebd2e3896e311e5f495cd4e81419950a` | 배포된 인덱스의 개수·누락·제외 |
| [Harness² 논문][H1] | arXiv `2610.14224v1`, 2026-10-09 제출 | 재귀적 개선 방법과 실험 |
| [Harness² 공식 코드][H2] | `1cfe254e8d25b15f37048c3730134686abe60335` | 비교 기록·점수·비용·실행 환경 |
| Underwrite | `b2bddfad9604bfbcc3634a51e45346757d4f5bfa`, app/core 0.1.0 | 현재 소비 계약과 재사용 경계 |

논문의 실험 조건, 공개 코드의 동작, 공개 아카이브의 상태는 별개의 근거다.
코드나 아카이브에서 발견한 차이를 곧바로 논문의 모든 실험에 적용하지 않는다.
성능 실험을 재현한 결과가 아니라 원문·공식 구현 검토와 공개 인덱스 재계산이다.

## 3. VeriHarness에서 확인한 내용

### 방법과 실험의 의미

VeriHarness는 같은 과제의 여러 실행 결과를 조사하는 검증 하네스다.
생성기와 같은 기반 모델을 사용하되, 서로 다른 답을 대조하는 resolver와
공통 결론·공통 누락을 반증하는 challenger를 별도 문맥에서 실행한다.
새 문맥의 adjudication이 두 기록을 검토해 기반 결과, 수정 계획, 미해결 항목을
정하고 산출물을 전달한다. 단순 투표나 전체 산출물에 점수 하나를 붙이는 방법이 아니다.
[논문 §2–3][V1]

주요 실험은 두 모델·다섯 벤치마크에서 같은 10개 후보 풀을 각 비교 방법에 제공한다.
논문은 수정까지 포함한 평균 개선을 Gemini 3.5 Flash +6.2점, Claude Opus 4.8
+6.4점으로 보고한다. 이는 벤치마크별 점수와 해당 설정에 대한 결과이며,
Underwrite의 예상 개선치나 보편적 품질 지표가 아니다. 후보 생성 비용을 맞췄다는
것도 전체 검증 비용이 같다는 뜻은 아니다. [논문 §4·제약·부록 K][V1]

스킬 개선에는 개발 과제의 실패 피드백을 사용하고, 별도 보류 과제로 평가한다.
반복적으로 잘된 사례가 나왔다고 모든 상황에 적용할 수 있는 검사 규칙이 된 것은
아니다. 정보 이득과 중단 시점도 구현에서 모델이 정성적으로 판단한다.
검증 기록은 보정된 정답 확률이나 산출물 전체의 정확성 보증을 제공하지 않는다.
[논문 §3·5·제약][V1]

### 소비할 수 있는 실제 기록

| 파일 | 주요 내용 | Underwrite가 보존해야 할 의미 |
| --- | --- | --- |
| `ledger_elim.json` | `question`, `checked`, `found`, `verdict` | 경쟁 주장과 검사 결과에 대한 생산자 기록. 자유 서술을 자동으로 참·거짓 판정하지 않음 |
| `ledger_fals.json` | `claim`, `tried`, `found`, `holds`, `because` | `holds=true`와 `"untested"`를 구분. 검사자가 맞다고 주장한 것과 독립 검증을 구분 |
| `finish.json` | `base`, `work`, `open`, `notes` | 선택·수정 계획과 미해결 해석을 유지 |
| `repair.json` | `applied`, `changes`, `open`, `summary` | 수정했다는 자기보고와 실제 산출물 바이트 변경을 구분 |

형식은 프롬프트로 정의된 JSON이며, 엄격하게 버전이 지정된 공통 JSON Schema와는
다르다. 원시 기록을 보존한 뒤, 실제 명시된 필드만 고정 프로필로 해석해야 한다.
문장 속 경로를 임의로 추출해 검증된 근거 참조로 만들지 않는다.
[기록 프롬프트][V4], [드라이버][V5]

드라이버의 `valid`는 전달 파일의 보존·이름·비어 있지 않음 같은 검사다.
의미적 정답성 검사가 아니다. 누락된 기반 파일을 복원하는 동작도 있으며,
변경·복원·추가 파일 목록에는 길이 제한이 있다. `valid`나 변경 목록 하나만으로
내용의 정확성 또는 전체 검사 범위를 확정할 수 없다. [드라이버][V5]

점수 처리도 별도 의미를 갖는다. 일반적인 수정 평가 경로는 기존 선택 점수에
현재 수정본 점수와 현재 기반 결과의 재평가 점수 차이를 더하고 범위를 제한한다.
수정 산출물이 유효하지 않거나 평가되지 못하면 선택 결과로 돌아가는 경로가 있고,
기반 재평가가 없을 때는 다른 경로가 적용된다. 소비자는 최종 점수뿐 아니라 원시
구성값·평가 실패·fallback·집계에 포함된 모집단을 보존해야 한다. [점수 코드][V6]

### 공개 인덱스를 독립적으로 재계산한 결과

고정 revision의 `index.jsonl`을 직접 받아 앞서 읽은 main 파일과 해시가 같음을
확인하고 다시 계산했다. 파일 크기는 **14,583,267바이트**, SHA-256은
`753f521a4511235b6533c10930a6d50551bde2ca6393dd3470ba90a87f425ebb`다.
[고정 인덱스][V7]

| 항목 | 개수 |
| --- | ---: |
| 전체 비어 있지 않은 JSONL 행 | 23,197 |
| `excluded=true` | 5 |
| `excluded=false` | 23,192 |
| `excluded=false`의 과제·모델 풀 | 2,330 |
| 전체에서 `has_trajectory=false` | 71 |
| `excluded=false`에서 `has_trajectory=false` | 66 |
| `score=null` | 2 |

여기서 제외 표시가 없다는 것은 생산자의 `excluded` 필드 조건만 만족한다는 뜻이다.
독립적으로 오염 없음·완전성·정답성을 인증한 적격 판정이 아니다.
누락·제외·오류 플래그는 중첩될 수 있으므로 서로 배타적인 실패 수처럼 합산하지 않는다.

`excluded=false` 행을 `(benchmark, model, key)`로 묶으면 풀 크기는 다음과 같다.

| 풀의 후보 수 | 해당 풀 수 |
| ---: | ---: |
| 2 | 2 |
| 5 | 8 |
| 6 | 8 |
| 7 | 5 |
| 8 | 13 |
| 9 | 41 |
| 10 | 2,191 |
| 11 | 62 |

따라서 **현재 배포된 인덱스에 모든 과제의 완전한 후보 10개가 있다고 가정할 수 없다.**
이 결과로 논문 실험의 모집단이 잘못됐다고 결론 내릴 수는 없다.
데이터 카드는 제외·파일 제거·마스킹을 설명하며, `flags`는 세미콜론으로 구분한
문자열이고 `official_id`도 JSON을 담은 문자열이다. `tar_sha256`은 배포된 아카이브
바이트 식별이며 원래 실행의 독립 출처 보증은 아니다. [데이터 카드][V3]

재계산의 핵심 규칙은 아래와 같다. 전체 산출물 아카이브나 모델 실행은 필요하지 않다.

```sh
uv run --frozen --no-sync python - <<'PY'
import hashlib
import json
from collections import Counter
from urllib.request import urlopen

revision = "214c7081ebd2e3896e311e5f495cd4e81419950a"
url = f"https://huggingface.co/datasets/caiqizh/veriharness/resolve/{revision}/index.jsonl"
limit = 20 * 1024 * 1024
with urlopen(url, timeout=40) as response:
    raw = response.read(limit + 1)
assert len(raw) <= limit
assert hashlib.sha256(raw).hexdigest() == (
    "753f521a4511235b6533c10930a6d50551bde2ca6393dd3470ba90a87f425ebb"
)
rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
assert all(type(row["excluded"]) is bool for row in rows)
selected = [row for row in rows if row["excluded"] is False]
pools = Counter((row["benchmark"], row["model"], row["key"]) for row in selected)
print(json.dumps({
    "rows": len(rows),
    "excluded": sum(row["excluded"] for row in rows),
    "not_excluded": len(selected),
    "task_model_pools": len(pools),
    "pool_size_distribution": dict(sorted(Counter(pools.values()).items())),
    "trajectory_unavailable_not_excluded": sum(
        row["has_trajectory"] is False for row in selected
    ),
    "null_scores": sum(row["score"] is None for row in rows),
}, indent=2))
PY
```

## 4. Harness²에서 확인한 내용

### 두 수준의 재귀

현재 과제에서는 Propose–Probe–Compose로 일반적인 절차 변경과 도메인별 변경을
실행·대조·조합한다. 과제 사이에서는 채택한 하네스와 경험을 라이브러리에 남겨
다음 개선에 활용한다. 새로운 과제는 공통 기반 하네스에서 시작하며, 이전 하네스
전체를 무조건 상속하는 구조로 설명하면 부정확하다. [논문 §2][H1]

실험은 두 실행 모델과 두 기본 하네스의 조합을 사용한다. 실행 모델의 가중치는
고정하지만 기본 개선 모델은 별도 Claude Opus 4.8이다. 따라서 VeriHarness의
생성기·검증기 동일 모델 설정과 동일시하면 안 된다. 실행 경로 차이는 개선의
조사 근거이며, 변경의 인과 효과나 다른 과제로의 전이를 자동 증명하지 않는다.
[논문 §3·제약][H1]

### 실제 기록과 해석 경계

| 자료 | 읽을 수 있는 내용 | 소비 시 유의점 |
| --- | --- | --- |
| `config.json` | 모델·평가기·선택 과제·순서·split·seed 등 | 선언과 실제 실행 조건의 독립 증명은 다름 |
| `summary.json` | 단계·시도·fallback·선택·오류·원장 | 완료 상태와 평가 성공을 구분 |
| `selection.json` | 선택한 실행, 후보 매핑, 탈락·fallback·이유 | 선택된 결과만으로 전체 시도의 품질을 추정하지 않음 |
| `judged.json`, `base_judged.json` | 과제별 점수 구성·채점 상태·오류 | 같은 과제 집합과 지표 의미를 먼저 확인 |
| `harness.diff`, 경험 기록 | 변경 내용과 재사용하려는 교훈 | 한 번의 대조 실행이나 묶음 변경만으로 원인을 확정하지 않음 |

구체적인 입력 경로는 [스케줄·반복 구현][H3], [선택 구현][H4],
[채점 구현][H5], [경험 라이브러리][H6]에서 확인할 수 있다.

JobBench의 `n_passed/n_criteria`는 가중 `total_score/max_score`를 나타낼 수 있다.
별도 표시가 있는 비가중 fallback도 있다. WorkBuddy 역시 점수를 정수 형태로
재표현하는 경로가 있으므로, 필드 이름만 보고 독립적인 성공 횟수와 시행 횟수로
이항 측정에 넣어서는 안 된다. [JobBench 변환][H7], [WorkBuddy 변환][H8]

미채점 행은 점수 분모에서 제외되며, 공식 보고는 미채점 수와 혼합 지표 경고를
이미 제공한다. 따라서 Underwrite가 같은 경고를 재출력하는 것만으로는 추가 가치가
크지 않다. 여러 결과의 비교에서 각자 다른 분모를 쓰는지, 의도한 변경 외의 조건이
달라졌는지, 혼합된 의미가 하나의 숫자로 소비되는지를 확인하는 과제가 필요하다.
[보고 집계 코드][H9]

비용 보고는 개선 작업 범위를 명시한다. 실행·제안·조합·선택·회고·재시도·채점의
모든 비용을 포함했다고 가정하지 않는다. 미측정 비용을 0으로 채우지 않고,
병렬 작업의 소요시간 합과 사용자가 기다린 경과시간도 구분해야 한다.
[비용 보고 코드][H10]

### 평가 자료 노출과 재현성

공식 실험 안내는 **공개 코드의 검증 실행**에서 Codex가 JobBench rubric을
자율적으로 내려받은 사례를 명시한다. 해당 릴리스는 네트워크를 제한하지 않으며
Codex 샌드박스를 비활성화한다. 이것은 실행 환경에서 통제해야 할 구체적 위험이고,
논문 실험 전체가 오염됐다는 증거는 아니다. “정답을 보지 말라”는 지침이나
로그에서 접근을 찾지 못했다는 사실만으로 비노출을 인증할 수 없다. [실험 안내][H11]

같은 안내에 따르면 seed는 과제 순서를 제어하며 모델 샘플링을 고정하지 않는다.
JobBench 다운로드 revision은 설치 시 결정되고 기록된다. 비교에는 데이터 revision,
선택된 과제·순서, 실행·개선·평가 모델, 예산, 라이브러리 시점과 접근 정책이 필요하다.
원래 변경하려던 하네스 요소까지 양쪽에서 같아야 한다는 뜻은 아니다.
**의도한 처치와 고정해야 할 나머지 조건**을 구분한다. [실험 안내][H11]

## 5. Underwrite가 제공할 수 있는 결과

| 우선순위 | 제안 | 사용자가 얻을 결과 | 도입 조건 |
| --- | --- | --- | --- |
| 1 | 후보군·평가 모집단 검사 | 제공·제외·미채점·자료 누락과 각 분모를 구분 | 고정 인덱스 한 개의 오프라인 소비 실험부터 시작 |
| 2 | 개선 비교 조건 검사 | 데이터·평가기·과제 집합·지표 의미 차이를 개선 효과로 오해하지 않음 | 기존 생산자 보고를 넘는 구체적 소비 과제가 확인돼야 함 |
| 3 | 검사와 최종 산출물의 결합 | 무엇의 어느 버전을 검사했으며 수정 후 무엇을 다시 확인해야 하는지 표시 | 실제 구조화된 참조와 최소 계약 확보 |
| 4 | 자원 사용의 측정 범위 | 부분 비용·미측정·재시도·시간 범위를 구분 | 실제 기록된 비용과 시각을 보존하고 없는 값을 추정해 채우지 않음 |

예를 들어 “새 하네스가 더 정확하고 저렴하다”는 결과가 들어오면, 다음과 같은
후속 과제가 유용하다. 이는 제안된 출력 의미이며 기존 CLI의 새 상태 이름이 아니다.

- 양쪽 채점 완료 과제가 다르면, 미채점 원인을 표시하고 고정한 동일 모집단으로
  재평가하도록 요청한다. 교집합 분석만으로 전체 모집단의 개선을 확정하지 않는다.
- 점수 정의나 평가기가 다르면 비교의 범위를 제한하고 동일 정의의 결과를 요청한다.
- 비용에 개선 호출만 포함됐다면 전체 비용 절감은 미확인으로 남기고 빠진 측정을 요청한다.
- 검사 이후 결과 파일이 바뀌었다면 최종 버전에 적용되는 검사를 다시 요청한다.

이런 결과는 호출 LLM이 다음 작업을 시작할 수 있게 한다. 실제 재실행이나 패치 생성,
문서 내용 판정까지 Underwrite가 맡을 필요는 없다.

## 6. 제품 내부와 개발 과정에 적용할 아이디어

### 불일치와 공통 누락을 별도 검사 대상으로 만들기

서로 다른 결과만 비교하면 모든 후보가 함께 빠뜨린 요구사항은 보이지 않는다.
명시된 요구사항 목록과 실제 검사 목록을 대조하고, 합의한 내용에도 검사 근거가
있는지 살핀다. 합의율은 정답 확률이 아니며, 모델이 추출한 요구사항 목록도
독립적인 정답 목록으로 승격하지 않는다. [VeriHarness §2–3][V1]

제품에서는 이런 항목을 **검사 대상**으로 반환할 수 있다. 검사 대상 선정과 판정은
다르다. 정성적인 정보 이득 아이디어를 임의의 오류 확률이나 신뢰도 점수로 만들지 않는다.
규칙 기반으로 확인할 수 있는 참조·버전·범위 관계부터 다룬다.

개발 과정에서도 코드 오류와 공통 계획 가정을 별도로 검토할 수 있다.
Underwrite 0.1.0 준비에서 신규 Goppi trajectory 계약을 필수 출시 조건으로
묶었던 것은 사용자 요구와 계획상의 가정을 다시 구분하며 수정한 사례다.
동일 모델의 여러 검토가 일치했다는 이유만으로 그 가정의 근거가 생기지는 않는다.

### 중간 검사와 최종 전달물을 연결하기

올바른 중간 계산이나 이전 파일 버전의 검사만으로 최종 전달물까지 확인됐다고
판정하지 않는다. VeriHarness의 오류 분석에서도 맞는 중간값을 검사했지만 잘못된
후속 결과를 놓치는 사례가 나타난다. [논문 §6·부록 J][V1]

Underwrite 적용안은 검사 대상 바이트·버전·범위와 전달물의 관계를 명시하는 것이다.
수정 이후 어떤 검사가 여전히 적용되는지, 어떤 검사는 다시 해야 하는지 반환한다.
다만 **참조가 해결되고 해시가 맞는 것**과 **그 근거가 주장을 논리적으로 지지하는 것**은
다르다. 후자는 생산자 또는 별도 검증자가 주장한 의미와 방법을 보존한다.

### 경험을 검증된 확장으로 승격하기

Harness²의 경험 재사용을 참고하되, 기본 지표·상태 의미·안전 경계를 자동으로
바꾸는 구조를 도입하지 않는다. 적용 순서는 다음과 같다.

`관측된 실패 → 재현 사례 → 후보 검사 규칙 → 별도 검증 사례 → 검토된 버전 채택`

도메인별 검사에는 적용 범위, 입력과 출력, 출처·방법·정의 버전, 실패 조건을 붙인다.
가능한 검사는 재현 테스트나 결정적 규칙으로 구현하고, 자연어 스킬은 검사 대상 추천을
보조한다. 학습에 쓴 과제를 다시 통과한 것만으로 일반화를 선언하지 않는다.
기본 규칙을 약화시키거나 생산자가 만든 스크립트를 Underwrite에서 자동 실행하지 않는다.

### 제한된 대조로 개선하기

내부 적용을 평가한다면 사용자 과제·예산·입력 버전과 성공 조건을 먼저 고정한다.
일반 절차 변경과 도메인 변경을 분리해 보되, 모든 변경에 여러 에이전트나 열 번의
유료 실행을 요구하는 상시 절차로 만들지 않는다. 실제 누락 해석, 원본 재조회,
최종 산출물 결합 같은 확인된 문제를 대상으로 한다.

검사 후보 발견률뿐 아니라 잘못된 안심, 추가 수작업, 총비용과 유지보수 부담을 함께
기록한다. 기준선 유지도 정상적인 결과다. 변경한 여러 요소를 한꺼번에 시험했다면
어느 요소가 원인인지 확정하지 않는다.

## 7. 현재 코드에서 재사용할 경계

0.1.0에는 VeriHarness·Harness² 전용 입력 프로필이 없다.
일반 파일 보관이 가능하다는 사실이 두 도구의 의미를 이미 정규화한다는 뜻은 아니다.

| 기존 소유자 | 재사용할 부분 | 넘어서는 안 되는 경계 |
| --- | --- | --- |
| [공유 파일 읽기](../../src/underwrite/_fileio.py)와 [입력 전송](../../src/underwrite/instrument/ingest/transport.py) | 제한된 파일 읽기·유한 오류 | 새 어댑터마다 별도 파일 처리 체계를 만들지 않음 |
| [records](../../src/underwrite/service/records.py) | 명시한 원시 파일 보관·무결성 확인·선택 바이트 재조회 | 해시 일치를 생산자 인증이나 의미적 정답성으로 해석하지 않음 |
| [공통 결과 계약](../../contracts/evidence_result.v1.schema.json) | 숫자·불리언 항목의 출처·가용성·의미 결합 | 자유 서술의 주장·검사 그래프를 기존 필드에 억지로 넣지 않음 |
| [선언 비교](../../src/underwrite/instrument/evidence/declarations.py) | 알려진 차이와 미확인을 동시에 보존 | 선언이 같다고 실제 조건·모집단·의미가 검증됐다고 하지 않음 |
| [측정 클레임](../../contracts/declared_claims.v2.schema.json) | 집계 측정과 정책·의미 digest의 결합 | 문서의 사실 주장이나 검사자의 자연어 판정과 같은 개념으로 취급하지 않음 |

`evidence_result.v1`의 좌표와 값 형식은 닫혀 있고, 관측된 항목의 `next_action`은
`none`이다. 현재의 조치 필드는 근거 누락 등에 대한 조치이며, 복합 검증 계획이나
에이전트 행동 단계 자체를 표현하는 필드가 아니다. `review-pair`도 기존 이진 쌍대
근거 경로이지 임의의 가중 점수 비교기가 아니다.

실제 사용자 과제가 복합 주장·검사·산출물 결합을 요구하면 별도의 최소 계약을
설계해야 한다. 기존 계약의 의미를 몰래 넓히지 않는다. 순수 계산과 CLI·파일 경계는
유지하고, 생산자 출력의 참조가 불충분하면 먼저 생산자와 필요한 교환 항목을 합의한다.

## 8. 첫 실험과 중단 기준

첫 실험 질문은 다음처럼 제한한다.

> 선택한 VeriHarness 벤치마크·모델·과제에서 어떤 공개 기록을 분석할 수 있고,
> 무엇이 제외·미채점·미보존됐으며, 비교를 시작하려면 무엇을 더 확인해야 하는가?

입력은 위 고정 인덱스와 명시한 선택·제외 정책이다. 비교 기준은 공식 카드·기존 보기와
과제를 제대로 수행하는 작은 스크립트다. 새 CLI나 범용 스키마를 먼저 만들지 않는다.
기존 스크립트가 갖지 않은 무결성·개인정보 보장을 주장하려면 동일한 보장 조건에서
비교해야 한다.

완료 기준은 다음과 같다.

1. 공개 행·제외 표시·분석 선택·채점 완료 모집단을 구분하고, 각 분모의 근거를 제시한다.
2. 후보 수가 달라도 임의 절단·보충하지 않고, 미채점·누락을 실패 점수 0으로 바꾸지 않는다.
3. 필요한 원본 위치와 확인할 다음 행동을 반환한다. 숫자 재출력만으로 효용을 주장하지 않는다.
4. 후속 프로필이 필요해진 경우에만 중복 ID·오래된 참조·설정 불일치·크기 초과 등
   의미 있는 반례를 고정하고 기존 읽기·저장·검증 소유자를 재사용한다.
5. 실제 해석 오류나 수작업 감소가 확인되지 않으면 도입을 멈춘다. 정확성 동등을
   우월성으로 포장하지 않으며, 이 실험의 성과를 제품 전체의 효과로 일반화하지 않는다.

Harness² 연동은 그다음 별도 과제로 평가한다. 이미 제공하는 경고를 반복하는 데
그치면 어댑터를 추가하지 않는다. 두 연구의 실행·하네스·문서 수정 엔진을
Underwrite 런타임 의존성으로 추가할 계획은 이 검토에 포함하지 않는다.

## 9. Goppi 조사와의 대조 및 소비자 측 답변

사용자가 제공한 문서는 Goppi의
`docs/research/VERIHARNESS_HARNESS2_GOPPI_FIT.ko.md`다. 읽은 파일의 SHA-256은
`021f95c4cbda6842797f83c3891b167b97c4267eaad54d3696e7ea97b11d16a6`이다.
당시 로컬 Goppi HEAD는 `69b46e79168893fdf167fb4af3fef30e4755285d`이고, 연구 문서는
아직 추적되지 않은 로컬 파일이었다. 이 대조를 원격 main 채택이나 새 기능 구현의
증거로 취급하지 않는다.

### 양쪽 판단이 일치하는 부분

- 범용 하네스 개선기, 경험 검색 DB, 새 claim ledger를 두 제품의 공통 코어로 만들지 않는다.
- Goppi는 기존 proposal·실행·수집·native binding·영수증·paired 평가와 advisory 경계를
  소유한다. Underwrite는 지원하는 버전의 실제 산출물에서 소비 가능한 의미와 근거를 다룬다.
- 두 논문의 성능 수치, 새 문맥, 반복 일치를 실제 제품의 효과나 독립성 보증으로 가져오지 않는다.
- `no_proposal`, `KEEP_BASE`, paired `rejected`와 `indeterminate`를 이름만 보고
  하나의 실패·보류 상태로 접지 않는다. 발생 단계와 판단 근거가 같다는 확인이 필요하다.

### 보완이 필요한 상태 해석

Goppi 문서의 **`KEEP_BASE`가 항상 probe 후 상태라는 설명은 보완해야 한다.**
Harness²는 제안 단계의 프롬프트에서도 무변경을 허용한다. 구현의 `kept_base`는
하네스 트리의 차이 유무로 계산되므로 제안 단계에서도 나타날 수 있고, 조합 단계의
유지와 구분해야 한다. 이 필드만으로 모델의 의도나 변경 효과까지 확인된 것은 아니다.
발생 단계, 유지한 하네스의 정체성, fallback·오류를 함께 보존하는 편이 정확하다.
[논문 부록 H][H1], [트리 수용과 propose→probe 순서][H3]

### Goppi가 제시한 좁은 실험에 대한 판단

Goppi 연구는 첫 reviewer가 제기한 finding에 대해 critic이 반론하는 것과,
아무도 제기하지 않은 결함을 새로 발견하는 것을 구분한다. 실제 로컬
`src/reviews/reconcile-reviewer.ts`를 읽으면 첫 finding이 없을 때 `settled`로 끝나고,
후속 reviewer turn의 새 finding ID는 거부한다. `reconcile-contracts.ts`의 critic turn은
기존 finding에 대한 verdict를 전달한다. 이는 범위 고정의 의도적인 계약이며,
누락 발견 효과가 부족한 결함이라고 곧바로 단정할 근거는 아니다.

소비자에게 중요한 것은 **`settled`를 알려지지 않은 결함까지 없다는 뜻으로 읽지 않는 것**이다.
새 문맥으로 실행했다는 사실도 오류의 통계적 독립성을 보증하지 않는다. 현재 Goppi의
review provenance가 `independence: not_established`를 유지하는 의미를 보존해야 한다.

Goppi의 A/B/C 제안은 연구할 가치가 있다. A는 현행 reviewer→critic, B는 같은 문맥의
발견 pass 연장, C는 기존 finding을 보지 않은 새 문맥의 발견이다. 단순히 현행 세션의
budget cap만 늘린 것을 B라고 부르지 않는다는 점에 동의한다. 원천 검토 자료의 투영은
맞추되, 문맥 이력의 차이는 실험하려는 요인으로 명시한다. 상한이 같다는 사실과 실제
지출이 비슷했다는 사실을 구분한다.

같은 입력의 기존 세션 재생을 새 반복 실행으로 세면 안 된다. 격리한 연구 저장소와
실험 manifest는 후보 접근이며, 새로운 실행·재생·복구 여부는 실제 생산자 기록에서
확인해야 한다. 기존 세션의 `settled` 상태나 finding ID 고정 계약을 소급 수정하지 않는다.

### 요청 1에 대한 답변: 재사용할 사례와 절차

| Underwrite의 현재 자료·기능 | 재사용 가능한 부분 | 이 실험에 바로 쓰면 안 되는 부분 |
| --- | --- | --- |
| [기존 평가 비교](../../EVIDENCE_REVIEW.md) | 같은 입력, 비교 질문, 기준선 범위, 미측정과 효과 주장 구분 | 합성 집계 문제의 정답을 proposal-review 결함 정답으로 취급 |
| [UME4 고정 fixture](../../fixtures/conformance/ume4/SOURCE.md) | 생산자 commit·원시 바이트·generator·선택 범위/누락 의미를 고정하는 캡처 방식 | 관측 true/false/missing을 reviewer의 결함 발견 label로 재명명 |
| [선택·확인 분리 검사](../../src/underwrite/acceptance/claims.py) | 선언된 선택과 확인 사례의 겹침을 거부하는 아이디어 | 같은 원문 변형·의미적 중복까지 자동으로 독립 분할됐다고 주장 |
| [기록 저장](../../STORAGE.md) | 승인한 파일만 선택해 보관하고, 검증한 바이트를 재조회 | 저장 성공을 provider의 보존 정책·비노출·사용 허가 확인으로 승격 |

현재 확인한 Underwrite 자료는 **누락 발견 recall을 평가할 독립적인 도메인 label
데이터셋을 제공하지 않는다.** VeriHarness 공개 인덱스도 그런 label을 대신하지 못한다.
Goppi의 기존 판단 모델 연구가 제안한 독립 저작·복수 검토·불일치 중재·privacy·split
절차를 먼저 재사용하고, 실제 사례가 proposal-review에 맞는지 별도로 감사하는 것이 좋다.
이는 그 기존 사례의 적합성을 이미 검증했다는 뜻이 아니다.

defect ID별 발견을 추적하되, 같은 subject에 속한 여러 결함과 반복을 전부 독립 표본으로
세지 않는다. 심각도별 놓침, 정상 사례 오경보, 사람 검토 시간의 분모와 측정 방법도
분리한다. 사람 시간은 실제 관측 없이는 미측정으로 남긴다. Underwrite의 현재 이진
측정 경로가 이 복합 실험의 통계 분석을 그대로 지원한다고 약속하지 않는다.
arm별 finding ID와 독립 label의 defect ID 대응은 별도 검수된 sidecar로 고정해야 한다.
같은 ID 문자열이나 유사한 문장만으로 동일 결함이라고 자동 판정하지 않으며,
Underwrite가 그 의미적 대응을 독립적으로 판정한다고 주장하지 않는다.

### 요청 2에 대한 답변: 소비에 필요한 최소 정보

아래는 합의된 새 스키마가 아니라 **기존 생산자 필드로 충족 가능한지 확인할 항목**이다.
최초 대조에서 필요한 것만 남기고, 다른 저장소에 같은 정본을 손으로 복제하지 않는다.

| 정보 묶음 | 소비자가 확인하려는 것 |
| --- | --- |
| 실험·subject 정체성 | task/subject, arm, 반복, session/run 참조, 의도한 변경 요인 |
| 검토 입력의 범위 | 입력 투영과 source bytes의 식별, 가용성·redaction, 모델·도구·정책·예산의 정체성 |
| 실행의 성격 | 새 실행·재생·복구·미실행과 관측 오류를 구분할 근거 |
| 발견과 조정 상태 | finding ID·근거 참조·범위, 최초 finding 수, critic 처리와 terminal 상태; 미발견을 무결함으로 바꾸지 않음 |
| 정답·분할 sidecar | 독립 label과 defect ID 매핑, 원문 그룹·split·중재 버전; blind provider 입력과 분리 |
| 자원·권한·출처 | 실측 범위와 누락, elapsed/token/cost의 단위, source receipt와 portable 한계, advisory 권한 |

원시 byte 읽기와 기록 manifest 무결성 검사에는 Underwrite의 기존 경계를 재사용할 수
있다. 새 review-session의 참조 관계 검사는 아직 지원하지 않는다.
Goppi의 private 인증을 portable 소비자가 독립적으로 재증명할 수 있다고 말하지 않는다.
새 review-session 프로필이 없으므로 `inspect-result`에 임의 변환해 넣거나
자연어 finding을 기존 숫자·불리언 항목으로 강제하지 않는다.

공동 다음 단계는 **Goppi의 기존 owner→필드 대응표와 작은 정상·부정 fixture**를 보고,
Underwrite가 실제 소비할 수 있는 범위를 좁히는 것이다. 잘못된 subject/arm/반복 참조,
재생을 새 실행으로 표시한 경우, finding 0개의 `settled`, 누락 비용과 redaction 등은
계약 시험 후보이며, 성능 효과의 정답 사례와 분리한다. 유료 발견 실험이나 코어 편입은
별도 사례·예산·검증 판단 이후의 일이다.

## 10. 참고 자료

- [V1: VeriHarness 본문과 부록][V1]
- [V2: VeriHarness 고정 코드][V2]
- [V3: VeriHarness 고정 데이터 카드][V3]
- [V4: VeriHarness 기록·판정 프롬프트][V4]
- [V5: VeriHarness 드라이버][V5]
- [V6: VeriHarness 점수 처리][V6]
- [V7: 재계산한 고정 인덱스][V7]
- [H1: Harness² 본문과 부록][H1]
- [H2: Harness² 고정 코드][H2]
- [H3: Harness² 반복 실행 기록][H3]
- [H4: Harness² 선택 기록][H4]
- [H5: Harness² 채점 기록][H5]
- [H6: Harness² 경험 라이브러리][H6]
- [H7: JobBench 점수 해석][H7]
- [H8: WorkBuddy 점수 해석][H8]
- [H9: Harness² 보고 집계][H9]
- [H10: Harness² 비용 보고][H10]
- [H11: Harness² 실험·재현성·접근 제한 안내][H11]
- [선행 Underwrite trajectory 검토](AGENT_TRAJECTORY_IMPROVEMENT.ko.md)

[V1]: https://arxiv.org/html/2610.00972v1
[V2]: https://github.com/google-research/veriharness/tree/0f10db9818920f8d79641b23d9311c4877f67702
[V3]: https://huggingface.co/datasets/caiqizh/veriharness/blob/214c7081ebd2e3896e311e5f495cd4e81419950a/README.md
[V4]: https://github.com/google-research/veriharness/tree/0f10db9818920f8d79641b23d9311c4877f67702/harness/prompts
[V5]: https://github.com/google-research/veriharness/blob/0f10db9818920f8d79641b23d9311c4877f67702/harness/driver.py
[V6]: https://github.com/google-research/veriharness/blob/0f10db9818920f8d79641b23d9311c4877f67702/harness/score.py
[V7]: https://huggingface.co/datasets/caiqizh/veriharness/resolve/214c7081ebd2e3896e311e5f495cd4e81419950a/index.jsonl
[H1]: https://arxiv.org/html/2610.14224v1
[H2]: https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335
[H3]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/core/recursion.py
[H4]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/core/selection.py
[H5]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/core/judge.py
[H6]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/core/library.py
[H7]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/benchmarks/jobbench.py
[H8]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/benchmarks/workbuddy.py
[H9]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/core/report.py
[H10]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/cli/report.py
[H11]: https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/docs/EXPERIMENTS.md

## 11. Goppi에 전달할 텍스트

아래 텍스트는 검토 요청 초안이다. 문서 작성만으로 전달이나 구현 합의가 이루어진 것은 아니다.

```text
Goppi의 docs/research/VERIHARNESS_HARNESS2_GOPPI_FIT.ko.md를 읽었습니다.
Underwrite 측 조사와 답변은 docs/research/VERIHARNESS_HARNESS2_APPLICATION.ko.md,
특히 9절에 정리했습니다. 읽은 Goppi 문서의 SHA-256은
021f95c4cbda6842797f83c3891b167b97c4267eaad54d3696e7ea97b11d16a6입니다.

제안·근거 결합, paired 평가, bounded reviewer/critic과 비권위적 결과를 Goppi의
기존 owner에 유지하고, 범용 claim ledger·재귀 하네스 생성기·경험 DB를 추가하지
않는 방향에 동의합니다. 상태 이름만으로 동일하게 정규화하지 않겠습니다.
한 가지 보완을 제안합니다. KEEP_BASE를 항상 probe 후로 설명하면 부정확합니다.
Harness²는 제안 단계에서도 무변경을 허용하고, 구현의 kept_base는 트리 차이로
계산됩니다. 논문 부록 H와 고정 코드 core/recursion.py의 _accept_tree 및
run_parallel 순서를 참고해, 발생 단계·유지한 하네스·fallback/오류를 함께 구분하는
것이 좋겠습니다. settled도 처음부터 알려지지 않은 결함이 없다는 보증으로 읽지
않겠습니다. 새 문맥과 오류의 통계적 독립성도 구분하겠습니다.

요청한 두 항목에 대한 Underwrite 측 답변입니다.

1. 사례·label·privacy·split 재사용
기존 Underwrite의 합성 집계 사례와 고정 Goppi baseline fixture는 원시 바이트 고정,
출처·누락 표현, 정상/부정 소비 계약을 검증하는 데 재사용할 수 있습니다. 하지만
proposal-review 누락 발견을 평가하는 독립적인 도메인 label 데이터셋은 아닙니다.
계획된 native-Pi trajectory fixture도 이미 전달된 review 사례처럼 취급하지 않겠습니다.
Goppi의 기존 판단 모델 연구에서 제안한 독립 저작·복수 label·불일치 중재·privacy·
원문 그룹별 split 절차를 우선하고, 그 실제 사례의 적합성부터 감사하는 것이 좋겠습니다.
Underwrite의 선언된 explore/confirm 검사나 private records가 의미적 중복, 실제
provider 비노출 또는 독립 정답을 인증하지는 않습니다.

defect ID별 발견을 추적하되 같은 subject의 여러 결함과 반복을 모두 독립 표본으로
세지 않아야 합니다. 심각 사례 놓침·정상 사례 오경보·사람 검토 시간의 분모도
분리해야 합니다. 현재 Underwrite 이진 측정이 이 실험 전체를 바로 분석한다고
약속하지 않겠습니다. A/B/C 비교는 원천 자료의 투영을 맞추고 문맥 이력의 차이를
처치로 선언하며, budget cap뿐 아니라 실제 지출을 기록해 비교하는 방향에 동의합니다.
arm별 finding ID와 독립 label의 defect ID 대응은 별도 검수된 sidecar로 고정하고,
같은 ID 문자열이나 유사 문장만으로 동일 결함이라고 자동 판정하지 않겠습니다.

2. 향후 소비에 필요한 최소 정보
새 공통 스키마를 먼저 만들기보다 기존 owner가 제공할 수 있는 다음 정보를 보겠습니다.
- subject/task, arm, 반복, session/run 참조와 의도한 변경 요인
- 입력 투영·원시 바이트 식별, 가용성/redaction, 모델·도구·정책·예산의 정체성
- 새 실행·재생·복구·미실행 및 관측 오류의 구분
- finding ID·근거·범위, 최초 finding 수, critic 처리와 terminal 상태
- provider 입력과 분리된 독립 label·defect ID·원문 그룹·split·중재 버전 sidecar
- 비용·token·시간의 실제 측정 범위와 누락, source receipt, portable 한계와 advisory 권한

Goppi의 private 인증을 portable 소비자가 독립 재증명했다고 하거나, 새 review
세션을 현재 inspect-result가 지원한다고 가정하지 않겠습니다. 기존 numeric/bool
evidence_result와 집계 측정용 declared_claims는 사실 주장 그래프와 다른 계약입니다.
필요한 소비자 계약이 확인되면 Underwrite가 최소 범위를 제안하고, Goppi의
native digest/binder나 세션 조정 로직은 재구현하지 않겠습니다.

다음에는 두 가지만 부탁드립니다.
(a) 위 정보와 현재 모듈·필드의 대응표를 현재 지원 / 부분 지원 / 제안으로 나누고,
    commit·형식 버전·원격 main 채택 여부 및 제공 가능한 입력 투영을 표시해 주세요.
(b) 기존 owner로 만들 수 있는 작은 정상/부정 교환 예시를 제안해 주세요.
    잘못된 subject/arm/반복 참조, 재생과 새 실행, finding 0개의 settled, 누락 비용과
    redaction 같은 계약 시험 후보를 포함하되, 성능 효과를 측정할 label 사례와는
    분리해 주세요. 예시를 본 뒤 실제 필요한 소비자 필드만 정하겠습니다.

Underwrite의 별도 후보인 VeriHarness 공개 인덱스 소비 실험은 이 A/B/C 발견 실험의
선행 조건이 아닙니다. 숫자 재집계나 기존 경고 반복 이상의 효용이 없으면 새 어댑터를
만들지 않겠습니다. 기존 native-Pi 2사례×2반복×2arm 계획도 별도 미완료 작업으로
보존하며, 이번 연구를 0.1.0 출시 조건이나 이미 검증된 효과로 바꾸지 않겠습니다.
이 메시지는 설계 검토 요청이며 유료 실행·자동 skill 승격·버전 증가·배포 또는
다른 저장소 변경 요청이 아닙니다.
```
