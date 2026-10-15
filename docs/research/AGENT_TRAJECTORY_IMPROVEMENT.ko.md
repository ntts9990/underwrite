# 에이전트 실행 경로 분석과 실행 가능한 개선 계획

이 문서는 **현재 확인된 구현**을 먼저 기록하고, 그 근거로 후속 개발 순서를 제안한다.
새 명령, JSON 예시, 분석 규칙은 별도로 표시한 **제안**이며 현재 제공 기능이나
`0.0.2` 릴리스 약속이 아니다. 외부 제품의 성능·우열을 평가한 문서도 아니다.

## 검토 기준과 질문

| 저장소 | 읽은 스냅샷 | 이 문서에서의 지위 |
| --- | --- | --- |
| Underwrite | `67469d661037d253bc00c0b81d9da07c541f07b5` | 이 문서의 구현 기준. 이후 릴리스 준비는 아래 측정 의미를 바꾸지 않는다. |
| Goppi | `9814f533c0d629f8b87ce43388d820f3ac3be213` (`origin/main`) | 수용된 main 구현 기준. |
| Goppi | `69b46e79168893fdf167fb4af3fef30e4755285d` (`feat/operator-answer-comparison-audit`) | 검사한 로컬 개발 분기. 아래에서 **개발 분기**라고 명시한 기능은 main 수용 기능으로 취급하지 않는다. |
| Goppi | `38b99893659be9b1f202075c75be85086f0356cb` (draft PR #13) | 생산자 응답이 언급한 수리된 로컬 구성 증명. **미병합**이며 main의 기능 보증이 아니다. |

질문은 “어느 단계가 달랐는가”를 넘어서 **같은 사례의 실행·도구 사용·검증 결과를
재확인 가능한 근거에 결합하고, 사람이나 호출 에이전트가 수행할 다음 조사/변경을
제시할 수 있는가**이다. 현재 데이터에 없는 내부 추론은 관측값으로 만들지 않는다.

## 확인된 구현과 해석 한계

### 1. Underwrite의 현재 측정 단위는 선언된 결과 사건이다

현재 CLI의 `measure` 경로는 `measurement_policy.v3`에 정의된
`binary-event-directional.v1` 의미와 네이티브 `underwrite.evidence-bundle` 관측을
결합해 `read.v2`를 만든다. 외부 평가 내보내기를 범용 작업 품질 수치로 측정하는
경로가 아니다. 구현은 관측된 이진 사건 비율, 같은 사건의 사전 결과 예측확률과
실제 결과에 대한 Brier/ECE 진단, 그리고 **관측된 `n`개 사례 중 양성 사건이
`c`개일 때 균등하게 고른 `k`개 사례가 모두 양성일 비율** `C(c,k)/C(n,k)`를
구분한다. 양성 사건은 선언에 따라 작업 성공이 아닐 수도 있다. 마지막 값은
“`k`번 중 한 번 이상 성공”인 pass@k나 동일 작업을 반복할 때의 신뢰도가 아니다.
[U1], [U2]

방향별 판정은 완전하고 사전 선언된 이진 사건 모집단, 순서, 귀무 가설과
가정 상태에 대한 beta-mixture e-value 계산이다. 이는 결과가 맞을 사후확률이
아니다. 예측 실패를 정확히 예측한 시스템도 완벽한 보정 진단을 얻을 수 있으므로,
보정값을 작업 품질 투표로 합치지 않는다. 구성·방향·단위·루브릭과 레이블 출처의
의미 digest는 **선언의 결합**을 확인할 뿐 진실이나 출처를 독립 검증하지 않는다.
[U1], [U3], [U4]

별도 `pair-binary`는 명시된 두 팔의 이진 사례를 쌍으로 비교하고 누락을 계산한다.
현재 추론 계산에는 완전한 **primary repeat 0** 쌍만 들어가며, 추가 반복을
독립 사례 수 `n`으로 부풀리지 않는다. bootstrap/sign-flip 결과도 그 모집단과
가정 안에서만 읽어야 한다. `review-pair`가 묶는 조건 설명은 작성자의 선언이지
실제 실험 조건의 독립 검증이 아니다. 이전 `measurement/read.py`의 jury 구조를
현재 CLI 측정 계약으로 소개해서도 안 된다. [U5], [U6], [U11]

DeepEval·Langfuse·Promptfoo 경로는 점수, 결측, 오류, 연결 및 출처가 주장한
집계를 프로필별로 기술한다. 소유자가 주장한 metric binding이 있어도 모든
생성 도구에 공통으로 검증된 품질 점수는 생기지 않는다. `inspect-result --view actions`
의 `next_action`은 **근거 항목에 기록된 다음 조치 상태별 개수**다. 실제 에이전트의 도구
호출이나 실행 단계를 세는 값이 아니다. [U7], [U8]

### 2. 보존된 trace와 사례별 인과 연결은 다르다

Underwrite의 OTLP/OpenInference/Inspect ingest는 선택한 형식의 span, event,
link, 속성 또는 EvalLog sample을 원천 주장으로 보존한다. 이는 후속 분석 재료지만,
현재 계약은 단계 정렬, 재시도 식별, 도구 결과 소비 관계, 사례 결과에 대한
인과 귀속을 계산하지 않는다. `metric_definition.v1`의 값은 숫자·불리언이고
표본 단위는 `case`, `row`, `metric_occurrence`, `run`이다. span/edge 단위나
실행 DAG를 기존 `evidence_result.v1` 항목에 억지로 넣지 않는다. 단계 연결은
생산자 소유의 인계 자료에서 제공하고, 독립 sidecar는 portable 인계 필요가
입증된 경우에만 검토한다. 필요한 경우 **사례 수준의 명시적 의미를 갖는 파생
지표**만 기존 측정 경계로 넘긴다. [U9], [U10]

Goppi의 수용된 Pi 어댑터는 assistant와 tool span의 시간, 순번, 공급자·모델,
토큰·비용, 도구 이름과 `ok/error`를 기록한다. lifecycle은 `toolCallId`, 인수,
결과를 보지만 정규화된 공개 tool span에는 call ID나 인수·결과 fingerprint,
retry 관계가 없다. 엄격한 trace span 계약에도 `traceId`, `parentSpanId`, link가
없다. 따라서 현재 span 순번이나 부모처럼 보이는 시간 순서만으로 “이 호출이
저 결과를 소비했다”거나 “정확히 이 재시도”라고 판정할 수 없다. Goppi의
Claude preview runner는 `offline_protocol_only` 범위와 더 좁은 집계 LLM 기록을
가지므로 Pi와 같은 단계 범위를 가정하면 안 된다. [G1], [G2], [G3], [G4]

Goppi의 수용된 안정 이진 쌍대 측정은 계획된 사례의 **primary repeat 0**에서
baseline/candidate 결과의 네 전이 셀(`fail_fail`, `fail_pass`, `pass_fail`,
`pass_pass`)과 candidate-minus-baseline 차이를 계산한다. 선언된 fixed-horizon
정책에 따라 Tango score interval과 단측 exact/mid-p McNemar 검사를 제공하며,
결측 민감도와 평가 신뢰도 조건을 별도로 점검한다. 추가 반복과 span을 독립 사례
수 `n`에 더하지 않는다. 이는 범위가 정해진 사례 결과 비교이지 특정 도구 호출의
인과 효과를 확인한 것은 아니다. [G11], [G12]

`goppi.eval_trace.v1`은 Goppi 고유의 닫힌 trace 계약이다. 내부에
`resourceSpans`가 있어도 Underwrite의 현재 OTLP JSON 입력 형식과 동일한
envelope로 간주할 수 없다. 생산자 인계가 실제로 제공된 뒤에만 정확한
버전별 adapter와 소비자 바이트 결합을 설계한다. [G3], [U9]

Goppi의 수용된 verifier bundle은 사례별 outcome을 담지만 `trace_digest`는
bundle 값과 일치하도록 요구한다. 검사한 **개발 분기**의 paired export도 각
사례 행에 bundle의 `trace_digest`를 복사한다. 이는 묶음 무결성의 단서이지
사례별 특정 tool span을 가리키는 참조가 아니다. 외부 소비자가 원본 trace와
outcome을 재해결할 수 있는 인계 자료 없이는 사례별 원인 진단을 주장할 수 없다.
[G5], [G6]

### 2026-10-15 보정: Goppi 내부 사례 결합은 이미 있다

위 한계는 **Goppi에 사례 ID가 없거나 내부 결합이 없다는 뜻이 아니다**. 수용된 main의
trace resource에는 `case_id`, `run_id`, `variant_id`, `split`, `config_hash`,
`agent.version` 및 manifest가 이미 있고, resource membership으로 사례의 span을
묶는다. `getTraceIdentities`와 `bindParsedVerifierOutcomes`는 trace·계획 사례·
verifier outcome의 identity와 provenance를 실제로 대조한다. 빠진
`parentSpanId`/retry 관계는 단계 간 인과·재시도 분석의 한계이지 사례 식별자
부재의 증거가 아니다. [G13], [G14]

확인한 `live-execution/cycle.ts`의 attestation 경로는 verifier bundle JSON을
기록하고 run/stage attestation에 evidence·trace digest를 남긴다. 그 경로에서
정규화된 `goppi.eval_trace.v1` **바이트 자체를 외부 소비자용으로 내보내는 동작은
확인되지 않았다**. 따라서 P0의 실제 공백은 Goppi가 이미 수행한 결합을
Underwrite가 재현하는 일이 아니라, 그 결과와 trace 바이트를 검토 가능한
portable handoff로 제공하는 일이다. 이는 해당 경로의 소스 검사 결론이며
Goppi 전체의 모든 비공개 저장 위치 부재 주장으로 넓히지 않는다. [G15]

이 보정 뒤에는 `calculateTraceDigest`, `calculateVerifierEvidenceDigest`,
`bindVerifierOutcomes`를 Python에 새로 옮기는 안을 P0에서 제외한다. 정렬,
canonical JSON 및 숫자 직렬화의 언어 간 동등성 비용을 치르며 Goppi의 고유
결합 규칙을 중복 소유하기 때문이다. [G16]

### 2026-10-15 생산자 응답: 작은 companion 인계 제안, 아직 미구현

Goppi 측의 별도 답변을 받았다. 이는 **생산자의 설계 제안**이지 채택된 main 구현,
공개 교환 표준, Underwrite 입력 계약 또는 `0.0.2` 범위 확정이 아니다. 답변은
먼저 기존 아티팩트만으로 가능한 **부분 연결**을 인정하고, Goppi가 네이티브 Pi
실행의 첫 trajectory capture/binding을 소유하자고 제안한다. 답변이 기준으로 든
`goppi.paired_run_exchange` v1의 네 파일 portable closure와 행의 사례·arm·반복·
verifier·bundle `trace_digest`는 정확한 결과/조건 anchor 후보이나, 관련 로컬
구성 증명 `38b998…`은 draft PR #13으로 **미병합**이다. 기존 v1 파일의 바이트를
바꾸지 않는 별도 버전의 trajectory companion이 그 manifest digest를 참조하도록
하자는 안이다. 이 companion은 아직 실제 export/동결 fixture가 아니다.
paired exchange v1 자체도 여기서는 **개발 분기의 후보 anchor**이며 수용된 main의
이미 제공되는 제품 입력으로 취급하지 않는다. [G17]

생산자는 현재 paired 실행에서 사례 trace를 메모리에서 합치고 verifier bundle과
process observation은 보관하지만, verifier가 digest한 **집계 trace payload
자체는 보관하지 않는다**고 설명했다. 필드만 추가하는 exporter는 없는 원본
바이트를 복원할 수 없다. [G18], [G15]
먼저 Goppi의 기존 runner·binder 경계에서 바로 그
post-aggregation trace를 제한된 비공개 불변 저장소에 보관·로컬 인증하고,
`paired manifest → cycle/stage input → repeat index/run ID → arm/case → 정확한
evidence bundle·고유 outcome → 보존 집계 trace digest → 일치하는 case resource →
고유 span ID`를 생산자 쪽에서 결합해야 한다. 단순 case ID, 순번, 시각 또는
재사용 span ID는 충분한 join key가 아니다. 이는 현재 구현을 더 확장하자는
제안이지 이미 존재하는 공개 handoff의 설명이 아니다. 계획/실행 identity와
evalset·split·assignment·verifier, 모델/profile/도구 환경·예산·반복 계획도
일치 여부를 분리해 확인해야 한다. [G13], [G14], [G15]

제안 계약은 서로 다른 세 상태를 분리한다: **원본 가용성**(`retained`,
`not_retained`, `unsupported`, `interrupted`, `malformed`, `storage_failure`),
**계측 프로필 안에서의 capture completeness**, **export redaction**.
Pi lifecycle이 완전해도 계측하지 않은 에이전트 행동까지 완전하다는 뜻은 아니다.
Verifier 실행은 application/process 관측으로 확인하며 model/tool span의
유무에서 추론하지 않는다.
이전 run은 `not_retained`이며 순번으로 역구성하지 않는다. 첫 프로필은 native
Pi에 한정하고, Claude preview의 좁은 포착 범위나 실행 관측이 없는 answer
assessment를 조용히 변환하지 않는다. prompts·answers·인수·결과·credential·
원시 stream은 기본 export에서 제외한다. 원본 trace digest와 공개 projection의
바이트 hash는 다른 identity다. Underwrite가 공개 바이트를 검사해도 비공개
원본→projection 변환이나 생산자 identity를 독립 증명한 것은 아니다.

답변은 첫 fixture로 **사례 2개 × 반복 2회 × 두 arm**을 제안한다. 실제 결정론적
Pi adapter와 binder를 통과하되 유료 모델 호출은 없다. 하나는 tool `error→ok`와
verifier `false→true`, 하나는 변화 없음으로 한다. 잘못된 run/arm/repeat/
verifier/harness, 중복·미해결 span, digest만 있고 trace 없음, 중단·redaction된
0-call trace, 병렬 순서 변경·앞 단계 삽입, 관측되지 않은 verifier, 연결 없는
유익한 retry, 비밀 canary를 반례로 고정한다. 이 fixture와 실제 export가 동결되기
전에는 새 Underwrite 교환 스키마나 CLI를 약속하지 않는다. 답변의 실험 제안은
현실의 개선 효과나 외부 소비자의 독립 호환성 증명이 아니다.

당장은 각 제품이 **이미 지원하는 범위**에서 identity·가용성의 부분 검사만
재사용할 수 있다. Underwrite의 한 파일 `inspect-result`와 Goppi의 portable
bundle closure 검사는 서로 다른 한계를 갖는다. 이 자료만으로 사례→tool span,
verifier 생략, 인수 오류, retry 효용, trajectory 원인 개선을 주장할 수 없다.
`not_measured`만 새 형식으로 내보내기 위한 bundle-audit 명령을 만들지 않고,
실제 사용자 과제에 필요한 capture/인계 작업을 명시한다.

### 3. Goppi에는 제안·재실행 경로가 이미 있다

수용된 Goppi main에는 제안 지원이 있다. `proposeReviewArtifact`는 **호출자가
제공한** redacted `failure_summary`, 귀속과 제한된 component target을 검사하고
provider의 patch 후보를 만든다. `diagnoseAttribution`은 근거 강도·모호성·단일
component·대상 일치를 검사하지, trace에서 자동으로 원인을 찾아내지 않는다.
산출물은 `human_review=required`, `apply_authorized=false`,
`merge_authorized=false`이다. `proposal-import`도 작성된 가설과 기존 근거 참조를
받는다. 새 계획은 이 제안기를 중복 구현하지 않고, **제안기로 보내기 전의 근거
연결과 조사 과제**를 보완해야 한다. [G7], [G8]

검사한 **개발 분기**에는 동일 사례를 맞춘 7상태 operator-answer 비교가 있으나
`effect_status=not_measured`이고 독립적인 효과 추정은 제공하지 않는다.
`get_run(include_attempts=true)`도 보존된 프로세스 receipt의 집계 진단을
추가하지만 개별 에이전트 단계의 정확한 재시도 분석은 아니다. 이를 main의
이미 수용된 기능이나 사례별 인과 근거로 인용하지 않는다. [G9], [G10]

### 지금의 결론

Goppi 내부의 trace ↔ 사례 ↔ outcome 결합은 이미 있다. 제품 간 부족한 연결은
**그 결합과 trace 바이트의 검토 가능한 인계 → Underwrite의 의미·coverage 비교 →
근거가 지지하는 진단 → 실행 가능한 다음 조사/변경 → 통제된 재실행**이다.
관측된 행동과 생성된 설명을 분리한다. 에이전트가 기록한 rationale은
`self_report`이지 내부 추론의 충실한 증거가 아니며, 비공개 내부 추론을 복원할
수 있다고 주장하지 않는다. 부모 span도 그 자체로 원인 의존성을 증명하지 않는다.

## 제안: 의미와 책임의 경계

구현 재사용보다 **제품 소유권**을 먼저 결정한다. Underwrite의 차별적 역할은
생산자 사이의 근거 의미·비교 가능성·결측과 다음 검증 과제다. Goppi는
실행·trace/receipt 수집, 원래의 trace/verifier 결합과 export, 기존 proposal 및
재실행을 소유한다. 호출 LLM은 이 JSON을 소비해 별도 `hypothesis`·서술·패치
초안을 작성할 수 있지만, 가설을 관측 사실로 승격하지 않는다. Underwrite 안에
모델 서비스·서버·Docker를 추가하지 않는다.

Goppi가 제공할 **정확한 제품 인계**가 확인되면 Underwrite는 명시적 버전의
adapter로 입력 크기·계약·선택한 원시 바이트와 manifest의 무결성·상호 참조·
선언된 사례 roster·coverage를 검사하고 관측 가능한 과정을 의미에 맞게 비교한다.
Goppi 고유 verifier binding은 여전히 **생산자 주장**이며 Underwrite가 독립
검증한 출처로 표시하지 않는다. 다른 구현이 아무리 좋아 보여도 그 고유 역할을
중복하기 전에 좁은 통합만으로 사용자 과제가 끝나는지 확인한다. 통합이 부족하고
Underwrite 고유 결과를 자신 있게 개선할 근거가 있다면 새 구현을 허용한다.
사용자 단계·의미·유지비용·측정된 결과를 비교하지, 새 코드의 양을 목표로 삼지
않는다. 독자적인 도메인 측정이 Underwrite의 고유 의미일 때는 계속 소유할 수 있다.

안정적인 공통 필드는 `meaning`, `unit`, `coverage`, `evidence`, `method identity`다.
도메인별 지표는 확장 가능하되 모든 지표를 모든 에이전트에 적용하지 않는다.
결과(outcome), 과정/도구 정확성, grounding, 효율, 복구, 검증/안전은 별도 축이다.
측정 자료가 없거나 비교 전제가 충족되지 않으면 해당 축을 `not_measured`로
두고 범용 `0–100` 점수를 만들지 않는다.

| 축 | 제안하는 최소 관측과 단위 | 성립하지 않을 때 |
| --- | --- | --- |
| 결과 | 결합된 사례·반복의 verifier disposition, 선언된 결과 의미 | 결과/분모가 없으면 `not_measured` |
| 과정·도구 | 사례별 호출 수, 관측된 error/status, 명시적으로 검증한 입력·결과 | 인수·결과가 없으면 “도구 사용 오류” 원인 귀속 보류 |
| grounding | 확인 가능한 자료 참조와 외부 평가 기준의 연결 | 문장 유사도만으로 사실성 판정 금지 |
| 효율 | 동일 조건의 시간·token·비용·호출 수와 결과 비열화 제약 | 호출 감소만으로 개선 판정 금지 |
| 복구 | 실패 뒤 실제 수정·재시도·성공 결합 | 재시도 link가 없으면 재시도 성공률 미측정 |
| 검증/안전 | 요구된 verifier 실행, 결과와 승인 경계 | verifier 생략을 비용 절감으로 보상하지 않음 |

선택적 provenance sidecar는 사례와 span의 결합 외에 `call_id`, `turn_id`,
`parent`, `retry_of`, `result_consumed_by`, 보호된 인수/결과 참조 또는
fingerprint, capture completeness와 redaction 상태를 제공할 수 있다. 이들 중
없는 필드는 `unknown`/`not_measured`로 남긴다. fingerprint도 알려진 비밀·
콘텐츠와 연결될 수 있으므로 원문 대체가 곧 비공개 보증은 아니다. 삽입·병렬
실행을 허용해야 하므로 `n`번째 단계끼리 맞추지 않는다. 비교 identity에는
최소한 입력·모델·도구 버전·평가기·예산·반복/계획 단위가 들어가야 한다.
질문 문자열만 같다는 이유로 같은 조건으로 간주하지 않는다.

### 미래 산출물 모양 예시 — **현재 미구현**

다음은 **교환 형식·스키마·작동하는 명령의 예가 아니라 설계 스케치**다. 현재 CLI가
받는 입력도 아니다. `<...>`는 실제
결합·재해결한 참조로 바뀌어야 한다. 현재 Pi trace는 도구의 `ok/error`만으로
도구 인수 오류를 증명할 수 없으므로, 아래 인수 가설을 확인하려면 보호된
인수/결과 캡처가 선행되어야 한다. 참조를 해결할 수 없으면 finding을 발행하지
않고 캡처/결합 과제를 반환한다.

```json
{
  "example_only": true,
  "state": "investigate",
  "comparison_identity": {
    "baseline": "<bound-baseline-run-ref>",
    "candidate": "<bound-candidate-run-ref>",
    "input": "<bound-input-ref>",
    "model": "<model-version-ref>",
    "tool": "<tool-version-ref>",
    "evaluator": "<evaluator-ref>",
    "budget": "<budget-ref>",
    "repeats": "<planned-repeat-ref>"
  },
  "coverage": {"planned_cases": 1, "bound_cases": 1, "complete_traces": 2},
  "evidence_refs": [
    {"role": "baseline_tool_status", "ref": "<bound-baseline-case-and-span-ref>"},
    {"role": "candidate_tool_status", "ref": "<bound-candidate-case-and-span-ref>"},
    {"role": "baseline_verifier_outcome", "ref": "<bound-baseline-case-outcome-ref>"},
    {"role": "candidate_verifier_outcome", "ref": "<bound-candidate-case-outcome-ref>"}
  ],
  "observed_finding": "같은 계획 사례에서 baseline tool은 error·검증 실패, candidate tool은 ok·검증 성공으로 기록됨",
  "hypothesis": "도구 인수의 차이가 오류에 기여했을 수 있음 — 아직 미검증",
  "alternatives": ["도구 서비스 장애", "다른 도구 버전 또는 실행 환경"],
  "limitations": ["도구 인수/결과 미캡처", "관측 차이의 원인 미확립"],
  "attribution": "insufficient",
  "target_snapshot": null,
  "next_task": {
    "kind": "investigate",
    "steps": [
      "원본 사례·span·outcome 참조와 capture completeness를 재확인",
      "보호된 call ID·인수/결과 참조를 수집해 실제 오류 이유를 확인",
      "대체 설명과 누락된 verifier 실행 여부를 점검"
    ]
  },
  "rerun_controls": ["동일 입력·모델·도구·평가기·예산·반복 계획을 고정"],
  "success_constraints": ["원치 않는 도구 실패로 확인된 경우에만 오류 감소 평가", "사례 결과 비열화 없음"],
  "nonregression_constraints": ["verifier 실행 유지", "비용·시간 증가를 별도로 보고"],
  "authority": {"human_review": "required", "apply_authorized": false, "merge_authorized": false}
}
```

실제 결과에서는 자리표시자, 해결 불가능한 `evidence_refs`, 추정한 target 경로를
허용하면 안 된다. 대상 snapshot/ref를 실제로 확인한 **변경** 과제만 Goppi의 기존
제안기 입력으로 연결한다. 귀속이 부족해도 단순 “판단 보류”로 끝내지 말고 위와
같은 **구체적 조사·계측 과제**를 반환한다.

## 후속 개발 초안 — 순서, 검증, 중단 조건

이 표는 구현 승인이나 `0.0.2` 범위 편입이 아니다. 각 단계는 앞 단계의 근거와
독립 검토를 통과해야 하며, 두 저장소의 버전·공개 일정은 따로 결정한다.

| 단계 | 소유자·재사용 지점 | 산출물과 선행 조건 | 반드시 거부할 반례 / 중단 조건 |
| --- | --- | --- | --- |
| **P0 — 생산자 인계와 동결 fixture** | Goppi: 기존 runner/trace/binder/digest owner, post-aggregation trace 보관·인증 및 제안된 별도 버전 companion; Underwrite: 현재 bounded read·의미·coverage 경계 | **미병합 후보 anchor인 paired exchange v1의 main 채택·동결 여부부터 확인**한다. 그 바이트를 유지하고 정확한 manifest digest에 연결된 portable companion을 Goppi가 실제로 제공하며, native Pi 2사례×2반복×2arm 결정론적 binder fixture를 동결한다. 원본 가용성·계측 완전성·export redaction을 각각 표기한다. Underwrite는 그 뒤에만 원시 바이트·manifest closure·상호 참조·선언 roster·coverage·출처 주장 한계를 검사할 최소 소비자 경계를 결정한다. | 응답을 구현으로 취급하거나 세 번째 trace/outcome 복사본·Python Goppi digest/bind·가짜 `--trace --evidence` 입력을 만들지 않는다. `not_retained`를 재구성하지 않는다. export/fixture가 없으면 새 CLI 없이 기존 부분 검사 결과와 구체적 capture 과제를 반환한다. 잘못된 run/arm/repeat/verifier/harness, 누락·중복 span, digest만 있는 trace, 중단·redaction·canary 반례가 동결되어야 P1에 들어간다. |
| **P1 — 첫 수직 슬라이스** | Underwrite: P0에서 실제로 제공된 버전의 bounded adapter·순수 기술 비교·현재 JSON 오류 소유자; Goppi: 생산자 고유 검증·protected storage | **동결된 export/fixture와 사용자 필요가 확인된 뒤에만** 같은 사례/반복의 tool `error/ok`와 verifier `false/true`를 근거 참조·coverage와 함께 기술하고 호출 에이전트의 다음 조사 과제를 JSON으로 반환한다. 소비자는 원시 바이트/manifest/join을 확인하지만 비공개 원본→projection이나 생산자 인증을 독립 증명했다고 말하지 않는다. | `not_measured`만 출력하려는 새 bundle-audit/CLI를 선제 출하하지 않는다. 기존 부분 inspection으로 충분하면 이를 재사용한다. 비교 identity가 다르거나 verifier 계측이 없으면 알 수 없는 상태와 수집 과제를 반환한다. 유익한 retry·결과 악화·expected error를 무시하거나 error만으로 인수 오류라 단정하지 않는다. 원인 귀속이 부족하면 `investigate`를 내고 임의 확률·점수는 내지 않는다. |
| **P2 — 선택적 과정 provenance** | Goppi: Pi lifecycle에서 이미 보는 call ID/인수/결과와 보호 저장; Underwrite: P0 handoff·P1 비교 | 선택적 `turn/call/retry_of/result_consumed_by`와 보호 참조·완전성 표시, 병렬·삽입을 견디는 관계 비교. Claude 등 좁은 exporter는 지원 필드만 명시. | 순서가 바뀐 병렬 호출·앞에 삽입된 단계·부모 span만 있는 경우를 같은 인과/재시도로 판정하지 않음. 비공개 참조 누출, 반복/span 수를 독립 사례 `n`으로 승격하면 중지. |
| **P3 — 제안과 통제된 재실행** | Goppi: 기존 `proposeReviewArtifact`/proposal-import·재실행·human review; Underwrite: 근거 결합 finding, 의미·비열화 제약 | 확인된 target snapshot과 충분한 근거가 있을 때만 `change` 과제를 기존 제안기로 넘기고, 그 외에는 `investigate`/계측 과제. 고정 입력·도구·모델·평가기·예산·반복에서 결과/과정/검증·비용을 따로 비교. | 무개입 상관만으로 인과 신뢰도 수치(예: `0.93`)를 만들지 않음. 결과가 나빠졌거나 verifier가 빠졌으면 호출 절감만으로 성공 처리하지 않음. 적용/병합 권한은 계속 false; 재실행 조건을 고정할 수 없으면 효과 주장을 중단. |

P0의 성공 조건은 해당 fixture에서 재해결 가능하고 제한·redaction·로컬 인증
상태가 정직하게 드러나는 생산자 결합이다. P1의 성공 조건은 고정된 비교 identity
안에서 정확한 기술 finding과 다음 과제이며, 위 반례의 거부/불완전 상태를
포함한다. 어느 단계도 자동 패치 적용·main 병합·유료 provider 호출이나 실제
세계의 에이전트 자기개선 주장을 승인하지 않는다.

P1의 `error` 수는 **과정 관측**이지 보편적 품질 악화가 아니다. 의도적인
negative probe나 기대한 `not_found`도 유효한 도구 사용일 수 있다. 도구 선택의
정확성, grounding, 검증 적절성은 span 이름·순서에서 추론하지 않고 해당 도메인의
명시적 의미, rubric과 계측된 결합을 요구한다. trace가 완전하고 해당 verifier의
계측 범위가 확인되지 않았다면 verifier span 부재를 “검증 생략”이라 하지 않는다.
`verification_not_observed/unknown`과 계측 과제를 반환한다. 잘린·redacted
trace의 호출/오류 수 0도 “없음”의 증명이 아니다.

### 단계별 수용 반례

| 입력·변화 | 허용되는 결론 / 필요한 다음 작업 |
| --- | --- |
| 후보가 verifier를 실행하지 않아 비용이 낮아 보임 | trace 완전성과 verifier 계측 범위가 모두 확인된 경우에만 생략 finding; 아니면 `not_observed/unknown`과 계측 과제. 어떤 경우에도 비용만으로 개선 판정하지 않는다. |
| 오류 뒤 재시도로 성공한 사례 | 명시적 `retry_of`와 결과 소비가 없으면 재시도 효용은 미측정. 확인된 경우에도 유익한 복구를 일괄 낭비로 세지 않는다. |
| 병렬 도구 호출의 완료 순서가 바뀌거나 앞에 단계가 삽입됨 | ordinal `n` 대 `n` 정렬을 거부하고 관계/의미 기반 비교로 제한한다. |
| trace가 잘리거나 redact되어 호출/오류가 0으로 보임 | `zero_observed`와 `none_proven`을 구분하고 coverage 보완 과제를 낸다. |
| 에이전트의 자기 설명과 실제 도구 기록이 충돌 | 도구 기록과 `self_report`를 각각 제시하고 충돌을 조사 과제로 남긴다. |
| span 또는 반복 수가 늘었음 | 독립 사례 수 `n`은 늘리지 않고 계획된 사례·반복 단위로 집계한다. |
| 근거 참조가 해시·경로만 있거나 재해결되지 않음 | finding/변경 과제를 발행하지 않고 정확한 바이트·사례·span binding의 수집을 요구한다. |

실제 P0 export를 확정할 때 두 제품 소유자가 정할 항목은 다음과 같다.

1. Goppi 응답의 **별도 버전 companion 제안**을 실제 export 계약·동결 fixture로
   좁혀 승인할지 정한다. paired exchange v1 바이트 보존, manifest digest 참조,
   사례별 trace 바이트/참조와 개인정보 보호·삭제·보관 경계를 확인한다. 미병합
   draft PR #13 결과를 main 계약으로 추정하지 않는다.
2. 원본 가용성, 계측 프로필의 completeness, export redaction을 독립적으로
   표현한다. 예정 사례·실행·span의 coverage, 보호 참조 재해결 권한, byte/row
   상한과 불완전 자료에서 허용할 관측 finding을 정한다.
3. 비교 identity와 통제 재실행에서 반드시 같아야 하는 필드, 의도적으로 바뀐
   필드, 현장에서 검증할 수 없는 필드를 구분한다.
4. `investigate`를 기존 Goppi CLI/MCP 다음 작업으로 어떻게 전달할지, 사람이
   승인한 `change`만 기존 proposal-import로 연결할지 정한다. Underwrite가
   패치 생성 권한을 가져오는 설계는 이 계획에 없다.

위 결정은 이 **현황 문서 작성**을 막지 않는다. Goppi가 export·fixture를 실제로
제공해야 P0의 소비자 계약과 Underwrite 어댑터 필요 여부를 판단할 수 있다.

## 근거 링크

- [U1] Underwrite 현재 사건 진단과 네이티브 입력
- [U2] Underwrite all-k 수식과 비-pass@k 한계
- [U3] Underwrite 방향별 e-value, [beta-mixture 엔진](../../src/underwrite/measurement/eprocess.py#L1-L7)
- [U4] Underwrite 의미 선언·가정·한계
- [U5] Underwrite primary repeat 0과 별도 반복 집계
- [U6] Underwrite review-pair의 선언 경계
- [U7] Underwrite 외부 result의 action count와 한계, [집계 owner](../../src/underwrite/instrument/evidence/common_result.py#L92-L111)
- [U8] Underwrite DeepEval metric binding 계약, [Langfuse accounting](../../src/underwrite/instrument/ingest/langfuse_accounting.py), [Promptfoo accounting](../../src/underwrite/instrument/ingest/accounting.py)
- [U9] Underwrite OTLP span/events/links, [OpenInference 보존](../../src/underwrite/instrument/ingest/codecs/openinference.py#L375-L382), [Inspect EvalLog 보존](../../src/underwrite/instrument/ingest/codecs/inspect.py#L187-L210)
- [U10] Underwrite metric 정의의 값과 표본 단위
- [U11] Underwrite CLI의 현재 event_read 연결, [이전 jury 기반 read](../../src/underwrite/measurement/read.py#L10-L24)
- [G1] Goppi main Pi tool lifecycle
- [G2] Goppi main Pi trace output
- [G3] Goppi main 엄격한 span 계약
- [G4] Goppi 개발 분기 Claude preview 범위, [집계 usage](https://github.com/ntts9990/goppi/blob/69b46e79168893fdf167fb4af3fef30e4755285d/src/runner/claude-agent-runner.ts#L475-L549)
- [G5] Goppi main verifier bundle digest와 사례 결과
- [G6] Goppi 개발 분기 paired export의 bundle digest 반복
- [G7] Goppi main 제안 생성·권한·귀속 검사
- [G8] Goppi main 작성된 가설을 받는 proposal-import
- [G9] Goppi 개발 분기 7상태 비교와 효과 비측정
- [G10] Goppi 개발 분기 attempt 집계, [`include_attempts` 출력](https://github.com/ntts9990/goppi/blob/69b46e79168893fdf167fb4af3fef30e4755285d/src/application/run-query.ts#L651-L661)
- [G11] Goppi main 이진 전이 셀과 primary repeat 결합, [fixed-horizon 계산](https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/measurement/fixed-horizon.ts#L15-L70)
- [G12] Goppi main 측정 프로필의 결측·반복·신뢰도 경계
- [G13] Goppi main [resource의 사례·실행 속성](https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/adapters/trace-output.ts#L27-L36)과 trace 계약의 사례별 span 묶음
- [G14] Goppi main [trace identity와 outcome 대조](https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/binding/identity.ts#L17-L72), [verifier binding](https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/binding/bind.ts#L60-L83)
- [G15] Goppi main live-execution attestation의 verifier bundle 기록과 digest 인계
- [G16] Goppi main [trace 정규화·digest](https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/binding/trace-digest.ts#L11-L49), [verifier evidence digest](https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/binding/verifier-evidence-digest.ts#L13-L24)
- [G17] Goppi **미병합 구성 증명**의 [paired exchange v1 네 파일 closure](https://github.com/ntts9990/goppi/blob/38b99893659be9b1f202075c75be85086f0356cb/src/application/paired-run-exchange.ts#L13-L23)와 [행의 결과 identity](https://github.com/ntts9990/goppi/blob/38b99893659be9b1f202075c75be85086f0356cb/src/application/paired-run-export.ts#L253-L278)
- [G18] Goppi main의 [사례 trace 집계와 digest 계산](https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/application/live-execution/paired-execution.ts#L457-L473)

[U1]: ../../src/underwrite/measurement/event_read.py#L106-L173
[U2]: ../../src/underwrite/measurement/calibration.py#L147-L164
[U3]: ../../src/underwrite/measurement/directional.py#L16-L61
[U4]: ../../src/underwrite/measurement_contract/semantics.py#L17-L64
[U5]: ../../src/underwrite/instrument/evidence/paired_binary.py#L469-L536
[U6]: ../../src/underwrite/cli/review_pair.py#L24-L44
[U7]: ../../src/underwrite/cli/inspect_result.py#L21-L33
[U8]: ../../contracts/deepeval_metric_bindings.v1.schema.json
[U9]: ../../src/underwrite/instrument/ingest/codecs/otlp_json.py#L34-L56
[U10]: ../../contracts/metric_definition.v1.schema.json#L30-L55
[U11]: ../../src/underwrite/cli/measurement_flow.py#L66-L87
[G1]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/adapters/lifecycle.ts#L181-L225
[G2]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/adapters/trace-output.ts#L39-L85
[G3]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/contracts/trace.ts#L50-L65
[G4]: https://github.com/ntts9990/goppi/blob/69b46e79168893fdf167fb4af3fef30e4755285d/src/application/live-execution/native-claude-preview.ts#L23-L29
[G5]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/binding/types.ts#L80-L145
[G6]: https://github.com/ntts9990/goppi/blob/69b46e79168893fdf167fb4af3fef30e4755285d/src/application/paired-run-export.ts#L233-L278
[G7]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/proposals/service.ts#L24-L125
[G8]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/application/live-execution/proposal-import.ts#L34-L48
[G9]: https://github.com/ntts9990/goppi/blob/69b46e79168893fdf167fb4af3fef30e4755285d/src/application/offline-assessment/comparison.ts#L171-L245
[G10]: https://github.com/ntts9990/goppi/blob/69b46e79168893fdf167fb4af3fef30e4755285d/src/application/live-execution/attempt-diagnostics.ts#L105-L144
[G11]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/measurement/paired.ts#L16-L131
[G12]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/docs/MEASUREMENT_PROFILES.md#L55-L70
[G13]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/contracts/trace.ts#L74-L100
[G14]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/binding/bind.ts#L60-L83
[G15]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/application/live-execution/cycle.ts#L434-L470
[G16]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/binding/trace-digest.ts#L11-L49
[G17]: https://github.com/ntts9990/goppi/blob/38b99893659be9b1f202075c75be85086f0356cb/src/application/paired-run-exchange.ts#L13-L23
[G18]: https://github.com/ntts9990/goppi/blob/9814f533c0d629f8b87ce43388d820f3ac3be213/src/application/live-execution/paired-execution.ts#L457-L473
