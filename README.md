# 코인 매크로 백테스트 & 공유 서비스 (MVP)

실거래 없이, 내가 만든 코인 매매 규칙(매크로)을 **과거 시세로 시뮬레이션(백테스트)** 하고,
결과를 **인증 카드**로 공유하며, 남의 매크로를 **복제**해서 다시 돌려보는 서비스입니다.

> ⚠️ **이 서비스는 실제 주문/자동매매를 하지 않습니다.** 거래소 주문 API 연동이 전혀 없습니다.
> 모든 수익률은 **과거 데이터 시뮬레이션 결과**이며 화면·카드에 항상 배지로 명시됩니다.

---

## 스택
- 백엔드: Python 3.11+ / FastAPI, pandas, numpy, SQLModel(SQLite)
- 시세: Binance 공개 `klines` REST (인증 불필요) + 로컬 SQLite 캐시
- 공유 카드: **서버 Pillow** 렌더링 (`GET /api/card/{slug}.png`)
- 프론트: React + Vite + TailwindCSS (SPA)

---

## 빠른 실행

### 1) 백엔드 (필수 — 엔진/API/카드/테스트 전부 여기서 동작)
```bash
cd backend
python -m pip install -r requirements.txt
python -m pytest          # 엔진 + API 유닛테스트 (18개) 통과 확인
python -m uvicorn app.main:app --reload --port 8000
```
- API 문서: http://localhost:8000/docs
- 헬스체크: http://localhost:8000/api/health

### 2) 프론트엔드
> ⚠️ **Node.js 18+ 가 필요합니다** (이 저장소가 만들어진 환경엔 Node가 없어 프론트는 빌드/실행 검증을 못 했습니다. 백엔드는 전부 검증됨). Node 설치 후:
```bash
cd frontend
npm install
npm run dev               # http://localhost:5173  (/api 는 8000 으로 프록시)
```
프로덕션 단일 프로세스로 돌리려면:
```bash
cd frontend && npm run build      # frontend/dist 생성
cd ../backend && python -m uvicorn app.main:app --port 8000
# 이제 http://localhost:8000 에서 SPA + API 가 한 포트로 동작 (딥링크 fallback 포함)
```

---

## 주요 가정 (Assumptions) — 백테스트 엔진
- **봉:** 일봉(1d) 순차 시뮬레이션.
- **체결가:** 신호는 **해당 봉 종가**로 평가하고 그 종가에서 체결(슬리피지 반영). 봉 내 고가/저가로 TP/SL을 트리거하지 않습니다(단순화).
- **수수료:** 매수/매도 각 `commission_pct` (기본 0.1%).
- **슬리피지:** 매 체결마다 불리한 방향으로 `slippage_pct` (기본 0.05%). 매수는 높게, 매도는 낮게 체결.
- **펀딩비:** 숏 보유 시 하루당 `funding_pct` (기본 **0**, 경로만 구현 — 파라미터로 조정 가능).
- **레버리지(기본 1배):** `leverage=1`이면 현물과 동일하고 청산이 없습니다(기존과 완전히 동일). 2배 이상이면 **격리 마진 청산 시뮬레이션**이 켜집니다(아래 별도 섹션). 숏 수익률은 부호 반전(가격 하락=이익).
- **invest_ratio:** 진입 시 현재 자산의 해당 비율만 투입(분산). 타입 C(DCA)는 `amount_per_buy`가 투입액을 정하므로 invest_ratio 미적용.
- **stop_loss:** 롱은 진입가 대비 하락 −Y%, 숏은 상승 +Y%에서 전량 청산. **숏 규칙(A·B)은 stop_loss 필수** (프론트·백엔드 양쪽에서 강제).
- **결정론적:** 같은 매크로 + 같은 데이터 = 항상 같은 결과 (랜덤 없음).
- **DCA 수익률 기준:** 계획된 총 투입액(`매수횟수 × amount_per_buy`) 대비 최종 평가금액. 매수-후-보유라 승률은 최종 손익 부호로 표기(참고용).

### 레버리지·청산 (격리 마진 근사) — v9
> ⚠️ **백테스트·페이퍼(가짜 돈)에만 적용됩니다. 실거래(bot.py)에는 레버리지를 적용하지 않습니다.**
> 정밀 바이낸스식(마크 프라이스·구간별 유지증거금·펀딩)과 **다른 단순 근사식**입니다. 목적은
> "화려한 수익률"이 아니라 **레버리지가 얼마나 위험한지 가짜 돈으로 안전하게 체험**하게 하는 것입니다.

- **대상:** 방향성 타입 **A·B·D·E·F·G·H·I·J**. **C(DCA)는 제외**(항상 1배). 최대 배수는 `MAX_LEVERAGE`(기본 20) 상한.
- **마진 모델(격리, isolated 고정):** 투입 자금 = **증거금(margin)**, 포지션 명목가 = `margin × leverage`. 수익·손실이 배수만큼 확대되고, 수수료·펀딩비는 **명목가 기준**으로 계산합니다.
- **청산가(근사식):** 진입가 `E`, 유지증거금률 `mmr`(기본 0.5%, `MAINTENANCE_MARGIN_RATE`), 보정 `corr = mmr + commission%/100`
  - 롱: `liq ≈ E × (1 − 1/L + corr)`
  - 숏: `liq ≈ E × (1 + 1/L − corr)`
  - 핵심 성질: **레버리지가 높을수록 청산가가 진입가에 가까워집니다**(≈ `100/L %` 반대 움직임에서 청산). `corr`은 청산을 아주 조금 **일찍**(보수적) 트리거합니다.
- **청산 처리:** 진행 중 가격이 청산가에 닿으면(롱: 저가 ≤ 청산가 / 숏: 고가 ≥ 청산가) 포지션을 **전액 손실**(투입 증거금 소멸)로 강제 종료하고 그 이벤트를 기록합니다. 손실은 격리라 **증거금 한도에서 멈춥니다**(계정 전체가 아니라 그 포지션 몫만).
- **봉내 동시 터치:** 한 봉이 청산가와 익절/손절을 동시에 터치하면 **청산 우선**(보수적). A·B는 종가 기준이라 종가가 청산가를 넘으면 청산.
- **멀티오더(D 그리드·H 마틴게일):** 보유 로트의 **수량가중 평균 진입가**와 총 증거금 기준으로 청산가를 계산하고, 닿으면 책 전체를 청산합니다.
- **결과 표기:** 백테스트 결과에 `liquidation_count`(청산 횟수)·`liquidated_loss`(청산으로 잃은 금액), 페이퍼 상태에 동일 필드를 노출합니다. 화면·공유 카드·리더보드에 **"고위험 레버리지" 뱃지**를 붙입니다.
- **회귀:** `leverage=1`은 위 로직을 전부 건너뛰어 기존 현물 엔진과 **완전히 동일**하게 동작합니다(유닛테스트로 고정).

### 데이터 소스 표기
API 응답의 `data_source` 필드:
- `binance` — Binance에서 새로 조회 후 캐시에 저장
- `cache` — 로컬 SQLite 캐시에서 반환 (같은 구간 재조회 시 네트워크 미사용)
- `synthetic` — **오프라인 폴백**. 네트워크가 없고 캐시도 없을 때 종목명 기반의 **결정론적 합성 시세**를 생성해 데모가 끊기지 않게 함. 화면·카드에 소스가 표기됩니다.

---

## 매크로 = 3가지 형태
1. **정규화 JSON** — 저장/복제/API 교환용
2. **human_summary 한 줄** — 예: `BTC · 숏 · 평단 대비 +5% 익절 / -3% 손절 후 재진입 · 자금 50% 투입`
3. **share_slug 링크** — 예: `/s/btc-5pct-short-x1a2` (공유·복제 진입점)

### 규칙 타입 (A~J, `rule_type` discriminated union)
원본 A·B·C(단일 포지션·DCA)는 그대로 유지되고, v4에서 D~J가 추가됐습니다. D~J는 공통 엔벌로프
(`candle_interval` + 고급 리스크)를 공유하며, 공용 캔들 엔진(`app/engine/candles.py`)에서 실행됩니다.

- **A** 익절/손절 후 재진입 (long/short) — `take_profit_pct`, `initial_capital`
- **B** 지정가 밴드 매매 (long/short) — `buy_price`, `sell_price`, `initial_capital`
- **C** 정기 분할매수 DCA (**long 전용**) — `amount_per_buy`, `interval_days`
- **D** 그리드 매매 (long, 멀티오더) — `lower_price`, `upper_price`, `grid_count`, `grid_mode`, `per_grid_invest?`, `band_exit_action`
- **E** 트레일링 스탑 (long) — `entry_mode`, `entry_dip`, `activation_profit`, `trail_percent`, `reenter_after_exit`
- **F** RSI 조건 매매 (long/short, 지표) — `rsi_period`, `entry_threshold`, `exit_threshold`, `confirm_candles`, `exit_mode`, `take_profit?`
- **G** 볼린저밴드 (long/short, 지표) — `bb_period`, `bb_std`, `strategy`, `exit_target`, `squeeze_filter`, `squeeze_lookback`
- **H** 마틴게일/세이프티오더 (long, 멀티오더) — `base_order_size`, `safety_order_size`, `price_deviation`, `*_scale`, `max_safety_orders`, `take_profit`
- **I** 변동성 돌파 (long) — `k`, `exit_mode`, `trail_percent`, `take_profit?`, `ma_filter_period?`, `session_start_hour`
- **J** 이동평균 크로스 (long/short, 지표) — `ma_type`, `fast_period`, `slow_period`, `exit_signal`, `take_profit?`, `confirm_candles`

**공통 엔진 규칙(D~J):**
- **룩어헤드 방지:** 지표 타입(F/G/J)은 봉 마감(close) 신호 → **다음 봉 시가(open) 체결**. 진행 중 봉 값으로 판정하지 않음.
- **봉내 동시 터치:** 한 봉이 익절(고가)·손절(저가)을 동시에 터치하면 **손절 우선**(보수적) 처리하고, 그런 봉 개수를 `same_bar_sl_bars`로 결과에 표기.
- **주문 구조:** D(그리드)·H(마틴게일)는 다중 미체결 주문(멀티오더), 나머지는 단일 포지션.
- **자금 사전검증:** H는 최대 물타기, D는 전 격자 체결 시 필요자금이 `initial_capital × invest_ratio`를 넘으면 저장 반려.
- **숏 대칭:** F·G·J는 params 그대로 두고 엔진에서 방향 반전(D·E·H·I는 롱 전용).
- **공통 고급 리스크(전 타입):** `risk.daily_max_loss_pct`(당일 거래 중단), `risk.max_holding_hours`(강제 청산), `risk.cooldown_minutes`(손절 후 재진입 금지).
- **재사용:** 페이퍼 트레이딩도 동일 캔들 엔진을 사용하며, 실시간 틱을 봉으로 집계해 **봉 마감 기준**으로 평가(`CandleAggregatorSim`, `PAPER_CANDLE_TICKS` 조절).

---

## API
| Method | Path | 설명 |
|---|---|---|
| POST | `/api/macros` | 매크로 JSON 저장 → `share_slug` + 대표 백테스트 스냅샷 |
| GET  | `/api/macros/{slug}` | 매크로 JSON 조회(복제용) |
| POST | `/api/backtest` | 매크로 JSON(+기간 override) → 지표 |
| GET  | `/api/gallery` | 공유 매크로 백테스트 수익률순 목록 |
| GET  | `/api/card/{slug}.png` | 인증 카드 이미지(Pillow) |
| POST | `/api/paper/start` | 페이퍼 세션 시작(매크로+심볼+mode) → session_id |
| POST | `/api/paper/{id}/stop` | 페이퍼 세션 중지 |
| GET  | `/api/paper/{id}` | 현재 평가금액·수익률·최근 체결(폴링용) |
| GET  | `/api/paper/{id}/trades` | 전체 매매 로그 |
| POST | `/api/realtrade/bundle` | 실거래 실행 파일 zip(데모 목업) 다운로드 |
| GET  | `/api/kimchi-premium?symbol=BTC` | 김치프리미엄 집계(업비트 KRW · 바이낸스 USDT × USDKRW) |
| GET  | `/api/hot-coins?limit=10` | '오늘의 경주마' — 급등+거래활발 종목(바이낸스 24h, 서버 캐시 공유) |
| POST | `/api/leaderboard/register` | 매크로 등록 → 페이퍼 세션 시작 + 오늘의 리더보드 엔트리 생성 |
| GET  | `/api/leaderboard?user_id=` | 오늘(KST) 엔트리 목록(실시간 수익률·좋아요, 좋아요순) + 초기화까지 남은 초 |
| POST | `/api/leaderboard/{id}/vote` | 좋아요(+1)/싫어요(-1) 토글(유저당 1표) |
| POST | `/api/leaderboard/{id}/edit` | 비밀번호 확인(서버 해시) 후 매크로 수정 + 페이퍼 재시작 |
| GET  | `/api/chat` | 오늘(KST) 채팅 메시지 목록 |
| POST | `/api/chat` | 채팅 전송(아이디+내용, 길이제한·rate limit) |

**출력 지표:** 최종 수익률(%), 승률, MDD(최대낙폭 %), 총 매매 횟수, 자산곡선(equity curve).

---

## v3 추가 기능

### A. 페이퍼 트레이딩 (실시간 모의매매)
- 실제 주문·거래소 계정·API 키 **없이** 공개 실시간 시세로 "샀다/팔았다 치고" 기록만 합니다. 화면에 "모의(페이퍼)" 배지 상시 표시.
- **핵심 재사용:** 페이퍼의 가상 체결은 백테스트와 **동일한 실행 엔진**(`app/engine/stepper.py`의 `PositionSim`/`DcaSim`)을 그대로 사용합니다. 백테스트는 과거 일봉을, 페이퍼는 실시간 틱을 같은 `step(price)`에 흘려보내는 차이뿐입니다(중복 구현 아님).
- **실시간 소스 선택:** REST 폴링 방식(`GET /api/v3/ticker/price`)을 선택했습니다(websocket 대비 구현 단순·안정). 폴링 간격은 env로 조절.
- **데모 안정성 장치:** ① 종목 자유 입력(빌더 symbol) ② 민감도 조절(익절/손절 0.3~1% 등 임계값을 낮게) ③ **`demo_replay` 모드** — 최근 수 시간치 1분봉을 빠르게 재생해 매매 로그가 촤르륵 쌓이게 함(오프라인이면 결정론적 합성 인트라데이로 폴백).
- **환경 변수:** `PAPER_POLL_SECONDS`(기본 3), `PAPER_REPLAY_SECONDS`(기본 0.4), `PAPER_REPLAY_HOURS`(기본 6).
- **주의(범위):** 세션은 단일 프로세스 메모리+SQLite에 유지됩니다(데모 전제). 다중 워커 배포 시 공유 저장소 필요.

### B. 실거래 실행 파일 다운로드 (⚠️ 데모 목업, 실거래 미구현)
- 페이퍼 화면의 "실거래 실행 파일 다운로드" 버튼 → 매크로 설정이 담긴 **zip**(`bot.py` + `macro.json` + `README-run.txt`) 다운로드.
- `bot.py`는 실행 시 **API Key/Secret 입력 화면(tkinter, 없으면 콘솔)까지만** 뜨고, 입력하면 `"[데모] 인증 정보가 입력되었습니다…"` 메시지 후 종료합니다.
- **거래소 연결·주문 없음. 입력한 키는 저장·전송하지 않고 그대로 버립니다**(코드 주석·README-run.txt 명시). 서버로도 키를 보내지 않습니다.

### C. 초보자용 용어 툴팁
- 빌더/결과/페이퍼 화면의 전문용어 옆 **ⓘ 아이콘**에 마우스 hover(모바일 tap) 시 쉬운 말 설명 표시. 재사용 컴포넌트 `InfoTooltip`, 문구는 `src/lib/glossary.js` 한 곳에서 관리(수정·번역 용이).

### 데모 플로우 (5~8분)
빌더에서 매크로 생성(용어 툴팁 확인) → 백테스트 → **페이퍼 트레이딩 시작(매매 로그 실시간 누적)** → 중지 → **실거래 실행 파일 다운로드 → `python bot.py` → 키 입력 화면**.

---

## v4 추가 기능

### A. 매크로 타입 D~J 확장
- 위 "규칙 타입 (A~J)" 참고. 빌더는 `rule_type` 선택에 따라 타입별 params 폼만 조건부 렌더하고, 각 필드에 `InfoTooltip`을 노출합니다.
- 백엔드는 타입별 pydantic params 모델(`app/engine/schema.py`의 `ParamsD`~`ParamsJ`)로 검증하고, 프론트도 저장 전 동일 규칙을 검사합니다(`frontend/src/lib/macro.js`의 `validate`).
- 저장/복제/공유(share_slug) 로직은 기존 것을 그대로 재사용합니다.

### B. 상단 실시간 김치프리미엄 배너
- 계산식: `김프(%) = (업비트_KRW / (바이낸스_USDT × USDKRW) - 1) × 100`. 양수=김프(빨강), 음수=역프(파랑).
- **집계는 백엔드에서** 수행(`GET /api/kimchi-premium`)하고 프론트는 이 엔드포인트만 폴링해 CORS·레이트리밋을 회피합니다. 각 외부 호출은 짧게 캐시(`KIMCHI_CACHE_SECONDS`, 기본 10초).
- 데이터 소스(모두 공개·무인증): 업비트 `GET /v1/ticker`, 바이낸스 `GET /api/v3/ticker/price`, USD/KRW 환율 `open.er-api.com`(무키). **환율 API 실패 시 상수 fallback**(`KIMCHI_FX_FALLBACK`, 기본 1380)으로 죽지 않고 `fx_is_fallback` 플래그로 안내합니다.
- 기준 종목은 기본 BTC이며 배너에서 ETH/XRP/SOL로 전환 가능. 배너에 참고용 면책 문구 + `InfoTooltip` 노출. **김프는 참고 지표일 뿐 매매 신호로 쓰지 않습니다.**
- **환경 변수:** `KIMCHI_CACHE_SECONDS`(기본 10), `KIMCHI_FX_FALLBACK`(기본 1380), 프론트 폴링 주기 `VITE_KIMCHI_POLL_MS`(기본 15000).

---

## v5 추가 기능

### 하단 "오늘의 경주마" 마퀴 🐎
- 페이지 하단 고정 띠에 **급등 + 거래 활발** 종목 Top 10을 CSS 애니메이션 marquee로 흘려보냅니다(외부 라이브러리 없음).
- 선정 로직(`app/hotcoins.py`의 순수 함수 `select_hot_coins`, 테스트 가능):
  1. USDT 마켓만, **레버리지 토큰(BTCUP/ETHDOWN…)·스테이블/법정화폐 페어 제외**(단, `JUP`처럼 접미사만 우연히 겹치는 실제 코인은 유지),
  2. **24h 거래대금 하한** 미만 제외(잡코인 노이즈 억제),
  3. 거래대금 상위 후보로 압축 → 그중 **상승률 Top 10**.
- **서버 캐시 공유(전역):** 바이낸스 24hr 호출은 캐시 주기당 1회. 클라이언트가 늘어도 외부 호출은 안 늘어납니다(`GET /api/hot-coins`만 폴링).
- **UX:** hover 시 흐름 일시정지, `prefers-reduced-motion` 시 애니메이션 정지(가로 스크롤 허용), 아이템 클릭 시 **해당 종목으로 빌더 진입**(`/?symbol=XRPUSDT` 프리필). 가격/상승률은 기존 `format.js` 재사용.
- **면책:** "급등 종목은 참고용이며 투자 조언이 아닙니다. 급등 코인은 변동성·손실 위험이 큽니다." 매매 신호로 쓰지 않습니다.
- **환경 변수:** `HOTCOINS_MIN_QUOTE_VOLUME`(기본 10,000,000), `HOTCOINS_CANDIDATE_POOL`(기본 100), `HOTCOINS_CACHE_SECONDS`(기본 45), 프론트 폴링 `VITE_HOTCOINS_POLL_MS`(기본 45000).

---

## v6 추가 기능

### A. 현물 시세 없는 종목 안전장치 (합성 폴백 제거)
- 선물 전용·미상장 등 **현물(spot) 시세가 없는 종목**으로 백테스트/페이퍼/리더보드 등록을 시도하면, 합성(가짜) 데이터로 돌지 않고 **명확히 거부**합니다.
  - `get_klines(..., allow_synthetic=False)` → 실데이터 없으면 `NoSpotDataError`. 백테스트/페이퍼 경로는 이 모드를 사용 → API가 **HTTP 422 + "이 종목은 현물 시세 데이터가 없어 시뮬레이션할 수 없습니다."** 반환.
  - 페이퍼는 세션 시작 전에 `ensure_spot_available(symbol)`로 검증(잘못된 심볼 400은 거부, 네트워크 불명 시 캐시 유무로 판단).
  - 프론트는 이 에러를 빨간 배너로 안내하고 해당 액션을 막습니다. **공유/갤러리 등 기존 흐름의 offline 합성 폴백(`allow_synthetic=True` 기본)은 그대로 유지.**
- 마퀴("오늘의 경주마")는 **현물 소스 유지** → 선물 전용 급등주는 애초에 노출되지 않습니다.

### B. 실거래 실행 파일 — `.bat` 원클릭
- 다운로드 zip에 **`run.bat`**(+`requirements.txt`)이 추가됩니다: 파이썬 확인 → 의존성 설치(이 데모는 표준 라이브러리만 써서 사실상 no-op) → `bot.py` 실행.
- 경로에 공백/한글이 있어도 되도록 `%~dp0` + 따옴표 처리, 파이썬 미설치 시 안내 후 `pause`로 창이 안 꺼짐. **여전히 실거래는 하지 않습니다**(키 입력 화면까지만, 저장/전송 없음).
- zip 구성: `run.bat` · `bot.py` · `requirements.txt` · `macro.json` · `README-run.txt`.

### C. "갤러리" → "오늘의 리더보드"
- 메뉴/라우트를 **오늘의 리더보드**(`/leaderboard`, 기존 `/gallery`는 리다이렉트)로 변경.
- **나만의 매크로 등록:** 버튼 → **빌더 모달(팝업)**. [저장] 시 그 매크로로 **페이퍼 세션을 시작**하고 리더보드에 올림(현물 없는 종목은 안전장치로 거부), [취소] 시 닫힘. 닉네임·유저 식별은 **localStorage 익명 id**(`lib/user.js`).
- **좋아요/싫어요:** 유저당 1표 토글(같은 값 재클릭 시 취소). **정렬: 좋아요 점수(likes−dislikes) 내림차순 → 실시간 수익률 → 등록순.**
- **KST 일일 초기화:** 엔트리는 `created_ms`로 저장하고 **조회 시 오늘(KST 00:00~) 것만 필터** → 스케줄러 없이 매일 자정 자동 리셋(과거 엔트리는 물리 삭제 아님). 등록 시각은 **KST HH:MM**으로 표시("오늘 14:32 등록").
- **상위 3등 이월(방어전):** 초기화가 전부를 지우지는 않는다. 그날 첫 `GET /api/leaderboard`가 **직전 보드 날짜의 상위 `LEADERBOARD_KEEP_TOP`(기본 3)등**의 `created_ms`(=보드 날짜)만 오늘 자정으로 민다. **페이퍼 세션·`created_at`·누적 수익률은 손대지 않는다** — 순위는 "등록 시점부터의 순수 매크로 수익률" 경쟁이므로 이월은 초기화가 아니다. 매크로가 제 조건(익절선·손절선·최대 보유시간)으로 종료돼 세션이 `stopped`가 돼도 그 **종료 시점 수익률로 계속 순위에 남는다**. `streak_days`가 하루씩 늘어 UI에 **"N일째 순위권 방어중"**으로 표시되고, 원 등록 시각은 `first_created_ms`에 한 번만 보관한다. AI 챌린지와 같은 lazy·idempotent 패턴(`leaderboardcarryover` 테이블의 KST 날짜 유니크 키)이라 별도 크론이 필요 없다.
- **초기화 카운트다운:** 응답의 `seconds_to_reset`(다음 KST 00:00까지 남은 초)를 프론트가 매초 로컬 감소 → `HH:MM:SS` 표시. KST는 DST가 없어 고정 +09:00 오프셋으로 계산(tz DB 의존성 회피).
- 실시간 수익률은 **기존 페이퍼 엔진 재사용**(각 엔트리의 paper 세션 상태 조회). 좋아요·수익률은 참고용이며 매수 신호가 아닙니다.

---

## v7 추가 기능 (리더보드 정렬·복사·소유권·채팅)

### A. 수익률 순 정렬 + 실시간 갱신
- 리더보드 **기본 정렬을 "현재 수익률(%) 내림차순"으로 변경**(좋아요는 카드에 계속 표시하되 정렬 기준에서는 부가). 동률은 **등록 빠른 순**, 아직 수익률이 없는(집계 전) 엔트리는 맨 뒤.
- 수익률은 프론트 폴링(5초)으로 갱신되고 순위도 자연 재정렬. 목록 조회는 **인메모리 페이퍼 상태만** 읽어 외부 호출 0. 페이퍼 세션의 실시간 시세는 **종목별 공유 캐시**(`get_ticker_price_cached`, 기본 2초 TTL)로 읽어 같은 종목 세션이 많아도 바이낸스 호출이 중복되지 않음.

### B. 매크로 복사하기 → 빌더
- 각 엔트리의 **📋 복사** 버튼 → 그 매크로가 그대로 프리필된 빌더로 이동(라우터 state로 macro 전달 → 기존 `macroToForm` 재사용). 값 수정 후 자기 것으로 재등록 가능.

### C. 아이디/비밀번호 소유권 (정식 인증 아님)
- 등록 시 **아이디(표시용)+비밀번호 필수**(둘 중 하나라도 비면 저장 불가). 리더보드에 **아이디 표출**.
- 비밀번호는 **PBKDF2-HMAC-SHA256 + 랜덤 솔트**(`app/security.py`, 표준 라이브러리 — bcrypt C빌드 의존성 회피)로 해시 저장. **평문 저장·전송·로깅·응답 포함 없음.** 아이디 중복은 허용(단순 표시용, 개인정보 미수집). 팝업에 "다른 서비스와 다른 임시 비밀번호 사용" 경고 노출.

### D. 엔트리 수정 (비밀번호 확인)
- 각 엔트리 **✏ 수정** → 비밀번호 입력 → **서버에서 해시 비교**가 일치할 때만 수정 허용(불일치 403 "비밀번호가 일치하지 않습니다"). 성공 시 기존 페이퍼 세션 중지 후 새 설정으로 재시작(현물 없는 종목은 안전장치로 거부). 실패 시도는 **(엔트리, IP)별 rate limit**(1분 5회).

### E. 리더보드 채팅
- 하단 실시간 채팅(아이디+내용, 3초 폴링). **XSS 안전**(React가 렌더 시 이스케이프, HTML 미출력), **길이 제한 300자**, **rate limit**(클라이언트별 10초 5개). **KST 자정 초기화 대상**(오늘 것만 조회), 시각 KST 표시, "투자 조언 아님" 면책 상시 노출.

---

## v8 추가 기능 (실행파일 안정화 · 테스트넷 실구동 · 라이트 테마)

### A. 실거래 다운로드 실행파일 견고화
- `run.bat`: `@echo off` + `chcp 65001`(한글 콘솔) + 마지막 `pause`. **파이썬 탐색 `py -3 → python → python3`** 순서, 없으면 설치 안내 후 `pause`. 모든 경로 `"%~dp0"` + 따옴표(공백/한글 경로 대응). 의존성 설치·실행 각 단계 실패 시 원인 출력 후 `pause`(창 즉시 안 꺼짐).
- 추가 동봉: `run.ps1`(PowerShell 실행정책 안내 포함), `requirements.txt`(`python-binance`). `README-run.txt`에 **수동 실행 명령**(`pip install -r requirements.txt` → `python bot.py`)도 명시.

### B. bot.py — 바이낸스 테스트넷 실구동 (실제 API·실제 주문, 가짜 자금)
- `python-binance`의 `Client(key, secret, testnet=True)`로 **Spot Testnet(testnet.binance.vision)**에 실제 연결·잔고조회·주문. 흐름/호출은 실거래와 동일.
- 실행: 테스트넷 키 입력 → 연결·USDT(가짜) 잔고 확인 → **시세 폴링 → 매크로 조건 평가 → 실제 주문(create_order) 전송 → 주문ID/체결 콘솔 출력**. 주문 수량은 자본×invest_ratio 기반, `LOT_SIZE`/`MIN_NOTIONAL` 필터에 맞춰 반올림.
- 단순화: 백테스트/페이퍼 엔진의 **진입 + 익절/손절/밴드** 부분을 실거래로 옮긴 버전(지표 전략은 해당 매크로의 익절/손절로 근사).
- **안전장치:** 1회 주문 상한 `MAX_ORDER_USDT`(기본 100), 중복 발주 방지(포지션 플래그), 주문 실패 재시도 제한·에러 로그, `Ctrl+C` 안전 종료(미청산 포지션 안내). **출금/이체 기능 없음**(주문만). **API 키 저장·전송·로깅 없음**(Secret은 `getpass`로 입력 숨김).
- **기본은 테스트넷.** 메인넷 전환은 사용자가 **`USE_TESTNET = True → False` 한 곳만** 변경(자동 전환 없음). `README-run.txt`에 테스트넷 키 발급법 + 메인넷 전환(거래권한만/출금끄기/소액/책임·컴플라이언스) 가이드 수록.

### C. 라이트 테마
- 전체 UI를 **다크 → 라이트**로 전환. 페이지 `bg-slate-50 text-slate-900`(index.css), 카드 `bg-white`, 보더 `slate-200/300`, 입력 `bg-slate-100`. **의미 색상 유지**(상승 초록·하락 빨강·김프/역프·좋아요/싫어요 등은 라이트 대비를 위해 400→600 셰이드로 조정), 채도 버튼(indigo/blue/red-600)은 `text-white` 유지.
- 마퀴·김프 배너·리더보드·빌더·결과/페이퍼·채팅·모달 전부 라이트 기준으로 정리. 색상 매핑은 한 번에 일괄 적용해 일관성 확보.

---

## 이번 MVP에서 선택한 기본값 (요약)
- 공유 카드는 **서버 Pillow** 방식 선택(프론트 빌드 없이도 공유 URL로 이미지 제공 가능). Windows `malgun.ttf`로 한글 렌더링.
- 저장소는 **SQLite** 단일. 매크로는 `backend/app.db`, 시세 캐시는 `backend/cache/market.db`.
- 로그인 없음(익명 생성·공유). 기간 preset은 조회 시점의 현재 시각 기준으로 해석.
- 오프라인에서도 데모가 되도록 **결정론적 합성 시세 폴백** 추가(소스 명시).
- 프론트 차트는 외부 라이브러리 없이 **인라인 SVG**로 자산곡선을 그림(의존성·오프라인 안전).

## 범위 밖 (다음 버전)
카피트레이딩, **실거래 레버리지**(현재 레버리지·청산은 백테스트/페이퍼 전용), cross 마진, 숏 DCA, 로그인.
(v4에서 트레일링 스탑·그리드·마틴게일·변동성 돌파·RSI/볼린저/MA크로스·최대손실 한도·재매수 쿨다운은 백테스트/페이퍼로 구현됨.
v9에서 **레버리지 조건 + 격리 마진 청산 시뮬레이션**이 백테스트/페이퍼로 구현됨.)

---

## 테스트
```bash
cd backend && python -m pytest -q
```
- 규칙 타입 × 롱/숏 백테스트, invest_ratio, 수수료 반영, 결정론성, 숏 stop_loss 강제, human_summary, API 플로우(생성→조회→재백테스트→갤러리→카드) 커버.
