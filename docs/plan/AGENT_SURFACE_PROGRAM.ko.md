# 에이전트 표면 프로그램 (첫 판)

작성: 2026-11-16. 코드 기준: `1d344a8`. 상태: **계획. MCP 코드는 아직 없다.**
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
재현 스크립트는 A0에서 만든다. 그 전까지 이 숫자는 기준값이 아니라 참고값이다.

| 항목 | 값 | 재는 방법 |
| --- | --- | --- |
| 최상위 명령 | 27개 항목(명령 26개와 별칭 `project`) | `underwrite --help` |
| `underwrite --help` | 3,086 B | `underwrite --help \| wc -c` |
| 명령별 `--help` 합계 | 22,149 B(27개). 최대 `project` 1,896 B, `ingest` 1,889 B, `records` 1,769 B | 명령마다 `underwrite <cmd> --help \| wc -c` |
| `capabilities --json` | 12,310 B. `capabilities.v1`, 명령 26개, profile 57개, codec 10개 | `underwrite capabilities --json \| wc -c` |
| JSON 계약 | `contracts/` 아래 스키마 82개 | `find contracts -name '*.schema.json' \| wc -l` |
| 공통 결과 봉투 | 없음. `next_action(s)`는 스키마 35개, `exit_code`는 26개, `interpretation_limits` 계열은 goppi trajectory 계약 8개에만 있음 | `grep -lE '"next_actions?"' contracts/*.schema.json` 등 |
| 오류 코드 | 중앙 registry 없음. 오류 생성 호출의 첫 인자로 쓰인 고유 코드 하한 444개, 104모듈. 둘째 이후 인자와 `require` 호출의 코드는 빠짐 | `scripts/structure_metrics.py`의 `error_code_estimate` |
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
- **오류 코드가 흩어져 있다.** 적어도 444개 코드가 104개 모듈의 문자열 literal이다. 같은 실패 의미가
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

## 5. 연계

- **구조 프로그램:** A1의 오류 registry와 결과 투영 규칙은
  [구조 프로그램](STRUCTURE_PROGRAM.ko.md)의 owner 통합 작업과 같은 래칫 형식을 쓴다.
- **producer:** MCP 도구가 producer 산출물을 읽는 경로는 CLI가 이미 지원하는 계약만 쓴다.
  CLI가 아직 소비하지 않는 producer 형식은 MCP에서도 다루지 않는다.
- **skill:** `underwrite-review` skill과 `AGENT_USAGE.md`는 MCP가 생긴 뒤에도 유지한다.
  같은 결과 계약을 가리키게 하고, 도구 설명과 문서가 다른 규칙을 말하지 않게 한다.

## 6. 열린 질문

1. A2의 패키징: 기본 설치에 넣을지, optional extra로 둘지. A0–A1 뒤 의존성 비교와 함께 정한다.
2. A1 registry의 위치: `service/_record_errors.py`를 넓힐지, 새 owner를 둘지. 기존 owner의
   책임과 변경 이유를 먼저 비교한다.
3. 결과 크기 상한의 기본값: 1절의 기존 상한(256 KiB–8 MiB)과 MCP 클라이언트의 실제 수용 크기를 재고 정한다.
