# 에이전트 표면 프로그램 (첫 판)

작성: 2026-11-16. 최초 표면 측정 코드 기준: `1d344a8`. 상태: **A1 검증 완료, A2 읽기 전용 도구 22개 검증 후 추가 확장 잠정 동결, A3 일곱 도구 첫 관측 평가 완료.**
결정 근거: [ADR-024](../adr/ADR-024-read-only-agent-surface.md).

## 0. 이 문서의 위치와 범위

- 코딩 에이전트가 Underwrite를 부르는 표면(CLI JSON, Python API, skill, 앞으로의 MCP 서버)을
  하나의 프로그램으로 다룬다.
- ADR-024의 경계를 따른다. 호출한 클라이언트가 띄우는 로컬 stdio MCP 서버만 두고,
  읽기 전용이며, CLI 결과 계약을 그대로 투영한다. 네트워크 서버, 상주 프로세스, 웹 화면은
  만들지 않는다.
- 스키마, 오류 상태, 필드 이름은 Underwrite 어휘를 쓴다.
- 순서: **측정 → 결과 계약과 오류 registry → 읽기 전용 서버와 첫 관문 → 도구 선택 평가 →
  (필요할 때만) 효과가 있는 도구**. 앞 단계가 끝나기 전에 다음 단계 코드를 넣지 않는다.

## 1. 현재 표면 측정

`1d344a8`, macOS, `uv run --frozen --no-sync`로 한 번 잰 값이다. 바이트는 stdout 기준이다.
표면 전체의 재현 스크립트는 A0에서 만든다. 그 전까지 표면 수치는 참고값이다.
오류 literal 후보만 `scripts/structure_metrics.py`에서 A1 측정법으로 다시 센 기준값이다.

| 항목 | 값 | 재는 방법 |
| --- | --- | --- |
| 최상위 명령 | 27개 항목(명령 26개와 별칭 `project`) | `underwrite --help` |
| `underwrite --help` | 3,086 B | `underwrite --help \| wc -c` |
| 명령별 `--help` 합계 | 22,149 B(27개). 최대 `project` 1,896 B, `ingest` 1,889 B, `records` 1,769 B | 명령마다 `underwrite <cmd> --help \| wc -c` |
| `capabilities --json` | 12,310 B. `capabilities.v1`, 명령 26개, profile 57개, codec 10개 | `underwrite capabilities --json \| wc -c` |
| JSON 계약 | `contracts/` 아래 스키마 82개 | `find contracts -name '*.schema.json' \| wc -l` |
| 공통 결과 봉투 | 없음. `next_action(s)`는 스키마 35개, `exit_code`는 26개, `interpretation_limits` 계열은 goppi trajectory 계약 8개에만 있음 | `grep -lE '"next_actions?"' contracts/*.schema.json` 등 |
| 오류 literal 후보 | A1의 현재 측정법으로 오류 생성·`require` 호출의 모든 인자 위치에 있는 대문자 문자열 675개, 108모듈. 범주 코드, 세부 reason, action이 섞임 | `scripts/structure_metrics.py`의 `error_code_estimate` |
| 권한 상수 | `acceptance/decision.py:287`의 `"human_review": "required"`, `:226`의 `AUTHORITY_FIELD_REFUSED` | 코드 확인 |
| 신뢰할 수 없는 문자열 | 전용 이스케이프 없음. `cli/evidence.py:24-26`의 `display()`가 `ensure_ascii=True`로 비 ASCII를 부수적으로 이스케이프하고 값마다 1,024자에서 자름 | 코드 확인 |
| 출력 상한 | 모듈마다 따로 있음. 256 KiB(`cli/release.py:16`)부터 8 MiB(`instrument/evidence/run_audit.py:13`)까지. `cli/records.py:30` 2 MiB, `cli/evidence.py:19` 1 MiB. 거부 코드는 `INPUT_LIMIT_EXCEEDED`, `OUTPUT_LIMIT_EXCEEDED` | 코드 확인 |
| 런타임 의존성 | 3개(`underwrite-core`, `jsonschema`, `pyyaml`). MCP SDK 없음 | `pyproject.toml` |
| `AGENT_USAGE.md` | 109,231 B | `git show 1d344a8:AGENT_USAGE.md \| wc -c` |


## 2. 문제: 표면의 owner가 하나가 아니다

- **발견(discovery)의 owner는 이미 하나다.** `cli/main.py`의 명령 descriptor와 codec registry가
  `capabilities.v1`을 만든다. MCP 도구 목록도 여기서 파생해야 한다. 손으로 따로 적은 도구 목록은
  두 번째 진실이 된다.
- **결과 의미는 명령마다 맞지만 봉투가 없다.** 상태, 사유, 한계, 다음 행동, 종료 코드가 계약마다
  다른 이름과 위치에 있다. 에이전트는 명령마다 해석법을 따로 배워야 한다.
- **오류 코드가 흩어져 있다.** 오류 호출에서 찾은 대문자 literal 후보 675개가 108모듈에 있다. 같은 실패 의미가
  여러 이름을 갖는지, 한 이름이 여러 의미를 갖는지 기계로 확인할 수 없다.
- **모델이 보는 문장에 대한 보호가 부수적이다.** 이스케이프와 자르기가 표시 helper의 부수 효과다.
  MCP의 text와 structuredContent 두 채널을 맡는 별도 owner가 필요하다.

## 3. 설계 원칙

1. **투영만 한다.** MCP 도구 하나는 CLI profile 하나에 대응하고, 같은 서비스 함수를 불러 같은
   결과 문서를 돌려준다. 서버는 판정, 오류 의미, 한계, 다음 행동을 새로 만들지 않는다.
   structuredContent는 CLI stdout JSON과 같은 스키마를 따르고, CLI stdout을 parse한 값과 같다.
   필요한 모양이 CLI 계약에 없으면(예: 상한 초과 때의 작은 결과) CLI 계약을 먼저 넓히고 CLI도 쓴다.
2. **실패 종류를 정규 필드에서 구분한다.** `not_measured`, `indeterminate`, `abstained`, 입력 거부,
   한도 초과, 저장 실패를 한 값으로 접지 않는다. 기존 계약의 구분을 그대로 드러낸다.
3. **읽기 전용이다.** 기록 저장처럼 파일을 쓰는 도구는 A4에서 따로 결정한다.
   도구 annotations로 효과를 밝히지만(`readOnlyHint` 등), annotations는 클라이언트에 주는 힌트일 뿐
   강제 수단이 아니다.
4. **신뢰할 수 없는 문자열은 두 갈래로 막는다.** 입력 파일의 경로, 이름, 자유 텍스트가 대상이다.
   - **text 채널과 CLI JSON 출력:** 제어·서식 문자(Unicode Cc, Cf, Zl, Zp)를 `\uXXXX`로 이스케이프한
     JSON을 낸다. 지금은 CLI 출력기의 `json.dumps` 기본값(`ensure_ascii=True`)이 이 역할을 하므로,
     이 설정을 끄는 변경은 이 보장을 깬다. 성공 기준은 "text를 parse한 값이 structuredContent와 같고, text에 원문 Cc·Cf·Zl·Zp가
     없다"이다. text만 모델에 넘기는 클라이언트에서 U+2028이나 U+202E가 가짜 줄이나 가짜 필드를 만들지
     못하게 한다.
   - **structuredContent:** 클라이언트가 JSON을 풀면 직렬화 이스케이프가 사라지므로 계약 설계로 막는다.
     에이전트가 행동하는 필드(상태, 판정, 사유 코드, 요약, 다음 행동, 오류)에는 Underwrite가 소유한
     어휘 값과 문법을 거친 식별자만 넣는다. 신뢰할 수 없는 문자열은 대상(subject) 안의 데이터 필드에만
     두고, 길이 상한과 문자 예산을 두며, 스키마 설명에 "데이터"라고 밝힌다. 이 표현 규칙은 A1이 정하고
     CLI도 같은 값을 쓴다.
   - **남는 위험:** 클라이언트가 데이터 필드를 원문으로 화면에 그리면 그 화면은 보호되지 않는다.
     출처 표시는 열린 후속이다.
5. **결과 크기에 상한을 둔다.** 상한을 넘으면 판정, 모집단, 잘린 사실을 담은 작은 결과를 낸다.
   잘렸다는 사실을 숨기지 않는다. 그 결과의 모양과 코드(`OUTPUT_LIMIT_EXCEEDED`)는 A1이 정한다.
6. **입력 검증 실패는 도구 실행 오류다.** `isError: true`, 오류 코드, 다음 행동을 낸다.
   [MCP 명세 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)를 따른다.
7. **권한을 만들지 않는다.** 결과는 병합, 배포, 승인 권한을 주지 않는다. 사람 검토가 필요한 곳은
   기존 계약대로 그렇게 적는다.
8. **이름은 `underwrite_` 접두로 시작한다.** 다른 MCP 서버와 같은 클라이언트에 붙어도
   겹치지 않게 한다.
9. **의존성은 비교 뒤에 정한다.** 공식 MCP Python SDK와 표준 라이브러리 stdio JSON-RPC 구현을
   명세 적합성, 유지 비용, 설치 크기, 순수 계층 격리로 비교하고 결과를 기록한 뒤 고른다.

## 4. 조각과 성공 기준

| 조각 | 내용 | 성공 기준 | 하지 않는 것 |
| --- | --- | --- | --- |
| **A0 측정** | 1절 표를 다시 만드는 측정 스크립트. 도구 후보 목록(명령 descriptor에서 파생)과 설명 초안의 모델 노출 바이트. 설명마다 "언제 쓰는가/언제 쓰지 않는가" 슬롯 | 같은 커밋에서 같은 숫자. 오류 코드는 `scripts/structure_metrics.py`의 측정을 모든 인자 위치로 넓혀 A1 기준값을 정한다 | 관문은 아직 두지 않는다. 예산과 줄어들기만 하는 래칫 관문은 A2에서 첫 도구와 함께 preflight 선택기 안에 둔다 |
| **A1 결과 계약과 오류 registry** | 오류 코드 registry 하나(코드, 실패 종류, 종료 코드, 다음 행동 종류). 기존 결과 계약에 공통 투영 규칙(상태, 사유, 한계, 다음 행동, 권한 상수)을 정의. 상한 초과 때의 작은 결과 모양. 흩어진 literal을 registry로 옮기는 래칫 | registry 밖 literal이 줄어들기만 함(관문). 같은 의미의 다른 코드는 registry 안의 별칭 표로 묶어 출력 코드는 그대로 둠(owner는 registry 하나). 별칭을 걷어 내는 조건(계약 버전을 올릴 때)을 registry에 적음. 다른 의미의 같은 코드는 0. 기존 CLI stdout/stderr/종료 코드 바이트 불변(회귀 시험). 행동 필드 가운데 이미 입력 문자열을 담는 곳을 목록으로 만들고, 그 이동은 이행 계획을 가진 별도 기능 조각으로 넘김 | 기존 스키마 필드 이름이나 출력 코드를 바꾸지 않는다. 새 봉투로 기존 출력을 감싸지 않는다 |
| **A2 읽기 전용 stdio 서버** | `underwrite_` 도구. 첫 범위는 `capabilities`, `audit-*`, `inspect`, `inspect-result`, `review-artifacts`, `review-pair`, `records list/show/read-json/verify`. 실험 프로필 `review-handoff`(P7)는 넣지 않는다. 이스케이프 owner와 크기 상한 owner 하나씩. 예산 관문, 줄어들기만 하는 래칫, "언제 쓰는가/언제 쓰지 않는가" 슬롯 규칙을 첫 도구와 **같은 커밋**에 넣는다. 처음 상한은 A2에서 잰 값 그대로(여유 0)이고, 올릴 때는 이유를 기록한다. README와 `AGENT_USAGE.md`의 해당 문장 갱신 | 도구마다 structuredContent가 같은 입력의 CLI stdout을 parse한 값과 같음. text를 parse한 값과 structuredContent의 일치, text의 원문 Cc·Cf·Zl·Zp 부재, 행동 필드에 입력 문자열이 들어가지 않음, 상한 초과 시험, 잘못된 입력의 `isError` 시험이 실제 stdio 진입점으로 통과. 보안 검토 통과 | 파일 쓰기, 네트워크, 모델 호출, 상주 프로세스 |
| **A3 도구 선택 평가** | 의도 묶음을 개발용과 held-out으로 나눔. 첫 호출 정확도, 호출 수, 금지 행동, 결과 해석 충실도. 같은 의도를 다시 쓰지 않음 | 사전에 정한 채택 규칙(구간이 겹치지 않는 개선, 금지 행동 증가 없음). 측정하지 않은 개선은 주장하지 않음 | 유료 모델 실행은 소유자 승인 뒤에만 |
| **A4 효과가 있는 도구** | 필요가 증명될 때만. 예: `records` 저장 | 시작 플래그, annotations(힌트), 별도 결정 기록 | 기본 활성화 |

A1은 MCP 없이도 CLI 사용자에게 이득이다. 결과 해석이 쉬워지고 오류 의미가 한 곳에 모인다.
그래서 A2보다 먼저 한다. A0은 A1과 A2의 기준값을 정하는 측정이다.

### A0/A2 도구 측정과 경계

- `scripts/agent_surface_metrics.py`는 descriptor의 명령·프로필 수와 실제
  `tools/list` canonical JSON 크기, 도구별 전체·출력 스키마·설명 크기,
  고유 출력 스키마 총량과 정확히 중복된 스키마 묶음·중복 바이트를 잰다.
  첫 도구만 있을 때
  2,528바이트였고, `inspect-result` v1·v2 프로필을 각각 추가한 세 도구 목록은
  11,708바이트였다. Goppi baseline 입력 디렉터리 도구까지 포함한 네 도구 목록은
  23,348바이트였다. `inspect-result` actions 보기 두 개를 포함한 여섯 도구 목록은
  34,738바이트였다. 설명은 순서대로 230·243·243·271·247·247바이트다.
  저장된 trajectory 기록 요약 도구를 더한 일곱 도구 목록은 41,564바이트이고,
  마지막 설명은 290바이트다.
- `tests/gates/test_agent_surface_budget.py`는 현재 측정값을 여유 없는 상한으로
  고정하고 설명·목록이 늘면 실패한다. 이번 증가분은 두 입력 계약을 별도 도구로
  구분하고 각각 설치된 출력 스키마를 노출하는 비용이다. 두 번째 증가분은
  `goppi-baseline-exchange-v2`에서 `evidence_result.v2`를 투영하는 데 필요한
  설치된 출력 스키마와 도구 설명의 비용이다. 세 번째 증가분은 두 입력 계약의
  `actions` 보기를 구분하고 닫힌 행동·의미 결합 개수 계약을 노출하는 비용이다.
  일곱 번째 도구에서 전체 `trajectory_record_read.v2` 계약(60,930바이트)을
  투영하면 MCP 성공 응답도 크게 중복된다. 그래서 CLI에 16KiB 이하인
  `trajectory_record_read_summary.v1` 보기를 별도로 추가했다. 도구 목록에는 이
  작은 설치 스키마(6,176바이트)를 싣는다. 목록 증가분은 6,826바이트다.
  A3 첫 관측의 `h01`에서 입력 파일이 없다는 이유로 `capabilities` 호출을
  놓쳐, 파일이 필요 없다는 문장을 설명에 명시했다. 설명은 230→221바이트,
  전체 목록은 41,564→41,555바이트로 줄어 관문 상한도 함께 낮췄다.
  여덟 번째 `review-pair` 요약 도구는 설치 스키마와 세 입력 설명을 포함해
  목록을 5,988바이트 늘린 47,543바이트이며 설명은 242바이트다.
  독립 검토에서 선언·이력·구획 상태와 `limitations`가 모순될 수 있는
  출력 스키마 결함을 찾아 원본 계약의 조건별 필수·금지 규칙을 모두
  투영했다. 설치 스키마가 552바이트 늘어 현재 목록 상한은 48,095바이트다.
  아홉 번째 `review-artifacts` strict 요약 도구는 세 필수 입력과 선택적
  source-bound case map, 새 설치 스키마를 싣는다. 관계·독자 버전 상태·작업
  코드별 소유자와 집계 키 유일성을 계약에 묶어 목록은 10,363바이트 늘어난
  58,458바이트이고 설명은 356바이트다. 기존 `review`·`readiness` CLI 계약을
  강화하지 않아 이전에 schema-valid했던 보관 문서를 거부하지 않는다. 새
  요약 계약만 현재 출력의 고정 한계 9개를 모두 요구한다.
  열 번째 `review-artifacts` readiness 요약 도구는 누락 메타데이터의 blocker
  코드별 개수와 다음 작업을 담는 설치 스키마를 싣는다. strict 요약의 불필요한
  schema 조각을 함께 제거한 뒤 목록은 순증 12,046바이트인 70,504바이트,
  설명은 380바이트다. 기존 readiness CLI 본문은 그대로 둔다.
  열한 번째 `records list` 페이지 도구는 설치된 페이지 계약과 이어 읽기
  인자와 각 현재 위치·다음 위치 쌍의 모순을 막는 조건을 포함해 목록을
  8,529바이트 늘린 79,033바이트이고 설명은 353바이트다.
  열두 번째 `records show` manifest 요약 도구는 정확한 파일 이름을 16개씩
  제공하는 설치 계약과 이어 읽기 인자·검사 조건을 포함해 목록을
  7,876바이트 늘린 86,909바이트이고 설명은 432바이트다.
  열세 번째 `records verify` 요약 도구는 설치된 결과 계약과 정확한 기록 ID
  입력을 포함해 목록을 3,167바이트 늘린 90,076바이트이고 설명은 300바이트다.
  열네 번째 `records read-json` 한정 도구는 기존 설치 계약과 정확한 파일
  선택자를 재사용한다. 내용은 선택 파일의 저장 바이트를 확인한 뒤 전체
  응답이 16KiB 이하일 때만 반환한다. 목록은 2,573바이트 늘어난
  92,649바이트이고 설명은 389바이트다.
  열다섯 번째 `inspect` 도구는 기존 `artifact_inspection.v1` 계약과
  단일 manifest 선택자를 싣는다. 목록은 3,456바이트 늘어난
  96,105바이트이고 설명은 341바이트다.
  열여섯 번째 `audit-promptfoo` 요약 도구는 기존 actions 계산에서 선언·
  제공 행의 개수와 finding 코드별 개수만 남기는 설치 계약을 싣는다.
  목록은 5,083바이트 늘어난 101,188바이트이고 설명은 347바이트다.
  열일곱 번째 `audit-counts` 도구는 같은 요약 계약에 manifest의 바이트
  정체성과 선택적 예상 해시 검사 상태를 명시한다. 두 지원 버전 프로필과
  입력 선택자가 목록을 6,990바이트 늘려 108,178바이트이며 설명은
  377바이트다.
  열여덟 번째 `audit-deepeval` 기본 회계 요약은 두 독자 프로필의 원래
  개수·선언 비교·한계 조건을 보존하고 사례·지표 이름과 평가기 오류 원문을
  제외한다. 목록은 10,347바이트 늘어난 118,525바이트이고 설명은
  402바이트다.
  열아홉 번째 DeepEval trace 요약은 제공된 사례·trace·span과 부모 참조
  상태의 개수를 원 계약에서 가져오고 개별 pointer를 제외한다. 목록은
  5,921바이트 늘어난 124,446바이트이고 설명은 316바이트다.
  스무 번째 owner-bound DeepEval 요약은 source와 binding pack의 서로
  다른 바이트 해시, 제공된 지표 상태·행동·binding 개수를 보존한다.
  목록은 7,882바이트 늘어난 132,328바이트이고 설명은 364바이트다.
  스물한 번째 `audit-langfuse` 요약은 blob-array와 CLI-list 독자의 별도
  집계·retrieval·누락 한계를 유지하고 관찰·점수 행을 제외한다.
  목록은 13,241바이트 늘어난 145,569바이트이고 설명은 356바이트다.
  스물두 번째 score-source 도구는 기존 설치 계약의 known/unknown source
  개수를 그대로 투영한다. 목록은 7,533바이트 늘어난 153,102바이트이고
  설명은 316바이트다.
  현재 22개 목록의 출력 스키마 합계는 137,560바이트(전체의 약 89.8%)이고,
  고유 스키마 합계는 123,031바이트다. 세 묶음에서 정확히 중복된
  14,529바이트는 전체의 약 9.5%다. A3 실행에서 사용한 동결 목록
  41,564바이트에 비해 현재 목록은 약 3.68배다. 실행 뒤 설명을 줄인
  일곱 도구 목록의 41,555바이트와는 구분한다. 이 값은 JSON 바이트이며
  모델 token 비용이나 선택 성능 측정이 아니다.
  **A2 도구 추가는 여기서 잠정 동결한다.** 첫 A3 관측은 일곱 도구만
  사용했으므로 나중에 추가한 15개 도구의 선택·해석 효과를 말할 수 없다.
  새 독립 의도 묶음과 현재 목록으로 비교하기 전에는 도구 병합, 외부 `$ref`
  또는 출력 스키마 생략을 단순한 크기 최적화로 취급하지 않는다. 현재
  예산 관문은 정확한 153,102바이트를 상한으로 유지한다.
  이후 증가는 용도와 비용을 기록하고 상한을 명시적으로 검토한다.
- `underwrite_capabilities`는 CLI의 `capabilities.v1`을 사용한다.
  `underwrite_inspect_result_v1`과 `_v2`는 각각 `evidence_result.v1`과 `.v2`
  파일을 CLI의 `inspect-result --json`으로 읽어 `common_result_inspection.v3`
  구조 요약만 돌려준다. 입력 계약이 도구와 다르면 CLI와 같은
  `UNSUPPORTED_RESULT_SCHEMA` 오류다. 성공 JSON text는 CLI stdout의 마지막 줄
  구분 개행만 뺀 본문이고 `structuredContent`는 이를 parse한 객체다. 입력·출력
  실패는 `isError`와 기존 `evidence_error.v1` text이며 부분 성공은 내지 않는다.
  요약은 원본 주장 검증, 측정, 승인이 아니다. 현재 CLI의 상한 실패도 기존 오류
  봉투로 투영한다. 아래의 **상한 초과 부분 결과** 초안은 차기 계약이며 아직 출력하지 않는다.
- `underwrite_audit_baseline`은 등록된 `goppi-baseline-exchange-v2` 프로필의
  로컬 exchange 디렉터리 하나를 CLI `audit-baseline --exchange ... --json`에 넘겨
  `evidence_result.v2`를 그대로 투영한다. 이 결과는 기술적 source claim이며
  Goppi verifier 실행, 독립 출처 확인, 개선 판정, 승인 권한을 더하지 않는다.
- `underwrite_inspect_result_v1_actions`와 `_v2_actions`는 각 입력 계약에 고정된
  CLI `inspect-result --view actions --json`을 투영한다. 요약에 항목별 다음 행동과
  선언된 의미 결합의 **개수**가 추가되며 원본 주장이나 검증 참조를 재검사하지 않는다.
  뒤에 추가된 `compare-results`는 두 actions 요약의 주변분포 차이와 항목 대응
  미확립을 별도 `common_result_marginal_comparison.v1` JSON으로 반환하는
  CLI/Python 경로다. 기본 MCP 22도구와 이 절의 고정 A3 catalog·점수에는
  포함하지 않는다. 같은 schema·단위에서 계산된 delta도 개별 항목 전이나
  의미적 개선을 뜻하지 않는다.
- `audit-trajectory`는 기본 읽기 전용 도구 목록에 넣지 않는다. 이 CLI는 임시
  스냅샷을 만들고 선택된 Goppi 실행 파일을 호출한다. 시간·출력 제한은 있으나
  실행 파일의 파일·네트워크 효과를 격리하지 못한다. [ADR-024](../adr/ADR-024-read-only-agent-surface.md)의
  효과 도구 규칙에 따라 별도 결정과 시작 시점의 명시적 허용 전에는 MCP 도구로
  표시하지 않는다. 현재 paired trajectory 검사는 CLI에서만 수행한다.
- `underwrite_records_read_trajectory`는 이미 저장된 기록의 `records
  read-trajectory --root ... --id ... --view summary --json`만 투영한다. 현재 저장 바이트와
  직접 링크를 검사하지만 Goppi를 호출하지 않고 원본 export를 재검사하지 않는다.
  성공 요약은 16KiB로 제한하며 전체 분석 본문은 의도적으로 생략한다.
  오류 분석의 상세 진단도 생략 상태와 full 보기 경로를 명시한다.
  `native_verification_performed_now: false`를 그대로 보존한다. 이 경로는 기록을
  새로 저장하지 않으며 기존 기록의 검증 이력을 새 권한으로 승격하지 않는다.
- `underwrite_review_pair` 조각의 실패 불변식은 전체 `paired_review.v1`이 문맥에서 온
  조건 이름·값·이력 문장을 최대 1MiB 출력에 포함한다는 점이다. MCP 기본 도구가
  이를 그대로 두 채널에 복사하면 선언 데이터가 모델의 지시 문장처럼 보일 수 있다.
  owner는 `cli/review_pair.py`의 결과 보기와 설치된 출력 계약이다. 기본 CLI
  `full` 출력 바이트는 유지하고, 새 `--view summary`가 기존 review를 한 번
  계산한 뒤 digest·상태·선언된 조건 분포·이력/partition 존재와 모집단 개수·
  고정 한계만 남긴다. 이름·값·사유 문장·taxonomy ID·이력 ID는 이 보기에 넣지
  않는다. 16KiB 상한과 생략 사실·full 보기 경로를 명시한다. MCP는 이 CLI
  profile만 투영한다. 세 입력 중 하나가 잘못되면 부분 결과 없이 기존 오류를
  내고, native verifier·저장·승인 기능은 추가하지 않는다.
- `underwrite_review_artifacts`는 strict `review-artifacts`에서 세 원본을 한 번씩
  읽고, 선택한 correspondence가 있으면 그 결속을 검사한다. 새 CLI `summary`
  보기는 source hash, reader profile·버전 선언 상태, 두 모집단, 대응 범위,
  발견 개수(합계가 짝 개수), 다음 작업의 `(code, owner, count)`와 고정 한계를 보존한다.
  짝별 값·분류·좌표, 일치하지 않은 좌표, producer 버전 원문은 생략하고
  16KiB로 제한한다. 사례 메타데이터가 부족하면 이 strict 경로는 기존 오류로
  거부한다.
- `underwrite_review_artifacts_readiness`는 원본 세 파일을 CLI
  `review-artifacts --view readiness-summary --json`으로 읽어 blocker의
  코드별 개수와 누락된 사례 ID·역할 상태, 다음 검증 작업, 모집단과 한계를
  보존한다. 행 좌표·경로·producer 버전 원문은 생략하고 16KiB로 제한한다.
  사례 비교는 수행하지 않으며 `correspondence` 입력을 거부한다.
- `underwrite_records_list`는 `records list --view page --json`의 고정 50개
  페이지를 투영한다. label·captured_at·root 경로를 모델 응답에서 생략하고,
  ID·상태·kind·manifest digest만 남긴다. 전체 catalog digest가 바뀌면
  성공 상태 `changed`와 빈 항목, `restart_offset: 0`을 돌려 누락·중복을
  막는다. 다음 페이지는 같은 root와 응답의 offset·digest로 호출한다.
  이 digest는 읽은 목록의 동일성만 나타내며 원자적 filesystem snapshot이나
  저장 파일 검증은 아니다. 잘못된 인자·저장소 오류는 CLI와 같은
  `record_error.v1`를 돌린다.
- `underwrite_records_show`는 `records show --view summary --json`으로 하나의
  canonical manifest를 읽어 ID·kind·시각·파일 역할별 개수·선언된 총 바이트·
  rerun/provenance 상태와 정확한 logical file name을 최대 16개씩 투영한다.
  라벨·metadata·source locator·link 대상은 생략한다. 파일 크기·해시는
  manifest 선언값이며 저장 바이트나 직접 링크를 검사하지 않는다. 다음 파일
  페이지는 같은 root·ID와 현재 manifest digest를 요구하고, digest가 바뀌면
  파일을 내지 않고 첫 페이지 재시작을 지시한다.
- `underwrite_records_verify`는 `records verify --view summary --json`을
  투영한다. 저장 파일 바이트와 직접 연결 manifest를 읽어 `valid`, 검사한 파일·
  링크 수를 각각 상태 코드 배열의 길이로 보존하고, 실패 코드는 배열에
  반복해 담는다. 실패한 항목의 파일 이름·연결
  기록 ID는 생략해 16KiB로 제한한다. `valid: false`는 도구 호출 오류가 아닌
  무결성 실패 결과이며 연결 대상 파일 바이트, producer 재실행·출처·승인은
  확인하지 않는다.
- `underwrite_records_read_json`은 `records read-json --view bounded`를
  투영한다. 선택 파일의 원래 바이트를 manifest와 대조한 뒤 전체 JSON
  응답이 개행 포함 16KiB 이하일 때만 `record_document.v1`을 반환한다.
  초과하면 부분 문서 없이 기존 `record_error.v1 / OUTPUT_LIMIT_EXCEEDED`를
  돌린다. 기본 CLI 전체 보기는 유지한다. 저장 `content`는 비신뢰 데이터로
  표시하며 다른 파일·연결 기록·출처·승인은 검사하지 않는다.
- `underwrite_inspect`는 기존 `inspect CASE.json --json`의 결과를 그대로
  투영한다. 선택 파일의 바이트 해시와 예상 해시 비교, 관찰 생성 상태 및
  고정 진단만 반환한다. `not_created`의 CLI 종료 코드 1은 MCP 도구 오류가
  아닌 성공 결과로 해석하며, 호출 오류는 기존
  `artifact_inspection_error.v1`을 사용한다.
- `underwrite_audit_promptfoo`는 기존 `audit-promptfoo`의 actions 계산을
  한 번 수행해 선언·제공 행 모집단, 차이, 관계, finding 코드별 개수와
  고정된 다음 검증 행동만 16KiB 이하로 투영한다. 행 좌표와 원본 내용은
  제외하며 원본 주장, export 완전성, 승인 여부는 구분한다.
- `underwrite_audit_counts`는 manifest가 선택한 Promptfoo artifact를
  예상 해시 검사와 같은 읽기에서 집계한다. `source_binding`의
  `manifest_case`는 manifest 바이트 해시와 `matched` 또는 `not_supplied`를
  담고, 직접 파일의 `direct_file`은 `not_applicable`을 담는다. 불일치한
  예상 해시는 호출 오류이며 manifest 해시는 출처 인증이 아니다.
- `underwrite_audit_deepeval`은 `audit-deepeval --view summary --json`으로
  공급된 사례·serialized metric 모집단, 오류·미채점 수, 선언된 사례 수의
  대응, 다음 행동과 한계를 16KiB 이하로 투영한다. 원본 사례·지표 이름과
  오류 문구는 제외한다. planned/expectation 모집단과 trace 구조, owner
  binding은 이 보기에서 측정하지 않는다.
- `underwrite_audit_deepeval_trace`는 `audit-deepeval --view trace-summary
  --json`으로 제공된 사례·trace·span, source status, 미해결 부모 참조와
  순환 구성원 개수 및 다음 행동을 16KiB 이하로 투영한다. 개별 pointer는
  제외하며 원본의 linkability 한계는 남은 해시만 가리키게 바꾼다.
  연결은 인과·시간 순서·계측 완전성의 증거가 아니다.
- `underwrite_audit_deepeval_bound`는 `audit-deepeval --bindings PACK
  --view bound-summary --json`을 투영한다. TestRun과 선언 pack을 각각 한 번
  읽고 source identity, 선언 identity, 제공된 metric occurrence 모집단,
  availability·item action·declared semantic binding 개수를 16KiB 이하로
  반환한다. 원본 값·이름·좌표는 제외하며 owner 의미의 진위와 출처는
  검증하지 않는다.
- `underwrite_audit_langfuse`는 `audit-langfuse --view summary --json`의
  두 입력 파일 원본 해시, score 종류·연관 상태 개수, planned unknown과
  CLI-list retrieval/pagination 상태를 16KiB 이하로 투영한다. 원본 행과
  값은 제외한다. 두 파일의 선택은 원자적 쌍이나 동일 수집 창을 증명하지
  않으며 export 완전성도 모른다.
- `underwrite_audit_langfuse_score_source`는 기존 `--view score-source`
  결과의 API/ANNOTATION/EVAL과 missing/null/unrecognized source 선언
  개수를 16KiB 이하로 투영한다. actor 신원·evaluator 실행은 검증하지
  않으며 score는 task verdict가 아니다.
- 프로토콜 지원 범위는 client가 시작하는 local stdio MCP `2025-11-25`의
  initialize/initialized, tools/list, tools/call뿐이다. 네트워크 listener,
  상주 프로세스, 모델 호출, 쓰기 도구를 추가하지 않는다.

### A1 첫 조각: 한도 오류 관문과 측정 기준

- 실패 불변식: `tests/unit/shell/instrument/test_next_actions.py`는 CLI와 ingest만 훑어
  `service/_record_errors.py`의 다음 행동을 놓쳤다. `OUTPUT_LIMIT_EXCEEDED`는 분석 출력에서
  `REDUCE_INPUT_SIZE`, 기록 출력에서 `REDUCE_OUTPUT_SIZE`, 릴리스 배치 출력에서
  `REDUCE_BATCH_SIZE`를 낸다. 릴리스 입력 한도에도 `REDUCE_BATCH_SIZE`가 적용된다.
- owner와 범위: 기존 오류 출력의 문맥별 값을 관문에 명시하고
  service까지 훑는다. `scripts/structure_metrics.py`가 모든 인자 위치의 후보 literal을 센다.
  이 조각은 출력과 스키마를 바꾸지 않는다. 분석 출력의 안내를 더 직접적으로 바꾸는 일은
  출력 호환성을 정한 별도 기능 조각에서 다룬다.
- 성공/실패 예: 기록 출력 한도 행동이 `REDUCE_OUTPUT_SIZE`이면 관문 통과,
  `REDUCE_INPUT_SIZE`로 바뀌면 실패한다. 시험에서 이 변경을 일부러 주입해 실패를 확인한다.
  측정은 둘째 위치 인자, 키워드 인자, `require`의 reason도 센다.
- 다음 owner 결정의 근거: 순수 계층은 `service`나 `ingest`를 가져올 수 없다.
  `measurement_contract`의 측정 전용 책임과 `underwrite_core`의 범용 무결성 책임을 비교한
  결과를 아래 조각에 기록한다. 코드와 문맥별 행동·종료 코드를 옮길 때 독립 오류 스키마와
  생성된 기록 계약을 대조하고, 기존 CLI 출력 바이트를 고정한다.

### A1 다음 조각: 기록 오류의 순수 owner

- 실패 불변식과 owner: 공통 행동 5개는 `instrument/ingest/application.py:28`에,
  기록 오류 21개는 `service/_record_errors.py:9`에 있다. 둘 다 셸 계층이라 순수 도메인이
  범용 오류 의미를 가져올 수 없다. 앱 전용 순수 패키지 `underwrite.error_contract`가
  코드의 실패 종류와 문맥별 출력 투영을 소유한다. `measurement_contract`는 측정 의미 전용이고
  `underwrite_core`는 별도 배포되는 범용 무결성 패키지이므로 이 책임을 넣지 않는다.
- 범위와 호환: 먼저 공통 행동 5개와 폐쇄형 `record_error.v1`의 21개 정의만 옮긴다.
  `application.NEXT_ACTIONS`는 registry view로 유지하고, 기록 service·CLI·스키마 생성기는
  registry를 직접 쓴다. 다른 여섯 오류 봉투와
  `cli_result.v1`은 이 조각에서 옮기지 않는다. Goppi 소비 계약도 바꾸지 않는다.
- 성공/실패 예: `OUTPUT_LIMIT_EXCEEDED`의 기록 문맥은 `REDUCE_OUTPUT_SIZE`와 종료 코드 2,
  `SOURCE_READ_FAILED`의 기록 문맥은 `CHECK_SOURCE_FILE`을 유지한다. 알 수 없는 코드나
  문맥은 성공 투영으로 처리하지 않는다. 실제 `records list --json` 실패의 stdout·stderr·종료
  코드를 바이트로 고정하고, 생성기 `--check`와 import·순수 계층 관문을 통과한다.
- 기록 계약의 내장 오류 `$defs`는 수작업 사본이 아니다. `_schema_compose.embed_contracts()`가
  독립 오류 스키마를 조합하고 각 생성기의 `--check` 시험이 결과 바이트를 확인한다.
  이 조각은 생성된 계약 파일을 수정하지 않는다.

### A1 측정 오류 투영

- 실패 불변식: `cli/measurement_flow.py:24`의 고정 행동 10개와 `MeasurementError`의 종료
  코드가 새 registry와 별도 표로 남아 있다. 같은 실패 코드는 기록과 측정에서 다른 안내를
  낼 수 있으므로 `measurement_error.v1` 문맥을 별도 투영으로 둔다.
- owner와 범위: `underwrite.error_contract`가 10개 코드의 다음 행동과 종료 코드 2를 소유한다.
  세 새 코드(`INVALID_OBSERVATION`, `INVALID_POLICY`, `UNSUPPORTED_PROFILE`)의 실패 종류도
  등록한다. 사유는 호출 지점이 정한 고정 어휘를 그대로 쓰며 측정 통계나 판정은 옮기지 않는다.
- 성공/실패 예: 측정 출력 상한은 기존 `REDUCE_INPUT_SIZE`를 유지하고, `measure --json`
  사용 오류의 stderr·stdout·종료 코드는 바이트가 같다. 코드/action enum의 집합이 registry와
  일치하고, 잘못된 행동·종료 코드가 새 관문에서 실패한다.

### A1 evidence 기본 오류 투영

- 실패 불변식: `cli/evidence.py:47`의 `ERROR_ACTIONS` 15개와 종료 코드 2가 순수 registry
  밖에 남아 있다. 입력 검증·출력 상한·계산 한도 같은 실패를 명령마다 달리 해석하지 않도록
  `evidence_error.v1` 기본 문맥의 행동과 종료 코드를 registry에서 투영한다.
- 범위: 명시적 행동 인자는 현재 `audit_trajectory.py:70` 한 곳에서
  `GOPPI_VERIFIER_UNAVAILABLE`의 실행 파일 선택을 고치도록 `CORRECT_ARGUMENTS`를 넘긴다.
  `SOURCE_READ_FAILED`의 기본 행동과 다른 실제 문맥 선택이므로 이번 조각은 그 호출과
  다른 소비 계약을 바꾸지 않는다. 문맥별 override의 registry 이관은 후속 조각으로 둔다.
- 성공/실패 예: 15개 코드와 행동 집합은 기존 스키마와 일치하고, 모든 기본 오류의 실제
  봉투 행동은 해당 registry 항목과 같다. `audit-promptfoo --json` 사용 오류의 stdout,
  stderr, 종료 코드 바이트를 고정한다.
- 기록 봉투의 조합 owner인 `cli/record_capture.py`는 오류 봉투의 종료 코드를 component
  invocation과 aggregate 결과에 투영한다. 기록 명령별 literal `2`는 없애되, 저장 실패의
  별도 종료 코드 3과 성공 코드 0은 기존 계약대로 유지한다.

### A1 오류 투영과 범주 literal 래칫

- `error_contract/registry.py`는 기록·측정·evidence 기본 오류에 이어 instrument의 코드별
  종료 상태, acceptance의 입력 위치별 행동, artifact inspection의 artifact 경로 한도,
  release의 manifest·artifact·batch 한도 행동을 소유한다. `OUTPUT_LIMIT_EXCEEDED`의
  분석 `REDUCE_INPUT_SIZE`, 기록 `REDUCE_OUTPUT_SIZE`, release `REDUCE_BATCH_SIZE`는
  기존 계약의 서로 다른 문맥이며 출력은 그대로 둔다. 알 수 없는 코드·문맥은 고정 오류로 거부한다.
- 예외 타입에서 세부 reason을 고르는 일은 각 ingest·도메인 adapter에 남긴다. registry의
  범주 코드 집합과 독립 오류 스키마 7개의 코드 집합을 관문에서 비교한다. `cli_result.v1`은
  코드가 없는 결과 상태 계약이므로 오류 코드 합집합에 넣지 않는다.
- `scripts/structure_metrics.py`가 모든 오류 생성·`require` 인자의 대문자 literal 후보와,
  그중 공개 오류 범주 코드와 일치하는 registry 밖 **인자 발생**을 따로 센다. 첫 A1
  기준은 후보 고유값 675개·108모듈, 공개 범주 코드 literal 인자 617개·71모듈이었다.
  동일한 evidence 출력 상한 생성 10곳을 기존 `output_limit_error()` owner로 모아
  현재 597개·71모듈이며, `tests/gates/test_structure_metrics.py`의 상한도 597로
  낮췄다. 같은 문자열이 code와 reason 두 인자에 있으면 둘 다 센다. 세부 reason의
  추가를 범주 코드의 회귀로 오인하지 않는다.
- `test_next_actions.py`는 context별 출력 행동과 registry lookup, CLI/error emitter를
  변이 시험으로 검사한다. 코드·행동 대응은 단순한 전역 코드 표가 아니다.

### A1 행동 필드의 입력 문자열 조사와 이행 경계

| 필드·계약 | 현재 경계와 근거 | 남은 이행 작업 |
| --- | --- | --- |
| 7개 독립 오류의 `code`, `next_action`, `exit_code` | 스키마 enum·const와 registry 투영을 관문에서 대조. `instrument_error.v1`에는 `next_action` 자체가 없음 | A2는 기존 계약을 그대로 투영하고 도구별 오류 플래그만 별도로 둔다 |
| `acceptance_error.v1`의 `location`, `reason` | `location`은 `/candidate`, `/claims`, `/reads/<정수>`의 구조 경로. reason은 소유자가 고른 토큰이며 입력 원문을 직접 넣는 경로를 찾지 못함 | 새 입력 위치·reason을 추가할 때 구조 경로와 유한 어휘를 시험한다 |
| `evidence_error.v1`의 `reason`, `location` | `cli/evidence.py`의 `EvidenceError`가 reason의 대문자 토큰·128자와 location의 512자 구조 포인터를 중앙에서 검사한다. 기존 Promptfoo·DeepEval의 고정 루트 위치 `/`와 trajectory의 `trajectory`는 바이트 호환을 위해 허용한다. 형식이 맞지 않으면 전체 진단을 고정 `INTERNAL_ERROR`로 바꾼다. `compare-declarations`의 예외 문장 경계도 등록된 세 토큰으로 닫는다 | 문법은 출처를 증명하지 않는다. 새 호출자는 입력 유래의 문법상 유효한 값도 행동 필드에 넣지 않도록 owner별 시험을 추가한다 |
| inspection·release 오류의 `location` | 입력 경로의 고정 필드와 정수 색인으로 조합한다. 계약의 pointer 문법은 닫혀 있다 | 새 배열·필드를 추가하면 pointer 문법과 실사용 출력을 함께 검사한다 |
| `cli_result.v1`의 `state`, `details.reason` | `state`는 닫힌 결과 어휘이고 `details.reason`은 명령별 enum이다. `details` 안의 파일 경로·입력 이름은 데이터이며 행동 지시가 아니다 | MCP text 요약에서 데이터 값을 행동 문장으로 승격하지 않는 시험을 둔다 |
| 임베디드 진단의 `source_path`, `field` | 입력에 연관된 대상 데이터로 취급한다. 이 값이 행동 필드로 투영되면 안전하지 않다 | A2의 structuredContent에서는 subject 하위에 두고 길이·문자 예산을 정한다 |

`evidence_error.v1`의 예외 전달 경로도 별도로 확인했다.

| 경로 | reason/location의 현재 출처와 처리 |
| --- | --- |
| `cli/inspect_result.py` ← `instrument/evidence/common_result.py`의 `ResultInvariantError` | reason은 코드가 고른 유한 토큰이고 location은 `/items/<정수>` 등 구조 경로다. `inspect-result` MCP 도구는 이 CLI 경로를 그대로 사용한다. |
| `cli/compare_declarations.py`의 예외 문장 분류 | 세 등록 토큰만 reason에 대응시키고 나머지는 고정 `INVALID_DECLARATION`을 쓴다. 입력 문장 자체를 reason에 내지 않는다. |
| `instrument/ingest/application.py`의 진단·`cli/local_json.py`의 파일 입력 오류 | 정상 진입점은 검사 단계·전송 실패를 고정 토큰으로 축약한다. OpenInference payload에 예외 문장이 들어갈 수 있으나 현재 CLI 오류 봉투 reason으로 직접 전달되는 경로는 찾지 못했다. |
| `cli/record_trajectory.py`와 기록 adapter | `TRAJECTORY_RECORD_` 접미사는 고정 `RecordError.code`에서 오고, `TransportError`도 정상 경로에서는 고정 토큰을 준다. |
| `cli/handoff_review.py` ← Goppi P7 영수증 capture | 영수증 파일 이름의 입력 유래 digest는 정확한 `logical_name`에만 남기고, 읽기·해시·크기·JSON 파싱 실패의 진단 위치는 `/regrade/files/<정수>`로 낸다. digest 첫 글자와 무관하게 같은 오류 의미를 유지한다. |
| 순수 도메인의 `PairedError`·`JudgeError` | 예외 문장에는 입력 ID가 섞일 수 있다. 현재 CLI의 `EvidenceError` reason/location으로 그 문장을 직접 전달하는 경로는 찾지 못했다. 새 forwarding에는 별도 검사가 필요하다. |

직접 forwarding 호출자는 `audit_counts`, `audit_deepeval`, `audit_promptfoo`,
`audit_trajectory`, `audit_baseline`, `project_baseline`, `prepare_event_policy`,
`review_artifacts`, `record_promptfoo`, `record_langfuse`, `record_deepeval`,
`record_deepeval_bound`, `record_cross_artifact`, `record_baseline`,
`record_trajectory`, `inspect_result`, `handoff_review`의 CLI adapter까지
확인했다. 각 adapter의 상위 오류 타입은 현재 고정 토큰을 사용하며, 위치는 대체로
고정 필드와 정수 색인으로 만든다. trajectory의 순수 분석은 기존 고정 위치
`trajectory`를 내므로 중앙 검사에서 이 값만 호환 예외로 보존한다.

`scripts/structure_metrics.py`는 `src/underwrite` 전체에서 `EvidenceError`의
`reason` 또는 `location`에 문자열 literal이 아닌 표현식을 전달하는 호출을 별도로 센다.
`EvidenceError as E` 같은 import 별칭도 같은 호출로 센다.
현재 기준값은 42개 호출 지점·23모듈이며 `tests/gates/test_structure_metrics.py`가
42 이하의 줄어들기만 하는 상한으로 고정한다. 이 AST 지표는 새 동적 forwarding의
조용한 증가를 막는 검토 신호다. 기존 동적 값이 닫힌 owner enum에서 왔는지, 또는
문법상 유효한 입력 문자열인지까지 증명하지는 않는다.

중앙 검사는 형식 위반과 제어·서식 문자 유입을 막으며, 기존의 유효한 CLI 오류 바이트는
유지한다. 문법상 유효한 입력 문자열의 출처까지 증명하지는 못한다. 입력 경로·이름을
`reason`이나 `location`에 새로 복사하지 말고 대상 데이터에 두어야 한다.
미등록 오류 code·context는 내부 registry에서 거부하고, CLI 최외곽은 고정
`INTERNAL_ERROR`로 축약한다. 생성자에서 미등록 값을 조용히 정상 코드로 바꾸지는 않는다.

### A1 출력 상한의 작은 결과 모양

현재 v1 CLI의 상한 실패는 각 명령의 오류 봉투를 내며, `cli_result.v1` 계열은
`UNCONFIGURED`와 `/output` 세부값을 낸다. 이 출력 바이트를 A1에서 바꾸지 않는다.
도구가 결과 본문을 생략할 때 쓸 **차기 CLI 계약 초안**은 아래 고정 필드를 갖는다.

```json
{
  "schema": "underwrite_overflow_result.v1",
  "state": "output_limited",
  "code": "OUTPUT_LIMIT_EXCEEDED",
  "source_schema": "<등록된 명령 결과 계약>",
  "input_digest": null,
  "policy_digest": null,
  "population": {"requested": null, "observed": null},
  "missingness": "result_body_omitted",
  "truncated": true,
  "next_action": "<registry의 해당 명령 문맥 행동>",
  "interpretation_limits": ["RESULT_BODY_NOT_AVAILABLE", "VERDICT_NOT_AVAILABLE"]
}
```

`null`은 계산하지 못했거나 그 명령에 없는 값이며 0으로 바꾸지 않는다. digest와 모집단은
실제로 확보한 값만 넣고, 부분 결과를 성공 판정처럼 내지 않는다. `source_schema`와
`next_action`은 닫힌 어휘이며 입력 경로·본문을 담지 않는다. 상한은 이 작은 결과에도 적용한다.
이를 실제 CLI 출력으로 채택할 때 명령별 v2 계약·생성기·회귀 시험과 함께 이행하고,
그 뒤에만 A2 MCP의 structuredContent/text에 투영한다. 이 초안은 현재 출력 계약이나
구현된 서버를 뜻하지 않는다.

## 5. 연계

- **구조 프로그램:** A1의 오류 registry와 결과 투영 규칙은
  [구조 프로그램](STRUCTURE_PROGRAM.ko.md)의 owner 통합 작업과 같은 래칫 형식을 쓴다.
- **producer:** MCP 도구가 producer 산출물을 읽는 경로는 CLI가 이미 지원하는 계약만 쓴다.
  CLI가 아직 소비하지 않는 producer 형식은 MCP에서도 다루지 않는다.
- **skill:** `underwrite-review` skill과 `AGENT_USAGE.md`는 MCP가 생긴 뒤에도 유지한다.
  같은 결과 계약을 가리키게 하고, 도구 설명과 문서가 다른 규칙을 말하지 않게 한다.

## 6. 열린 질문

1. A2의 첫 도구 패키징은 [ADR-024](../adr/ADR-024-read-only-agent-surface.md)에 따라
   기존 의존성만 쓰는 기본 설치로 정했다. 이후 SDK 도입이 필요해지면 optional extra를
   포함해 다시 비교한다.
2. A1의 남은 경계: 일곱 독립 오류 봉투의 범주·행동 투영은 registry에 모였다.
   `evidence_error.v1`의 현재 예외 전달 경로를 분류하고 중앙 문법 검사를 넣었다.
   새 경로의 출처 검사와 `cli_result.v1`의 결과 상태 투영은 후속 계약 조각이다.
3. 결과 크기 상한의 기본값: 1절의 기존 상한(256 KiB–8 MiB)과 MCP 클라이언트의 실제 수용 크기를 재고 정한다.
