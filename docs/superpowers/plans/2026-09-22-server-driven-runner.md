# 서버 신호 실행기 + 실봉 페이퍼 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전략 판단을 서버 엔진 한 곳에 두어 백테스트·리더보드(페이퍼)·실계좌 실행기가 같은 봉·같은 로직으로 돌게 한다. 실행기는 서버 명령대로 주문만 넣는다.

**Architecture:** `engine/candle_feed.py`가 바이낸스 마감봉을 (종목·간격·시장)별로 한 번씩 받아 구독자에게 밀어 준다. `engine/driver.py`의 `StrategyDriver`가 페이퍼 `_Runner`에서 뽑아낸 시뮬레이션 코어(레그·틱·체결 요약·상태 저장/복구·웜업)이고, 페이퍼(`paper.py`)와 새 `runner_engine.py`(실행기 세션)가 함께 쓴다. `runner_engine`은 Fill을 `RunnerCommand` 행으로 바꿔 heartbeat 응답에 실어 주고, 실행기 v8은 그 명령을 실행해 `acks`로 보고한다. v8 미만 실행기는 A/B 외 매크로를 시작할 수 없다(426).

**Tech Stack:** Python 3.12 · FastAPI · SQLModel(SQLite 개발 / Supabase Postgres 운영) · asyncio · pytest · tkinter 실행기(PyInstaller) · unittest(실행기)

**Spec:** [docs/superpowers/specs/2026-09-22-server-driven-runner-design.md](../specs/2026-09-22-server-driven-runner-design.md)

## Global Constraints

- 백엔드 테스트: `cd backend && .venv/Scripts/python -m pytest tests/<file> -q -p no:warnings`. 기존 실패(prefect 미설치 3건, 수집 오류 6건)는 이 작업과 무관 — 새로 깨지는 것만 본다.
- 실행기 테스트: 레포 루트에서 `python -m unittest discover -s runner -p "test_*.py"`.
- 사용자 문구는 한국어 · 해요체("~해요", "~해 주세요"). 오류 메시지는 스펙 문구를 그대로 쓴다.
- 커밋은 `git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "<한국어 메시지>\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>"`.
- `seongbeen` 등 다른 사람 브랜치에 푸시하지 않는다. 작업 브랜치 `feat/server-driven-runner`에서 하고, 끝나면 `main`에 머지한다.
- 환경변수 기본값: `CANDLE_GRACE_SECONDS=2`, `CANDLE_RETRY_SECONDS=30`, `COMMAND_TTL_SECONDS=90`, `RUNNER_SIGNAL_MIN_VERSION=8`, `PAPER_CHECKPOINT_SECONDS=10`(기존). `PAPER_CANDLE_TICKS`는 제거한다.
- 웜업 봉 수 `WARMUP_CANDLES = 500`(MA `slow_period ≤ 400`).
- 봉 마감 판정은 바이낸스 `closeTime ≤ now`(`get_recent_klines`의 `closed`)만 믿는다. 진행 중 봉은 절대 전략에 넣지 않는다.
- 실행기 v8은 스스로 진입/청산을 판단하지 않는다. 로컬에 남는 것은 안전망(손절·일일 손실·최대 보유시간)만.
- 파일 편집은 Windows 셸 인용 문제를 피하려면 Edit/Write 도구를 쓴다(heredoc 안의 따옴표가 깨진 전례 있음).

---

## 파일 구조

| 파일 | 역할 |
|---|---|
| `backend/app/engine/candle_feed.py` (신규) | 마감봉 피드 — 구독·폴링·웜업 이력 |
| `backend/app/engine/stepper.py` | `Fill.reason/qty_before`, `make_sim`이 `LiveCandleSim` 반환 |
| `backend/app/engine/candles.py` | `LiveCandleSim`(`CandleAggregatorSim` 대체), `CandleSim.warmup/reset_book/_reset_position_state`, 지표 사유 |
| `backend/app/engine/driver.py` (신규) | `Leg`, `StrategyDriver` — 페이퍼·실행기 공용 시뮬레이션 코어 |
| `backend/app/paper.py` | `_Runner`가 드라이버를 품음, 피드 구독/웜업, 복구 순서 |
| `backend/app/db.py` | `RunSession.state_json`, `RunnerCommand` |
| `supabase/migrations/20260922120000_runner_commands.sql` (신규) | 운영 DB 마이그레이션 |
| `backend/app/runner_engine.py` (신규) | 실행기 세션 드라이버·명령 변환·ack·복구 |
| `backend/app/runner.py` | 버전 게이트(426)·heartbeat 프로토콜·드라이버 시작/정지 훅 |
| `backend/app/main.py` | 요청 모델 `acks`, claim 라우트 버전 전달, lifespan |
| `runner/installation.py`, `runner/macro_runner.py` | v8: 로컬 전략 제거, 명령 실행·ack |
| `frontend/src/features/agents/modules/runnerLog.js` | `warn` 종류 라벨 |
| `DEPLOY.md`, `README.md`, `runner/README.md` | 배포·동작 설명 |

---

### Task 0: 브랜치

- [ ] **Step 1: 브랜치 만들기**

```bash
cd C:/Users/RHJ/Desktop/gg_parrot && git checkout -b feat/server-driven-runner
```

---

### Task 1: `Fill.reason` · `Fill.qty_before`

**Files:**
- Modify: `backend/app/engine/stepper.py:29-38` (Fill), `:317-320` (PositionSim._fill), `:445-448` (DcaSim._fill)
- Modify: `backend/app/engine/candles.py:223-226` (CandleSim._fill), `_open_long/_open_short/_close_all/_reduce_long/_liquidate_all`
- Test: `backend/tests/test_fill_meta.py` (신규)

**Interfaces:**
- Produces: `Fill(side, price, qty, equity_after, return_pct, reason: str = "", qty_before: float = 0.0)`. 청산 Fill의 `qty_before`는 체결 직전 보유 수량(전량 청산이면 `== qty`). `reason` 기본값: 진입 `"진입"`, 청산 `"청산"`, 손절 `"손절"`, 부분 청산 `"부분 청산"`, 강제 청산 `"강제 청산"`.
- `CandleSim._open_long(margin, close, ts, mark, *, reason="진입")`, `_open_short(close, ts, mark, *, reason="진입")`, `_close_all(close, mark, *, is_stop, ts, reason=None)` — `reason=None`이면 `"손절" if is_stop else "청산"`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_fill_meta.py`:
```python
"""Fill 에 사유·직전 수량이 붙는다 — 실행기 명령 변환(qty_frac)과 로그 문구의 근거."""
from datetime import datetime, timezone

from app.engine import Macro
from app.engine.stepper import make_sim


def _a():
    return Macro.model_validate({
        "symbol": "BTCUSDT", "rule_type": "A", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"take_profit_pct": 2, "initial_capital": 1000},
        "risk": {"stop_loss_pct": 1, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    })


def _rsi():
    return Macro.model_validate({
        "symbol": "BTCUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 1000},
        "risk": {"stop_loss_pct": 0, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    })


def test_position_sim_fill_carries_reason_and_qty_before():
    sim = make_sim(_a(), 1000.0)
    t = datetime(2026, 9, 22, tzinfo=timezone.utc)
    entry = sim.step(100.0, t)
    assert entry.side == "buy" and entry.reason == "진입" and entry.qty_before == 0.0
    exit_ = sim.step(98.0, t)  # 손절
    assert exit_.side == "sell" and exit_.reason == "손절"
    assert abs(exit_.qty_before - entry.qty) < 1e-12 and abs(exit_.qty - exit_.qty_before) < 1e-12


def test_candle_sim_indicator_reason_names_the_signal():
    inner = make_sim(_rsi(), 1000.0).inner  # LiveCandleSim.inner == RSISim (Task 2 가 LiveCandleSim 을 만든다)
    t = datetime(2026, 9, 22, tzinfo=timezone.utc)
    closes = [100, 90, 80, 70, 60]  # RSI(2) 가 30 아래로
    fills = []
    for c in closes:
        fills += inner.on_candle(c, c, c, c, t)
    for c in [61, 70, 90, 120, 150]:
        fills += inner.on_candle(c, c, c, c, t)
    sides = [f.side for f in fills]
    assert sides[:2] == ["buy", "sell"]
    assert fills[0].reason.startswith("RSI ") and fills[0].reason.endswith("· 진입")
    assert fills[1].reason.startswith("RSI ") and fills[1].reason.endswith("· 청산")
    assert abs(fills[1].qty_before - fills[0].qty) < 1e-9
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_fill_meta.py -q -p no:warnings`
Expected: FAIL — `Fill.__init__() got an unexpected keyword argument 'reason'` 또는 `AttributeError: reason`. (두 번째 테스트는 Task 2 전까지 `.inner`가 없어 실패해도 된다 — Task 2 끝에 다시 돈다.)

- [ ] **Step 3: Fill 확장**

`backend/app/engine/stepper.py` `Fill`:
```python
@dataclass
class Fill:
    """One simulated execution produced by a sim step."""

    side: str  # "buy" | "sell" | "short" | "cover"
    price: float
    qty: float
    equity_after: float
    return_pct: float
    # 실행기 명령·로그용 메타. 사유는 사람이 읽는 한 줄("RSI 23.1 ≤ 25 · 진입"), 직전 수량은
    # 청산 비율(qty / qty_before)을 낼 때 쓴다. 장부가 이미 바뀐 뒤 _fill 이 불리므로 되계산한다.
    reason: str = ""
    qty_before: float = 0.0


EXIT_SIDES = frozenset({"sell", "cover"})
_DEFAULT_REASON = {"buy": "진입", "short": "진입", "sell": "청산", "cover": "청산"}


def _fill_meta(side: str, qty: float, qty_after: float, reason: str) -> dict:
    before = qty_after + qty if side in EXIT_SIDES else max(0.0, qty_after - qty)
    return {"reason": reason or _DEFAULT_REASON.get(side, ""), "qty_before": float(before)}
```

`PositionSim._fill`(317줄 근처):
```python
    def _fill(self, side: str, price: float, qty: float, mark: float, reason: str = "") -> Fill:
        eq = self.equity(mark)
        ret = (eq - self.initial_capital) / self.initial_capital * 100.0
        return Fill(side=side, price=price, qty=qty, equity_after=eq, return_pct=ret,
                    **_fill_meta(side, qty, self.qty if self.in_pos else 0.0, reason))
```
`_do_close`의 마지막 줄을 `return self._fill(side, f, traded_qty, mark, reason="손절" if is_stop else "청산")`, `_liquidate`의 마지막 줄을 `return self._fill(side, px, traded_qty, mark, reason="강제 청산")`으로.

`DcaSim._fill`(445줄 근처):
```python
    def _fill(self, side: str, price: float, qty: float, mark: float, reason: str = "") -> Fill:
        eq = self.equity(mark)
        ret = (eq - self.initial_capital) / self.initial_capital * 100.0
        return Fill(side=side, price=price, qty=qty, equity_after=eq, return_pct=ret,
                    **_fill_meta(side, qty, self.qty, reason))
```

- [ ] **Step 4: CandleSim 사유**

`backend/app/engine/candles.py` — 상단 import에 `from .stepper import ... _fill_meta` 추가(이미 `Fill, buy_fill, sell_fill`을 가져오는 줄에 붙인다).

```python
    def _fill(self, side: str, price: float, qty: float, mark: float, reason: str = "") -> Fill:
        eq = self.equity(mark)
        ret = (eq - self.initial_capital) / self.initial_capital * 100.0
        return Fill(side=side, price=price, qty=qty, equity_after=eq, return_pct=ret,
                    **_fill_meta(side, qty, abs(self.total_qty()), reason))

    def _open_long(self, margin: float, close: float, ts: datetime, mark: float, *, reason: str = "진입") -> Optional[Fill]:
        ...(기존 본문)...
        return self._fill("buy", f, qty, mark, reason)

    def _open_short(self, close: float, ts: datetime, mark: float, *, reason: str = "진입") -> Optional[Fill]:
        ...
        return self._fill("short", f, qty, mark, reason)

    def _close_all(self, close: float, mark: float, *, is_stop: bool, ts: datetime, reason: Optional[str] = None) -> Optional[Fill]:
        ...
        return self._fill(side, f, qty, mark, reason if reason is not None else ("손절" if is_stop else "청산"))
```
`_reduce_long` 마지막 줄 → `return self._fill("sell", f, sold_qty, mark, "부분 청산")`; `_liquidate_all` 마지막 줄 → `return self._fill(side, px, qty, mark, "강제 청산")`. `GridSim` 531줄 `fills.append(self._fill("sell", f, lot.qty, c))` → `fills.append(self._fill("sell", f, lot.qty, c, f"격자 {sell_level:g} 도달 · 매도"))`.

`_IndicatorSim`에 신호 메모 필드와 사유 전달:
```python
    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        self._pending: Optional[str] = None  # "enter" | "exit" | None
        self.tp = self.p.get("take_profit")
        self._signal_note = ""  # 마지막 판정의 한 줄 근거 — Fill.reason 에 실린다

    def _strategy(self, o, h, l, c, ts, fills):
        if self._pending == "exit" and self.in_position():
            f = self._close_all(o, o, is_stop=False, ts=ts, reason=f"{self._signal_note} · 청산" if self._signal_note else None)
            ...
        elif self._pending == "enter" and not self.in_position() and not self._entry_blocked(ts):
            reason = f"{self._signal_note} · 진입" if self._signal_note else "진입"
            if self.side is PositionSide.SHORT:
                f = self._open_short(o, ts, c, reason=reason)
            else:
                f = self._open_long(self.invest_ratio * self.cash, o, ts, c, reason=reason)
```
(나머지 본문은 그대로.) 각 지표 sim의 `_signal`에서 `_pending`을 세울 때 메모를 함께 둔다:
- `RSISim._signal`: `self._pending = "enter"` 직전에 `self._signal_note = f"RSI {v:.1f} {'≥' if short else '≤'} {self.exit_th if short else self.entry_th:g}"`, `"exit"` 직전에 `self._signal_note = f"RSI {v:.1f} {'≤' if short else '≥'} {self.entry_th if short else self.exit_th:g}"`.
- `BollingerSim._signal`: 진입 `f"볼린저 하단 {lower:.4g} 이탈"`(숏은 상단), 청산 `f"볼린저 {'중심' if ... else '상단'} {level:.4g} 도달"` — 기존 판정 변수 이름을 그대로 쓴다(파일에서 확인).
- `MACrossSim._signal`: 진입 `"골든크로스"`(숏은 `"데드크로스"`), 청산 반대.

- [ ] **Step 5: 통과 확인 (첫 테스트) + 기존 엔진 테스트**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_fill_meta.py::test_position_sim_fill_carries_reason_and_qty_before tests/test_engine.py tests/test_paper_reuse.py tests/test_advanced_risk.py -q -p no:warnings`
Expected: PASS (백테스트 결과는 Fill 필드 추가에 영향받지 않는다).

- [ ] **Step 6: 커밋**

```bash
git add backend/app/engine/stepper.py backend/app/engine/candles.py backend/tests/test_fill_meta.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "엔진 Fill 에 사유·직전 수량 — 실행기 명령 변환과 로그의 근거

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `LiveCandleSim` + `warmup/reset_book`

**Files:**
- Modify: `backend/app/engine/candles.py` (`CandleAggregatorSim` 삭제 → `LiveCandleSim`; `CandleSim.warmup/reset_book/_reset_position_state`; 각 sim `_reset_position_state`)
- Modify: `backend/app/engine/stepper.py:450-462` (`make_sim`)
- Modify: `backend/tests/test_leaderboard_state.py:497` 주석, `README.md:111`
- Test: `backend/tests/test_candles_warmup.py` (신규)

**Interfaces:**
- Produces: `LiveCandleSim(macro, initial_capital=None)` — `inner: CandleSim`, `step(price, ts=None) -> Optional[Fill]`(큐 드레인만), `on_candle(o, h, l, c, ts) -> int`(큐에 넣은 Fill 수), `equity(price)`, `state()`, `restore(...)`, `warmup(candles)`, `pending() -> int`.
- `CandleSim.warmup(candles: Iterable)` — 각 원소는 `(t_ms, o, h, l, c)` 튜플/네임드튜플. `ts = datetime.fromtimestamp(t_ms/1000, timezone.utc)`.
- `CandleSim.reset_book()`, `CandleSim._reset_position_state()`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_candles_warmup.py`:
```python
"""실봉 어댑터 — 틱은 전략을 돌리지 않고, 봉이 와야 판단한다. 웜업은 지표만 채우고 장부는 비운다."""
from datetime import datetime, timezone

import pytest

from app.engine import Macro
from app.engine.candles import LiveCandleSim
from app.engine.stepper import make_sim

T = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _macro(rule, params, **over):
    base = {
        "symbol": "BTCUSDT", "rule_type": rule, "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "5m", "period": {"preset": "3m"}, "params": {"initial_capital": 1000, **params},
        "risk": {"stop_loss_pct": 0, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    base.update(over)
    return Macro.model_validate(base)


RSI = _macro("F", {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70})


def _bars(closes, start_ms=1_700_000_000_000, step=300_000):
    return [(start_ms + i * step, c, c, c, c) for i, c in enumerate(closes)]


def test_make_sim_returns_live_candle_sim_for_candle_types():
    assert isinstance(make_sim(RSI, 1000.0), LiveCandleSim)
    assert not isinstance(make_sim(_macro("A", {"take_profit_pct": 2}), 1000.0), LiveCandleSim)


def test_ticks_never_trade_only_candles_do():
    sim = make_sim(RSI, 1000.0)
    for p in [100, 90, 80, 70, 60, 50, 40]:
        assert sim.step(p, T) is None  # 3틱 합성봉이 없다
    assert sim.state()["in_position"] is False
    for c in [100, 90, 80, 70, 60]:
        sim.on_candle(c, c, c, c, T)
    n = sim.on_candle(60, 60, 60, 60, T)  # 직전 봉의 enter 의도가 이 봉 시가에 실행
    assert n == 1 and sim.pending() == 1
    fill = sim.step(60.0, T)
    assert fill is not None and fill.side == "buy" and sim.pending() == 0
    assert sim.step(61.0, T) is None
    assert abs(sim.equity(66.0) - 1000.0 * 66.0 / 60.0) < 1e-6  # 틱은 평가만 갱신


def test_warmup_keeps_indicator_but_resets_book():
    sim = make_sim(RSI, 1000.0)
    sim.warmup(_bars([100, 90, 80, 70, 60, 50, 60, 70, 100, 130, 160]))  # 안에서 매수·매도가 일어났다
    st = sim.state()
    assert st["in_position"] is False and st["qty"] == 0.0
    assert sim.inner.cash == 1000.0 and sim.inner.lots == [] and sim.inner.closed_trades == []
    assert sim.inner.rsi._count > 0  # 지표 상태는 살아 있다
    assert sim.pending() == 0  # 웜업 체결은 큐에 남지 않는다


def test_warmup_then_first_live_candle_can_trade_immediately():
    sim = make_sim(RSI, 1000.0)
    sim.warmup(_bars([100, 90, 80, 70, 60, 50]))  # 마지막 봉에서 RSI(2)<30 → enter 의도 유지
    sim.on_candle(50, 50, 50, 50, T)
    assert sim.step(50.0, T).side == "buy"


@pytest.mark.parametrize("rule,params", [
    ("E", {"trail_percent": 2.0, "activation_profit": 1.0}),
    ("D", {"lower_price": 50, "upper_price": 150, "grid_count": 4}),
    ("H", {"base_order_size": 100, "safety_order_size": 100, "price_deviation": 5, "take_profit": 3}),
    ("I", {"k": 0.5}),
    ("G", {"bb_period": 3, "bb_std": 1.0}),
    ("J", {"fast_period": 2, "slow_period": 3}),
])
def test_every_candle_sim_resets_position_state_after_warmup(rule, params):
    sim = make_sim(_macro(rule, params), 1000.0)
    sim.warmup(_bars([100, 95, 90, 85, 80, 90, 100, 110, 120, 110, 100]))
    inner = sim.inner
    assert inner.state()["in_position"] is False and inner.cash == 1000.0 and inner.stopped is False
    for name in ("_peak", "_armed", "_ref", "_so_done", "_base_price", "_exit_next_open", "_defended"):
        if hasattr(inner, name):
            assert not getattr(inner, name), name
    if hasattr(inner, "holdings"):
        assert inner.holdings == {}
```
(K는 `_macro`의 필수 파라미터가 많아 별도 확인 — `SarSim._reset_position_state`는 `_reset_after_flat()`을 부른다.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_candles_warmup.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'LiveCandleSim'`.

- [ ] **Step 3: `CandleSim.warmup/reset_book/_reset_position_state`**

`backend/app/engine/candles.py` `CandleSim`에 `state()` 아래 추가:
```python
    # -- 실봉 웜업 ---------------------------------------------------------
    def reset_book(self) -> None:
        """장부·포지션·공통 리스크 상태를 시작값으로. 지표 상태는 건드리지 않는다(웜업용)."""
        self.cash = self.initial_capital
        self.lots = []
        self.closed_trades = []
        self.same_bar_sl = 0
        self.liquidations = 0
        self.liquidated_loss = 0.0
        self._day = None
        self._day_start_equity = self.initial_capital
        self._halted_day = None
        self._cooldown_until = None
        self._entry_time = None
        self.stopped = False
        self._reset_position_state()

    def _reset_position_state(self) -> None:
        """포지션이 있어야 의미 있는 전략 상태를 되돌린다. 기본은 없음 — 하위 sim 이 덮어쓴다."""

    def warmup(self, candles) -> None:
        """과거 마감봉으로 지표·전략 상태를 채운다. 그동안의 가상 체결은 버리고 장부만 되돌린다.

        _IndicatorSim 의 ``_pending`` 은 남긴다 — 백테스트가 다음 봉 시가에 실행하는 의도와 같다.
        """
        for t_ms, o, h, l, c in candles:
            ts = datetime.fromtimestamp(int(t_ms) / 1000, timezone.utc)
            self.on_candle(float(o), float(h), float(l), float(c), ts)
        self.reset_book()
```

하위 sim마다:
```python
# TrailingSim
    def _reset_position_state(self) -> None:
        self._peak = 0.0
        self._armed = False
        self._ref = None

# GridSim
    def _reset_position_state(self) -> None:
        self.holdings = {}

# MartingaleSim
    def _reset_position_state(self) -> None:
        self._so_done = 0
        self._base_price = 0.0

# BreakoutSim
    def _reset_position_state(self) -> None:
        self._peak = 0.0
        self._exit_next_open = False

# SarSim
    def _reset_position_state(self) -> None:
        self._reset_after_flat()
```
`_IndicatorSim`은 기본(no-op) — `_pending`·streak는 지표 상태다.

- [ ] **Step 4: `LiveCandleSim`**

`backend/app/engine/candles.py` 끝의 `_TICKS_PER_CANDLE`·`CandleAggregatorSim`을 지우고:
```python
# --- paper/runner adapter: real closed candles ---------------------------
# 캔들형 전략은 봉 마감에만 판단한다. 실시간에서는 CandleFeed 가 바이낸스 마감봉을 밀어 주고
# (on_candle), 3초 틱은 평가 갱신과 체결 큐 드레인만 한다(step). 이전의 "3틱=1봉" 합성은 없다.
class LiveCandleSim:
    """Wrap a :class:`CandleSim` behind the stepper's ``step(price)`` contract, fed by real candles."""

    def __init__(self, macro: Macro, initial_capital: Optional[float] = None) -> None:
        self.inner = make_candle_sim(macro, initial_capital=initial_capital)
        self._queue: deque[Fill] = deque()

    def step(self, price: float, ts: Optional[datetime] = None) -> Optional[Fill]:
        return self._queue.popleft() if self._queue else None

    def on_candle(self, o: float, h: float, l: float, c: float, ts: datetime) -> int:
        fills = self.inner.on_candle(o, h, l, c, ts)
        self._queue.extend(fills)
        return len(fills)

    def pending(self) -> int:
        return len(self._queue)

    def warmup(self, candles) -> None:
        self.inner.warmup(candles)
        self._queue.clear()

    def equity(self, price: float) -> float:
        return self.inner.equity(price)

    def state(self) -> dict:
        return self.inner.state()

    def restore(self, equity: float, *, in_position: bool, qty: float, entry_price: float,
                last_price: float, cooldown_until_ms: Optional[int] = None) -> None:
        self.inner.restore(equity, in_position=in_position, qty=qty, entry_price=entry_price,
                           last_price=last_price, cooldown_until_ms=cooldown_until_ms)

    @property
    def liquidations(self) -> int:
        return self.inner.liquidations

    @property
    def liquidated_loss(self) -> float:
        return self.inner.liquidated_loss
```
`import os`가 이 파일에서 더 안 쓰이면 지운다.

`backend/app/engine/stepper.py` `make_sim`:
```python
    if macro.rule_type in CANDLE_TYPES:
        # Types D~K are candle-based; real closed candles arrive via CandleFeed → on_candle.
        from .candles import LiveCandleSim

        return LiveCandleSim(macro, initial_capital=initial_capital)
```

`backend/tests/test_leaderboard_state.py:497` 주석을 `# LiveCandleSim`으로. `README.md:111`을
`- **재사용:** 페이퍼 트레이딩과 실행기 신호도 동일 캔들 엔진을 사용하며, 바이낸스 **실제 마감봉**을 받아 봉 마감 기준으로 평가(\`LiveCandleSim\` + \`CandleFeed\`).`로.

- [ ] **Step 5: 통과 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_candles_warmup.py tests/test_fill_meta.py tests/test_leaderboard_state.py tests/test_engine.py -q -p no:warnings`
Expected: PASS. `test_leaderboard_state.py`의 캔들 restore 라운드트립도 그대로 통과.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/engine backend/tests/test_candles_warmup.py backend/tests/test_leaderboard_state.py README.md
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "캔들형 페이퍼 어댑터를 실봉 방식으로 — LiveCandleSim, 웜업(지표만 남기고 장부 초기화)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `CandleFeed`

**Files:**
- Create: `backend/app/engine/candle_feed.py`
- Test: `backend/tests/test_candle_feed.py`

**Interfaces:**
- Produces:
  ```python
  Candle = namedtuple("Candle", "t o h l c")           # t = open time ms
  class CandleFeed:
      def __init__(self, *, fetch=None, now_ms=None, sleep=None)
      def subscribe(self, symbol, interval, market, callback) -> Subscription   # callback: async def (symbol, Candle) -> None
      def unsubscribe(self, sub) -> None
      async def history(self, symbol, interval, market, n) -> list[Candle]      # 마감봉 최근 n개(오래된 것부터)
      async def poll_once(self, key) -> list[Candle]                            # 한 번 받아 새 마감봉을 배달, 배달한 봉 반환
      @staticmethod
      def next_close_ms(interval, now_ms) -> int
  feed = CandleFeed()   # 모듈 싱글턴
  ```
  `fetch(symbol, interval, limit, market) -> list[dict]`(기본: `get_recent_klines`를 `asyncio.to_thread`로). 반환 dict 키 `t,o,h,l,c,closed`.
- Consumes: `app.data.binance.get_recent_klines`, `_INTERVAL_MS`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_candle_feed.py`:
```python
"""마감봉 피드 — 마감봉만, 한 번씩, 구독자 격리."""
import asyncio

import pytest

from app.engine.candle_feed import Candle, CandleFeed


def _k(t, c, closed=True):
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "closed": closed}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_next_close_ms_is_the_next_interval_boundary():
    assert CandleFeed.next_close_ms("5m", 1_700_000_000_000) == 1_700_000_100_000  # 1_700_000_000_000 은 5m 경계 아님
    assert CandleFeed.next_close_ms("1h", 1_700_002_800_000) == 1_700_006_400_000  # 경계 위면 다음 경계


def test_poll_once_delivers_only_new_closed_candles_in_order():
    calls = []
    now = 1_700_000_600_000
    rows = [_k(now - 900_000, 1.0), _k(now - 600_000, 2.0), _k(now - 300_000, 3.0, closed=False)]

    async def fetch(symbol, interval, limit, market):
        return rows

    feed = CandleFeed(fetch=fetch, now_ms=lambda: now, sleep=lambda s: asyncio.sleep(0))
    got = []

    async def cb(symbol, candle):
        got.append((symbol, candle))

    sub = feed.subscribe("BTCUSDT", "5m", "spot", cb)
    key = ("BTCUSDT", "5m", "spot")
    delivered = _run(feed.poll_once(key))
    assert [c.c for c in delivered] == [1.0, 2.0]
    assert got == [("BTCUSDT", Candle(now - 900_000, 1.0, 1.0, 1.0, 1.0)), ("BTCUSDT", Candle(now - 600_000, 2.0, 2.0, 2.0, 2.0))]
    assert _run(feed.poll_once(key)) == []  # 같은 봉은 두 번 배달하지 않는다
    feed.unsubscribe(sub)
    assert key not in feed._subs


def test_callback_exception_does_not_break_other_subscribers():
    async def fetch(symbol, interval, limit, market):
        return [_k(1_700_000_000_000, 5.0)]

    feed = CandleFeed(fetch=fetch, now_ms=lambda: 1_700_000_900_000, sleep=lambda s: asyncio.sleep(0))
    seen = []

    async def bad(symbol, candle):
        raise RuntimeError("boom")

    async def good(symbol, candle):
        seen.append(candle.c)

    feed.subscribe("ETHUSDT", "1h", "spot", bad)
    feed.subscribe("ETHUSDT", "1h", "spot", good)
    _run(feed.poll_once(("ETHUSDT", "1h", "spot")))
    assert seen == [5.0]


def test_history_returns_closed_candles_oldest_first():
    async def fetch(symbol, interval, limit, market):
        assert limit == 4  # n + 1: 마지막 진행 중 봉을 뺀다
        return [_k(1, 1.0), _k(2, 2.0), _k(3, 3.0), _k(4, 4.0, closed=False)]

    feed = CandleFeed(fetch=fetch)
    assert [c.c for c in _run(feed.history("BTCUSDT", "5m", "spot", 3))] == [1.0, 2.0, 3.0]


def test_run_loop_stops_when_last_subscriber_leaves():
    async def fetch(symbol, interval, limit, market):
        return []

    async def scenario():
        feed = CandleFeed(fetch=fetch, now_ms=lambda: 1_700_000_000_000, sleep=lambda s: asyncio.sleep(0))

        async def cb(symbol, candle):
            pass

        sub = feed.subscribe("BTCUSDT", "1m", "spot", cb)
        task = feed._tasks[("BTCUSDT", "1m", "spot")]
        feed.unsubscribe(sub)
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()

    _run(scenario())
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_candle_feed.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: app.engine.candle_feed`.

- [ ] **Step 3: 구현**

`backend/app/engine/candle_feed.py`:
```python
"""마감봉 피드 — 바이낸스 마감봉을 (종목·간격·시장)별로 한 번씩 받아 구독자에게 밀어 준다.

캔들형 전략(D~K)은 봉 마감에만 판단한다. 페이퍼·실행기 세션이 같은 봉을 구독하므로 백테스트와
같은 봉으로 돈다. 진행 중 봉은 절대 배달하지 않는다(``closed`` 만 믿는다).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import namedtuple
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from ..data.binance import _INTERVAL_MS, get_recent_klines

log = logging.getLogger(__name__)

Candle = namedtuple("Candle", "t o h l c")
Key = Tuple[str, str, str]  # (symbol, interval, market)
Callback = Callable[[str, Candle], Awaitable[None]]

GRACE_SECONDS = float(os.environ.get("CANDLE_GRACE_SECONDS", "2"))
RETRY_SECONDS = float(os.environ.get("CANDLE_RETRY_SECONDS", "30"))
RETRY_STEP_SECONDS = 2.0


class Subscription:
    __slots__ = ("key", "callback")

    def __init__(self, key: Key, callback: Callback) -> None:
        self.key = key
        self.callback = callback


async def _default_fetch(symbol: str, interval: str, limit: int, market: str) -> list[dict]:
    return await asyncio.to_thread(get_recent_klines, symbol, interval, limit, market=market)


def _to_candle(row: dict) -> Candle:
    return Candle(int(row["t"]), float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"]))


class CandleFeed:
    def __init__(self, *, fetch=None, now_ms=None, sleep=None) -> None:
        self._fetch = fetch or _default_fetch
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleep or asyncio.sleep
        self._subs: Dict[Key, List[Subscription]] = {}
        self._tasks: Dict[Key, asyncio.Task] = {}
        self._last_t: Dict[Key, int] = {}

    # -- 구독 -----------------------------------------------------------
    def subscribe(self, symbol: str, interval: str, market: str, callback: Callback) -> Subscription:
        key: Key = (symbol.upper(), interval, market)
        sub = Subscription(key, callback)
        self._subs.setdefault(key, []).append(sub)
        if key not in self._tasks:
            # 구독 시점의 마지막 마감봉부터 새 봉만 배달한다(웜업은 history 로 따로).
            self._last_t.setdefault(key, self.next_close_ms(interval, self._now_ms()) - _INTERVAL_MS[interval] - 1)
            self._tasks[key] = asyncio.get_event_loop().create_task(self._run(key))
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.key)
        if not subs:
            return
        if sub in subs:
            subs.remove(sub)
        if not subs:
            self._subs.pop(sub.key, None)
            self._last_t.pop(sub.key, None)
            task = self._tasks.pop(sub.key, None)
            if task is not None:
                task.cancel()

    # -- 시각 -----------------------------------------------------------
    @staticmethod
    def next_close_ms(interval: str, now_ms: int) -> int:
        step = _INTERVAL_MS[interval]
        return (now_ms // step + 1) * step

    # -- 조회 -----------------------------------------------------------
    async def history(self, symbol: str, interval: str, market: str, n: int) -> List[Candle]:
        n = max(1, min(int(n), 999))
        rows = await self._fetch(symbol.upper(), interval, n + 1, market)
        closed = [_to_candle(r) for r in rows if r.get("closed")]
        return closed[-n:]

    async def poll_once(self, key: Key) -> List[Candle]:
        symbol, interval, market = key
        rows = await self._fetch(symbol, interval, 3, market)
        last = self._last_t.get(key, -1)
        fresh = sorted((_to_candle(r) for r in rows if r.get("closed") and int(r["t"]) > last), key=lambda c: c.t)
        for candle in fresh:
            self._last_t[key] = candle.t
            for sub in list(self._subs.get(key, [])):
                try:
                    await sub.callback(symbol, candle)
                except Exception:
                    log.exception("candle feed: subscriber failed for %s %s", symbol, interval)
        return fresh

    # -- 루프 -----------------------------------------------------------
    async def _run(self, key: Key) -> None:
        symbol, interval, market = key
        try:
            while key in self._subs:
                target = self.next_close_ms(interval, self._now_ms()) + int(GRACE_SECONDS * 1000)
                await self._sleep(max(0.0, (target - self._now_ms()) / 1000.0))
                deadline = self._now_ms() + int(RETRY_SECONDS * 1000)
                while key in self._subs:
                    try:
                        if await self.poll_once(key):
                            break
                    except Exception:
                        log.exception("candle feed: fetch failed for %s %s", symbol, interval)
                    if self._now_ms() >= deadline:
                        break  # 이번 봉은 다음 라운드의 limit=3 으로 따라잡는다
                    await self._sleep(RETRY_STEP_SECONDS)
        except asyncio.CancelledError:
            raise


feed = CandleFeed()
```
`subscribe`가 이벤트 루프 밖(테스트의 동기 문맥)에서 불릴 수 있으므로 `asyncio.get_event_loop()` 대신 다음을 쓴다:
```python
        if key not in self._tasks:
            ...
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            self._tasks[key] = loop.create_task(self._run(key))
```
(`test_poll_once…`는 루프 밖에서 `subscribe`하고 `_run`으로 `poll_once`만 돌린다. 태스크는 `_run`에서 `_sleep`이 `asyncio.sleep(0)`이라 실제로 돌 일 없이 종료된 루프에 남는다 — 테스트 마지막에 `unsubscribe`로 취소한다.)

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_candle_feed.py -q -p no:warnings`
Expected: PASS (5).

- [ ] **Step 5: 커밋**

```bash
git add backend/app/engine/candle_feed.py backend/tests/test_candle_feed.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "마감봉 피드 — 종목·간격·시장별 1구독, 마감봉만 배달, 웜업용 이력

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `StrategyDriver` 추출 + `paper.py` 연결

**Files:**
- Create: `backend/app/engine/driver.py`
- Modify: `backend/app/paper.py` (`_Leg` → `driver.Leg` 별칭, `_Runner`가 드라이버 보유, `_tick/_aggregate/_note_fill/_state_view/sim_state/_rebuild_runner` 위임)
- Test: `backend/tests/test_driver.py` (신규); 기존 `test_leaderboard_state.py`, `test_paper_portfolio.py`, `test_paper*.py` 통과

**Interfaces:**
- Produces (`app/engine/driver.py`):
  ```python
  EXIT_SIDES = frozenset({"sell", "cover"})
  def sim_state(sim) -> dict                      # paper.sim_state 이동(paper 는 재export)
  class Leg: symbol, sim, initial, last_price, equity, ret, liquidations, liquidated_loss, replay_prices; view() -> dict
  class StrategyDriver:
      def __init__(self, legs: list[Leg], initial: float, *, macro: Optional[Macro] = None, now_ms=None)
      legs, initial, macro, last_price, equity, ret, liquidations, liquidated_loss, trade_count, last_fill, entry_returns
      symbol -> legs[0].symbol ; sim -> legs[0].sim ; symbols -> [..] ; is_portfolio() ; leg_for(symbol)
      def tick(self, price, ts=None, symbol=None) -> Optional[Fill]     # step + 평가 + 합산 + note_fill
      def note_fill(self, fill, symbol) -> None
      def is_candle_based(self) -> bool
      def candle_keys(self) -> list[tuple[str, str, str]]               # macro 없으면 []
      def push_candle(self, symbol, candle) -> int                       # LiveCandleSim.on_candle
      def warmup(self, candles_by_symbol: dict[str, list]) -> None
      def state(self) -> dict                                            # = 옛 paper._state_view
      def restore(self, state: dict, *, leg_equity: dict[str, float], total_equity: float) -> None
      def restore_fills(self, trades: list[dict]) -> None                # trade_count/entry_returns/last_fill 재구성
  ```
- `paper.py`는 `_Leg = Leg`, `sim_state`를 driver에서 import해 재export, 모듈 함수 `_tick(runner, price, ts, symbol)`, `_note_fill(runner, fill, symbol)`, `_state_view(runner)`, `_aggregate(runner)`를 드라이버 위임 래퍼로 남긴다(테스트 호환).
- `_Runner`: `self.driver`, 그리고 `legs/sim/symbol/last_price/equity/ret/liquidations/liquidated_loss/trade_count/last_fill/entry_returns`는 드라이버로 위임하는 프로퍼티(setter 포함 — 테스트가 `runner.trade_count = …` 같이 대입할 수 있다).

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_driver.py`:
```python
"""공용 전략 드라이버 — 페이퍼와 실행기가 같은 코어를 쓴다."""
from datetime import datetime, timezone

from app.engine import Macro
from app.engine.driver import Leg, StrategyDriver
from app.engine.stepper import make_sim

T = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _macro(rule="A", **over):
    base = {
        "symbol": "BTCUSDT", "rule_type": rule, "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "5m", "period": {"preset": "3m"},
        "params": {"take_profit_pct": 2, "initial_capital": 1000} if rule == "A"
                  else {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 1000},
        "risk": {"stop_loss_pct": 1, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    base.update(over)
    return Macro.model_validate(base)


def _driver(macro, initial=1000.0):
    return StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)], initial, macro=macro)


def test_tick_steps_sim_and_notes_fill():
    d = _driver(_macro("A"))
    fill = d.tick(100.0, T)
    assert fill.side == "buy" and d.trade_count == 1 and d.last_fill["kind"] == "" and d.entry_returns["BTCUSDT"] == 0.0
    assert d.tick(98.0, T).side == "sell"
    assert d.trade_count == 2 and d.last_fill["kind"] == "sl" and "BTCUSDT" not in d.entry_returns
    assert d.last_price == 98.0 and d.equity < 1000.0 and d.ret < 0


def test_candle_keys_and_push_candle():
    tick = _driver(_macro("A"))
    assert tick.is_candle_based() is False and tick.candle_keys() == []
    cd = _driver(_macro("F"))
    assert cd.is_candle_based() is True and cd.candle_keys() == [("BTCUSDT", "5m", "spot")]
    for c in [100, 90, 80, 70, 60]:
        cd.push_candle("BTCUSDT", (0, c, c, c, c))
    assert cd.push_candle("BTCUSDT", (0, 60, 60, 60, 60)) == 1
    assert cd.tick(60.0, T).side == "buy"


def test_state_restore_round_trip_and_fill_history():
    d = _driver(_macro("A"))
    d.tick(100.0, T)
    st = d.state()
    assert st["in_position"] is True and st["legs"][0]["symbol"] == "BTCUSDT" and st["trade_count"] == 1
    fresh = _driver(_macro("A"))
    fresh.restore(st, leg_equity={}, total_equity=d.equity)
    fresh.restore_fills([{"symbol": "BTCUSDT", "side": "buy", "return_at_trade": 0.0, "ts": "2026-09-22T00:00:00Z"}])
    assert fresh.state()["in_position"] is True and fresh.trade_count == 1 and fresh.entry_returns == {"BTCUSDT": 0.0}
    assert abs(fresh.sim.equity(100.0) - d.equity) < 1e-6


def test_warmup_feeds_only_candle_legs():
    d = _driver(_macro("F"))
    d.warmup({"BTCUSDT": [(0, 100, 100, 100, 100), (1, 90, 90, 90, 90), (2, 80, 80, 80, 80)]})
    assert d.sim.inner.rsi._count > 0 and d.state()["in_position"] is False
    _driver(_macro("A")).warmup({"BTCUSDT": []})  # 틱형은 무시, 예외 없음
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_driver.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: app.engine.driver`.

- [ ] **Step 3: `driver.py` 작성**

`backend/app/engine/driver.py` — `paper.py`의 `_Leg`(63~95줄), `_tick`(414~434), `_aggregate`(436~442), `sim_state`(472~478), `_note_fill`(480~490), `_state_view`(492~513), `_rebuild_runner`의 sim.restore·체결 재구성 부분(266~320)을 옮긴다:
```python
"""공용 전략 드라이버 — 페이퍼 세션과 실행기 세션이 같은 시뮬레이션 코어를 돈다.

DB·리더보드·주문 명령은 모른다. 레그(종목별 sim)·틱·봉·체결 요약·상태 저장/복구·웜업만 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from .schema import Macro
from .stepper import EXIT_SIDES, Fill


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def sim_state(sim) -> dict:
    """state() 가 없는 시뮬레이터(테스트 더미 등)에는 '포지션 없음' 기본값."""
    getter = getattr(sim, "state", None)
    if getter is None:
        return {"in_position": False, "dir": 1, "qty": 0.0, "entry_price": 0.0, "cooldown_until_ms": None, "halted_today": False}
    return getter()


class Leg:
    """One symbol of a session: its own sim and its share of the capital."""

    __slots__ = ("symbol", "sim", "initial", "last_price", "equity", "ret", "liquidations", "liquidated_loss", "replay_prices")

    def __init__(self, symbol: str, sim, initial: float):
        self.symbol = symbol
        self.sim = sim
        self.initial = initial
        self.last_price = 0.0
        self.equity = initial
        self.ret = 0.0
        self.liquidations = 0
        self.liquidated_loss = 0.0
        self.replay_prices: List[float] = []

    def view(self) -> dict:
        return {
            "symbol": self.symbol,
            "virtual_balance": round(self.initial, 2),
            "current_equity": round(self.equity, 2),
            "current_return": round(self.ret, 4),
            "last_price": round(self.last_price, 4),
            "liquidations": self.liquidations,
        }


class StrategyDriver:
    def __init__(self, legs: List[Leg], initial: float, *, macro: Optional[Macro] = None, now_ms=_now_ms) -> None:
        if not legs:
            raise ValueError("at least one leg")
        self.legs = legs
        self.initial = initial
        self.macro = macro
        self._now_ms = now_ms
        self.last_price = 0.0
        self.equity = initial
        self.ret = 0.0
        self.liquidations = 0
        self.liquidated_loss = 0.0
        # 체결 요약 — 리더보드 행 상태·실행기 로그가 읽는다.
        self.trade_count = 0
        self.last_fill: Optional[dict] = None
        # 종목별 진입 시점 누적 수익률 — 포트폴리오에서 다른 레그와 섞이지 않게 심볼로 나눈다.
        self.entry_returns: Dict[str, float] = {}

    # -- 접근자 -----------------------------------------------------------
    @property
    def symbol(self) -> str:
        return self.legs[0].symbol

    @property
    def sim(self):
        return self.legs[0].sim

    @property
    def symbols(self) -> List[str]:
        return [leg.symbol for leg in self.legs]

    def is_portfolio(self) -> bool:
        return len(self.legs) > 1

    def leg_for(self, symbol: Optional[str]) -> Leg:
        if symbol is None:
            return self.legs[0]
        for leg in self.legs:
            if leg.symbol == symbol:
                return leg
        raise KeyError(symbol)

    # -- 틱 --------------------------------------------------------------
    def tick(self, price: float, ts: Optional[datetime] = None, symbol: Optional[str] = None) -> Optional[Fill]:
        leg = self.leg_for(symbol)
        leg.last_price = price
        fill = leg.sim.step(price, ts)
        leg.equity = leg.sim.equity(price)
        leg.ret = (leg.equity - leg.initial) / leg.initial * 100.0
        leg.liquidations = getattr(leg.sim, "liquidations", 0)
        leg.liquidated_loss = getattr(leg.sim, "liquidated_loss", 0.0)
        self.aggregate()
        if fill is not None:
            self.note_fill(fill, leg.symbol)
        return fill

    def aggregate(self) -> None:
        self.last_price = self.legs[0].last_price
        self.equity = sum(leg.equity for leg in self.legs)
        self.ret = (self.equity - self.initial) / self.initial * 100.0
        self.liquidations = sum(leg.liquidations for leg in self.legs)
        self.liquidated_loss = sum(leg.liquidated_loss for leg in self.legs)

    def note_fill(self, fill: Fill, symbol: str) -> None:
        self.trade_count += 1
        kind = ""
        if fill.side in EXIT_SIDES:
            entry_return = self.entry_returns.pop(symbol, None)
            kind = "exit" if entry_return is None else ("tp" if fill.return_pct > entry_return else "sl")
        else:
            self.entry_returns[symbol] = float(fill.return_pct)
        self.last_fill = {"ms": self._now_ms(), "side": fill.side, "return": round(float(fill.return_pct), 4), "kind": kind, "symbol": symbol}

    # -- 봉 --------------------------------------------------------------
    def is_candle_based(self) -> bool:
        return any(hasattr(leg.sim, "on_candle") for leg in self.legs)

    def candle_keys(self) -> List[tuple]:
        if self.macro is None or not self.is_candle_based():
            return []
        interval, market = self.macro.candle_interval, self.macro.resolved_market()
        return [(leg.symbol, interval, market) for leg in self.legs if hasattr(leg.sim, "on_candle")]

    def push_candle(self, symbol: str, candle) -> int:
        leg = self.leg_for(symbol)
        on_candle = getattr(leg.sim, "on_candle", None)
        if on_candle is None:
            return 0
        t_ms, o, h, l, c = candle
        ts = datetime.fromtimestamp(int(t_ms) / 1000, timezone.utc) if t_ms else datetime.now(timezone.utc)
        return int(on_candle(float(o), float(h), float(l), float(c), ts))

    def warmup(self, candles_by_symbol: Dict[str, list]) -> None:
        for leg in self.legs:
            warm = getattr(leg.sim, "warmup", None)
            if warm is not None and candles_by_symbol.get(leg.symbol):
                warm(candles_by_symbol[leg.symbol])

    # -- 상태 --------------------------------------------------------------
    def state(self) -> dict:
        legs, cooldowns = [], []
        for leg in self.legs:
            st = sim_state(leg.sim)
            legs.append({"symbol": leg.symbol, "qty": round(st["qty"], 8), "dir": st["dir"],
                         "entry_price": round(st["entry_price"], 4), "last_price": round(leg.last_price, 4),
                         "in_position": bool(st["in_position"])})
            if st["cooldown_until_ms"] is not None:
                cooldowns.append(int(st["cooldown_until_ms"]))
        last = self.last_fill or {}
        return {
            "in_position": any(l["in_position"] for l in legs),
            "halted_today": any(sim_state(leg.sim)["halted_today"] for leg in self.legs),
            "cooldown_until_ms": max(cooldowns) if cooldowns else None,
            "trade_count": self.trade_count,
            "last_fill_ms": last.get("ms"), "last_fill_side": last.get("side", ""),
            "last_fill_return": last.get("return"), "last_fill_kind": last.get("kind", ""),
            "last_price": round(self.last_price, 4), "checkpoint_ms": self._now_ms(), "legs": legs,
        }

    def restore(self, state: dict, *, leg_equity: Dict[str, float], total_equity: float) -> None:
        """체크포인트 상태로 각 레그의 sim 을 되살린다. 캔들형은 warmup() 뒤에 불러야 한다(웜업이 장부를 비운다)."""
        leg_state = {leg.get("symbol"): leg for leg in (state.get("legs") or [])}
        for leg in self.legs:
            equity = leg_equity.get(leg.symbol) if self.is_portfolio() else total_equity
            if not equity or equity <= 0:
                equity = leg.initial
            st = leg_state.get(leg.symbol) or {}
            restore = getattr(leg.sim, "restore", None)
            if restore is not None:
                restore(equity, in_position=bool(st.get("in_position")), qty=float(st.get("qty") or 0.0),
                        entry_price=float(st.get("entry_price") or 0.0), last_price=float(st.get("last_price") or 0.0),
                        cooldown_until_ms=state.get("cooldown_until_ms"))
            leg.equity = float(equity)
            leg.ret = (leg.equity - leg.initial) / leg.initial * 100.0
            leg.last_price = float(st.get("last_price") or 0.0)
        self.aggregate()

    def restore_fills(self, trades: List[dict]) -> None:
        """체결 요약을 DB 체결 행으로 다시 센다 — 메모리 카운터는 프로세스와 함께 사라졌다."""
        self.trade_count = len(trades)
        entry_returns: Dict[str, float] = {}
        last_kind = ""
        for t in trades:
            sym = t.get("symbol") or self.symbol
            if t["side"] in EXIT_SIDES:
                base = entry_returns.pop(sym, None)
                last_kind = "exit" if base is None else ("tp" if t["return_at_trade"] > base else "sl")
            else:
                entry_returns[sym] = t["return_at_trade"]
                last_kind = ""
        self.entry_returns = entry_returns
        self.last_fill = None
        if trades:
            last = trades[-1]
            self.last_fill = {"ms": _iso_ms(last.get("ts", "")), "side": last["side"],
                              "return": round(last["return_at_trade"], 4), "kind": last_kind,
                              "symbol": last.get("symbol") or self.symbol}


def _iso_ms(ts: str) -> int:
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0
```

- [ ] **Step 4: `paper.py` 연결**

1. import: `from .engine.driver import Leg, StrategyDriver, sim_state  # noqa: F401 (sim_state 재export)`; `_Leg = Leg`. 63~95줄의 `_Leg` 클래스 정의 삭제.
2. `_Runner.__init__`: 기존 필드 중 `legs/sim/symbol/last_price/equity/ret/liquidations/liquidated_loss/trade_count/last_fill/entry_returns` 대입을 지우고
   ```python
        self.driver = StrategyDriver(legs if legs else [Leg(symbol, sim, initial)], initial)
   ```
   나머지(`session_id, mode, initial, stop_flag, task, status, recent, last_checkpoint_monotonic, finalize_lock, finalized, inflight_persist`)는 그대로. 그리고 위임 프로퍼티:
   ```python
    legs = property(lambda self: self.driver.legs)
    sim = property(lambda self: self.driver.sim)
    symbol = property(lambda self: self.driver.symbol)
    symbols = property(lambda self: self.driver.symbols)
    last_price = property(lambda self: self.driver.last_price)
    equity = property(lambda self: self.driver.equity)
    ret = property(lambda self: self.driver.ret)
    liquidations = property(lambda self: self.driver.liquidations)
    liquidated_loss = property(lambda self: self.driver.liquidated_loss)
    trade_count = property(lambda self: self.driver.trade_count, lambda self, v: setattr(self.driver, "trade_count", v))
    last_fill = property(lambda self: self.driver.last_fill, lambda self, v: setattr(self.driver, "last_fill", v))
    entry_returns = property(lambda self: self.driver.entry_returns, lambda self, v: setattr(self.driver, "entry_returns", v))
    def is_portfolio(self): return self.driver.is_portfolio()
    def leg_for(self, symbol): return self.driver.leg_for(symbol)
   ```
   (기존 `symbols`/`is_portfolio`/`leg_for`/`replay_prices` 정의는 이 위임으로 교체·유지.)
3. 모듈 함수들을 래퍼로:
   ```python
   def _tick(runner, price, ts=None, symbol=None):
       return runner.driver.tick(price, ts, symbol)

   def _aggregate(runner):
       runner.driver.aggregate()

   def _note_fill(runner, fill, symbol):
       runner.driver.note_fill(fill, symbol)

   def _state_view(runner):
       return runner.driver.state()
   ```
   `_tick_and_checkpoint`에서 `_tick(...)` 뒤의 `_note_fill(runner, fill, leg.symbol)` 호출은 **삭제**(드라이버 `tick`이 이미 센다 — 남기면 두 번 센다).
4. `_rebuild_runner`: 레그 생성 후
   ```python
    runner = _Runner(info["id"], legs[0].sim, symbols[0], "live", initial, legs=legs)
    runner.driver.macro = macro
    runner.driver.restore(info["state"], leg_equity=leg_equity, total_equity=float(info["current_equity"] or 0.0))
    runner.driver.restore_fills(info["trades"])
    return runner
   ```
   로 바꾸고, 그 안의 수동 `restore(...)` 호출·체결 재구성 루프·`_ts_ms` 사용을 지운다(`_ts_ms`는 다른 곳에서 안 쓰면 삭제). `start_session`에서 `runner = _Runner(...)` 직후 `runner.driver.macro = macro`를 넣는다(Task 5의 피드 구독이 쓴다).

- [ ] **Step 5: 통과 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_driver.py tests/test_leaderboard_state.py tests/test_paper_portfolio.py tests/test_paper_reuse.py tests/test_paper.py tests/test_leaderboard.py -q -p no:warnings`
Expected: PASS. (`tests/test_paper.py`, `tests/test_leaderboard.py`가 없으면 `ls backend/tests | grep -i paper`로 실제 파일명을 넣는다.)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/engine/driver.py backend/app/paper.py backend/tests/test_driver.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "페이퍼 시뮬레이션 코어를 StrategyDriver 로 추출 — 실행기 세션과 공유할 준비

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: 페이퍼가 실봉을 구독한다

**Files:**
- Modify: `backend/app/paper.py` (`start_session`, `_rebuild_runner`/`resume_running_sessions`, `_finalize_async`, `_Runner.subs`)
- Modify: `DEPLOY.md` (리더보드 실시간 상태 절)
- Test: `backend/tests/test_paper_candles.py` (신규)

**Interfaces:**
- Consumes: `engine.candle_feed.feed`(`history`, `subscribe`, `unsubscribe`), `StrategyDriver.candle_keys/warmup/push_candle`.
- Produces: `paper.WARMUP_CANDLES = 500`; `async def _attach_feed(runner) -> None`(웜업 + 구독, `runner.subs`에 저장); `_detach_feed(runner)`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_paper_candles.py`:
```python
"""페이퍼 캔들형 세션은 실봉을 구독하고 시작 전에 웜업한다."""
import asyncio
from types import SimpleNamespace

from app import paper
from app.engine import Macro


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _rsi():
    return Macro.model_validate({
        "symbol": "BTCUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "5m", "period": {"preset": "3m"},
        "params": {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 1000},
        "risk": {"stop_loss_pct": 0, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    })


class _FakeFeed:
    def __init__(self):
        self.history_calls, self.subs = [], []

    async def history(self, symbol, interval, market, n):
        self.history_calls.append((symbol, interval, market, n))
        return [(i, 100 - i, 100 - i, 100 - i, 100 - i) for i in range(5)]

    def subscribe(self, symbol, interval, market, callback):
        sub = SimpleNamespace(key=(symbol, interval, market), callback=callback)
        self.subs.append(sub)
        return sub

    def unsubscribe(self, sub):
        self.subs.remove(sub)


def test_start_session_warms_up_and_subscribes(monkeypatch):
    fake = _FakeFeed()
    monkeypatch.setattr(paper, "feed", fake)
    monkeypatch.setattr(paper, "ensure_spot_available", lambda s: None)
    monkeypatch.setattr(paper, "_create_session", lambda *a: 91)

    async def no_loop(runner):
        return None

    monkeypatch.setattr(paper, "_run_loop", no_loop)
    try:
        _run(paper.start_session(_rsi(), None, "live"))
        runner = paper._running[91]
        assert fake.history_calls == [("BTCUSDT", "5m", "spot", paper.WARMUP_CANDLES)]
        assert [s.key for s in fake.subs] == [("BTCUSDT", "5m", "spot")]
        assert runner.sim.inner.rsi._count > 0 and runner.sim.state()["in_position"] is False
        # 피드 콜백이 봉을 밀어 넣으면 다음 틱에서 체결이 나온다.
        _run(fake.subs[0].callback("BTCUSDT", (0, 60, 60, 60, 60)))
        _run(fake.subs[0].callback("BTCUSDT", (1, 60, 60, 60, 60)))
        assert runner.sim.pending() >= 1
        _run(paper._finalize_async(runner))
        assert fake.subs == []  # 종료하면 구독 해제
    finally:
        paper._running.pop(91, None)


def test_tick_type_session_does_not_touch_feed(monkeypatch):
    fake = _FakeFeed()
    monkeypatch.setattr(paper, "feed", fake)
    monkeypatch.setattr(paper, "ensure_spot_available", lambda s: None)
    monkeypatch.setattr(paper, "_create_session", lambda *a: 92)

    async def no_loop(runner):
        return None

    monkeypatch.setattr(paper, "_run_loop", no_loop)
    macro = Macro(symbol="BTCUSDT", rule_type="A", params={"take_profit_pct": 1.0, "initial_capital": 1_000.0})
    try:
        _run(paper.start_session(macro, None, "live"))
        assert fake.history_calls == [] and fake.subs == []
    finally:
        paper._running.pop(92, None)
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_paper_candles.py -q -p no:warnings`
Expected: FAIL — `AttributeError: module 'app.paper' has no attribute 'feed'`.

- [ ] **Step 3: 구현**

`backend/app/paper.py`:
```python
from .engine.candle_feed import feed  # 모듈 이름으로 참조 — 테스트가 monkeypatch 한다
WARMUP_CANDLES = 500  # MA slow_period ≤ 400 을 덮는다


async def _attach_feed(runner: _Runner) -> None:
    """캔들형 세션: 과거 마감봉으로 웜업하고 새 마감봉을 구독한다. 틱형은 아무것도 하지 않는다."""
    keys = runner.driver.candle_keys()
    if not keys:
        return
    history: Dict[str, list] = {}
    for symbol, interval, market in keys:
        try:
            history[symbol] = await feed.history(symbol, interval, market, WARMUP_CANDLES)
        except Exception:
            log.exception("paper %s: warmup history failed for %s — starting cold", runner.session_id, symbol)
    runner.driver.warmup(history)

    async def on_candle(symbol: str, candle) -> None:
        if runner.stop_flag:
            return
        runner.driver.push_candle(symbol, candle)

    runner.subs = [feed.subscribe(symbol, interval, market, on_candle) for symbol, interval, market in keys]


def _detach_feed(runner: _Runner) -> None:
    for sub in getattr(runner, "subs", []):
        try:
            feed.unsubscribe(sub)
        except Exception:
            log.exception("paper %s: unsubscribe failed", runner.session_id)
    runner.subs = []
```
- `_Runner.__init__`에 `self.subs: list = []`.
- `start_session`: `runner = _Runner(...)`, `runner.driver.macro = macro` 다음, `_running[session_id] = runner` 전에 `if mode == "live": await _attach_feed(runner)`.
- `resume_running_sessions`: `runner = _rebuild_runner(info)` 성공 후 `_running[...] = runner` 전에 `await _attach_feed_for_resume(runner, info)`:
  ```python
  async def _attach_feed_for_resume(runner: _Runner, info: dict) -> None:
      """복구는 웜업 → 복구 순서다: 웜업이 장부를 비우므로 체크포인트 상태를 그 뒤에 얹는다."""
      await _attach_feed(runner)
      if runner.driver.candle_keys():
          runner.driver.restore(info["state"], leg_equity={leg.get("symbol"): float(leg.get("current_equity") or 0.0) for leg in info["legs"]},
                                total_equity=float(info["current_equity"] or 0.0))
  ```
  (`_rebuild_runner`는 그대로 restore를 한 번 하고, 캔들형은 웜업 뒤 한 번 더 얹는다 — 틱형은 웜업이 없으니 두 번째 호출을 건너뛴다.)
- `_finalize_async`의 시작(락 획득 직후)에 `_detach_feed(runner)`.
- `shutdown_running_sessions`에서도 각 러너에 `_detach_feed`.

`DEPLOY.md` "리더보드 실시간 상태" 절에 추가:
```
- 캔들형(D~K) 페이퍼 세션은 바이낸스 **실제 마감봉**(`candle_interval`)을 받아 봉 마감마다 판단한다(이전 3틱 합성봉 폐기, `PAPER_CANDLE_TICKS` 제거).
  시작·복구 때 최근 500봉으로 지표를 웜업한다. 피드는 종목·간격·시장별 1구독이라 호출량은 봉당 1회.
  `CANDLE_GRACE_SECONDS`(기본 2) · `CANDLE_RETRY_SECONDS`(기본 30).
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_paper_candles.py tests/test_paper_portfolio.py tests/test_leaderboard_state.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/paper.py backend/tests/test_paper_candles.py DEPLOY.md
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "페이퍼 캔들형 세션이 실봉을 구독 — 시작·복구 시 500봉 웜업, 종료 시 해제

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: DB — `RunSession.state_json`, `RunnerCommand`

**Files:**
- Modify: `backend/app/db.py` (`RunSession` 필드, 새 모델, `_migrate` sqlite, `_PG_ADDED_COLUMNS["runsession"]`)
- Create: `supabase/migrations/20260922120000_runner_commands.sql`
- Test: `backend/tests/test_runner_commands_schema.py` (신규)

**Interfaces:**
- Produces:
  ```python
  class RunSession: ... state_json: str = ""   # 드라이버 상태(JSON) — 체크포인트마다 갱신
  class RunnerCommand(SQLModel, table=True):
      id: Optional[int] (pk); session_id: int (index); seq: int = 0
      action: str = ""            # buy | sell | short | cover
      notional_frac: float = 0.0  # 진입: 초기자본 대비 주문 금액 비율
      qty_frac: float = 0.0       # 청산: 보유 수량 대비 비율(1.0 = 전량)
      signal_price: float = 0.0
      reason: str = ""
      status: str = "pending" (index)   # pending | acked | failed | expired
      attempts: int = 0
      created_at: str = ""; created_ms: int (BigInteger) = 0; expires_ms: int (BigInteger) = 0
      acked_at: str = ""; executed_qty: float = 0.0; fill_price: float = 0.0; error: str = ""
  ```

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_runner_commands_schema.py`:
```python
"""실행기 명령 테이블과 runsession.state_json 이 sqlite 개발 DB 에 생긴다."""
from sqlmodel import select

from app.db import RunnerCommand, RunSession, get_session


def test_runner_command_round_trip():
    with get_session() as db:
        row = RunnerCommand(session_id=1, seq=1, action="buy", notional_frac=0.5, signal_price=100.0,
                            reason="RSI 23.1 ≤ 25 · 진입", created_at="2026-09-22T00:00:00Z", created_ms=1, expires_ms=2)
        db.add(row)
        db.commit()
        got = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == 1)).first()
        assert got.status == "pending" and got.attempts == 0 and got.qty_frac == 0.0
        db.delete(got)
        db.commit()


def test_runsession_has_state_json_default_empty():
    assert RunSession.model_fields["state_json"].default == ""
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_runner_commands_schema.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'RunnerCommand'`.

- [ ] **Step 3: 구현**

`backend/app/db.py` `RunSession` 끝(`final_unrealized_pct` 아래):
```python
    # 서버 신호(v8+) 세션의 드라이버 상태(JSON, paper.state_json 과 같은 형식). 체크포인트마다 갱신,
    # 재기동 복구에 쓴다. 구버전 실행기 세션은 빈 문자열.
    state_json: str = ""
```
`RunSessionEvent` 아래에:
```python
class RunnerCommand(SQLModel, table=True):
    """서버가 실행기에 내리는 주문 명령 한 줄(v8+).

    엔진 Fill 을 비율로 바꿔 둔다 — 진입은 초기자본 대비 금액 비율, 청산은 보유 수량 대비 비율.
    실행기는 heartbeat 응답으로 pending 을 받아 실행하고 다음 heartbeat 의 acks 로 결과를 보고한다.
    TTL 이 지난 pending 은 expired — 늦은 진입 신호를 뒤늦게 실행하지 않는다.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    seq: int = 0
    action: str = ""  # buy | sell | short | cover
    notional_frac: float = 0.0
    qty_frac: float = 0.0
    signal_price: float = 0.0
    reason: str = ""
    status: str = Field(default="pending", index=True)  # pending | acked | failed | expired
    attempts: int = 0
    created_at: str = ""
    created_ms: int = Field(default=0, sa_type=BigInteger)
    expires_ms: int = Field(default=0, sa_type=BigInteger)
    acked_at: str = ""
    executed_qty: float = 0.0
    fill_price: float = 0.0
    error: str = ""
```
`_migrate()`의 `"runsession"` 사전에 `"state_json": "ALTER TABLE runsession ADD COLUMN state_json TEXT DEFAULT ''",` 추가. `_PG_ADDED_COLUMNS["runsession"]`에 `"state_json": "TEXT DEFAULT ''",` 추가. (새 테이블은 `create_all`이 만든다 — `_pg_missing_tables`가 잡는다.)

`supabase/migrations/20260922120000_runner_commands.sql`:
```sql
-- 서버 신호 실행기 (2026-09-22)
-- ① runsession.state_json — 드라이버 상태(JSON). ② runnercommand — 서버가 실행기에 내리는 주문 명령.
-- 서버 전용 테이블: RLS 켜고 anon/authenticated 권한 회수(다른 서버 전용 테이블과 같은 정책).

alter table public.runsession add column if not exists state_json text not null default '';

create table if not exists public.runnercommand (
  id bigserial primary key,
  session_id integer not null,
  seq integer not null default 0,
  action varchar not null default '',
  notional_frac double precision not null default 0,
  qty_frac double precision not null default 0,
  signal_price double precision not null default 0,
  reason varchar not null default '',
  status varchar not null default 'pending',
  attempts integer not null default 0,
  created_at varchar not null default '',
  created_ms bigint not null default 0,
  expires_ms bigint not null default 0,
  acked_at varchar not null default '',
  executed_qty double precision not null default 0,
  fill_price double precision not null default 0,
  error varchar not null default ''
);
create index if not exists ix_runnercommand_session_id on public.runnercommand (session_id);
create index if not exists ix_runnercommand_status on public.runnercommand (status);

alter table public.runnercommand enable row level security;
revoke all on table public.runnercommand from anon, authenticated;
```
(`20260921120000_chat_rooms.sql` 끝부분의 RLS/권한 문장을 열어 같은 형식인지 맞춘다.)

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_runner_commands_schema.py tests/test_db_startup_migrations.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/db.py supabase/migrations/20260922120000_runner_commands.sql backend/tests/test_runner_commands_schema.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "DB: runsession.state_json + runnercommand — 서버 신호 실행기 명령 큐

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `runner_engine.py` — 실행기 세션 드라이버·명령

**Files:**
- Create: `backend/app/runner_engine.py`
- Test: `backend/tests/test_runner_engine.py` (신규)

**Interfaces:**
- Consumes: `engine.driver.StrategyDriver/Leg`, `engine.stepper.make_sim/EXIT_SIDES`, `engine.candle_feed.feed`, `paper.parse_state`, `data.get_ticker_price_cached`, `db.RunSession/RunnerCommand/get_session`.
- Produces:
  ```python
  COMMAND_TTL_SECONDS = float(os.environ.get("COMMAND_TTL_SECONDS", "90")); EXIT_RETRY_MAX = 3; WARMUP_CANDLES = 500
  POLL_SECONDS (paper 와 같은 env PAPER_POLL_SECONDS, 기본 3); CHECKPOINT_SECONDS (PAPER_CHECKPOINT_SECONDS, 기본 10)
  def install(loop: asyncio.AbstractEventLoop) -> None          # lifespan 이 부른다 — 이후 schedule_* 가 동작
  def schedule_start(session_id: int) -> None                    # 스레드 안전, 루프 없으면 로그만
  def schedule_stop(session_id: int) -> None
  async def start_driver(session_id: int) -> bool                # DB 에서 macro_json 읽어 드라이버·웜업·구독·루프
  async def stop_driver(session_id: int) -> None
  async def resume_running_runner_sessions() -> int
  def build_driver(macro: Macro, symbol: str) -> StrategyDriver  # initial = macro.initial_capital or 100.0
  def command_from_fill(fill: Fill, initial: float) -> dict      # {"action","notional_frac","qty_frac","signal_price","reason"}
  def insert_command(db, row: RunSession, cmd: dict, now_ms: int) -> RunnerCommand
  def pending_commands(db, row: RunSession, now_ms: int) -> list[dict]   # 만료 처리 후 pending 을 seq 순으로
  def apply_acks(db, row: RunSession, acks: list, now_ms: int) -> None
  def check_position_mismatch(db, row: RunSession, reported_in_position: bool) -> None
  ```
  응답용 명령 dict: `{"id", "seq", "action", "notional_frac", "qty_frac", "signal_price", "reason", "expires_ms"}`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_runner_engine.py`:
```python
"""실행기 세션 드라이버 — Fill→명령, 만료, ack, 불일치, 복구."""
import asyncio
import json
from datetime import datetime, timezone

from sqlmodel import select

from app import runner_engine as eng
from app.db import RunnerCommand, RunSession, RunSessionEvent, get_session
from app.engine import Macro
from app.engine.stepper import Fill


def _rsi_json():
    return Macro.model_validate({
        "symbol": "ONEUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "5m", "period": {"preset": "1w"},
        "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 32},
        "risk": {"invest_ratio": 1.0, "cooldown_minutes": 0}, "fees": {"commission_pct": 0.1, "slippage_pct": 0.05},
    }).model_dump_json()


def _session(**over):
    with get_session() as db:
        row = RunSession(user_id=1, symbol="ONEUSDT", position_side="long", leverage=1, market="spot",
                         status="running", started_at="2026-09-22T00:00:00Z", runner_version="8", macro_json=_rsi_json(), **over)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _cleanup(session_id):
    with get_session() as db:
        for t in (RunnerCommand, RunSessionEvent):
            for r in db.exec(select(t).where(t.session_id == session_id)).all():
                db.delete(r)
        row = db.get(RunSession, session_id)
        if row:
            db.delete(row)
        db.commit()


def test_command_from_fill_entry_is_notional_fraction_of_initial():
    cmd = eng.command_from_fill(Fill("buy", 0.0125, 1280.0, 32.0, 0.0, reason="RSI 23.1 ≤ 25 · 진입", qty_before=0.0), 32.0)
    assert cmd["action"] == "buy" and abs(cmd["notional_frac"] - 0.5) < 1e-9 and cmd["qty_frac"] == 0.0
    assert cmd["signal_price"] == 0.0125 and cmd["reason"] == "RSI 23.1 ≤ 25 · 진입"


def test_command_from_fill_exit_is_qty_fraction_of_position():
    full = eng.command_from_fill(Fill("sell", 0.013, 1280.0, 33.0, 3.1, reason="청산", qty_before=1280.0), 32.0)
    assert full["qty_frac"] == 1.0 and full["notional_frac"] == 0.0
    part = eng.command_from_fill(Fill("sell", 0.013, 640.0, 33.0, 3.1, reason="부분 청산", qty_before=1280.0), 32.0)
    assert abs(part["qty_frac"] - 0.5) < 1e-9
    orphan = eng.command_from_fill(Fill("cover", 1.0, 5.0, 1.0, 0.0, qty_before=0.0), 32.0)
    assert orphan["qty_frac"] == 1.0  # 직전 수량을 모르면 전량


def test_pending_commands_expire_and_order_by_seq():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.0, "reason": "a"}, now_ms=1_000)
            eng.insert_command(db, row, {"action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 1.1, "reason": "b"}, now_ms=200_000)
            db.commit()
            out = eng.pending_commands(db, row, now_ms=200_001)
            db.commit()
            assert [c["seq"] for c in out] == [2] and out[0]["action"] == "sell" and out[0]["expires_ms"] == 200_000 + 90_000
            first = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid, RunnerCommand.seq == 1)).one()
            assert first.status == "expired"
    finally:
        _cleanup(sid)


def test_ack_ok_marks_acked_and_logs_order_event():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            cmd = eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.0, "reason": "RSI 20 ≤ 25 · 진입"}, now_ms=1_000)
            db.commit()
            eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": True, "executed_qty": 30.0, "fill_price": 1.01}], now_ms=5_000)
            db.commit()
            db.refresh(cmd)
            assert cmd.status == "acked" and cmd.executed_qty == 30.0 and cmd.fill_price == 1.01 and cmd.acked_at
            kinds = [e.kind for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert "order" in kinds
    finally:
        _cleanup(sid)


def test_failed_exit_is_retried_three_times_then_failed_and_noted():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            cmd = eng.insert_command(db, row, {"action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 1.0, "reason": "청산"}, now_ms=1_000)
            db.commit()
            for i in range(1, 4):
                eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": False, "error": "insufficient"}], now_ms=1_000 + i)
                db.commit()
                db.refresh(cmd)
                if i < 3:
                    assert cmd.status == "pending" and cmd.attempts == i and cmd.expires_ms == 1_000 + i + 90_000
            assert cmd.status == "failed" and cmd.attempts == 3
            assert row.note == "청산 실패 — 확인 필요"
    finally:
        _cleanup(sid)


def test_failed_entry_is_final():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            cmd = eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.0, "reason": "진입"}, now_ms=1_000)
            db.commit()
            eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": False, "error": "min notional"}], now_ms=2_000)
            db.commit()
            db.refresh(cmd)
            assert cmd.status == "failed" and cmd.attempts == 1
            msgs = [e.message for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert any("진입 실패" in m for m in msgs)
    finally:
        _cleanup(sid)


def test_position_mismatch_warns_once():
    sid = _session(state_json=json.dumps({"in_position": True, "legs": []}))
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            eng.check_position_mismatch(db, row, reported_in_position=False)
            eng.check_position_mismatch(db, row, reported_in_position=False)
            db.commit()
            warns = [e for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all() if e.kind == "warn"]
            assert len(warns) == 1
            eng.check_position_mismatch(db, row, reported_in_position=True)  # 일치하면 다음 불일치에 다시 1회
            eng.check_position_mismatch(db, row, reported_in_position=False)
            db.commit()
            warns = [e for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all() if e.kind == "warn"]
            assert len(warns) == 2
    finally:
        _cleanup(sid)


def test_start_driver_warms_up_subscribes_and_writes_commands_on_fill(monkeypatch):
    sid = _session()
    events = []

    class FakeFeed:
        subs = []

        async def history(self, symbol, interval, market, n):
            return [(i, 100 - i, 100 - i, 100 - i, 100 - i) for i in range(20)]

        def subscribe(self, symbol, interval, market, cb):
            self.subs.append(cb)
            return ("sub", symbol)

        def unsubscribe(self, sub):
            self.subs.clear()

    fake = FakeFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s: 50.0)

    async def scenario():
        assert await eng.start_driver(sid) is True
        live = eng._drivers[sid]
        assert fake.subs and live.driver.state()["in_position"] is False
        for c in (50, 50):  # 웜업 끝 RSI(7) 낮음 → enter → 다음 봉 시가 체결
            await fake.subs[0]("ONEUSDT", (0, c, c, c, c))
        await eng._tick_once(live)  # 큐 드레인 → 명령 기록
        with get_session() as db:
            cmds = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all()
            assert [c.action for c in cmds] == ["buy"] and cmds[0].reason.startswith("RSI ")
            row = db.get(RunSession, sid)
            assert json.loads(row.state_json)["in_position"] is True
        await eng.stop_driver(sid)
        assert sid not in eng._drivers and fake.subs == []

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(sid)


def test_resume_only_v8_running_sessions(monkeypatch):
    old = _session(runner_version="7")
    new = _session(runner_version="8", status="stopped")
    started = []

    async def fake_start(session_id):
        started.append(session_id)
        return True

    monkeypatch.setattr(eng, "start_driver", fake_start)
    try:
        n = asyncio.new_event_loop().run_until_complete(eng.resume_running_runner_sessions())
        assert n == 0 and started == []
    finally:
        _cleanup(old)
        _cleanup(new)
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_runner_engine.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: app.runner_engine`.

- [ ] **Step 3: 구현**

`backend/app/runner_engine.py`:
```python
"""실행기(실계좌) 세션의 서버 측 전략 드라이버.

실행기 v8 은 스스로 진입/청산을 판단하지 않는다. 세션마다 여기서 StrategyDriver 를 돌려(페이퍼와 같은
엔진·같은 마감봉) Fill 을 RunnerCommand 로 바꿔 두면, 실행기가 heartbeat 응답으로 받아 주문만 넣고
다음 heartbeat 의 acks 로 결과를 보고한다.

수명: runner.start_session → schedule_start → start_driver(웜업·구독·틱 루프) … mark_stopped → schedule_stop.
재기동 시 resume_running_runner_sessions 가 state_json 으로 이어 간다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlmodel import select

from .data import get_ticker_price_cached
from .db import RunnerCommand, RunSession, get_session
from .engine import Macro
from .engine.candle_feed import feed
from .engine.driver import Leg, StrategyDriver
from .engine.stepper import EXIT_SIDES, Fill, make_sim

log = logging.getLogger(__name__)

POLL_SECONDS = float(os.environ.get("PAPER_POLL_SECONDS", "3"))
CHECKPOINT_SECONDS = max(1.0, float(os.environ.get("PAPER_CHECKPOINT_SECONDS", "10")))
COMMAND_TTL_SECONDS = float(os.environ.get("COMMAND_TTL_SECONDS", "90"))
EXIT_RETRY_MAX = 3
WARMUP_CANDLES = 500
FALLBACK_CAPITAL = 100.0  # 실행기 MAX_ORDER_USDT 기본과 같다
EXIT_FAIL_NOTE = "청산 실패 — 확인 필요"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ms() -> int:
    return int(time.time() * 1000)


class _Live:
    __slots__ = ("session_id", "driver", "task", "subs", "stop_flag", "last_checkpoint")

    def __init__(self, session_id: int, driver: StrategyDriver) -> None:
        self.session_id = session_id
        self.driver = driver
        self.task: Optional[asyncio.Task] = None
        self.subs: list = []
        self.stop_flag = False
        self.last_checkpoint = 0.0


_drivers: Dict[int, _Live] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None


# --- 스레드 → 루프 -------------------------------------------------------
def install(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _schedule(coro_factory) -> None:
    if _loop is None or _loop.is_closed():
        log.warning("runner engine: no event loop installed; skipping %s", getattr(coro_factory, "__name__", "task"))
        return
    _loop.call_soon_threadsafe(lambda: _loop.create_task(coro_factory()))


def schedule_start(session_id: int) -> None:
    _schedule(lambda: start_driver(session_id))


def schedule_stop(session_id: int) -> None:
    _schedule(lambda: stop_driver(session_id))


# --- 드라이버 ------------------------------------------------------------
def build_driver(macro: Macro, symbol: str) -> StrategyDriver:
    initial = float(macro.initial_capital or FALLBACK_CAPITAL)
    sym = (symbol or macro.symbol).upper()
    return StrategyDriver([Leg(sym, make_sim(macro, initial_capital=initial), initial)], initial, macro=macro)


def _load_session(session_id: int) -> Optional[dict]:
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.status != "running" or not row.macro_json:
            return None
        return {"symbol": row.symbol, "macro_json": row.macro_json, "state_json": row.state_json}


async def start_driver(session_id: int) -> bool:
    if session_id in _drivers:
        return True
    info = await asyncio.to_thread(_load_session, session_id)
    if info is None:
        return False
    try:
        macro = Macro.model_validate_json(info["macro_json"])
        driver = build_driver(macro, info["symbol"])
    except Exception:
        log.exception("runner engine: session %s macro invalid", session_id)
        return False
    live = _Live(session_id, driver)
    keys = driver.candle_keys()
    history: Dict[str, list] = {}
    for symbol, interval, market in keys:
        try:
            history[symbol] = await feed.history(symbol, interval, market, WARMUP_CANDLES)
        except Exception:
            log.exception("runner engine: warmup failed for %s — starting cold", symbol)
    driver.warmup(history)
    from .paper import parse_state  # 늦은 import: paper ↔ runner_engine 순환 방지
    state = parse_state(info["state_json"])
    if state:
        driver.restore(state, leg_equity={}, total_equity=driver.initial)  # 자산은 초기자본 기준(실계좌 손익은 실행기가 안다)
        driver.trade_count = int(state.get("trade_count") or 0)

    async def on_candle(symbol: str, candle) -> None:
        if not live.stop_flag:
            driver.push_candle(symbol, candle)

    live.subs = [feed.subscribe(symbol, interval, market, on_candle) for symbol, interval, market in keys]
    _drivers[session_id] = live
    live.task = asyncio.get_running_loop().create_task(_run(live))
    return True


async def stop_driver(session_id: int) -> None:
    live = _drivers.pop(session_id, None)
    if live is None:
        return
    live.stop_flag = True
    for sub in live.subs:
        try:
            feed.unsubscribe(sub)
        except Exception:
            log.exception("runner engine: unsubscribe failed")
    live.subs = []
    if live.task is not None and live.task is not asyncio.current_task():
        live.task.cancel()


async def _run(live: _Live) -> None:
    try:
        while not live.stop_flag:
            await _tick_once(live)
            await asyncio.sleep(POLL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("runner engine: session %s loop died", live.session_id)
        _drivers.pop(live.session_id, None)


async def _tick_once(live: _Live) -> None:
    """시세 한 번 → 틱 → 체결이면 명령 기록 → 주기적 체크포인트."""
    try:
        price = await asyncio.to_thread(get_ticker_price_cached, live.driver.symbol)
    except Exception:
        price = None
    if not price:
        return
    fill = live.driver.tick(float(price), datetime.now(timezone.utc))
    if fill is not None:
        await asyncio.to_thread(_persist_fill, live, fill)
        live.last_checkpoint = time.monotonic()
    elif time.monotonic() - live.last_checkpoint >= CHECKPOINT_SECONDS:
        await asyncio.to_thread(_persist_state, live)
        live.last_checkpoint = time.monotonic()


def _persist_state(live: _Live) -> None:
    with get_session() as db:
        row = db.get(RunSession, live.session_id)
        if row is None or row.status != "running":
            live.stop_flag = True
            return
        row.state_json = json.dumps(live.driver.state())
        db.add(row)
        db.commit()


def _persist_fill(live: _Live, fill: Fill) -> None:
    from .runner import _append_events
    with get_session() as db:
        row = db.get(RunSession, live.session_id)
        if row is None or row.status != "running":
            live.stop_flag = True
            return
        cmd = insert_command(db, row, command_from_fill(fill, live.driver.initial), _now_ms())
        row.state_json = json.dumps(live.driver.state())
        db.add(row)
        _append_events(db, row, [{"ts": _now_iso(), "kind": "signal",
                                  "message": f"[신호 #{cmd.seq}] {cmd.reason} → {cmd.action.upper()} @ {fill.price:g}"}])
        db.commit()


# --- Fill → 명령 -----------------------------------------------------------
def command_from_fill(fill: Fill, initial: float) -> dict:
    if fill.side in EXIT_SIDES:
        frac = 1.0 if fill.qty_before <= 0 else min(1.0, fill.qty / fill.qty_before)
        if frac >= 0.999:
            frac = 1.0
        return {"action": fill.side, "notional_frac": 0.0, "qty_frac": float(frac),
                "signal_price": float(fill.price), "reason": fill.reason or "청산"}
    notional = fill.qty * fill.price
    return {"action": fill.side, "notional_frac": float(notional / initial) if initial > 0 else 0.0, "qty_frac": 0.0,
            "signal_price": float(fill.price), "reason": fill.reason or "진입"}


def insert_command(db, row: RunSession, cmd: dict, now_ms: int) -> RunnerCommand:
    last = db.exec(select(RunnerCommand.seq).where(RunnerCommand.session_id == row.id).order_by(RunnerCommand.seq.desc())).first()
    item = RunnerCommand(session_id=row.id, seq=int(last or 0) + 1, action=cmd["action"],
                         notional_frac=cmd["notional_frac"], qty_frac=cmd["qty_frac"], signal_price=cmd["signal_price"],
                         reason=str(cmd["reason"])[:200], status="pending", created_at=_now_iso(), created_ms=now_ms,
                         expires_ms=now_ms + int(COMMAND_TTL_SECONDS * 1000))
    db.add(item)
    db.flush()
    return item


def _view(c: RunnerCommand) -> dict:
    return {"id": c.id, "seq": c.seq, "action": c.action, "notional_frac": c.notional_frac, "qty_frac": c.qty_frac,
            "signal_price": c.signal_price, "reason": c.reason, "expires_ms": c.expires_ms}


def pending_commands(db, row: RunSession, now_ms: int) -> List[dict]:
    rows = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == row.id, RunnerCommand.status == "pending")
                   .order_by(RunnerCommand.seq.asc())).all()
    out = []
    for c in rows:
        if c.expires_ms <= now_ms:
            c.status = "expired"
            db.add(c)
            continue
        out.append(_view(c))
    return out


def apply_acks(db, row: RunSession, acks: list, now_ms: int) -> None:
    from .runner import _append_events
    if not isinstance(acks, list):
        return
    events = []
    for ack in acks[:100]:
        if not isinstance(ack, dict):
            continue
        try:
            cmd = db.get(RunnerCommand, int(ack.get("command_id")))
        except (TypeError, ValueError):
            continue
        if cmd is None or cmd.session_id != row.id or cmd.status != "pending":
            continue
        ok = bool(ack.get("ok"))
        cmd.attempts += 1
        cmd.acked_at = _now_iso()
        cmd.executed_qty = float(ack.get("executed_qty") or 0.0)
        cmd.fill_price = float(ack.get("fill_price") or 0.0)
        cmd.error = str(ack.get("error") or "")[:200]
        if ok:
            cmd.status = "acked"
            events.append({"ts": cmd.acked_at, "kind": "order",
                           "message": f"[주문 #{cmd.seq}] {cmd.action.upper()} 체결 {cmd.executed_qty:g} @ {cmd.fill_price:g} · {cmd.reason}"})
        elif cmd.action in EXIT_SIDES and cmd.attempts < EXIT_RETRY_MAX:
            cmd.status = "pending"
            cmd.expires_ms = now_ms + int(COMMAND_TTL_SECONDS * 1000)
            row.note = EXIT_FAIL_NOTE
            events.append({"ts": cmd.acked_at, "kind": "error",
                           "message": f"⚠ 청산 주문 실패({cmd.attempts}/{EXIT_RETRY_MAX}) — 다시 시도합니다 · {cmd.error}"})
        elif cmd.action in EXIT_SIDES:
            cmd.status = "failed"
            row.note = EXIT_FAIL_NOTE
            events.append({"ts": cmd.acked_at, "kind": "error",
                           "message": f"⚠ 청산 주문이 {EXIT_RETRY_MAX}회 실패했어요 — 거래소에서 포지션을 확인해 주세요 · {cmd.error}"})
        else:
            cmd.status = "failed"
            events.append({"ts": cmd.acked_at, "kind": "error",
                           "message": f"⚠ 진입 실패 — 이번 사이클은 건너뜁니다 · {cmd.error}"})
        db.add(cmd)
    if events:
        db.add(row)
        _append_events(db, row, events)


_MISMATCH_NOTED: set = set()  # session_id — 연속 중복 경고 방지(프로세스 메모리)


def check_position_mismatch(db, row: RunSession, reported_in_position: bool) -> None:
    from .paper import parse_state
    from .runner import _append_events
    state = parse_state(row.state_json)
    if not state:
        return
    pending = db.exec(select(RunnerCommand.id).where(RunnerCommand.session_id == row.id, RunnerCommand.status == "pending")).first()
    if pending is not None:
        return
    expected = bool(state.get("in_position"))
    if expected == bool(reported_in_position):
        _MISMATCH_NOTED.discard(row.id)
        return
    if row.id in _MISMATCH_NOTED:
        return
    _MISMATCH_NOTED.add(row.id)
    _append_events(db, row, [{"ts": _now_iso(), "kind": "warn",
                              "message": ("서버 전략은 포지션 보유 중인데 실행기는 비어 있어요" if expected
                                          else "서버 전략은 비어 있는데 실행기는 포지션을 들고 있어요")
                                         + " — 거래소 포지션을 확인해 주세요."}])


# --- 재기동 복구 -------------------------------------------------------------
def _running_v8_ids() -> List[int]:
    from .runner import supports_signals
    with get_session() as db:
        rows = db.exec(select(RunSession.id, RunSession.runner_version).where(RunSession.status == "running")).all()
    return [int(sid) for sid, ver in rows if supports_signals(str(ver or ""))]


async def resume_running_runner_sessions() -> int:
    count = 0
    for sid in await asyncio.to_thread(_running_v8_ids):
        try:
            if await start_driver(sid):
                count += 1
        except Exception:
            log.exception("runner engine resume: session %s failed", sid)
    if count:
        log.info("runner engine resume: revived %d session(s)", count)
    return count
```
`supports_signals`는 Task 8에서 `runner.py`에 만든다. 이 Task의 마지막 테스트(`test_resume_only_v8_running_sessions`)를 위해 임시로 `runner.py` 상단에 다음을 먼저 넣는다(Task 8이 그대로 쓴다):
```python
SIGNAL_MIN_VERSION = os.environ.get("RUNNER_SIGNAL_MIN_VERSION", "8").strip() or "8"


def supports_signals(version: str) -> bool:
    """실행기가 서버 신호 프로토콜(v8+)을 쓰는가. 숫자 아닌 값·빈 값은 미지원."""
    v = (version or "").strip()
    return v.isascii() and v.isdigit() and len(v) <= 6 and int(v) >= int(SIGNAL_MIN_VERSION)
```
(`runner.py`에 `import os`가 없으면 추가.)

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_runner_engine.py -q -p no:warnings`
Expected: PASS (9). `test_start_driver_…`가 RSI 웜업 후 첫 봉에서 buy를 못 내면 웜업 시계열을 더 급락시킨다(`100 - 3*i`).

- [ ] **Step 5: 커밋**

```bash
git add backend/app/runner_engine.py backend/app/runner.py backend/tests/test_runner_engine.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "실행기 세션 서버 드라이버 — Fill→명령(RunnerCommand), 만료·ack·재시도, 불일치 경고, 복구

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: `runner.py`/`main.py` — 버전 게이트·heartbeat 프로토콜·수명 훅

**Files:**
- Modify: `backend/app/runner.py` (`needs_signals`, `start_session`, `claim_launch_ticket`, `heartbeat`, `mark_stopped`, `_EVENT_KINDS`)
- Modify: `backend/app/main.py` (`RunnerHeartbeatRequest.acks`, claim 라우트, lifespan)
- Modify: `backend/tests/test_startup_initialization.py` (mock 추가)
- Test: `backend/tests/test_runner_signals_api.py` (신규)

**Interfaces:**
- Produces (`runner.py`): `needs_signals(macro: Macro) -> bool` (rule_type ∉ {A, B}); `SIGNAL_REQUIRED_DETAIL = "지표형 매크로는 실행기 v8 이상이 필요해요. 실행기를 업데이트해 주세요."`; `claim_launch_ticket(ticket, runner_version="")`; `heartbeat(...)` 응답에 `"commands": [...]`.
- Consumes: `runner_engine.schedule_start/schedule_stop/apply_acks/pending_commands/check_position_mismatch`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_runner_signals_api.py`:
```python
"""서버 신호 실행기 API — 구버전 차단(426), v8 매크로 필수(422), heartbeat commands/acks."""
from fastapi.testclient import TestClient
from sqlmodel import select

from app import runner as runner_mod
from app import runner_engine as eng
from app.db import RunnerCommand, RunSession, get_session
from app.main import app
from tests.test_runner import _auth, _signup

client = TestClient(app)

RSI = {"symbol": "ONEUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1, "candle_interval": "5m",
       "period": {"preset": "1w"}, "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 32},
       "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0.1, "slippage_pct": 0.05}}
A = {"symbol": "BTCUSDT", "rule_type": "A", "position_side": "long", "params": {"take_profit_pct": 3.0, "initial_capital": 1000},
     "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0}, "period": {"preset": "3m"}}


def _key(token):
    return client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]


def _start(key, macro, version):
    body = {"symbol": macro["symbol"], "position_side": "long", "leverage": 1, "market": "spot", "testnet": True,
            "human_summary": "t", "macro": macro, "runner_version": version}
    return client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})


def test_old_runner_cannot_start_indicator_macro():
    key = _key(_signup())
    r = _start(key, RSI, "7")
    assert r.status_code == 426 and r.json()["detail"] == runner_mod.SIGNAL_REQUIRED_DETAIL
    assert _start(key, RSI, "").status_code == 426  # 버전 없는 v6 도 막힌다


def test_old_runner_can_still_start_rule_a():
    key = _key(_signup())
    assert _start(key, A, "7").status_code == 200


def test_v8_requires_macro_and_schedules_driver(monkeypatch):
    scheduled = []
    monkeypatch.setattr(eng, "schedule_start", lambda sid: scheduled.append(sid))
    key = _key(_signup())
    no_macro = client.post("/api/runner/start", json={"symbol": "ONEUSDT", "runner_version": "8"}, headers={"X-Runner-Key": key})
    assert no_macro.status_code == 422
    r = _start(key, RSI, "8")
    assert r.status_code == 200 and scheduled == [r.json()["session_id"]]


def test_heartbeat_returns_pending_commands_and_applies_acks(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    monkeypatch.setattr(eng, "schedule_stop", lambda sid: None)
    key = _key(_signup())
    sid = _start(key, RSI, "8").json()["session_id"]
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 0.01, "reason": "RSI 20 ≤ 25 · 진입"}, now_ms=eng._now_ms())
        db.commit()
        cmd_id = cmd.id
    hb = client.post("/api/runner/heartbeat", json={"session_id": sid, "in_position": False, "last_price": 0.01}, headers={"X-Runner-Key": key})
    body = hb.json()
    assert body["action"] == "continue" and [c["id"] for c in body["commands"]] == [cmd_id]
    hb2 = client.post("/api/runner/heartbeat", json={"session_id": sid, "in_position": True, "last_price": 0.01,
                                                     "acks": [{"command_id": cmd_id, "ok": True, "executed_qty": 3000, "fill_price": 0.0101}]},
                      headers={"X-Runner-Key": key})
    assert hb2.json()["commands"] == []
    with get_session() as db:
        assert db.get(RunnerCommand, cmd_id).status == "acked"
    client.post("/api/runner/stopped", json={"session_id": sid, "status": "stopped"}, headers={"X-Runner-Key": key})


def test_claim_rejects_old_runner_for_indicator_macro(monkeypatch):
    token = _signup()
    saved = client.post("/api/me/macros", json={"macro": RSI, "name": "rsi"}, headers=_auth(token)).json()["item"]
    ticket = client.post("/api/me/runner/launch-tickets", json={"user_macro_id": saved["id"], "testnet": True}, headers=_auth(token)).json()
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket["ticket"], "runner_version": "7"})
    assert r.status_code == 426 and r.json()["detail"] == runner_mod.SIGNAL_REQUIRED_DETAIL
    status = client.get(f"/api/me/runner/launch-tickets/{ticket['launch_id']}", headers=_auth(token)).json()
    assert status["status"] == "rejected"
```
(`/api/me/runner/launch-tickets` 응답 키가 `ticket`/`launch_id`가 아니면 `tests/test_runner.py`의 티켓 테스트에서 실제 키를 확인해 맞춘다.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_runner_signals_api.py -q -p no:warnings`
Expected: FAIL — `AttributeError: SIGNAL_REQUIRED_DETAIL` 등.

- [ ] **Step 3: `runner.py`**

상단(Task 7의 `supports_signals` 옆):
```python
from . import runner_engine
from .engine import RuleType

SIGNAL_REQUIRED_DETAIL = "지표형 매크로는 실행기 v8 이상이 필요해요. 실행기를 업데이트해 주세요."
MACRO_REQUIRED_DETAIL = "실행기 v8 은 매크로 설정을 함께 보내야 해요."


def needs_signals(macro: Optional[Macro]) -> bool:
    """A/B(익절·손절 재진입, 지정가 밴드)는 실행기 로컬 로직이 맞다. 그 외는 서버 신호가 필요하다.
    매크로를 안 보낸 구버전(v6)은 판단할 수 없으므로 '필요'로 본다."""
    return macro is None or macro.rule_type not in (RuleType.A, RuleType.B)
```
`_EVENT_KINDS`에 `"warn"` 추가: `{"start", "info", "signal", "order", "fill", "error", "warn", "stop"}`.

`start_session`: `normalized_macro` 계산 직후(`raw_user_macro_id` 처리 전)에
```python
    signal_runner = supports_signals(runner_version)
    if signal_runner and normalized_macro is None:
        raise HTTPException(status_code=422, detail=MACRO_REQUIRED_DETAIL)
    if not signal_runner and needs_signals(normalized_macro):
        raise HTTPException(status_code=426, detail=SIGNAL_REQUIRED_DETAIL)
```
그리고 함수 끝 `notify_sessions_changed(user.id)` 뒤에 `if signal_runner: runner_engine.schedule_start(result["session_id"])`.

`claim_launch_ticket(ticket: str, runner_version: str = "")`: 매크로를 파싱한 직후(UPDATE 전)에
```python
        if not supports_signals(runner_version) and needs_signals(macro):
            db.rollback()
            mark_launch_ticket_rejected(raw_ticket, runner_version)
            raise _ticket_error(426, SIGNAL_REQUIRED_DETAIL)
```
(`mark_launch_ticket_rejected`는 자기 세션을 연다 — 현재 세션을 먼저 롤백한다.)

`heartbeat`: 시그니처 그대로. `events = snapshot.pop("events", None)` 옆에 `acks = snapshot.pop("acks", None)`. 세션 갱신 뒤, `action = ...` 전에:
```python
        commands: list = []
        if supports_signals(row.runner_version):
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            runner_engine.apply_acks(db, row, acks or [], now_ms)
            commands = runner_engine.pending_commands(db, row, now_ms)
            runner_engine.check_position_mismatch(db, row, row.in_position)
```
반환을 `return {"action": action, "commands": commands}`로. (구버전엔 빈 리스트 — 무해.)

`mark_stopped`: `db.commit()` 뒤 `notify_sessions_changed(user.id)` 옆에 `runner_engine.schedule_stop(session_id)`.

- [ ] **Step 4: `main.py`**

- `RunnerHeartbeatRequest`에 `acks: list[dict] = []` (주석: `# v8: 직전 heartbeat 로 받은 명령의 실행 결과 [{command_id, ok, executed_qty, fill_price, error}]`).
- claim 라우트 마지막 줄 → `return runner_mod.claim_launch_ticket(req.ticket, runner_version=current)`.
- lifespan: `await paper_mod.resume_running_sessions()` try 블록 뒤에
  ```python
    runner_engine_mod.install(asyncio.get_running_loop())
    try:
        await runner_engine_mod.resume_running_runner_sessions()
    except Exception:
        logging.getLogger(__name__).exception("runner engine resume failed at startup")
  ```
  (`from . import runner_engine as runner_engine_mod`, `import asyncio` 확인.)
- `tests/test_startup_initialization.py` 스크립트에 `main.runner_engine_mod.resume_running_runner_sessions = AsyncMock()` 한 줄 추가(`main.paper_mod.resume_running_sessions = AsyncMock()` 다음).

- [ ] **Step 5: 통과 확인**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_runner_signals_api.py tests/test_runner.py tests/test_runner_origin_events.py tests/test_runner_stop_state.py tests/test_runner_engine.py -q -p no:warnings`
Expected: PASS. 기존 `test_runner.py`가 rule A 매크로를 v7/빈 버전으로 시작하므로 계속 통과해야 한다. 만약 기존 테스트가 매크로 없이 빈 버전으로 시작하는 케이스가 있으면(v6 흐름) 426이 나는데 — 그 테스트는 이제 스펙상 차단이 맞으므로 기대값을 426으로 바꾸고 주석에 이유를 남긴다.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/runner.py backend/app/main.py backend/tests/test_runner_signals_api.py backend/tests/test_startup_initialization.py backend/tests/test_runner.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "실행기 API: 구버전+지표형 426 차단, v8 heartbeat commands/acks, 드라이버 시작·정지·재기동 복구

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: 실행기 v8 — 로컬 전략 제거, 명령 실행·ack

**Files:**
- Modify: `runner/installation.py:7` (`RUNNER_VERSION = "8"`)
- Modify: `runner/macro_runner.py` (`_strategy_targets`, `_should_enter/_should_exit/_was_stop_exit/DEFAULT_TP_PCT` 삭제, `ServerClient.heartbeat`, `BotThread.run`, `_execute_command`, `_local_stop_loss`)
- Modify: `runner/test_macro_runner_execution.py` (기존 run 테스트가 `heartbeat` 반환형 변화에 맞게), 신규 `runner/test_macro_runner_commands.py`
- Modify: `runner/README.md`

**Interfaces:**
- `ServerClient.heartbeat(snapshot, acks=None) -> dict` — `{"action": str, "commands": list}`; 통신 실패 시 `{"action": "continue", "commands": [], "offline": True}`. `acks` 전송 실패 시 `pending_acks`로 되돌린다.
- `BotThread._execute_command(cmd: dict, price: float) -> dict` — ack 항목 `{"command_id", "ok", "executed_qty", "fill_price", "error"}`. 같은 `id`는 한 번만 실행(`self._done_command_ids: deque(maxlen=200)`).
- `BotThread._local_stop_loss(price) -> bool` — `risk.stop_loss_pct`가 있고 진입가 대비 넘었으면 True.

- [ ] **Step 1: 실패하는 테스트**

`runner/test_macro_runner_commands.py`:
```python
"""실행기 v8 — 서버 명령만 실행하고 결과를 ack 로 돌려준다. 스스로 진입/청산을 판단하지 않는다."""
import unittest
from collections import deque
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner


def _bot(in_position=False, held=0.0, entry=0.0):
    bot = object.__new__(macro_runner.BotThread)
    bot.client = Mock()
    bot.market, bot.symbol, bot.side = "spot", "ONEUSDT", "long"
    bot.leverage = 1
    bot.log = Mock()
    bot.in_position, bot.held_qty, bot.entry_price = in_position, held, entry
    bot.realized, bot.step = 0.0, 1.0
    bot.position_uncertain = False
    bot.macro = {"rule_type": "F", "params": {"initial_capital": 32}, "risk": {"invest_ratio": 1.0, "stop_loss_pct": 5}}
    bot.capital = 32.0
    bot._done_command_ids = deque(maxlen=200)
    bot.pending_acks = []
    return bot


class CommandExecutionTests(unittest.TestCase):
    def setUp(self):
        patch.object(macro_runner.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def test_buy_command_spends_capital_fraction(self):
        bot = _bot()
        bot._place = Mock(side_effect=lambda side, qty, reduce_only=False: (setattr(bot, "_last_fill_qty", qty), setattr(bot, "_last_fill_price", 0.01), True)[-1])
        ack = bot._execute_command({"id": 5, "action": "buy", "notional_frac": 0.5, "qty_frac": 0.0, "reason": "RSI 20 ≤ 25 · 진입"}, price=0.01)
        bot._place.assert_called_once_with("BUY", 1600.0)  # 32 * 0.5 / 0.01
        self.assertEqual(ack["command_id"], 5)
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["executed_qty"], 1600.0)

    def test_sell_command_uses_fraction_of_held(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.01)
        bot._place = Mock(return_value=True)
        bot._last_fill_qty, bot._last_fill_price = 500.0, 0.012
        bot._execute_command({"id": 6, "action": "sell", "notional_frac": 0.0, "qty_frac": 0.5, "reason": "부분 청산"}, price=0.012)
        bot._place.assert_called_once_with("SELL", 500.0, reduce_only=False)

    def test_full_exit_uses_close_position(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.01)
        bot._close_position = Mock(return_value=True)
        bot._last_fill_qty, bot._last_fill_price = 1000.0, 0.013
        ack = bot._execute_command({"id": 7, "action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "reason": "청산"}, price=0.013)
        bot._close_position.assert_called_once_with()
        self.assertTrue(ack["ok"])

    def test_exit_when_flat_is_a_noop_ack(self):
        bot = _bot()
        bot._place = Mock()
        ack = bot._execute_command({"id": 8, "action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "reason": "청산"}, price=0.013)
        bot._place.assert_not_called()
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["executed_qty"], 0.0)

    def test_duplicate_command_id_is_not_executed_twice(self):
        bot = _bot()
        bot._place = Mock(return_value=True)
        bot._last_fill_qty, bot._last_fill_price = 3200.0, 0.01
        cmd = {"id": 9, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"}
        bot._execute_command(cmd, price=0.01)
        again = bot._execute_command(cmd, price=0.01)
        self.assertEqual(bot._place.call_count, 1)
        self.assertTrue(again["ok"])

    def test_failed_order_acks_error(self):
        bot = _bot()
        bot._place = Mock(side_effect=RuntimeError("주문 상태 REJECTED"))
        ack = bot._execute_command({"id": 10, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"}, price=0.01)
        self.assertFalse(ack["ok"])
        self.assertIn("REJECTED", ack["error"])

    def test_add_to_position_averages_entry(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.010)

        def place(side, qty, reduce_only=False):
            bot._last_fill_qty, bot._last_fill_price = qty, 0.012
            bot.held_qty += qty
            return True

        bot._place = Mock(side_effect=place)
        bot._execute_command({"id": 11, "action": "buy", "notional_frac": 0.375, "qty_frac": 0.0, "reason": "격자"}, price=0.012)
        self.assertAlmostEqual(bot.held_qty, 2000.0)
        self.assertAlmostEqual(bot.entry_price, 0.011)

    def test_local_stop_loss_is_the_only_local_exit(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.010)
        self.assertFalse(bot._local_stop_loss(0.0096))
        self.assertTrue(bot._local_stop_loss(0.0094))
        self.assertFalse(hasattr(macro_runner, "_should_enter"))
        self.assertFalse(hasattr(macro_runner, "DEFAULT_TP_PCT"))


class HeartbeatProtocolTests(unittest.TestCase):
    def test_heartbeat_sends_acks_and_returns_commands(self):
        client = object.__new__(macro_runner.ServerClient)
        client.session_id, client.base, client._headers = 3, "http://x", {}
        client._drain_events, client._requeue_events = Mock(return_value=[]), Mock()
        resp = Mock(); resp.json.return_value = {"action": "continue", "commands": [{"id": 1}]}; resp.raise_for_status = Mock()
        with patch.object(macro_runner.requests, "post", return_value=resp) as post:
            out = client.heartbeat({"in_position": False}, acks=[{"command_id": 0, "ok": True}])
        self.assertEqual(out["commands"], [{"id": 1}])
        self.assertEqual(post.call_args.kwargs["json"]["acks"], [{"command_id": 0, "ok": True}])

    def test_heartbeat_offline_keeps_acks(self):
        client = object.__new__(macro_runner.ServerClient)
        client.session_id, client.base, client._headers = 3, "http://x", {}
        client._drain_events, client._requeue_events = Mock(return_value=[]), Mock()
        with patch.object(macro_runner.requests, "post", side_effect=OSError("down")):
            out = client.heartbeat({}, acks=[{"command_id": 2, "ok": True}])
        self.assertEqual(out["action"], "continue")
        self.assertTrue(out["offline"])
        self.assertEqual(client.unsent_acks, [{"command_id": 2, "ok": True}])
```

- [ ] **Step 2: 실패 확인**

Run: `cd C:/Users/RHJ/Desktop/gg_parrot && python -m unittest runner.test_macro_runner_commands -v`
Expected: FAIL — `AttributeError: _execute_command` 등.

- [ ] **Step 3: 구현**

`runner/installation.py`: `RUNNER_VERSION = "8"`.

`runner/macro_runner.py`:
1. 95줄 `DEFAULT_TP_PCT = 3.0` 삭제. 379~413줄 `_was_stop_exit`, `_should_enter`, `_should_exit` 삭제. `_strategy_targets`는 `tp_pct/buy_price/sell_price`를 빼고:
   ```python
   def _strategy_targets(macro: dict) -> dict:
       p = macro.get("params", {})
       risk = macro.get("risk", {})
       return {
           "rule": macro.get("rule_type", "A"),
           "sl_pct": float(risk["stop_loss_pct"]) if risk.get("stop_loss_pct") else None,
           "invest_ratio": float(risk.get("invest_ratio", 1.0)),
           "capital": float(p.get("initial_capital", 0) or 0),
           "risk": risk,
       }
   ```
2. `ServerClient.__init__`에 `self.unsent_acks: list[dict] = []`. `heartbeat`:
   ```python
    def heartbeat(self, snapshot: dict, acks: list[dict] | None = None) -> dict:
        """상태와 명령 실행 결과(acks)를 올리고 {"action", "commands"} 를 받는다.
        네트워크 오류면 action="continue", commands=[], offline=True — 진입은 멈추고(명령 없음) 로컬 안전망만 돈다."""
        if self.session_id is None:
            return {"action": "continue", "commands": []}
        body = dict(snapshot)
        body["session_id"] = self.session_id
        events = self._drain_events()
        body["events"] = events
        pending = list(self.unsent_acks) + list(acks or [])
        self.unsent_acks = []
        body["acks"] = pending
        try:
            r = requests.post(f"{self.base}/api/runner/heartbeat", json=body, headers=self._headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            return {"action": data.get("action", "continue"), "commands": list(data.get("commands") or [])}
        except Exception:
            self._requeue_events(events)
            self.unsent_acks = pending
            return {"action": "continue", "commands": [], "offline": True}
   ```
3. `BotThread.__init__`에 `self.capital = float((macro.get("params") or {}).get("initial_capital") or 0) or MAX_ORDER_USDT`, `self._done_command_ids: deque = deque(maxlen=200)`(`from collections import deque`), `self.pending_acks: list[dict] = []`, `self._offline_logged = False`.
4. 새 메서드:
   ```python
    def _local_stop_loss(self, price: float) -> bool:
        """서버와 끊겨도 손절은 된다 — 로컬에 남긴 유일한 청산 판단. 서버 손절 명령이 뒤에 오면 보유 0 이라 무주문 ack."""
        sl = (self.macro.get("risk") or {}).get("stop_loss_pct")
        if not sl or not self.in_position or self.entry_price <= 0:
            return False
        sl = float(sl) / 100.0
        return price <= self.entry_price * (1 - sl) if self.side == "long" else price >= self.entry_price * (1 + sl)

    def _execute_command(self, cmd: dict, price: float) -> dict:
        """서버 명령 하나를 실행하고 ack 를 만든다. 같은 id 는 한 번만."""
        cid = cmd.get("id")
        ack = {"command_id": cid, "ok": True, "executed_qty": 0.0, "fill_price": 0.0, "error": ""}
        if cid in self._done_command_ids:
            return ack
        self._done_command_ids.append(cid)
        action = str(cmd.get("action", "")).lower()
        reason = str(cmd.get("reason") or "")
        try:
            if action in ("buy", "short"):
                notional = min(self.capital * float(cmd.get("notional_frac") or 0.0), MAX_ORDER_USDT)
                qty, _ = _order_qty(price, self.step, 0, notional, self.leverage, self.market)
                if qty <= 0:
                    raise RuntimeError("주문 수량이 최소 단위보다 작아요.")
                word = "BUY" if action == "buy" else "SELL"
                self.log(f"[신호] {reason} → {word} {qty} {self.symbol} @ {price}")
                prev_qty, prev_entry = (self.held_qty, self.entry_price) if self.in_position else (0.0, 0.0)
                if not self._place(word, qty):
                    raise RuntimeError("주문이 체결되지 않았어요.")
                filled_qty, filled_px = self._last_fill_qty, self._last_fill_price
                if prev_qty > 0 and filled_qty > 0:  # 추가 진입(그리드·마틴게일): 가중평균
                    self.held_qty = prev_qty + filled_qty
                    self.entry_price = (prev_qty * prev_entry + filled_qty * filled_px) / self.held_qty
                    self.in_position = True
                ack.update(executed_qty=filled_qty, fill_price=filled_px)
            elif action in ("sell", "cover"):
                if not self.in_position or self.held_qty <= 0:
                    self.log(f"[신호] {reason} → 보유 없음, 건너뜀")
                    return ack
                frac = float(cmd.get("qty_frac") or 1.0)
                self.log(f"[신호] {reason} → {'전량' if frac >= 0.999 else f'{frac:.0%}'} 청산 @ {price}")
                if frac >= 0.999:
                    if not self._close_position():
                        raise RuntimeError("청산 주문이 체결되지 않았어요.")
                else:
                    qty = _round_step(self.held_qty * frac, self.step)
                    word = "SELL" if self.side == "long" else "BUY"
                    if not self._place(word, qty, reduce_only=(self.market == "futures")):
                        raise RuntimeError("부분 청산 주문이 체결되지 않았어요.")
                ack.update(executed_qty=self._last_fill_qty, fill_price=self._last_fill_price)
            else:
                raise RuntimeError(f"알 수 없는 명령 {action}")
        except Exception as exc:
            ack.update(ok=False, error=str(exc)[:200])
            self.log(f"  ⚠ 명령 실행 실패: {exc}")
        return ack
   ```
   (`_place`가 `reduce_only=False`일 때 기존처럼 `closing = reduce_only or self.in_position`으로 청산을 판단한다 — 현물 부분 청산도 `in_position`이라 closing 경로로 간다. 테스트 `test_sell_command_uses_fraction_of_held`는 `reduce_only=False`를 기대한다.)
5. `BotThread.run` 루프의 3)·4)를 교체:
   ```python
                # 3) 로컬 안전망 — 일일 손실·최대 보유시간(RiskGuard) + 손절. 진입 판단은 서버가 한다.
                if self.in_position:
                    unreal = _pnl_usdt(self.held_qty, self.entry_price, price, self.side)
                    forced, why = guard.force_close(unreal)
                    stop = self._local_stop_loss(price)
                    if forced or stop:
                        self.log(f"[{'강제청산: ' + why if forced else '손절'}] {price} (진입 {self.entry_price})")
                        entry_price, realized_before = self.entry_price, self.realized
                        if self._close_position():
                            pnl = self.realized - realized_before
                            self.log(f"  손익 {_pnl_pct(entry_price, self._last_fill_price, self.side):+.2f}% ({pnl:+.2f} USDT) · 누적 {self.realized:+.2f} USDT")
                            guard.on_exit(pnl, was_stop=stop)

                # 4) 하트비트 — 상태·ack 를 올리고 명령을 받아 순서대로 실행
                snap = self._snapshot()
                self.on_status(snap)
                reply = self.server.heartbeat(snap, acks=self.pending_acks)
                self.pending_acks = []
                if reply.get("offline"):
                    if not self._offline_logged:
                        self.log("서버 연결 재시도 중 — 신호 대기(진입 없음, 손절만 로컬에서 봅니다)")
                        self._offline_logged = True
                else:
                    self._offline_logged = False
                action = reply.get("action", "continue")
                if action in ("stop_only", "close_and_stop"):
                    self.log(f"원격 종료 명령 수신: {action}")
                    self.set_command(action)
                else:
                    for cmd in reply.get("commands") or []:
                        blocked, why = guard.entry_blocked()
                        if blocked and str(cmd.get("action", "")).lower() in ("buy", "short"):
                            self.log(f"  ⏸ 진입 보류: {why}")
                            self.pending_acks.append({"command_id": cmd.get("id"), "ok": False, "executed_qty": 0.0, "fill_price": 0.0, "error": f"로컬 리스크 보류: {why}"})
                            continue
                        ack = self._execute_command(cmd, price)
                        if ack["ok"] and str(cmd.get("action", "")).lower() in ("buy", "short") and ack["executed_qty"] > 0:
                            guard.on_entry()
                        self.pending_acks.append(ack)
   ```
   기존 4)의 heartbeat 호출과 `_should_enter` 분기는 모두 지운다. 2)의 시세 오류 분기에서 `self.server.heartbeat(snapshot)` 호출도 `reply = self.server.heartbeat(snapshot, acks=self.pending_acks); self.pending_acks = []; action = reply.get("action", "continue")`로 바꾼다. `run` 시작부의 `guard = RiskGuard(...)` 로그 줄은 그대로.
6. `_finish_position` 등 종료 경로는 그대로. 종료 직전 `self.server.stopped(...)` 호출 전에 남은 `pending_acks`가 있으면 heartbeat 한 번 더 보내지 않아도 된다(서버는 만료로 정리).
7. 기존 `runner/test_macro_runner_execution.py`에서 `bot.server.heartbeat.return_value = "continue"`인 곳을 `{"action": "continue", "commands": []}`로 바꾸고, `_should_enter/_should_exit`를 직접 부르는 테스트가 있으면 삭제한다(그 로직은 서버로 갔다). `prepare_run`의 macro에 `"rule_type": "A"`는 그대로 두되 결과 기대가 "즉시 진입"이던 테스트는 "명령 없으면 주문 없음"으로 바꾼다.

`runner/README.md`에 절 추가:
```
## v8 — 서버 신호
실행기는 스스로 진입/청산을 판단하지 않습니다. 서버가 백테스트·리더보드와 같은 엔진으로 봉 마감마다 판단해
heartbeat 응답 `commands` 로 내려 주면 주문만 넣고, 다음 heartbeat 의 `acks` 로 결과를 보고합니다.
로컬에 남은 판단은 안전망(손절 %, 일일 손실, 최대 보유시간)뿐입니다 — 서버와 끊기면 진입은 멈추고 손절만 됩니다.
v7 이하로는 A/B(익절·손절, 지정가) 매크로만 시작할 수 있고, 지표형(C~K)은 426 으로 거절됩니다.
```

- [ ] **Step 4: 통과 확인**

Run: `cd C:/Users/RHJ/Desktop/gg_parrot && python -m unittest discover -s runner -p "test_*.py"`
Expected: OK (기존 + 신규 10). 스모크: `python -c "import runner.macro_runner as m; print(m.RUNNER_VERSION)"` → `8`.

- [ ] **Step 5: 커밋**

```bash
git add runner/installation.py runner/macro_runner.py runner/test_macro_runner_commands.py runner/test_macro_runner_execution.py runner/README.md
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "실행기 v8 — 로컬 전략 판단 제거, 서버 명령 실행·ack, 안전망(손절·일일 손실·보유시간)만 로컬

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: 프론트 `warn` 라벨 + 배포 문서

**Files:**
- Modify: `frontend/src/features/agents/modules/runnerLog.js:4-26`
- Modify: `frontend/tests/runnerLogOrigin.test.js` (한 케이스 추가) 또는 신규 `frontend/tests/runnerLogWarn.test.js`
- Modify: `DEPLOY.md`

- [ ] **Step 1: 실패하는 테스트**

`frontend/tests/runnerLogWarn.test.js`:
```js
import test from "node:test";
import assert from "node:assert/strict";
import { runnerLogModule } from "../src/features/agents/modules/runnerLog.js";

test("warn 종류는 '주의' 라벨과 warning 심각도로 그려진다", () => {
  const session = { session_id: 1 };
  const featureStates = { runner_log: { data: { session_id: 1, events: [{ id: 9, kind: "warn", ts: "2026-09-22T00:00:00Z", message: "포지션 불일치" }] } } };
  const [ev] = runnerLogModule.buildEvents({ session, featureStates });
  assert.equal(ev.severity, "warning");
  assert.match(ev.title ?? ev.label ?? JSON.stringify(ev), /주의/);
});
```
(`buildEvents`가 만드는 객체의 라벨 키 이름은 파일 45줄 이후를 열어 `KIND_LABEL[kind]`가 들어가는 필드명으로 맞춘다.)

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && node --test tests/runnerLogWarn.test.js`
Expected: FAIL — severity가 `info`.

- [ ] **Step 3: 구현**

`runnerLog.js`: `KIND_LABEL`에 `warn: "주의",`; `severityFor`와 `expressionFor`에 `if (kind === "warn") return "warning";`.

`DEPLOY.md`에 절 "서버 신호 실행기 (2026-09-22)":
```
- 마이그레이션: `supabase/migrations/20260922120000_runner_commands.sql` (runsession.state_json, runnercommand).
- 배포 순서: ① 서버(main) — 구버전 실행기는 A/B 만 시작 가능, 지표형은 426. ② 실행기 v8 exe 빌드·서명·태그. ③ `RUNNER_EXE_VERSION=8` 과 다운로드 URL 갱신.
- 환경변수(기본값으로 충분): `RUNNER_SIGNAL_MIN_VERSION=8`, `COMMAND_TTL_SECONDS=90`, `CANDLE_GRACE_SECONDS=2`, `CANDLE_RETRY_SECONDS=30`.
- 동작: 실행기 세션마다 서버가 StrategyDriver 를 돌려(페이퍼와 같은 엔진·같은 마감봉) Fill 을 runnercommand 로 남기고 heartbeat 응답 `commands` 로 내려 준다. 실행기는 주문만 넣고 `acks` 로 보고. 만료(90s) 명령은 실행되지 않는다. 재배포 시 running v8 세션은 state_json 으로 복구된다.
- 주의: v6 실행기는 매크로를 보내지 않아 규칙을 알 수 없으므로 전부 426 이 난다(업데이트 유도).
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && node --test "tests/*.test.js" && npx vite build --logLevel error`
Expected: 전부 PASS, 빌드 성공.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/agents/modules/runnerLog.js frontend/tests/runnerLogWarn.test.js DEPLOY.md
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "실행 로그 warn 라벨 + 서버 신호 실행기 배포 메모

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: 전체 검증 · 로컬 스모크 · 마무리

**Files:** 없음(검증)

- [ ] **Step 1: 백엔드 전체**

Run: `cd backend && .venv/Scripts/python -m pytest tests -q -p no:warnings -x --deselect tests/test_startup_initialization.py 2>&1 | tail -5`
Expected: 기존 알려진 실패(prefect ×3, 수집 오류 6) 외 실패 없음. `-x`로 첫 실패에서 멈추면 원인을 고치고 다시 돈다.

- [ ] **Step 2: 실행기 전체 + 프론트**

Run: `python -m unittest discover -s runner -p "test_*.py"` 그리고 `cd frontend && node --test "tests/*.test.js"`.
Expected: OK.

- [ ] **Step 3: 로컬 스모크 (backend-dev 재시작)**

`preview_stop`/`preview_start backend-dev` 후 `preview_logs`에 `runner engine resume` 또는 오류 없음 확인. 로컬 admin 계정(1063)으로 리더보드 페이지 열어 캔들형 페이퍼 세션이 "복구 중…"→상태 표시로 넘어가는지 확인(브라우저 탭 프런트 필요).

- [ ] **Step 4: 스펙 보정**

스펙 §4.3의 `async def push_candle` → `def push_candle` (동기), §4.4의 `expires_at` → `expires_ms`, "v8 미만(빈 값 포함)" 문장에 "매크로를 안 보낸 v6는 규칙을 알 수 없어 전부 426" 한 줄 추가. 커밋 메시지 `스펙 보정: 구현과 맞춤(push_candle 동기, expires_ms, v6 처리)`.

- [ ] **Step 5: 마무리**

`superpowers:finishing-a-development-branch` 스킬로 `main` 머지 → 노희재 이름으로 푸시(사용자 지시대로). 머지 후 Supabase SQL 에디터에서 `20260922120000_runner_commands.sql` 실행이 필요함을 사용자에게 알린다(서버 `_migrate_pg`도 컬럼/테이블을 만들 수 있지만 RLS/권한 문장은 마이그레이션 파일에만 있다).

---

## 자기 검토

**스펙 대응표**
- §4.1 피드 → Task 3. §4.2 어댑터·웜업 → Task 2. §4.3 드라이버·페이퍼 → Task 4·5. §4.4 DB → Task 6, 명령·ack·만료·재시도·불일치·수명·복구 → Task 7·8. §4.5 실행기 → Task 9. §4.6 프론트 → Task 10. §7 배포 메모 → Task 10·11.
- 스펙과 다른 점(의도): `push_candle` 동기, `expires_ms`(ISO 대신 ms), v6(매크로 없음)은 전부 426 — Task 11 Step 4에서 스펙에 반영.
- 실행기 세션의 `virtual_balance` 개념은 없다 — 드라이버 `initial = macro.initial_capital or 100`을 비율 기준으로만 쓴다(`notional_frac`). 복구 시 자산은 초기자본으로 되돌리되 포지션(수량·평단)은 `state_json`에서 이어 간다.

**타입/이름 일관성**
- `Fill.reason`, `Fill.qty_before`(Task 1) ← `command_from_fill`(Task 7), 실행기 테스트의 `reason`(Task 9).
- `LiveCandleSim.inner/pending()/on_candle()`(Task 2) ← Task 4·5·7 테스트.
- `StrategyDriver.tick/push_candle/candle_keys/warmup/state/restore/restore_fills`(Task 4) ← Task 5·7.
- `feed.history/subscribe/unsubscribe`(Task 3) ← Task 5·7(가짜 피드도 같은 이름).
- `runner.supports_signals/needs_signals/SIGNAL_REQUIRED_DETAIL/MACRO_REQUIRED_DETAIL`(Task 7·8) ← Task 8 테스트.
- `runner_engine.insert_command/pending_commands/apply_acks/check_position_mismatch/schedule_start/schedule_stop/_tick_once/_drivers`(Task 7) ← Task 8.
- 실행기: `ServerClient.heartbeat(snapshot, acks=None) -> dict`, `unsent_acks`; `BotThread._execute_command/_local_stop_loss/pending_acks/_done_command_ids/capital`(Task 9).
