# 필드 대조 표

이 문서는 현행 핸드오프 양식의 필드가 새 양식의 어디로 가는지를 소유한다. 이관 시점의 대조 기록이며, 이행이 끝나면 「현행」은 이관 전 양식을 가리킨다.

현행 양식의 필드 가운데 새 양식에 위치가 없는 필드가 0건인지 판정하는 근거 표다. 이 표는 애매한 항목을 모두 「유지」로 분류한다. 판정 결과는 문서 끝 「대조 결과:」 줄에 있다.

## 읽는 법

| 약어 | 뜻 |
|---|---|
| 핵심 양식 | 이 저장소 `commands/handoff.md`의 파일 양식 절 |
| 핵심 쓰기 | 같은 파일의 Step 0~6·Ground rules·Safety rules |
| 핵심 재개 | `commands/resume-handoff.md` |
| 핵심 검사기 | `scripts/check_handoff.py` |
| 핵심 상태 스크립트 | `scripts/handoff_state.py` |
| 사용자 층 | `~/.claude/handoff-rules.local.md`. 적용 순서는 핵심, 사용자 층, 프로젝트 층이다 |
| 프로젝트 층 | `<toplevel>/.claude/handoff-rules.local.md`. 덮어쓸 수 있는 지점은 닫힌 목록 4개다 |
| 덮어쓰기 지점 4개 | `runner-completion`(러너 완료 판정 방법) · `state-files`(추가로 읽을 상태 파일) · `forbidden-writes`(쓰기 금지 경로) · `values`(값 설정) |

- 현행 위치의 `파일:줄`은 이관 전 `~/.claude/` 아래 설치본 기준이다. 약칭은 다음과 같다.
  - `handoff.md`: 쓰기 명령
  - `resume-handoff.md`: 재개 명령
  - `check_handoff.py`
  - `handoff_state.py`
- 처리 값은 다음 7가지다.
  - 유지
  - 이름 변경
  - 분리
  - 선택 줄
  - 프로젝트 덮어쓰기 지점
  - 스크립트 출력으로 대체
  - 제거(사용자 결정)
- 새 양식에만 있는 칸은 표에 넣지 않는다. 그런 칸은 현행 필드가 아니다. 예: 근거 결정 · 반환 뒤 할 일 · `**Toplevel:**` · `fires-at` · `diffstat:`.

## A. 머리 줄·구분

| # | 현행 필드 | 현행 위치(파일:줄) | 새 위치 | 처리 | 근거 |
|---|---|---|---|---|---|
| 1 | 제목 줄 `# Session: YYYY-MM-DD` | handoff.md:173 | 핵심 양식 · 제목 | 유지 | 제목에는 날짜만 쓴다는 일반 규칙. 현행도 날짜만 쓴다 |
| 2 | `**Started:**` | handoff.md:175 | 핵심 양식 · 머리 줄 | 유지 | 결정 없음 |
| 3 | `**Last Updated:**` | handoff.md:176 | 핵심 양식 · 머리 줄 | 유지 | 결정 없음 |
| 4 | `**Project:**` | handoff.md:177 | 핵심 양식 · 머리 줄 | 유지 | 결정 없음. 바로 다음 줄에 새 칸 `**Toplevel:**`(프로젝트 판별용 `project key:` 값)이 더해진다 |
| 5 | `**Topic:**` | handoff.md:178 | 핵심 양식 · 머리 줄 | 유지 | 결정 없음. Auto-resume 줄이 빠져 이 줄을 위치 기준으로 쓰는 용도는 사라진다 |
| 6 | `**Auto-resume:**` 머리 줄 | handoff.md:179 | 없음 | 제거(사용자 결정) | 자동 재개 감시자를 없애기로 한 사용자 결정이다 |
| 7 | Auto-resume 기록 조건(계속 지시가 기록됐을 때만 쓰고 아니면 줄 생략) | handoff.md:166-169 | 없음 | 제거(사용자 결정) | 표지가 없으므로 기록 조건도 없다 |
| 8 | Auto-resume 위치(`Topic` 바로 아래) · 40줄 이내 · 형식 `yes · cwd=<절대 경로>` | handoff.md:166, check_handoff.py:35-36, 108-111 | 없음 | 제거(사용자 결정) | 표지 검사를 검사기에서 뺀다. 표지가 없는 핸드오프가 표지 때문에 실패하지 않는다 |
| 9 | Auto-resume 근거 지시어 검사(Decisions Made·Requirement Changes에서 지시어 4종 찾기) | check_handoff.py:37-38, 112-114 | 없음 | 제거(사용자 결정) | 표지 근거 지시어 검사를 검사기에서 뺀다 |
| 10 | Auto-resume `yes` 판정 출력(`handoff auto-resume:`) | handoff_state.py:162-163 | 없음 | 제거(사용자 결정) | `handoff auto-resume:` 줄을 더 이상 출력하지 않는다. `continue:` 줄이 대신한다 |
| 11 | 첫 줄 주석 `<!-- context-budget: … -->` | handoff.md:118-119 | 핵심 양식 · 첫 줄(선택) | 선택 줄 | 스크립트가 `context-budget:` 줄을 낸 경우에만 첫 줄에 넣는다. 양식 코드 블록에 선택 줄로 표시한다 |
| 12 | 주석 도구가 종료 코드 2여도 줄을 쓰고 계속하는 규칙 | handoff.md:119 | 핵심 쓰기 · 선택 줄 | 선택 줄 | `handoff_state.py`가 `context_budget.py --line`을 불러 `context-budget:` 줄로 내고, 종료 코드 2여도 줄을 낸다 |
| 13 | 주석 줄·`#` 줄·`---`·`[…]` 자리 표시 줄을 내용 줄 수에서 제외 | handoff_state.py:105-111 | `is_placeholder` 공통 판정(줄 수 기준 폐기) | 스크립트 출력으로 대체 | 문서에 없는 줄 수 기준을 공통 자리 표시 판정으로 바꾼다 |
| 14 | 절 구분선 `---` | handoff.md:181, 191 외 | 핵심 양식 | 유지 | 결정 없음 |

## B. 절 구조 일반 규칙

| # | 현행 필드 | 현행 위치(파일:줄) | 새 위치 | 처리 | 근거 |
|---|---|---|---|---|---|
| 15 | 핵심 절은 항상 두고 비면 `None`을 쓴다 | handoff.md:112-113 | 핵심 쓰기 · Step 5 | 유지 | 빈 절이 「고려했음」 신호다 |
| 16 | 조건부 절은 내용이 없으면 통째로 생략한다 | handoff.md:115-116 | 핵심 쓰기 · Step 5 | 유지 | 결정 없음 |
| 17 | `## Confidence`와 값 high·medium·low | handoff.md:183-185 | 핵심 양식 | 유지 | 재개 브리핑이 읽는다(resume-handoff.md:95) |
| 18 | Confidence 값 기준(compaction 0~1회 · 2회 · 3회 이상) | handoff.md:187-189 | 핵심 양식 | 유지 | 결정 없음 |
| 19 | compaction 3회 이상이면 파일을 읽고 확인한 뒤 상태를 단언 | handoff.md:68-69 | 핵심 쓰기 · Step 1 | 유지 | 결정 없음 |

## C. Session State

| # | 현행 필드 | 현행 위치(파일:줄) | 새 위치 | 처리 | 근거 |
|---|---|---|---|---|---|
| 20 | `## Session State` 절과 git 저장소 밖이면 절 통째 생략 | handoff.md:193-195, 77 | 핵심 양식 | 유지 | 결정 없음 |
| 21 | Step 0 셸 명령은 정확히 1개 | handoff.md:34-38 | 핵심 쓰기 · Step 0 | 유지 | 결정 없음 |
| 22 | Session State는 Step 0 출력만으로 쓰고 명령을 더하지 않는다 | handoff.md:71-73 | 핵심 쓰기 · Step 2 | 유지 | 결정 없음 |
| 23 | `toplevel:` 출력 줄 | handoff_state.py:51-56 | 핵심 상태 스크립트 출력 | 유지 | 결정 없음 |
| 24 | `linked worktree:` 출력 줄 | handoff_state.py:58-62 | 핵심 상태 스크립트 출력 | 유지 | 결정 없음 |
| 25 | `rules file:` 출력 줄(프로젝트 규칙 파일 경로, 링크된 작업 트리면 주 작업 트리로 대체) | handoff.md:40-41, handoff_state.py:64-68 | 핵심 상태 스크립트 출력 | 유지 | 프로젝트 층 경로는 그대로, 사용자 층이 둘째 층으로 더해진다 |
| 26 | 규칙 파일이 있으면 먼저 읽고, 현행 파일과 다른 곳은 규칙 파일이 이긴다 | handoff.md:41-44, resume-handoff.md:35-36 | 프로젝트 층(닫힌 목록 4개) | 프로젝트 덮어쓰기 지점 | 현행은 모든 곳을 덮어쓸 수 있고 새 양식은 4개 지점만 덮어쓴다 |
| 27 | `- **Branch:**` 필드와 `branch:` 출력 줄 | handoff.md:197, 70-76, handoff_state.py:70-76 | 핵심 양식 · Session State | 유지 | 재개가 `- **Branch:**` 접두와 첫 낱말로 읽는다(handoff_state.py:136-141) |
| 28 | detached HEAD를 `detached <sha>`로 표기 | handoff_state.py:73-75 | 핵심 상태 스크립트 | 유지 | 결정 없음 |
| 29 | `- **Uncommitted changes:**` 필드(파일 목록 또는 `none`) | handoff.md:198, 80 | 핵심 양식 · Session State | 분리 | 이 세션이 바꾼 파일만 이름으로 적고 나머지는 개수로 적는다. 상태는 `status:` 1줄과 `status file:`로 나뉜다 |
| 30 | `status --short: N lines`와 파일 목록(최대 40줄, `... +N more`) | handoff_state.py:27, 86-92 | 핵심 상태 스크립트 출력 | 스크립트 출력으로 대체 | `status: N changed (M a · ?? b)` 1줄 + `status file: <경로>`로 대체한다. 이름을 블록에 나열하지 않는다 |
| 31 | 이 세션이 바꾼 파일 수집(`git diff --stat HEAD` 또는 대화 기억) | handoff.md:52-53 | 대화 기억은 Uncommitted 필드, 통계는 `diffstat:` 1줄 | 분리 | 대화 기억 부분은 그대로 유지한다 |
| 32 | `- **Unpushed commits:**` 필드(개수 · `none` · `no upstream configured`)와 `upstream:` 출력 | handoff.md:199, 79, handoff_state.py:78-84 | 핵심 양식 · Session State | 유지 | 결정 없음 |
| 33 | `- **Lockfile changed:**` 필드(yes, install … · no)와 lockfile 이름 목록 | handoff.md:200, handoff_state.py:33-37, 94-95 | 핵심 양식 · Session State | 유지 | 결정 없음 |
| 34 | `- **Live background work:**` 단일 필드 | handoff.md:201 | 핵심 양식 · Session State | 분리 | `Live subagents`·`Live runners`·`Live scheduled` 세 필드로 나누고 항상 둔다. 없으면 none. 모니터·cron·wakeup처럼 결과 경로가 없는 대기 작업은 `Live scheduled`가 받는다 |
| 35 | Live 항목의 이름과 id | handoff.md:201, 83 | `Live subagents`·`Live runners` 항목의 `name` 칸 | 이름 변경 | 새 항목에는 id 칸이 없다. 두 필드 모두 `name`에 `<이름> (<id>)`로 함께 적는다. 괄호 안은 agent id · task id · shell id · PID 중 하나이고 검사기가 끝의 `(…)`를 검사한다 |
| 36 | Live 항목의 `what it does` | handoff.md:201, 83 | 항목의 `does` 칸 | 이름 변경 | 키 이름만 바뀌고 뜻은 같다 |
| 37 | Live 항목의 `result path` | handoff.md:201, 83-84 | 항목의 `result` 칸 | 이름 변경 | 서브에이전트는 전사 절대 경로, 러너는 로그 절대 경로다. `.claude/jobs/` 아래는 검사기가 거부한다. 결과 경로가 없는 `Live scheduled` 항목은 `fires-at`(시각 또는 트리거)를 쓴다 |
| 38 | Live 항목의 `returned when: <observation>` | handoff.md:201, 85-86 | 항목의 `done-when` 칸 | 이름 변경 | 키 이름만 바뀌고 뜻은 같다. 아직 도는 jobs 기반 작업은 jobs 경로를 이 칸에 적는다 |
| 39 | 러너 완료를 무엇으로 판정하는지(항목마다 관찰 1개를 적는 방식) | handoff.md:83-86 | 프로젝트 층 · 러너 완료 판정 방법 | 프로젝트 덮어쓰기 지점 | 프로젝트 러너마다 판정 표지가 다르다 |
| 40 | Live 항목은 스크립트가 아니라 대화에서 가져온다. 항목 종류는 서브에이전트 · 잡 · 모니터 · 러너 | handoff.md:83-84 | 핵심 쓰기 · Step 1 · Step 2 | 유지 | 대화에서 가져오는 방식은 그대로다. 종류는 `Live subagents` · `Live runners` · `Live scheduled`(모니터 · cron · wakeup)로 나뉘어 모두 기록할 자리가 있다 |
| 41 | 값을 만들어 쓰지 않는다. 포트·Docker·마이그레이션·CI 결과는 수집하지 않는다 | handoff.md:88-90 | 핵심 쓰기 · Step 2 | 유지 | 결정 없음 |
| 42 | Live 필드 이름 3종 허용(`Live background work`·`Live subagents`·`Live runners`) | check_handoff.py:40, 52-54 | 핵심 검사기 | 분리 | 새 양식은 `Live subagents`·`Live runners`·`Live scheduled` 세 줄을 항상 요구하고 `Live background work`는 인정하지 않는다 |
| 43 | Live 필드 값 규칙: 비면 실패, `none`·`없음`이 아니면 `/`를 포함한 결과 경로 | check_handoff.py:41, 55-60 | 핵심 검사기 | 분리 | 값은 `none` 또는 항목 목록이다. 항목마다 6개 키(`Live scheduled`는 `result` 대신 `fires-at`)와 `name`의 `(<id>)`가 있어야 하고, `result`는 절대 경로이며 `.claude/jobs/` 아래면 실패한다 |
| 44 | Session State의 필수 필드 4개(Branch · Uncommitted changes · Unpushed commits · Lockfile changed) | check_handoff.py:39, 49-51 | 핵심 검사기 | 유지 | 4개는 그대로이고 Live 필드 3개가 더해진다(행 42). 검사기가 `--git yes`를 받으면 Session State 절이 없을 때도 실패한다 |

## D. 본문 절과 항목 형식

| # | 현행 필드 | 현행 위치(파일:줄) | 새 위치 | 처리 | 근거 |
|---|---|---|---|---|---|
| 45 | `## What We Are Building`(1~3문단, 기억 없는 사람이 이해할 분량) | handoff.md:205-208 | 핵심 양식 | 유지 | 결정 없음 |
| 46 | `## What WORKED (with evidence)`와 근거 없으면 Not Tried로 옮기는 규칙 | handoff.md:212-215 | 핵심 양식 | 유지 | 결정 없음 |
| 47 | WORKED 항목 형식 `— confirmed by:` | handoff.md:217 | 핵심 양식 | 유지 | 결정 없음 |
| 48 | WORKED 빈 문구 `Nothing confirmed working yet.` | handoff.md:219 | 핵심 양식 | 유지 | 결정 없음 |
| 49 | `## What Did NOT Work (and why)`와 정확한 이유 규칙 | handoff.md:223-226 | 핵심 양식 | 유지 | 결정 없음 |
| 50 | 실패 항목 형식 `— failed because:` | handoff.md:228 | 핵심 양식 | 유지 | 결정 없음 |
| 51 | 실패 빈 문구 `No failed approaches yet.` | handoff.md:230 | 핵심 양식 | 유지 | 결정 없음 |
| 52 | `## Discarded Hypotheses`(비면 절 생략) | handoff.md:234-238 | 핵심 양식(조건부 절) | 유지 | 결정 없음 |
| 53 | 기각 항목 형식 `— rejected because:` | handoff.md:240 | 핵심 양식 | 유지 | 결정 없음 |
| 54 | `## What Has NOT Been Tried Yet`과 결정된 항목의 세 줄 | handoff.md:244-248 | 핵심 양식 | 유지 | 결정 없음 |
| 55 | 미시도 항목 형식 `— what: · why: · how:` | handoff.md:250 | 핵심 양식 | 유지 | 결정 없음 |
| 56 | 미시도 빈 문구 `No specific untried approaches identified.` | handoff.md:252 | 핵심 양식 | 유지 | 결정 없음 |
| 57 | `## Current State of Files`와 표 열 이름 `File`·`Status`·`Notes` | handoff.md:256-258 | 핵심 양식 | 유지 | 결정 없음 |
| 58 | Status 값 4개(Complete · In progress · Broken · Not started) | handoff.md:260-263 | 핵심 양식 | 유지 | 결정 없음 |
| 59 | 파일 표기 규칙(구체 경로, glob 금지, 범위는 `:L10-L45`) | handoff.md:265 | 핵심 양식 | 유지 | 결정 없음 |
| 60 | 파일 빈 문구 `No files modified this session.` | handoff.md:267 | 핵심 양식 | 유지 | 결정 없음 |
| 61 | `## Decisions Made` | handoff.md:271-273 | 핵심 양식 | 유지 | 결정 없음 |
| 62 | Decisions 항목 형식 `— reason:` | handoff.md:275 | 핵심 양식 · `What`·`Why`·`How` 세 칸 | 이름 변경 | `reason`은 `Why` 칸으로 가고 `What`·`How` 칸이 더해진다. `How`는 설계 문서 경로로 대신할 수 있다 |
| 63 | Decisions 빈 문구 `No major decisions made this session.` | handoff.md:277 | 핵심 양식 | 유지 | 결정 없음 |
| 64 | `## User Assumptions`(비면 절 생략) | handoff.md:281-284 | 핵심 양식(조건부 절) | 유지 | 결정 없음 |
| 65 | 사용자 주장 표지 `[user-stated — not in code]` | handoff.md:286 | 핵심 양식 | 유지 | 재개 브리핑이 읽는다(resume-handoff.md:126) |
| 66 | `## Requirement Changes`(범위가 안 움직였으면 절 생략) | handoff.md:290-292 | 핵심 양식(조건부 절) | 유지 | 결정 없음 |
| 67 | 요구 변경 항목 형식 `State 1` · `State 2` · `State N (current)` | handoff.md:294-296 | 핵심 양식 | 유지 | 결정 없음 |
| 68 | `## Blockers & Open Questions` | handoff.md:300-303 | 핵심 양식 | 유지 | 결정 없음 |
| 69 | Blockers 항목(막힌 것·답 없는 질문·기다리는 외부 의존) | handoff.md:305 | 핵심 양식 | 유지 | 결정 없음 |
| 70 | Blockers 빈 문구 `No active blockers.` | handoff.md:307 | 핵심 양식 | 유지 | 결정 없음 |
| 71 | `## Exact Next Step`(재개 즉시 할 한 가지, 모르면 모른다고 쓰고 방향을 정하는 절을 가리킴) | handoff.md:311-314 | 핵심 양식 | 유지 | Live 항목이 있으면 첫 단계를 「재개 판정」으로 쓴다는 일반 규칙이 더해진다 |
| 72 | Exact Next Step은 지시문으로 쓴다 | handoff.md:332-334 | 핵심 양식 · 쓰기 노트 | 유지 | 결정 없음 |
| 73 | `## Environment & Setup Notes`(필요할 때만, 환경 변수는 이름만) | handoff.md:318-321 | 핵심 양식(조건부 절) | 유지 | 결정 없음 |
| 74 | `What Did NOT Work`와 `Discarded Hypotheses`가 이 명령의 핵심 가치라는 노트 | handoff.md:330-331 | 핵심 쓰기 · Notes | 유지 | 결정 없음 |

## E. 검사기가 판정하는 규칙

| # | 현행 필드 | 현행 위치(파일:줄) | 새 위치 | 처리 | 근거 |
|---|---|---|---|---|---|
| 75 | 세 칸 라벨 쌍(`what:`/무엇을 · `why:`/왜 · `how:`/어떻게, 대소문자 무시) | check_handoff.py:31, 85-89 | 핵심 검사기 | 유지 | 결정 없음 |
| 76 | 세 칸 검사 대상 절 목록(What Has NOT Been Tried Yet · Decisions Made) | check_handoff.py:30 | 핵심 검사기 | 분리 | `What Has NOT Been Tried Yet`은 라벨 쌍 검사를 그대로 받는다. Decisions Made는 불릿이 아니라 표 행이라 라벨 쌍 검사 대상이 아니고, 행마다 세 칸이 모두 있고 자리 표시가 아닌지 따로 검사한다 |
| 77 | 설계 문서 경로 패턴(`…-design.md` · `…-plan.md` · `plans/…`)이 세 칸을 대신함 | check_handoff.py:32, 87-88 | 핵심 검사기 | 유지 | 프로젝트 전용 계획 폴더 패턴은 프로젝트 층 「추가로 읽을 상태 파일」 지점이 받는다 |
| 78 | 40자 미만 항목과 `- none`·`- 없음`·`- no ` 시작 항목은 상태 줄로 보고 건너뜀 | check_handoff.py:33, 139-140 | 핵심 검사기 | 유지 | 결정 없음 |
| 79 | 항목 분리 규칙(불릿·번호 시작, 들여쓴 줄은 이어지는 줄) | check_handoff.py:67-82 | 핵심 검사기 | 유지 | 결정 없음 |
| 80 | Exact Next Step이 있고 비어 있지 않아야 함 | check_handoff.py:61-63 | 핵심 검사기 | 유지 | 비어 있는 것에 더해 자리 표시만 있는 절(여러 줄 대괄호 덩어리 포함)도 실패한다 |
| 81 | 종료 코드 0(통과) · 1(항목·표지·재개 계약 실패) · 2(파일 없음) | check_handoff.py:23-24, 123-128, 157-160 | 핵심 검사기 | 분리 | 표지 실패는 제거(행 8). 0 · 1은 그대로이고 2는 파일 없음에 더해 예상 못 한 예외에도 쓴다 |
| 82 | 실패 메시지 3종(항목 / 표지 / 재개 계약)과 통과 메시지 | check_handoff.py:145-159 | 핵심 검사기 | 분리 | 표지 메시지는 제거(행 8). 항목 · 재개 계약 메시지와 통과 메시지는 남는다 |
| 83 | 쓰기 뒤 검사기가 exit 0일 때까지 고치고 다시 실행 | handoff.md:123-128 | 핵심 쓰기 · Step 6 | 유지 | 결정 없음 |
| 84 | 검사기 기본값 상수(최소 항목 길이 40 · 헤더 40줄 · 지시어 목록) | check_handoff.py:33, 36, 38 | 핵심 검사기 | 분리 | 최소 항목 길이 40은 핵심 기본값으로 남는다. 헤더 40줄과 지시어 목록은 표지 검사와 함께 제거(행 8 · 9) |

## F. 파일 이름·위치·안전

| # | 현행 필드 | 현행 위치(파일:줄) | 새 위치 | 처리 | 근거 |
|---|---|---|---|---|---|
| 85 | 저장 폴더 `~/.claude/handoffs/`를 없을 때만 생성 | handoff.md:91-92 | 핵심 쓰기 · Step 3 | 유지 | 결정 없음 |
| 86 | 플러그인이 소유한 폴더·`session-data`에는 쓰지 않는다 | handoff.md:93-94 | 핵심 쓰기 · Step 3 | 유지 | 결정 없음 |
| 87 | 프로젝트 상태 폴더에는 쓰지 않는다 | handoff.md:94 | 프로젝트 층 · 쓰기 금지 경로 | 프로젝트 덮어쓰기 지점 | 상태 폴더 이름은 프로젝트마다 다르다 |
| 88 | 파일 이름 `YYYY-MM-DD-<short-id>-session.tmp`(오늘 날짜) | handoff.md:98 | 핵심 쓰기 · Step 4 | 유지 | 결정 없음 |
| 89 | short-id 규칙(허용 문자 · 첫 글자 · 소문자 8자 이상) | handoff.md:100-104 | 핵심 쓰기 · Step 4 | 유지 | short-id 규칙이 일반 규칙으로 핵심에 올라간다 |
| 90 | 유효 예시와 「틀리면 재개가 못 찾는다」 | handoff.md:106-108 | 핵심 쓰기 · Step 4 | 유지 | 결정 없음 |
| 91 | 세션마다 자기 파일, 덧붙이지 않고 재실행은 통째 재작성 | handoff.md:328-329 | 핵심 쓰기 · Notes | 유지 | 결정 없음 |
| 92 | 환경 변수는 이름만 기록 | handoff.md:157 | 핵심 쓰기 · Safety rules | 유지 | 민감값 금지 일반 규칙 |
| 93 | diff를 붙이지 않고 `path:L10-L45`로 가리킴 | handoff.md:158 | 핵심 쓰기 · Safety rules | 유지 | 결정 없음 |
| 94 | 자격 증명은 가리고 가렸다고 밝힘 | handoff.md:159 | 핵심 쓰기 · Safety rules | 유지 | 민감값 금지 일반 규칙 |
| 95 | 파일은 홈 폴더에 두고 저장소 안에는 두지 않음 | handoff.md:160 | 핵심 쓰기 · Safety rules | 유지 | 결정 없음 |

## G. 쓰기 명령의 동작 규칙

| # | 현행 필드 | 현행 위치(파일:줄) | 새 위치 | 처리 | 근거 |
|---|---|---|---|---|---|
| 96 | 두 명령은 한 쌍이고 다른 명령을 부르거나 의존하지 않는다 | handoff.md:9-10, resume-handoff.md:9-10 | 핵심 쓰기 · 핵심 재개 | 유지 | 결정 없음 |
| 97 | 파일 하나만 쓴다 | handoff.md:14 | 핵심 쓰기 · Ground rules | 유지 | 결정 없음 |
| 98 | 서브에이전트·백그라운드 잡·모니터·예약 작업을 멈추지 않고 멈추라고도 하지 않는다 | handoff.md:15-16 | 핵심 쓰기 · Ground rules | 유지 | 결정 없음 |
| 99 | `/compact`·새 세션 열기·세션 종료를 하지 않는다 | handoff.md:17 | 핵심 쓰기 · Ground rules | 유지 | 결정 없음 |
| 100 | 확인 질문을 하지 않는다 | handoff.md:18-20 | 핵심 쓰기 · Ground rules | 유지 | 결정 없음 |
| 101 | 사용 시점 4개(세션 끝 · 컨텍스트 한계 전 · 비싼 추론 직후 · 언제든) | handoff.md:22-28 | 핵심 쓰기 · When to Use | 유지 | 결정 없음 |
| 102 | 수집 항목(바꾼 파일 · 시도와 실패 이유 · 기각한 접근 · 코드에 안 보이는 사용자 주장 · 요구 변경 순서 · 백그라운드 작업) | handoff.md:53-60 | 핵심 쓰기 · Step 1 | 유지 | 백그라운드 작업은 `ListAgents`로 확인하고 스폰 시각과 반환 예상(`started` · `expected-by`)을 함께 모은다 |
| 103 | 열린 항목과 결정마다 무엇 · 왜(사용자 말 인용) · 어떻게 세 줄 | handoff.md:61-64 | 핵심 쓰기 · Step 1 | 유지 | Decisions 세 칸 필수 결정과 같은 규칙이다 |
| 104 | 대화에서만 정해진 설계는 지속 파일에 먼저 쓰고 핸드오프가 그 경로를 가리킴 | handoff.md:64-66 | 핵심 쓰기 · Step 1 | 유지 | 프로젝트 층 「추가로 읽을 상태 파일」이 그 경로를 받을 수 있다 |
| 105 | 보고 첫 줄 `Handoff saved to [resolved path]` | handoff.md:131-132 | 핵심 쓰기 · Step 6 | 유지 | 결정 없음 |
| 106 | 보고 둘째 줄 `Live background work: [n] item(s), still running` | handoff.md:134 | 핵심 쓰기 · Step 6 | 분리 | `Live subagents` · `Live runners` · `Live scheduled` 개수로 나눠 보고한다 |
| 107 | 보고 셋째 줄 `Resume: ready — …` 또는 `not ready — <reason>` | handoff.md:135, 138-139 | 핵심 쓰기 · Step 6 | 유지 | 결정 없음 |
| 108 | ready 4조건(검사기 exit 0 · 모든 Live 항목에 결과 위치 · 결정·설계가 파일에 있음 · 쓰기 대기 없음) | handoff.md:141-148 | 핵심 쓰기 · Step 6 | 유지 | `Live scheduled` 항목은 결과 위치 대신 `fires-at`을 가진다 |
| 109 | `/clear`는 쓰기 명령이 하지 않고 사용자가 정한다 | handoff.md:150 | 핵심 쓰기 · Step 6 | 유지 | 결정 없음 |
| 110 | 보고 뒤 진행 중이던 작업을 계속하고, 지적받으면 파일을 다시 쓴다 | handoff.md:152-153 | 핵심 쓰기 · Step 6 | 유지 | 결정 없음 |
| 111 | 재개가 파일 전체를 읽으므로 모든 절이 이어진다는 노트 | handoff.md:335 | 핵심 쓰기 · Notes | 유지 | 결정 없음 |

## H. 재개 명령이 읽는 필드와 출력

| # | 현행 필드 | 현행 위치(파일:줄) | 새 위치 | 처리 | 근거 |
|---|---|---|---|---|---|
| 112 | 인자 4형식(없음 · 날짜 · slug · 경로) | resume-handoff.md:14-19 | 핵심 재개 · Usage | 유지 | 결정 없음 |
| 113 | Step 0 명령 `handoff_state.py <cwd> --resume [<arg>]` 1회 | resume-handoff.md:25-36 | 핵심 재개 · Step 0 | 유지 | 결정 없음 |
| 114 | Steps 0~3 호출 5개 표와 `TOOLS USED (Steps 0–3)` 보고 | resume-handoff.md:38-53, 102 | 핵심 재개 · Step 0 | 유지 | 호출 5개는 그대로다. Read는 핸드오프 1 + 규칙 파일 최대 2 = 최대 3이고, 읽기 전용 에이전트 입력에 결과 경로 · `status file:` · `state-files` 경로가 더해지며, `ListAgents`를 쓸 수 없으면 0회로 적는다 |
| 115 | 후보 파일 규칙(경로는 그 파일 그대로 · 날짜 · slug는 날짜 바로 뒤 · 인자 없음은 전부) | resume-handoff.md:57-63, handoff_state.py:115-133 | 핵심 재개 · Step 1 | 유지 | 날짜 · slug · 인자 없음 후보는 `project key:`와 `**Toplevel:**`이 같은 파일로 거른다. 경로 인자는 거르지 않고 프로젝트가 다르면 `continue: no (project mismatch)`다 |
| 116 | 후보 거름 규칙(빈 파일·읽기 불가·제목과 자리 표시만 있는 파일 제외, 최신 수정 시각, 동률이면 큰 파일, 그다음 경로 사전순) | resume-handoff.md:65-66, handoff_state.py:129-133 | 핵심 재개 · Step 1 | 유지 | 결정 없음 |
| 117 | 최소 내용 줄 수 10(스크립트에만 있고 재개 명령 문서에는 없음) | handoff_state.py:29, 129 | `is_placeholder` 공통 판정(줄 수 기준 폐기) | 스크립트 출력으로 대체 | 문서에 없는 줄 수 기준을 공통 자리 표시 판정으로 바꾼다 |
| 118 | `handoff: none found` 보고 뒤 중단 | resume-handoff.md:68, handoff_state.py:146-148 | 핵심 재개 · Step 1 | 유지 | 출력은 `handoff: none found for this project`가 된다. 가장 최근 파일이 다른 프로젝트 것이면 경고가 더해진다 |
| 119 | 파일 전체를 읽은 뒤 요약 | resume-handoff.md:72 | 핵심 재개 · Step 2 | 유지 | 프로젝트 층이 추가 상태 파일을 지정하면 함께 읽는다 |
| 120 | live 상태 3값 `running` · `returned` · `lost`와 읽기 전용 확인만 허용 | resume-handoff.md:76-85 | 핵심 재개 · Step 3 | 유지 | 서브에이전트는 전사 마지막 `stop_reason`으로 판정한다는 일반 규칙과 확인되지 않은 진행 중 상태 `running?`이 더해진다 |
| 121 | `handoff age:` 출력과 7일 초과 경고 | handoff_state.py:30, 150-152, resume-handoff.md:153 | 핵심 상태 스크립트 출력 | 유지 | 결정 없음 |
| 122 | `handoff branch:` 출력과 브랜치 변경 경고 | handoff_state.py:153-160, resume-handoff.md:153 | 핵심 상태 스크립트 출력 | 유지 | 결정 없음 |
| 123 | `handoff check: exit N`과 검사 출력 최대 8줄 | handoff_state.py:28, 164-176 | 핵심 상태 스크립트 출력 | 유지 | 결정 없음 |
| 124 | 브리핑 머리·꼬리 줄 `HANDOFF LOADED: [path]` | resume-handoff.md:92 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 125 | 브리핑 `PROJECT:`(Project·Topic 머리 줄을 읽음) | resume-handoff.md:94 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 126 | 브리핑 `CONFIDENCE:`(Confidence 절을 읽음, low면 코드로 확인) | resume-handoff.md:95 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 127 | 브리핑 `GOAL:`(What We Are Building을 요약) | resume-handoff.md:97-98 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 128 | 브리핑 `STATE:`(Branch · Uncommitted · Unpushed · Lockfile을 읽음) | resume-handoff.md:100-101 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 129 | 브리핑 `LIVE WORK:`(Live 필드를 읽고 각 항목의 상태와 결과 위치를 보고) | resume-handoff.md:104-105 | 핵심 재개 · Step 4 | 유지 | 세 필드로 분리된 Live를 모두 읽고 `decision`과 `after-return`도 싣는다 |
| 130 | 브리핑 `OPEN WORK:`(Current State of Files의 In progress·Not started를 읽음) | resume-handoff.md:107-108 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 131 | 브리핑 `CONFIRMED WORKING:`(What WORKED의 개수와 한 줄 요약) | resume-handoff.md:110-111 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 132 | 브리핑 `DECIDED (do not relitigate):`(Decisions Made 전부) | resume-handoff.md:113-114 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 133 | 브리핑 `REQUIREMENT NOW:`(Requirement Changes의 현재 상태) | resume-handoff.md:116-117 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 134 | 브리핑 `SETUP:`(Environment & Setup Notes 한 줄 요약) | resume-handoff.md:119-120 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 135 | 브리핑 `DO NOT RETRY:`(What Did NOT Work + Discarded Hypotheses 전부) | resume-handoff.md:122-124 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 136 | `DO NOT RETRY`는 `none`이어도 항상 표시 | resume-handoff.md:138 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 137 | 브리핑 `UNVERIFIED USER STATEMENTS:`(User Assumptions 전부) | resume-handoff.md:126-127 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 138 | 브리핑 `BLOCKERS / OPEN QUESTIONS:` | resume-handoff.md:129-130 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 139 | 브리핑 `NEXT STEP:`(Exact Next Step) | resume-handoff.md:132-133 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 140 | Exact Next Step이 없을 때의 문구 `No next step recorded — review 'What Has NOT Been Tried Yet' together first` | resume-handoff.md:134 | 핵심 재개 · Step 4 | 유지 | 결정 없음 |
| 141 | 사용자가 이 세션에서 이미 계속하라고 했으면 대기 없이 수행하고 질문 창을 열지 않는다 | resume-handoff.md:143-146 | 없음. 계속 여부는 핵심 재개 · Step 5의 `continue:` 줄이 정한다 | 제거(사용자 결정) | 본문의 상시 계속 지시와 이 세션의 계속 지시로 계속하는 경로를 없앴다. 계속 여부 규칙은 핵심 재개 Step 5가 소유한다 |
| 142 | 대기할 때는 파일을 건드리지 않고, 계속하라는 말이 오면 Exact Next Step을 그대로 수행하되 `returned`·`lost` Live 항목부터 처리 | resume-handoff.md:148-149 | 핵심 재개 · Step 5 | 유지 | 결정 없음 |
| 143 | 브리핑에 넣을 경고(`WARNING:` 접미 · 검사 exit 1과 문제 목록 · 파일 없음 경고) | resume-handoff.md:153-155 | 핵심 재개 · Warnings | 유지 | 무시한 인자 낱말 · 다른 프로젝트의 핸드오프 경고가 더해진다 |
| 144 | 핸드오프 파일은 수정하지 않는 읽기 전용 기록 | resume-handoff.md:159 | 핵심 재개 · Notes | 유지 | 결정 없음 |
| 145 | 새 세션을 저장하려면 `/handoff`를 다시 실행 | resume-handoff.md:160 | 핵심 재개 · Notes | 유지 | 결정 없음 |

대조 결과: 현행 145개 · 새 양식에 위치 있음 139개 · 사용자 결정 제거 6개 · 빠짐 0개
