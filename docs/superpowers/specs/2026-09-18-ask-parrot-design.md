# 껄무새에게 물어볼까? — 카드형 매크로 조합 탐색 (v1) 설계

작성: 2026-09-18 · 상태: 승인됨(구현 계획 단계)

## 0. 한 줄 요약

직접 만들기(Studio) 조건 판에 "껄무새에게 물어볼까?" 버튼을 두고, 카드(선택 칩) 다섯 장으로
성향·시장·종목·기간·매매 빈도를 받아 **백테스트 상위 3개 조합**을 보여 주고 조건 판에 바로 싣는다.
AI 는 조합의 뼈대만 제안하고, 성과 숫자는 전부 기존 백테스트 엔진이 계산한다.

## 1. 원칙 — 법적 설계를 UI 구조로 강제

가상자산은 자본시장법 투자자문 대상이 아니지만, 2단계 입법·AI 기본법·표시광고법·민사 분쟁을
고려해 "추천"이 아니라 "탐색 도구"로 보이고 실제로도 그렇게 동작해야 한다.

| 원칙 | 구현 |
|---|---|
| 종목은 사용자가 고른 것만 | 종목 카드 외에 AI 가 종목을 고르는 경로 없음. 프롬프트에도 종목 고정 |
| 숫자는 AI 가 만들지 않는다 | AI 는 매크로 뼈대(rule_type·params)만 제안. 수익률·MDD·승률은 `_run_any` 백테스트 결과만 표시 |
| "추천" 표현 금지 | 화면·API·프롬프트·로그 전부 "후보 조합 상위 3개" |
| 목표 수익 입력 없음 | "기간"은 백테스트 구간 선택지. 수익률·목표 입력칸 없음 |
| 자유 텍스트 없음 | 종목 검색창 하나만 자유 입력(심볼 검증). 채팅 입력창 없음. 후속 질문은 미리 정한 버튼만 |
| 성향 반영 | 안정형은 선물·H(세이프티 주문) 후보 제외, 성향별 MDD 상한 적용 |
| AI 고지 + 동의 + 기록 | 첫 1회 동의(서버 저장), 결과마다 고정 고지, 요청·결과 전부 서버 기록 |
| 무료 | 플랜 게이팅 없음. 하루 횟수 제한만 (대가성 자문으로 보이지 않게) |

고정 고지 문구(결과 카드 하단, `DISCLAIMER_VERSION = "ask-v1"`):

> AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요

## 2. 진입점

- 위치: Studio(직접 만들기) 조건 판 상단, 매크로 파일 등록 버튼 옆. 라벨 "껄무새에게 물어볼까?"
- 비로그인: 로그인으로 보냄(`/login?next=/builder?ask=1`). 기록을 남겨야 하므로 로그인 필수.
- 클릭 → 모달 `AskParrotDialog`. 조건 판에 기본값이 아닌 입력이 있으면 결과를 불러올 때
  "지금 조건이 바뀌어요" 확인 후 덮어씀.

## 3. 카드 흐름

껄무새 말풍선(왼쪽) + 선택 칩. 고른 칩은 오른쪽 내 말풍선으로 굳는다. 이전 내 말풍선을 누르면
그 단계로 돌아가 다시 고르고, 그 뒤 단계는 초기화된다.

| # | 껄무새 말풍선 | 선택지 | 매핑 |
|---|---|---|---|
| 0 | 동의(첫 1회만). "과거 데이터로 조합을 찾아 주는 도구예요. 투자 권유가 아니고 결과가 미래 수익을 뜻하지 않아요." | [알겠어요] | `POST /api/ask/consent` → `user.ask_consent_version` |
| 1 | "손실은 어디까지 견딜 수 있어요?" | 안정형(-10%까지) / 균형형(-20%까지) / 공격형(제한 없음) | `risk_profile: stable|balanced|aggressive` |
| 2 | "어느 시장에서요?" | 현물 / 선물 1x·2x·3x | `market: spot|futures`, `leverage`. 안정형이면 선물 칩 비활성 + "안정형은 현물만 살펴봐요" |
| 3 | "어떤 종목이 궁금해요? (최대 3개)" | 최근 백테스트 종목 칩 · 인기 칩(BTCUSDT ETHUSDT SOLUSDT XRPUSDT DOGEUSDT) · 검색 | `symbols[]` 1~3개. 2개 이상이면 포트폴리오 후보도 생성 |
| 4 | "어느 기간을 살펴볼까요?" | 최근 3개월 / 6개월 / 1년 | `period_preset: 3m|6m|1y` |
| 5 | "얼마나 자주 사고팔고 싶어요?" | 자주(1h) / 보통(4h) / 느긋하게(1d) | `interval` |
| — | "돌려 볼게요…" 진행 표시 | | 요청 1회, 완료까지 스피너 + "후보 N개를 백테스트하는 중" |
| 6 | 결과 3장 | 카드마다: 유형 한글명 · 핵심 설정 3줄 · 수익률 / MDD / 승률 / 거래 수 · "왜 이 조합?" 5줄(AI 표시) · [조건 판에 불러오기] [백테스트 상세] | |
| 7 | 후속 버튼 | [더 안정적으로] [더 공격적으로] [다른 종목으로] [처음부터] | 각각 1번/1번/3번 카드로 되돌아가 재실행. 자유 입력 없음 |

- "최근 백테스트 종목"은 브라우저 localStorage 의 최근 심볼(없으면 인기 칩만).
- 결과가 3개 미만이면 있는 만큼 보여 주고 "이 조건에선 후보가 적었어요 — 기간이나 빈도를 바꿔 보세요".
- 결과 0개면 카드 7 만 표시.

## 4. 백엔드

### 4.1 `POST /api/ask/consent` (로그인 필수)
- 본문 없음. `user.ask_consent_version = DISCLAIMER_VERSION`, `ask_consent_at` 저장. 응답 `{ok, version}`.

### 4.2 `POST /api/ask/macros` (로그인 필수)

요청:
```json
{ "risk_profile": "stable|balanced|aggressive", "market": "spot|futures", "leverage": 1,
  "symbols": ["BTCUSDT"], "period_preset": "3m|6m|1y", "interval": "1h|4h|1d" }
```
검증: 심볼 1~3개(대문자 USDT 페어), 안정형 + 선물 → 422, 레버리지 1~3, 선물이 아닌데 레버리지>1 → 422.
동의 버전 불일치 → 403 `{code:"consent_required"}`. 하루 한도 초과 → 429 `{code:"daily_limit", remaining_today:0, resets_at}`.

파이프라인 (`backend/app/ask.py`):

1. **후보 생성** `build_candidates(req) -> list[Macro]`
   - 성향별 템플릿 그리드(각 유형 파라미터 프리셋 2~3개, `initial_capital` 1,000,000):
     - stable: C(적립) · J(이평 교차) · G(밴드 회귀) · A(익절/손절)
     - balanced: stable + F(RSI) · E(트레일링)
     - aggressive: balanced + I(변동성 돌파) · H(세이프티 주문)
     - D(그리드)·B(지정가 밴드)는 절대 가격 구간이 필요해 템플릿에서 제외. C(적립)는 레버리지를 못 쓰므로 선물이면 제외.
   - `candle_interval = interval`, `market`, `leverage`, `period.preset`, `position_side = long` 고정.
   - 심볼마다 단일 매크로 + 심볼 2개 이상이면 `symbols=[...]` 포트폴리오 매크로 1벌.
   - Gemini 제안: `ai_challenge` 의 프롬프트를 성향·시장·interval 인지형으로 확장한 `_ai_propose(req)`
     (허용 rule_type 은 성향 집합으로 제한, `Macro(**dict)` 검증, 실패분 폐기). 키 없으면 건너뜀.
   - 총 ≤ 24개. 템플릿은 '각 유형의 첫 프리셋 × 모든 종목'을 먼저, 두 번째 프리셋을 나중에 넣고 초과분은 뒤에서 자른다.
2. **백테스트** — 후보마다 `_run_any(macro)`; 예외는 그 후보만 건너뜀. 캔들은 기존 캐시를 공유.
3. **필터** — 성향별 MDD 상한(stable 10 / balanced 20 / aggressive 없음), `total_trades >= 3`.
4. **정렬 점수** — stable `return / max(mdd, 1)`, balanced `return - 0.5 * mdd`, aggressive `return`.
   상위 3개. **같은 rule_type 은 1개까지**(다양성). 점수 동률이면 거래 수 많은 쪽.
5. **설명** — `ai_explain.enrich(result, macro)` (캐시 있음, 실패 시 규칙 기반 문장). `ai_generated` 플래그 포함.
6. **기록** — `AskMacroSession` 행 1개: user_id, request_json, candidate_count, results_json(매크로+지표 3개),
   disclaimer_version, ai_used(bool), elapsed_ms, created_ms/created_at.

응답:
```json
{ "results": [ { "label": "이평 교차 · 1h", "rule_type": "J", "macro": {...},
                 "metrics": { "final_return_pct": 0, "mdd_pct": 0, "win_rate_pct": 0, "total_trades": 0 },
                 "explanation": "...", "ai_generated": true } ],
  "candidate_count": 18, "disclaimer_version": "ask-v1", "remaining_today": 4 }
```

- 한도: `ASK_DAILY_LIMIT` (기본 5) / 계정 / KST 날짜. `AskMacroSession` 의 (user_id, day_kst) 행 수로 센다(성공 응답만 기록).
- `GET /api/ask/status` (로그인 필수) → `{consented, remaining_today, daily_limit, disclaimer_version}` — 모달을 열 때 0번 카드 표시 여부와 남은 횟수를 정한다.
- 실행 시간 상한 `ASK_TIME_BUDGET_SEC` (기본 20): 넘으면 남은 후보는 건너뛰고 지금까지로 정렬.
- Gemini 실패·키 없음은 조용히 템플릿만으로 진행. 응답 `ai_used` 로 구분.

### 4.3 DB
- `user.ask_consent_version: str = ""`, `user.ask_consent_at: str = ""`
- `askmacrosession` 테이블 (`AskMacroSession` SQLModel): id, user_id(index), request_json, candidate_count,
  results_json, disclaimer_version, ai_used, elapsed_ms, day_kst, created_at, created_ms(BigInteger), 인덱스 (user_id, day_kst).
- sqlite `_migrate` ALTER + `_PG_ADDED_COLUMNS`, Supabase 마이그레이션 `2026091812xxxx_ask_macro_sessions.sql`
  (테이블 + user 컬럼 2개 + RLS 켜고 Data API 권한 회수), `supabase/tests/gg_parrot_rls.sql` 에 테이블 추가.

## 5. 프론트

- `frontend/src/lib/askFlow.js` — 카드 상태 머신 **순수 리듀서**. 상태 `{step, answers, consented}`,
  액션 `choose(step, value)`, `back(step)`, `reset`, `followUp(kind)`. 규칙: 안정형이면 선물 선택 불가,
  뒤로 가면 이후 답 초기화, 종목 최대 3개. `toRequest(answers)` 로 API 본문 생성.
- `frontend/src/components/AskParrotDialog.jsx` + `AskParrotDialog.css` — 말풍선/칩 UI, 진행 표시, 결과 카드,
  후속 버튼, 고정 고지. 결과 카드 "조건 판에 불러오기" → `macroToForm(macro)` → `setForm`, 모달 닫고
  토스트 "조건 판에 불러왔어요. 숫자 한 번 보고 백테스트부터 돌려 보세요".
- `frontend/src/api.js` — `askConsent()`, `askMacros(body)`.
- `Studio.jsx` — 버튼과 모달 배선, `?ask=1` 쿼리로 자동 열기(로그인 리다이렉트 복귀용).
- 카드 라벨·고지 문구는 `lib/askCopy.js` 한 곳에.

## 6. v1 제외

자유 채팅 · 종목 자동 제안 · 실행기 바로 실행 · 유료 게이팅 · 결과 저장/공유 · 관리자 화면 집계(로그만 남김)

## 7. 테스트

백엔드 (`backend/tests/test_ask.py`):
- 안정형 후보에 선물·H 가 없고 MDD 10 초과가 걸러진다
- 결과 3개의 rule_type 이 서로 다르다
- Gemini 없이도(키 미설정) 결과가 나온다
- 동의 없으면 403, 동의 후 200
- `ASK_DAILY_LIMIT` 초과 시 429, `remaining_today` 감소
- 성공 시 `askmacrosession` 행 1개, request/results JSON 보존
- 안정형+선물 / 심볼 4개 / 현물+레버리지 2 → 422

프론트 (`frontend/tests/askFlow.test.js`, `node --test`):
- 카드 순서대로 진행, 뒤로 가면 이후 답 초기화
- 안정형이면 선물 선택이 거부된다
- 종목 4개째 거부, 후속 버튼이 올바른 단계로 되돌린다
- `toRequest` 가 API 본문을 정확히 만든다

## 8. 확정한 결정

① 무료 + 하루 5회 ② 로그인 필수 ③ 진입점은 직접 만들기(Studio)만 ④ 종목 최대 3개 ⑤ 결과 3개는 규칙 유형이 서로 다르게
