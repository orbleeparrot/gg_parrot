# 프로 빌더 1차 — 검증 · 분석 · 근거 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 결과를 전문가가 믿을 수 있게 검증하고, 이상 구간의 근거를 찾아 보여 준다.

**Architecture:** 기존 백테스트 엔진은 한 줄도 고치지 않는다. 검증은 `equity_curve` 를 다시
읽거나, 캔들을 한 번만 받아 DataFrame 을 잘라 `run_backtest` 를 여러 번 부르는 상위
레이어다(`engine/portfolio.py` 와 같은 패턴). 과최적화 판정은 서버의 결정론적 규칙이 내고,
AI 는 그 판정을 설명만 한다.

**Tech Stack:** Python 3.14 · FastAPI · SQLModel · pandas · pytest / React · Vite · node:test

**Spec:** `docs/superpowers/specs/2026-10-02-pro-builder-design.md`

**범위:** 이 계획은 스펙의 §4 검증 · §6 분석 · §7 근거 · §8 화면(검증 탭까지)을 덮는다.
**§5 코치는 별도 계획**(`2026-10-02-pro-builder-coach.md`)이다. 이 계획만 끝나도
`/studio/pro` 에서 검증과 근거를 쓸 수 있는 소프트웨어가 된다.

## Global Constraints

- **엔진 불변** — `backend/app/engine/backtest.py` 와 `schema.py` 의 기존 동작을 바꾸지 않는다.
  `run_backtest` 와 `BacktestResult` 는 읽기만 한다.
- **판정은 서버, 설명은 AI** — 과최적화 플래그는 `validation.py` 가 낸다. AI 는 플래그를
  받아 설명만 한다. AI 응답으로 플래그를 만들거나 지우지 않는다.
- **근거는 지어내지 않는다** — AI 에 넘기는 기사는 서버가 실제로 찾은 것뿐이다. 못 찾으면
  빈 목록을 넘기고 "못 찾았다" 고 말하게 한다.
- **금지어** — 기존 `추천 · 보장 · 확실 · 무조건` 에 **`유망 · 안전 · 좋은 전략`** 을 더한다.
  코드 · 주석 · 문구 · 테스트 이름에 "추천" 을 쓰지 않는다(데이터 값으로서의 금지어 목록은 예외).
- **색** — 프론트 CSS 는 `--c-*` 토큰만 쓴다. `.dark` 에서 토큰이 뒤집히므로 파일에 다크 규칙을
  따로 두지 않는다.
- **폴백 표시** — AI 분석이 실패해 템플릿을 보여줄 때 "AI 분석" 이라고 하지 않는다.

## Review Focus

스펙이 전제하지만 어느 태스크도 자연히 건드리지 않는 입력들. 각 줄의 시험을 해당 태스크에 넣었다.

1. **거래 0 회 · `equity_curve` 2 점 이하** — 백테스트가 신호를 못 찾은 경우. 지표 계산이
   0 으로 나누거나 터지지 않고, 표본 부족으로 처리돼야 한다. → Task 1, Task 2
2. **워크포워드 구간에 캔들이 없음** — 상장 전 구간이 섞이면 그 구간만 비고 나머지는 나와야
   한다. 전체가 실패하면 안 된다. → Task 3
3. **Google News 0 건 · 타임아웃** — 근거를 못 찾은 것은 정상 경로다. 예외가 호출자까지
   올라가면 안 되고 "못 찾음" 으로 내려와야 한다. → Task 7
4. **이상 구간이 0 개** — 변동이 거의 없는 종목. 근거 영역이 빈 채로 정상 응답해야 한다.
   → Task 6
5. **AI 가 경고를 무시하고 수익률부터 말함** — 경고 플래그가 있는데 첫 문장이 수익 자랑이면
   폴백으로 갈아친다. → Task 8

---

## File Structure

| 파일 | 책임 |
| --- | --- |
| `backend/app/engine/validation.py` | 순수 함수. `equity_curve` 재분석 지표 + 과최적화 판정 |
| `backend/app/engine/walkforward.py` | 캔들 1 회 수신 → DataFrame 분할 → `run_backtest` N 회 |
| `backend/app/news_archive.py` | `NewsHeadlineArchive` 적재 · 조회 |
| `backend/app/evidence.py` | 1·2·3 층 근거 수집 |
| `backend/app/validate_explain.py` | AI 분석 — 프롬프트 · 검증 · 캐시 · 폴백 |
| `backend/app/db.py` | `NewsHeadlineArchive` 테이블 + 마이그레이션 (수정) |
| `backend/app/main.py` | `/api/validate`, `/api/validate/explain` (수정) |
| `frontend/src/lib/validationView.js` | 검증 결과 → 화면용 값 (순수) |
| `frontend/src/pages/StudioPro.jsx` | 프로 빌더 화면 |
| `frontend/src/pages/StudioPro.css` | 화면 스타일 |
| `frontend/src/App.jsx` | `/studio/pro` 라우트 (수정) |

---

### Task 1: 지표 — `equity_curve` 재분석

**Files:**
- Create: `backend/app/engine/validation.py`
- Test: `backend/tests/test_validation_metrics.py`

**Interfaces:**
- Consumes: `app.engine.backtest.BacktestResult`, `EquityPoint`
- Produces:
  - `monthly_returns(curve: list[EquityPoint]) -> list[dict]` — `[{"month": "2026-03", "pct": 12.4}]`
  - `concentration(curve) -> dict` — `{"top_month_share_pct": float|None, "months": int}`
  - `drawdown_window(curve) -> dict|None` — `{"start": iso, "trough": iso, "recovered": iso|None, "depth_pct": float, "recovery_days": int|None}`
  - `sortino(curve) -> float|None`
  - `calmar(curve, mdd_pct: float) -> float|None`

- [ ] **Step 1: Write the failing test**

```python
"""equity_curve 만 다시 읽어 내는 지표 — 백테스트를 다시 돌리지 않는다."""
from app.engine.backtest import EquityPoint
from app.engine import validation


def curve(points):
    return [EquityPoint(t=t, equity=e) for t, e in points]


def test_monthly_returns_splits_the_curve_by_calendar_month():
    rows = validation.monthly_returns(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 110.0),
        ("2026-02-28T00:00:00Z", 99.0),
    ]))
    assert [r["month"] for r in rows] == ["2026-01", "2026-02"]
    assert rows[0]["pct"] == 10.0
    assert rows[1]["pct"] == -10.0


def test_concentration_reports_the_biggest_month_share():
    result = validation.concentration(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 180.0),
        ("2026-02-28T00:00:00Z", 190.0), ("2026-03-31T00:00:00Z", 200.0),
    ]))
    assert result["months"] == 3
    assert result["top_month_share_pct"] == 80.0


def test_drawdown_window_finds_depth_and_recovery():
    result = validation.drawdown_window(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-10T00:00:00Z", 60.0),
        ("2026-01-20T00:00:00Z", 100.0),
    ]))
    assert result["depth_pct"] == 40.0
    assert result["recovery_days"] == 10


def test_a_curve_that_never_recovers_reports_no_recovery():
    result = validation.drawdown_window(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-10T00:00:00Z", 60.0),
    ]))
    assert result["recovered"] is None and result["recovery_days"] is None


def test_too_short_a_curve_yields_no_metrics_instead_of_dividing_by_zero():
    """거래가 하나도 없으면 곡선이 비거나 두 점뿐이다 — 터지지 않아야 한다."""
    for short in ([], curve([("2026-01-01T00:00:00Z", 100.0)])):
        assert validation.monthly_returns(short) == []
        assert validation.concentration(short)["top_month_share_pct"] is None
        assert validation.drawdown_window(short) is None
        assert validation.sortino(short) is None
        assert validation.calmar(short, 0.0) is None


def test_calmar_is_none_when_there_was_no_drawdown():
    rows = curve([("2026-01-01T00:00:00Z", 100.0), ("2026-12-31T00:00:00Z", 200.0)])
    assert validation.calmar(rows, 0.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validation_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.validation'`

- [ ] **Step 3: Write the implementation**

```python
"""백테스트 결과를 다시 읽어 내는 검증 지표 — 엔진을 부르지 않는 순수 함수.

``equity_curve`` 하나로 월별 수익 · 집중도 · 낙폭 구간 · 소르티노 · 칼마를 낸다.
거래가 없어 곡선이 비거나 짧으면 지표 대신 None 을 돌려준다(0 으로 나누지 않는다).
"""
from __future__ import annotations

import math
from datetime import datetime

_DAY_SECONDS = 86_400.0


def _points(curve) -> list[tuple[datetime, float]]:
    out = []
    for point in curve or []:
        try:
            stamp = datetime.fromisoformat(str(point.t).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        out.append((stamp, float(point.equity)))
    return out


def monthly_returns(curve) -> list[dict]:
    """달력 월별 수익률(%). 각 달의 마지막 값끼리 비교한다."""
    points = _points(curve)
    if len(points) < 2:
        return []
    last_of_month: dict[str, float] = {}
    for stamp, equity in points:
        last_of_month[stamp.strftime("%Y-%m")] = equity
    months = sorted(last_of_month)
    base = points[0][1]
    rows = []
    for month in months:
        end = last_of_month[month]
        rows.append({"month": month,
                     "pct": round((end / base - 1.0) * 100.0, 2) if base > 0 else 0.0})
        base = end
    return rows


def concentration(curve) -> dict:
    """가장 많이 번 달이 전체 '번 돈' 에서 차지하는 몫."""
    rows = monthly_returns(curve)
    gains = [row["pct"] for row in rows if row["pct"] > 0]
    if not gains:
        return {"top_month_share_pct": None, "months": len(rows)}
    return {"top_month_share_pct": round(max(gains) / sum(gains) * 100.0, 2),
            "months": len(rows)}


def drawdown_window(curve) -> dict | None:
    """가장 깊었던 낙폭의 시작 · 바닥 · 회복 시점."""
    points = _points(curve)
    if len(points) < 2:
        return None
    peak_stamp, peak = points[0]
    best = None
    for stamp, equity in points:
        if equity > peak:
            peak_stamp, peak = stamp, equity
            continue
        depth = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        if best is None or depth > best["depth_pct"]:
            best = {"start": peak_stamp, "trough": stamp, "depth_pct": round(depth, 2),
                    "peak": peak}
    if best is None or best["depth_pct"] <= 0:
        return None
    recovered = None
    for stamp, equity in points:
        if stamp > best["trough"] and equity >= best["peak"]:
            recovered = stamp
            break
    days = None if recovered is None else round(
        (recovered - best["trough"]).total_seconds() / _DAY_SECONDS)
    return {"start": best["start"].isoformat(), "trough": best["trough"].isoformat(),
            "recovered": None if recovered is None else recovered.isoformat(),
            "depth_pct": best["depth_pct"], "recovery_days": days}


def _returns(points) -> list[float]:
    out = []
    for (_, before), (_, after) in zip(points, points[1:]):
        if before > 0:
            out.append(after / before - 1.0)
    return out


def sortino(curve) -> float | None:
    """하방 변동만으로 나눈 위험조정 수익 — 샤프보다 손실에 민감하다."""
    points = _points(curve)
    rets = _returns(points)
    if len(rets) < 2:
        return None
    downside = [r for r in rets if r < 0]
    if not downside:
        return None
    deviation = math.sqrt(sum(r * r for r in downside) / len(downside))
    if deviation <= 0:
        return None
    return round((sum(rets) / len(rets)) / deviation * math.sqrt(len(rets)), 2)


def calmar(curve, mdd_pct: float) -> float | None:
    """연수익 / 최대낙폭. 전문가가 먼저 보는 값이다."""
    points = _points(curve)
    if len(points) < 2 or mdd_pct is None or float(mdd_pct) <= 0:
        return None
    start, end = points[0][1], points[-1][1]
    if start <= 0:
        return None
    years = (points[-1][0] - points[0][0]).total_seconds() / (_DAY_SECONDS * 365.0)
    if years <= 0:
        return None
    annual = ((end / start) ** (1.0 / years) - 1.0) * 100.0
    return round(annual / float(mdd_pct), 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validation_metrics.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/validation.py backend/tests/test_validation_metrics.py
git commit -m "검증 지표 — equity_curve 재분석으로 월별 수익·집중도·낙폭·소르티노·칼마"
```

---

### Task 2: 과최적화 판정 (서버 규칙)

**Files:**
- Modify: `backend/app/engine/validation.py`
- Test: `backend/tests/test_validation_warnings.py`

**Interfaces:**
- Consumes: Task 1 의 `monthly_returns`, `concentration`
- Produces:
  - `WARNING_CODES: tuple[str, ...]` — `("한_구간_집중", "표본_부족", "후반부_음수", "거래_집중")`
  - `warnings(*, curve, total_trades, window_returns: list[float], top_trade_share_pct: float|None) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
"""과최적화 경고는 서버의 결정론적 규칙이 낸다 — AI 가 아니다.

경고가 헛돌면 사용자가 무시하게 되고 기능이 죽는다. 떠야 할 때 뜨고
안 떠야 할 때 안 뜨는 것을 골든 케이스로 고정한다.
"""
from app.engine.backtest import EquityPoint
from app.engine import validation


def curve(points):
    return [EquityPoint(t=t, equity=e) for t, e in points]


OVERFIT = curve([
    ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 280.0),
    ("2026-02-28T00:00:00Z", 285.0), ("2026-03-31T00:00:00Z", 288.0),
    ("2026-04-30T00:00:00Z", 290.0),
])
HEALTHY = curve([
    ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 112.0),
    ("2026-02-28T00:00:00Z", 121.0), ("2026-03-31T00:00:00Z", 133.0),
    ("2026-04-30T00:00:00Z", 147.0),
])


def test_an_overfit_curve_raises_every_warning():
    found = validation.warnings(curve=OVERFIT, total_trades=5,
                                window_returns=[180.0, 2.0, -4.0, -1.0],
                                top_trade_share_pct=74.0)
    assert set(found) == set(validation.WARNING_CODES)


def test_a_healthy_curve_raises_nothing():
    assert validation.warnings(curve=HEALTHY, total_trades=120,
                               window_returns=[9.0, 11.0, 8.0, 12.0],
                               top_trade_share_pct=14.0) == []


def test_each_rule_fires_on_its_own():
    assert validation.warnings(curve=OVERFIT, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) \
        == ["한_구간_집중"]
    assert validation.warnings(curve=HEALTHY, total_trades=9,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) \
        == ["표본_부족"]
    assert validation.warnings(curve=HEALTHY, total_trades=120,
                               window_returns=[9.0, -1.0], top_trade_share_pct=14.0) \
        == ["후반부_음수"]
    assert validation.warnings(curve=HEALTHY, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=51.0) \
        == ["거래_집중"]


def test_no_trades_is_sample_shortage_not_a_crash():
    """거래가 없으면 곡선도 비어 있다 — 터지지 않고 표본 부족으로 센다."""
    assert validation.warnings(curve=[], total_trades=0, window_returns=[],
                               top_trade_share_pct=None) == ["표본_부족"]


def test_missing_optional_inputs_do_not_invent_warnings():
    assert validation.warnings(curve=HEALTHY, total_trades=120, window_returns=[],
                               top_trade_share_pct=None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validation_warnings.py -v`
Expected: FAIL — `AttributeError: module 'app.engine.validation' has no attribute 'warnings'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/engine/validation.py`:

```python
# --- 과최적화 경고 -----------------------------------------------------------
# 임계값은 합성 곡선(tests/test_validation_warnings.py)으로 맞춘 값이다. 바꾸면
# 그 시험이 먼저 깨진다.
TOP_MONTH_SHARE_LIMIT = 70.0   # 한 달이 번 돈의 이만큼 이상이면 집중
MIN_TRADES = 10                # 이보다 적으면 통계로 못 쓴다
TOP_TRADE_SHARE_LIMIT = 50.0   # 상위 몇 거래가 수익의 절반 이상이면 운에 가깝다

WARNING_CODES = ("한_구간_집중", "표본_부족", "후반부_음수", "거래_집중")


def warnings(*, curve, total_trades: int, window_returns: list[float],
             top_trade_share_pct: float | None) -> list[str]:
    """켜진 경고 코드들. 순서는 WARNING_CODES 와 같다.

    입력이 없는 항목은 경고를 만들지 않는다 — 모르는 것과 나쁜 것은 다르다.
    """
    found = []
    share = concentration(curve)["top_month_share_pct"]
    if share is not None and share >= TOP_MONTH_SHARE_LIMIT:
        found.append("한_구간_집중")
    if int(total_trades or 0) < MIN_TRADES:
        found.append("표본_부족")
    if len(window_returns or []) >= 2 and float(window_returns[-1]) < 0:
        found.append("후반부_음수")
    if top_trade_share_pct is not None and float(top_trade_share_pct) >= TOP_TRADE_SHARE_LIMIT:
        found.append("거래_집중")
    return [code for code in WARNING_CODES if code in found]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validation_warnings.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/validation.py backend/tests/test_validation_warnings.py
git commit -m "과최적화 경고 — 서버 규칙 네 가지, 골든 케이스로 고정"
```

---

### Task 3: 워크포워드 — 캔들 1 회, 분할 실행

**Files:**
- Create: `backend/app/engine/walkforward.py`
- Test: `backend/tests/test_walkforward.py`

**Interfaces:**
- Consumes: `app.engine.backtest.run_backtest`, `app.engine.schema.Macro`
- Produces:
  - `split_frame(df, windows: int) -> list[DataFrame]`
  - `run_windows(macro: Macro, df, windows: int = 4) -> list[dict]` —
    `[{"index": 1, "start": iso, "end": iso, "return_pct": float|None, "trades": int, "error": str}]`

- [ ] **Step 1: Write the failing test**

```python
"""워크포워드는 캔들을 한 번만 받아 DataFrame 을 잘라 돌린다 — 네트워크 재호출 없음."""
import pandas as pd
import pytest

pytest.importorskip("pandas")

from app.engine import walkforward
from app.engine.schema import Macro


def frame(rows: int) -> pd.DataFrame:
    stamps = pd.date_range("2026-01-01", periods=rows, freq="1D", tz="UTC")
    price = [100.0 + i for i in range(rows)]
    return pd.DataFrame({"open_time": stamps, "open": price, "high": [p * 1.01 for p in price],
                         "low": [p * 0.99 for p in price], "close": price,
                         "volume": [10.0] * rows})


def macro() -> Macro:
    return Macro(symbol="BTCUSDT", rule_type="A", candle_interval="1d",
                 params={"take_profit_pct": 5, "initial_capital": 1_000_000})


def test_split_frame_divides_rows_evenly_and_keeps_every_row():
    parts = walkforward.split_frame(frame(100), 4)
    assert len(parts) == 4
    assert sum(len(part) for part in parts) == 100


def test_split_frame_refuses_more_windows_than_usable_rows():
    parts = walkforward.split_frame(frame(6), 4)
    assert len(parts) <= 3, "구간마다 최소 두 행은 있어야 수익률을 낸다"


def test_run_windows_reports_one_row_per_window_in_order():
    rows = walkforward.run_windows(macro(), frame(120), windows=4)
    assert [row["index"] for row in rows] == [1, 2, 3, 4]
    assert all(row["start"] < row["end"] for row in rows)


def test_a_window_without_candles_fails_alone(monkeypatch):
    """상장 전 구간이 섞이면 그 구간만 비고 나머지는 나와야 한다."""
    calls = {"n": 0}
    real = walkforward.run_backtest

    def flaky(macro_arg, df):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("no candles")
        return real(macro_arg, df)

    monkeypatch.setattr(walkforward, "run_backtest", flaky)
    rows = walkforward.run_windows(macro(), frame(120), windows=4)
    assert len(rows) == 4
    assert rows[1]["return_pct"] is None and rows[1]["error"]
    assert rows[0]["return_pct"] is not None and rows[3]["return_pct"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_walkforward.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.walkforward'`

- [ ] **Step 3: Write the implementation**

```python
"""워크포워드 — 같은 매크로를 기간 구간별로 돌려 안정성을 본다.

캔들은 호출자가 한 번만 받아 넘긴다. 구간마다 다시 받지 않는다(네트워크 비용이
구간 수만큼 늘어나고, 구간 경계가 어긋날 수 있다). 한 구간이 실패해도 나머지는
그대로 돌려준다 — 상장 전 구간이 섞이는 일이 흔하다.
"""
from __future__ import annotations

import logging

from .backtest import run_backtest
from .schema import Macro

logger = logging.getLogger(__name__)

MIN_ROWS_PER_WINDOW = 2
DEFAULT_WINDOWS = 4


def split_frame(df, windows: int) -> list:
    """행 수가 모자라면 구간 수를 줄인다 — 빈 구간을 만들지 않는다."""
    rows = len(df)
    count = max(1, min(int(windows), rows // MIN_ROWS_PER_WINDOW))
    if count <= 1:
        return [df] if rows >= MIN_ROWS_PER_WINDOW else []
    size = rows // count
    parts = [df.iloc[i * size:(i + 1) * size] for i in range(count - 1)]
    parts.append(df.iloc[(count - 1) * size:])
    return parts


def _edge(part, column: str = "open_time"):
    try:
        return str(part[column].iloc[0]), str(part[column].iloc[-1])
    except (KeyError, IndexError):
        return "", ""


def run_windows(macro: Macro, df, windows: int = DEFAULT_WINDOWS) -> list[dict]:
    """구간별 성과. 실패한 구간은 return_pct=None 과 error 로 남는다."""
    rows = []
    for index, part in enumerate(split_frame(df, windows), start=1):
        start, end = _edge(part)
        try:
            result = run_backtest(macro, part)
        except Exception as exc:  # noqa: BLE001 — 한 구간 실패가 전체를 막지 않는다
            logger.warning("walk-forward window failed: index=%d reason=%s",
                           index, type(exc).__name__)
            rows.append({"index": index, "start": start, "end": end,
                         "return_pct": None, "trades": 0, "error": type(exc).__name__})
            continue
        rows.append({"index": index, "start": start, "end": end,
                     "return_pct": round(float(result.final_return_pct), 2),
                     "trades": int(result.total_trades), "error": ""})
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_walkforward.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/walkforward.py backend/tests/test_walkforward.py
git commit -m "워크포워드 — 캔들 한 번 받아 구간별로 돌리고, 실패 구간은 혼자 빈다"
```

---

### Task 4: `POST /api/validate`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_validate_api.py`

**Interfaces:**
- Consumes: Task 1~3 (`validation.monthly_returns` 등, `walkforward.run_windows`)
- Produces: 응답 모양
  `{"result": {...}, "monthly": [...], "concentration": {...}, "drawdown": {...},
    "sortino": float|None, "calmar": float|None, "windows": [...], "warnings": [...]}`

- [ ] **Step 1: Write the failing test**

```python
"""검증 API 는 백테스트 결과에 검증 레이어를 얹어 한 번에 돌려준다."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MACRO = {"symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
         "params": {"take_profit_pct": 5, "initial_capital": 1000000},
         "risk": {"stop_loss_pct": 3}, "period": {"preset": "1y"}}


def test_validate_returns_metrics_warnings_and_windows():
    response = client.post("/api/validate", json={"macro": MACRO, "windows": 4})
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"result", "monthly", "concentration", "drawdown",
                         "sortino", "calmar", "windows", "warnings"}
    assert isinstance(body["warnings"], list)
    assert len(body["windows"]) <= 4


def test_window_count_is_bounded():
    response = client.post("/api/validate", json={"macro": MACRO, "windows": 999})
    assert response.status_code == 422


def test_an_invalid_macro_is_rejected_in_korean():
    bad = {**MACRO, "params": {}}
    response = client.post("/api/validate", json={"macro": bad, "windows": 4})
    assert response.status_code == 422
    assert response.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validate_api.py -v`
Expected: FAIL — 404 (route not defined)

- [ ] **Step 3: Write the implementation**

Add to `backend/app/main.py` near the other backtest routes:

```python
from .engine import validation as validation_mod
from .engine import walkforward as walkforward_mod


class ValidateIn(BaseModel):
    macro: dict
    windows: int = Field(default=walkforward_mod.DEFAULT_WINDOWS, ge=2, le=12)


@app.post("/api/validate")
def validate_macro(body: ValidateIn) -> dict:
    """백테스트 + 검증 레이어. 판정은 서버가 내고 AI 는 부르지 않는다."""
    try:
        macro = Macro(**body.macro)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"매크로 설정을 확인해 주세요: {exc}") from exc

    start_ms, end_ms = resolve_period(macro.period.preset, macro.period.start, macro.period.end)
    df, _source = fetch_klines_for_macro(macro, start_ms, end_ms)
    result = run_backtest(macro, df)
    windows = walkforward_mod.run_windows(macro, df, body.windows)
    window_returns = [w["return_pct"] for w in windows if w["return_pct"] is not None]
    curve = result.equity_curve
    return {
        "result": compact_backtest_result(result),
        "monthly": validation_mod.monthly_returns(curve),
        "concentration": validation_mod.concentration(curve),
        "drawdown": validation_mod.drawdown_window(curve),
        "sortino": validation_mod.sortino(curve),
        "calmar": validation_mod.calmar(curve, result.mdd_pct),
        "windows": windows,
        "warnings": validation_mod.warnings(
            curve=curve, total_trades=result.total_trades,
            window_returns=window_returns,
            top_trade_share_pct=result.top_trade_share_pct),
    }
```

`result.top_trade_share_pct` 는 아직 없다. 다음 단계에서 더한다.

- [ ] **Step 4: 거래 집중도를 결과에 더하는 실패 시험을 쓴다**

`거래_집중` 경고는 거래별 손익이 있어야 나온다. 엔진 안에는 이미
`closed_trades: List[float]` 이 있고 `_profit_factor` 와 `_max_consecutive_losses` 가
그것을 쓴다. 다만 `BacktestResult` 로 나오지 않아 지금 구조로는 **그 경고가 영원히 뜨지
않는다.**

필드를 하나 **더하기만** 한다. 기존 값은 하나도 바뀌지 않으므로 "엔진 불변" 원칙과
어긋나지 않는다. 그것을 시험으로 고정한다.

`backend/tests/test_backtest_trade_share.py`:

```python
"""거래 집중도 — 상위 세 거래가 수익에서 차지하는 몫. 기존 값은 그대로여야 한다."""
from app.engine.backtest import _summarize_trade_share


def test_a_few_big_winners_show_a_high_share():
    assert _summarize_trade_share([80.0, 10.0, 5.0, 3.0, 2.0]) == 95.0


def test_evenly_spread_wins_show_a_low_share():
    assert _summarize_trade_share([10.0] * 20) == 15.0


def test_losses_do_not_count_toward_the_denominator():
    assert _summarize_trade_share([50.0, 50.0, -40.0]) == 100.0


def test_no_winning_trades_yields_none():
    assert _summarize_trade_share([]) is None
    assert _summarize_trade_share([-5.0, -3.0]) is None
```

- [ ] **Step 5: 시험이 실패하는 것을 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_backtest_trade_share.py -v`
Expected: FAIL — `ImportError: cannot import name '_summarize_trade_share'`

- [ ] **Step 6: 엔진에 필드를 더한다 (더하기만)**

`backend/app/engine/backtest.py` 에:

```python
TOP_TRADES = 3


def _summarize_trade_share(closed_trades: List[float]) -> Optional[float]:
    """상위 세 거래가 '번 돈' 에서 차지하는 몫(%). 이긴 거래가 없으면 None.

    한두 번의 대박이 수익의 대부분이면 전략이 아니라 운에 가깝다.
    """
    wins = sorted((pnl for pnl in closed_trades if pnl > 0), reverse=True)
    total = sum(wins)
    if not wins or total <= 0:
        return None
    return round(sum(wins[:TOP_TRADES]) / total * 100.0, 2)
```

`BacktestResult` 에 필드를 더한다(기본값이 있으므로 기존 호출부는 그대로 돈다):

```python
    # 상위 세 거래가 번 돈에서 차지하는 몫(%). 이긴 거래가 없으면 None.
    top_trade_share_pct: Optional[float] = None
```

결과를 만드는 자리에서 `top_trade_share_pct=_summarize_trade_share(closed_trades)` 를
넘긴다. **다른 필드의 계산은 건드리지 않는다.**

- [ ] **Step 7: 시험이 통과하고 기존 백테스트 시험도 그대로인지 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_backtest_trade_share.py tests/test_backtest.py -v`
Expected: PASS — 새 시험 4 건, 기존 백테스트 시험 전부 그대로

- [ ] **Step 8: 검증 API 시험이 통과하는지 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validate_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Commit**

```bash
git add backend/app/main.py backend/app/engine/backtest.py         backend/tests/test_validate_api.py backend/tests/test_backtest_trade_share.py
git commit -m "POST /api/validate — 검증 레이어와 거래 집중도까지 한 번에 돌려준다"
```

---

### Task 5: 뉴스 제목 아카이브

**Files:**
- Create: `backend/app/news_archive.py`
- Modify: `backend/app/db.py`
- Create: `supabase/migrations/20261002090000_news_headline_archive.sql`
- Test: `backend/tests/test_news_archive.py`

**Interfaces:**
- Produces:
  - `store(asset: str, items: list[dict]) -> int` — `items` 는 `{"published_ms", "title", "source", "url"}`
  - `lookup(asset: str, *, start_ms: int, end_ms: int, limit: int = 5) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
"""근거용 경량 아카이브 — 제목·날짜·출처·URL 만 무기한 보관한다(본문은 기존대로 30 일)."""
import pytest

pytest.importorskip("sqlmodel")

from sqlmodel import Session, SQLModel, create_engine

from app import news_archive
from app.db import NewsHeadlineArchive

DAY = 86_400_000
BASE = 1_800_000_000_000


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'archive.db'}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(news_archive, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def item(offset_days, title):
    return {"published_ms": BASE + offset_days * DAY, "title": title,
            "source": "CoinDesk", "url": f"https://example.test/{offset_days}"}


def test_store_then_lookup_returns_items_inside_the_window(db):
    assert news_archive.store("BTC", [item(0, "첫날"), item(5, "닷새 뒤")]) == 2
    found = news_archive.lookup("BTC", start_ms=BASE - DAY, end_ms=BASE + DAY)
    assert [row["title"] for row in found] == ["첫날"]


def test_storing_the_same_url_twice_does_not_duplicate(db):
    news_archive.store("BTC", [item(0, "첫날")])
    news_archive.store("BTC", [item(0, "첫날")])
    assert len(news_archive.lookup("BTC", start_ms=BASE - DAY, end_ms=BASE + DAY)) == 1


def test_lookup_is_scoped_to_the_asset(db):
    news_archive.store("BTC", [item(0, "비트 기사")])
    assert news_archive.lookup("ETH", start_ms=BASE - DAY, end_ms=BASE + DAY) == []


def test_lookup_caps_the_number_of_rows(db):
    news_archive.store("BTC", [item(0, f"기사 {i}") for i in range(20)])
    assert len(news_archive.lookup("BTC", start_ms=BASE - DAY, end_ms=BASE + DAY, limit=5)) == 5


def test_items_without_a_title_or_url_are_skipped(db):
    assert news_archive.store("BTC", [{"published_ms": BASE, "title": "", "source": "", "url": ""}]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_archive.py -v`
Expected: FAIL — `ImportError: cannot import name 'NewsHeadlineArchive' from 'app.db'`

- [ ] **Step 3: Write the implementation**

Add to `backend/app/db.py` beside the other news tables:

```python
class NewsHeadlineArchive(SQLModel, table=True):
    """근거용 제목 아카이브 — 본문 없이 제목·날짜·출처만 무기한 보관한다.

    기사 본문과 요약은 기존대로 30 일 뒤 지워진다(collector.run_maintenance).
    백테스트의 과거 구간에 근거를 붙이려면 그 뒤로도 제목이 남아 있어야 한다.
    행당 약 200 바이트라 하루 200 건이어도 연 15MB 수준이다.
    """

    archive_key: str = Field(primary_key=True, max_length=64)  # sha256(asset+url)
    asset_symbol: str = Field(max_length=20, index=True)
    published_ms: int = Field(default=0, sa_type=BigInteger, index=True)
    title: str = ""
    source: str = Field(default="", max_length=120)
    url: str = ""
```

Add the migration entries in `db.py` (`_PG_ADDED_COLUMNS` needs no entry for a new table;
`_PG_BIGINT_COLUMNS` gets one) and write the SQL file:

```sql
-- 근거용 뉴스 제목 아카이브. 서버가 기동 때 자동으로도 만들지만 기록을 남긴다.
CREATE TABLE IF NOT EXISTS newsheadlinearchive (
  archive_key VARCHAR(64) PRIMARY KEY,
  asset_symbol VARCHAR(20) NOT NULL DEFAULT '',
  published_ms BIGINT NOT NULL DEFAULT 0,
  title TEXT NOT NULL DEFAULT '',
  source VARCHAR(120) NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_newsheadlinearchive_asset_published
  ON newsheadlinearchive (asset_symbol, published_ms);
```

Create `backend/app/news_archive.py`:

```python
"""근거용 제목 아카이브 — 앞으로는 수집이, 뒤로는 과거 조회가 채운다."""
from __future__ import annotations

import hashlib

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

from .db import NewsHeadlineArchive, get_session

MAX_ROWS = 5


def _key(asset: str, url: str) -> str:
    return hashlib.sha256(f"{asset}\n{url}".encode("utf-8")).hexdigest()


def store(asset: str, items: list[dict]) -> int:
    """제목이 있고 URL 이 있는 것만 적재한다. 같은 URL 은 한 번만."""
    asset = str(asset or "").strip().upper()
    rows = []
    seen = set()
    for item in items or []:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not asset or not url or not title:
            continue
        key = _key(asset, url)
        if key in seen:
            continue
        seen.add(key)
        rows.append(dict(archive_key=key, asset_symbol=asset,
                         published_ms=int(item.get("published_ms") or 0),
                         title=title[:500], source=str(item.get("source") or "")[:120],
                         url=url[:1000]))
    if not rows:
        return 0
    with get_session() as db:
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        db.exec(insert(NewsHeadlineArchive).values(rows).on_conflict_do_nothing(
            index_elements=[NewsHeadlineArchive.archive_key]))
        db.commit()
    return len(rows)


def lookup(asset: str, *, start_ms: int, end_ms: int, limit: int = MAX_ROWS) -> list[dict]:
    """구간 안의 제목들. 없으면 빈 목록 — 그게 정상 경로다."""
    asset = str(asset or "").strip().upper()
    if not asset or end_ms < start_ms:
        return []
    with get_session() as db:
        rows = db.exec(select(NewsHeadlineArchive).where(
            NewsHeadlineArchive.asset_symbol == asset,
            NewsHeadlineArchive.published_ms >= int(start_ms),
            NewsHeadlineArchive.published_ms <= int(end_ms),
        ).order_by(NewsHeadlineArchive.published_ms).limit(max(1, int(limit)))).all()
    return [{"title": row.title, "source": row.source, "url": row.url,
             "published_ms": row.published_ms} for row in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_archive.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/news_archive.py backend/app/db.py \
        supabase/migrations/20261002090000_news_headline_archive.sql \
        backend/tests/test_news_archive.py
git commit -m "뉴스 제목 아카이브 — 근거용으로 제목·날짜·출처만 무기한 보관"
```

---

### Task 6: 근거 1층 — 시장 대비

**Files:**
- Create: `backend/app/evidence.py`
- Test: `backend/tests/test_evidence_market.py`

**Interfaces:**
- Produces:
  - `anomalies(df, *, sigma: float = 2.0, limit: int = 5) -> list[dict]` —
    `[{"date": "2026-03-14", "change_pct": 31.2, "volume_ratio": 8.1}]`
  - `market_context(rows: list[dict], btc_df) -> list[dict]` — 각 행에 `btc_change_pct`,
    `verdict` (`"시장"` | `"종목"`) 추가

- [ ] **Step 1: Write the failing test**

```python
"""1 층 근거 — 뉴스보다 먼저 '시장 베타인가 종목 알파인가' 를 가린다."""
import pandas as pd
import pytest

pytest.importorskip("pandas")

from app import evidence


def frame(closes, volumes=None):
    stamps = pd.date_range("2026-03-01", periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"open_time": stamps, "close": closes,
                         "volume": volumes or [10.0] * len(closes)})


def test_anomalies_picks_the_days_that_moved_far_more_than_usual():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    rows = evidence.anomalies(frame(closes))
    assert rows and rows[0]["date"] == "2026-03-21"
    assert rows[0]["change_pct"] > 30


def test_a_flat_coin_has_no_anomalies():
    """변동이 거의 없으면 근거 영역이 빈 채로 정상이어야 한다."""
    assert evidence.anomalies(frame([100.0] * 30)) == []


def test_anomalies_are_capped_and_sorted_by_size():
    closes = [100.0] * 10
    for bump in (40.0, 25.0, 60.0, 30.0, 50.0, 35.0):
        closes += [closes[-1] * (1 + bump / 100.0)]
        closes += [closes[-1]] * 3
    rows = evidence.anomalies(frame(closes), limit=3)
    assert len(rows) == 3
    assert rows[0]["change_pct"] >= rows[1]["change_pct"] >= rows[2]["change_pct"]


def test_volume_ratio_compares_against_the_trailing_average():
    closes = [100.0] * 20 + [131.0]
    volumes = [10.0] * 20 + [80.0]
    rows = evidence.anomalies(frame(closes, volumes))
    assert rows[0]["volume_ratio"] == 8.0


def test_market_context_separates_a_market_day_from_a_coin_day():
    rows = [{"date": "2026-03-21", "change_pct": 31.0, "volume_ratio": 8.0},
            {"date": "2026-03-25", "change_pct": 9.0, "volume_ratio": 1.2}]
    btc = frame([100.0] * 20 + [131.0] + [139.0] * 9)
    out = evidence.market_context(rows, btc)
    assert out[0]["verdict"] == "시장"      # BTC 도 같이 31% 올랐다
    assert out[1]["verdict"] == "종목"      # BTC 는 조용한데 이 종목만
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_evidence_market.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.evidence'`

- [ ] **Step 3: Write the implementation**

```python
"""근거 — 백테스트에서 튄 날이 왜 튀었는지 찾는다.

1 층(이 파일의 anomalies/market_context)은 이미 받고 있는 캔들만 쓰므로 전 구간에서
항상 된다. "왜 올랐나" 보다 "시장 전체인가 이 종목인가" 가 먼저 할 질문이다.
"""
from __future__ import annotations

import statistics

SIGMA = 2.0
MAX_ANOMALIES = 5
VOLUME_LOOKBACK = 20
MARKET_SHARE = 0.5  # BTC 가 이 종목 변동의 절반 이상을 설명하면 '시장'


def _series(df):
    try:
        stamps = [str(value)[:10] for value in df["open_time"]]
        closes = [float(value) for value in df["close"]]
    except (KeyError, TypeError, ValueError):
        return [], [], []
    try:
        volumes = [float(value) for value in df["volume"]]
    except (KeyError, TypeError, ValueError):
        volumes = [0.0] * len(closes)
    return stamps, closes, volumes


def _changes(closes) -> list[float]:
    return [(after / before - 1.0) * 100.0 if before > 0 else 0.0
            for before, after in zip(closes, closes[1:])]


def anomalies(df, *, sigma: float = SIGMA, limit: int = MAX_ANOMALIES) -> list[dict]:
    """일간 변동이 표준편차의 sigma 배를 넘는 날들. 큰 순으로 최대 limit 개."""
    stamps, closes, volumes = _series(df)
    changes = _changes(closes)
    if len(changes) < VOLUME_LOOKBACK // 2:
        return []
    try:
        deviation = statistics.pstdev(changes)
    except statistics.StatisticsError:
        return []
    if deviation <= 0:
        return []
    threshold = deviation * float(sigma)
    rows = []
    for index, change in enumerate(changes):
        if abs(change) < threshold:
            continue
        day = index + 1
        window = volumes[max(0, day - VOLUME_LOOKBACK):day]
        average = sum(window) / len(window) if window else 0.0
        rows.append({"date": stamps[day], "change_pct": round(change, 2),
                     "volume_ratio": round(volumes[day] / average, 2) if average > 0 else None})
    rows.sort(key=lambda row: abs(row["change_pct"]), reverse=True)
    return rows[:max(1, int(limit))]


def market_context(rows: list[dict], btc_df) -> list[dict]:
    """같은 날 BTC 가 얼마나 움직였나로 '시장' 과 '종목' 을 가른다."""
    stamps, closes, _volumes = _series(btc_df)
    changes = _changes(closes)
    by_date = {stamps[index + 1]: change for index, change in enumerate(changes)}
    out = []
    for row in rows or []:
        btc = by_date.get(row["date"])
        verdict = ""
        if btc is not None and row["change_pct"]:
            verdict = "시장" if abs(btc) >= abs(row["change_pct"]) * MARKET_SHARE else "종목"
        out.append({**row,
                    "btc_change_pct": None if btc is None else round(btc, 2),
                    "verdict": verdict})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_evidence_market.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/evidence.py backend/tests/test_evidence_market.py
git commit -m "근거 1층 — 이상 구간과 시장 대비 판정(시장인가 종목인가)"
```

---

### Task 7: 근거 2·3층 — 우리 DB 와 과거 뉴스 조회

**Files:**
- Modify: `backend/app/evidence.py`
- Test: `backend/tests/test_evidence_news.py`

**Interfaces:**
- Consumes: `app.news_archive.lookup` / `.store`
- Produces:
  - `headlines(asset: str, date: str) -> dict` —
    `{"items": [...], "found": bool, "reason": "" | "보존_범위_밖" | "조회_실패"}`
  - `fetch_historical(asset: str, date: str) -> list[dict]` — Google News `after:`/`before:`

- [ ] **Step 1: Write the failing test**

```python
"""2·3 층 근거 — DB 에 없으면 과거 뉴스를 조회하고, 그 결과가 아카이브를 채운다.

못 찾는 것은 정상 경로다. 예외가 호출자까지 올라가면 안 되고, 없는 기사를
지어내서도 안 된다.
"""
import pytest

from app import evidence

DATE = "2026-03-14"


def test_db_hit_does_not_touch_the_network(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup",
                        lambda *a, **k: [{"title": "상장 공지", "source": "CoinDesk",
                                          "url": "https://x.test/1", "published_ms": 1}])
    def explode(*a, **k):
        raise AssertionError("DB 에 있으면 외부 조회를 하면 안 된다")
    monkeypatch.setattr(evidence, "fetch_historical", explode)

    found = evidence.headlines("BTC", DATE)
    assert found["found"] is True and found["items"][0]["title"] == "상장 공지"


def test_a_miss_falls_through_to_historical_search_and_fills_the_archive(monkeypatch):
    stored = {}
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    monkeypatch.setattr(evidence, "fetch_historical",
                        lambda asset, date: [{"title": "그날 기사", "source": "Decrypt",
                                              "url": "https://x.test/2", "published_ms": 2}])
    monkeypatch.setattr(evidence.news_archive, "store",
                        lambda asset, items: stored.setdefault(asset, items) and len(items))

    found = evidence.headlines("BTC", DATE)
    assert found["found"] is True
    assert stored["BTC"][0]["title"] == "그날 기사", "조회 결과가 아카이브를 채워야 한다"


def test_nothing_found_reports_a_reason_instead_of_raising(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    monkeypatch.setattr(evidence, "fetch_historical", lambda asset, date: [])
    found = evidence.headlines("BTC", DATE)
    assert found == {"items": [], "found": False, "reason": "보존_범위_밖"}


def test_a_network_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    def boom(asset, date):
        raise TimeoutError("slow")
    monkeypatch.setattr(evidence, "fetch_historical", boom)
    found = evidence.headlines("BTC", DATE)
    assert found["found"] is False and found["reason"] == "조회_실패"


def test_historical_query_uses_an_exclusive_date_window():
    query = evidence.historical_query("BTC", DATE)
    assert "after:2026-03-13" in query and "before:2026-03-16" in query
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_evidence_news.py -v`
Expected: FAIL — `AttributeError: module 'app.evidence' has no attribute 'headlines'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/evidence.py`:

```python
# --- 2·3 층 근거 -------------------------------------------------------------
import logging
from datetime import datetime, timedelta, timezone

from . import news_archive

logger = logging.getLogger(__name__)

HEADLINE_WINDOW_DAYS = 1
_NO_NEWS = {"items": [], "found": False, "reason": "보존_범위_밖"}


def _day_bounds(date: str) -> tuple[int, int]:
    day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    start = day - timedelta(days=HEADLINE_WINDOW_DAYS)
    end = day + timedelta(days=HEADLINE_WINDOW_DAYS)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def historical_query(asset: str, date: str) -> str:
    """Google News 검색의 날짜 연산자. after/before 는 경계를 포함하지 않는다."""
    day = datetime.fromisoformat(date)
    after = (day - timedelta(days=HEADLINE_WINDOW_DAYS + 1)).strftime("%Y-%m-%d")
    before = (day + timedelta(days=HEADLINE_WINDOW_DAYS + 1)).strftime("%Y-%m-%d")
    return f"{asset} crypto after:{after} before:{before}"


def fetch_historical(asset: str, date: str) -> list[dict]:
    """과거 구간 뉴스. 기존 Google News 페처를 쿼리만 바꿔 쓴다."""
    from . import news as news_mod

    return news_mod.fetch_google_news_items(historical_query(asset, date))


def headlines(asset: str, date: str) -> dict:
    """그 날짜의 제목들. 2 층(DB) 먼저, 없으면 3 층(과거 조회).

    못 찾는 것은 정상 경로다 — 예외를 올리지 않고 이유를 담아 돌려준다.
    """
    start_ms, end_ms = _day_bounds(date)
    cached = news_archive.lookup(asset, start_ms=start_ms, end_ms=end_ms)
    if cached:
        return {"items": cached, "found": True, "reason": ""}
    try:
        fetched = fetch_historical(asset, date)
    except Exception as exc:  # noqa: BLE001 — 근거를 못 찾는 것이 기능을 막지 않는다
        logger.warning("historical headline lookup failed: asset=%s date=%s reason=%s",
                       asset, date, type(exc).__name__)
        return {"items": [], "found": False, "reason": "조회_실패"}
    if not fetched:
        return dict(_NO_NEWS)
    news_archive.store(asset, fetched)
    return {"items": fetched[:news_archive.MAX_ROWS], "found": True, "reason": ""}
```

`backend/app/news.py` 에는 공개 헬퍼가 없다 — `fetch_google()` 이 다른 함수 안의 중첩
함수다(약 4286 행). 그 안의 요청·파싱을 모듈 수준 `fetch_google_news_items(query: str)
-> list[dict]` 로 끌어내고 기존 호출부가 그것을 쓰게 바꾼다. 반환은
`[{"title", "source", "url", "published_ms"}]`, 전송 실패는 예외로 올린다. 기존 뉴스
시험(`tests/test_news.py`)이 그대로 통과해야 한다.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_evidence_news.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/evidence.py backend/app/news.py backend/tests/test_evidence_news.py
git commit -m "근거 2·3층 — DB 먼저, 없으면 과거 조회. 못 찾으면 못 찾았다고 한다"
```

---

### Task 8: AI 분석

**Files:**
- Create: `backend/app/validate_explain.py`
- Test: `backend/tests/test_validate_explain.py`

**Interfaces:**
- Consumes: `app.ai_runtime.get_ai_client`, `ai_cache_key`, `get_ai_runtime`
- Produces:
  - `BANNED_WORDS: tuple[str, ...]`
  - `explain(payload: dict) -> dict` — `{"text": str, "source": "ai" | "fallback"}`

- [ ] **Step 1: Write the failing test**

```python
"""분석은 좋은 말을 하지 않는다 — 경고가 있으면 먼저 말하고, 금지어는 걸러진다."""
from app import validate_explain

PAYLOAD = {"final_return_pct": 142.0, "buy_hold_return_pct": 38.0, "mdd_pct": 31.0,
           "total_trades": 14, "calmar": 1.2, "sortino": 0.9,
           "windows": [180.0, 5.0, -12.0, 3.0],
           "warnings": ["한_구간_집중", "표본_부족"],
           "evidence": []}


def test_banned_words_cover_the_new_pro_vocabulary():
    for word in ("추천", "보장", "확실", "무조건", "유망", "안전", "좋은 전략"):
        assert word in validate_explain.BANNED_WORDS


def test_a_banned_word_in_the_answer_falls_back(monkeypatch):
    monkeypatch.setattr(validate_explain, "_ask_model",
                        lambda payload: "이 전략은 유망합니다. 거래가 14 회입니다.")
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"


def test_an_answer_that_leads_with_profit_falls_back(monkeypatch):
    """경고가 있는데 수익률부터 말하면 안 된다."""
    monkeypatch.setattr(validate_explain, "_ask_model",
                        lambda payload: "수익률 142% 로 홀딩을 크게 앞섰습니다. 다만 표본이 적습니다.")
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"


def test_a_warning_first_answer_is_kept(monkeypatch):
    monkeypatch.setattr(validate_explain, "_ask_model",
                        lambda payload: "거래가 14 회뿐이라 통계로 쓰기 어렵습니다. 수익의 대부분이 첫 구간에서 나왔습니다.")
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "ai"


def test_the_fallback_never_claims_to_be_ai(monkeypatch):
    def boom(payload):
        raise RuntimeError("provider down")
    monkeypatch.setattr(validate_explain, "_ask_model", boom)
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"
    assert "AI" not in out["text"]
    assert "표본" in out["text"], "서버 판정은 폴백에서도 읽혀야 한다"


def test_evidence_is_never_invented(monkeypatch):
    """서버가 근거를 못 찾았으면 AI 가 기사 제목을 지어내도 버린다."""
    monkeypatch.setattr(validate_explain, "_ask_model",
                        lambda payload: '거래가 적습니다. 「바이낸스 상장 공지」 기사가 있었습니다.')
    out = validate_explain.explain({**PAYLOAD, "evidence": []})
    assert out["source"] == "fallback"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validate_explain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.validate_explain'`

- [ ] **Step 3: Write the implementation**

```python
"""검증 결과를 사람 말로 옮긴다 — 판정은 이미 서버가 내렸다.

이 분석은 좋은 말을 하면 안 된다. 수익률이 높아도 표본이 모자라면 그렇게 말한다.
칭찬하는 순간 도구가 아니라 장난감이 된다.
"""
from __future__ import annotations

import json
import logging

from .ai_runtime import ai_available, ai_cache_key, default_model, get_ai_client, get_ai_runtime

logger = logging.getLogger(__name__)

BANNED_WORDS = ("추천", "보장", "확실", "무조건", "유망", "안전", "좋은 전략")
_PROFIT_LEAD = ("수익률", "수익 ", "벌었", "앞섰")
_QUOTE_MARKS = ("「", "」", "“", "”")
_PROMPT_VERSION = "validate-explain-v1"

_WARNING_TEXT = {
    "한_구간_집중": "수익 대부분이 한 구간에서 나왔습니다",
    "표본_부족": "거래 수가 적어 통계로 쓰기 어렵습니다",
    "후반부_음수": "뒤쪽 구간에서 성과가 음수입니다",
    "거래_집중": "소수의 거래가 수익의 절반 이상을 만들었습니다",
}

_SYSTEM = (
    "너는 백테스트 검증 결과를 설명하는 분석가야. 주어진 숫자와 경고만 쓰고 "
    "없는 사실을 만들지 마. 경고가 있으면 반드시 그것부터 말해. 수익률 자랑으로 "
    "시작하지 마. 전략을 쓰라거나 피하라는 말은 하지 마. 기사 제목은 입력으로 "
    "받은 것만 인용하고, 입력에 없으면 인용하지 마. 3~5 문장, 한국어."
)


def _fallback(payload: dict) -> str:
    lines = [_WARNING_TEXT[code] for code in payload.get("warnings") or []
             if code in _WARNING_TEXT]
    if not lines:
        lines = ["서버 판정에서 걸린 항목은 없습니다"]
    return " · ".join(lines) + "."


def _ask_model(payload: dict) -> str:
    response = get_ai_client().messages.create(
        model=default_model(), max_tokens=700, system=_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        purpose="validate_explain",
    )
    for block in response.content:
        if getattr(block, "type", None) == "text" and str(block.text or "").strip():
            return str(block.text).strip()
    raise ValueError("empty analysis response")


def _acceptable(text: str, payload: dict) -> bool:
    if not text or len(text) > 1200:
        return False
    if any(word in text for word in BANNED_WORDS):
        return False
    head = text.split(".")[0]
    if payload.get("warnings") and any(lead in head for lead in _PROFIT_LEAD):
        return False
    if not payload.get("evidence") and any(mark in text for mark in _QUOTE_MARKS):
        return False
    return True


def explain(payload: dict) -> dict:
    """{"text", "source"}. source 가 fallback 이면 화면에 'AI 분석' 이라 쓰지 않는다."""
    if not ai_available():
        return {"text": _fallback(payload), "source": "fallback"}
    key = ai_cache_key("validate-explain", _PROMPT_VERSION, default_model(), payload)
    try:
        text = get_ai_runtime().call(key, lambda: _ask_model(payload), retries=0)[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("validation analysis failed: reason=%s", type(exc).__name__)
        return {"text": _fallback(payload), "source": "fallback"}
    if not _acceptable(text, payload):
        logger.warning("validation analysis rejected: warnings=%s", payload.get("warnings"))
        return {"text": _fallback(payload), "source": "fallback"}
    return {"text": text, "source": "ai"}
```

Also add `("validate_explain", "검증 결과 해설", None)` to `PURPOSES` in
`backend/app/api_usage.py` so the cost screen shows it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validate_explain.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/validate_explain.py backend/app/api_usage.py \
        backend/tests/test_validate_explain.py
git commit -m "검증 해설 — 경고부터 말하게 하고, 칭찬·지어낸 인용은 폴백으로 버린다"
```

---

### Task 9: `POST /api/validate/explain`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_validate_explain_api.py`

**Interfaces:**
- Consumes: Task 6~8 (`evidence.anomalies` 등, `validate_explain.explain`)
- Produces: `{"text": str, "source": "ai"|"fallback", "evidence": [...]}`

- [ ] **Step 1: Write the failing test**

```python
"""해설 API 는 서버가 모은 근거만 AI 에 넘기고, 응답에 출처를 함께 내려준다."""
from fastapi.testclient import TestClient

from app import validate_explain
from app.main import app

client = TestClient(app)

BODY = {"macro": {"symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
                  "params": {"take_profit_pct": 5, "initial_capital": 1000000},
                  "period": {"preset": "1y"}},
        "summary": {"final_return_pct": 142.0, "mdd_pct": 31.0, "total_trades": 14,
                    "windows": [180.0, 5.0, -12.0, 3.0],
                    "warnings": ["한_구간_집중", "표본_부족"]}}


def test_explain_returns_text_with_its_source(monkeypatch):
    monkeypatch.setattr(validate_explain, "explain",
                        lambda payload: {"text": "거래가 적습니다.", "source": "ai"})
    response = client.post("/api/validate/explain", json=BODY)
    assert response.status_code == 200
    assert response.json()["source"] in ("ai", "fallback")
    assert "evidence" in response.json()


def test_a_missing_summary_is_rejected():
    assert client.post("/api/validate/explain", json={"macro": BODY["macro"]}).status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validate_explain_api.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Write the implementation**

Add to `backend/app/main.py`:

```python
from . import evidence as evidence_mod
from . import validate_explain as validate_explain_mod


class ExplainIn(BaseModel):
    macro: dict
    summary: dict


@app.post("/api/validate/explain")
def validate_explain_route(body: ExplainIn) -> dict:
    """검증 결과 해설. 근거는 서버가 모아서 넘기고 AI 는 설명만 한다."""
    try:
        macro = Macro(**body.macro)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"매크로 설정을 확인해 주세요: {exc}") from exc

    start_ms, end_ms = resolve_period(macro.period.preset, macro.period.start, macro.period.end)
    df, _source = fetch_klines_for_macro(macro, start_ms, end_ms)
    btc = macro.model_copy(update={"symbol": "BTCUSDT", "symbols": None})
    btc_df, _ = fetch_klines_for_macro(btc, start_ms, end_ms)
    rows = evidence_mod.market_context(evidence_mod.anomalies(df), btc_df)
    asset = macro.symbol[:-4] if macro.symbol.endswith("USDT") else macro.symbol
    for row in rows:
        row["headlines"] = evidence_mod.headlines(asset, row["date"])

    payload = {**body.summary, "evidence": rows}
    out = validate_explain_mod.explain(payload)
    return {**out, "evidence": rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_validate_explain_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_validate_explain_api.py
git commit -m "POST /api/validate/explain — 서버가 모은 근거와 함께 해설을 내려준다"
```

---

### Task 10: 프론트 — 검증 결과 표시 로직

**Files:**
- Create: `frontend/src/lib/validationView.js`
- Test: `frontend/tests/validationView.test.js`

**Interfaces:**
- Produces:
  - `warningText(code: string) -> string`
  - `windowBars(windows: array) -> array` — `{index, pct, failed}`
  - `analysisLabel(source: string) -> string` — `"AI 분석"` | `"자동 요약"`

- [ ] **Step 1: Write the failing test**

```js
import assert from "node:assert/strict";
import { test } from "node:test";
import { analysisLabel, warningText, windowBars } from "../src/lib/validationView.js";

test("경고 코드는 사람이 읽는 문장이 된다", () => {
  assert.match(warningText("한_구간_집중"), /한 구간/);
  assert.equal(warningText("없는_코드"), "");
});

test("실패한 구간은 막대 대신 표시로 남는다", () => {
  const bars = windowBars([
    { index: 1, return_pct: 12.0, error: "" },
    { index: 2, return_pct: null, error: "ValueError" },
  ]);
  assert.equal(bars[0].failed, false);
  assert.equal(bars[1].failed, true);
  assert.equal(bars[1].pct, 0);
});

test("폴백은 AI 분석이라고 부르지 않는다", () => {
  assert.equal(analysisLabel("ai"), "AI 분석");
  assert.equal(analysisLabel("fallback"), "자동 요약");
  assert.equal(analysisLabel(""), "자동 요약");
});

test("빈 입력에도 터지지 않는다", () => {
  assert.deepEqual(windowBars(null), []);
  assert.deepEqual(windowBars([]), []);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && node --test "tests/validationView.test.js"`
Expected: FAIL — cannot find module `../src/lib/validationView.js`

- [ ] **Step 3: Write the implementation**

```js
// 검증 결과를 화면용 값으로 — 순수 함수만. 서버가 낸 판정을 바꾸지 않는다.
const WARNING_TEXT = {
  한_구간_집중: "수익 대부분이 한 구간에서 나왔어요",
  표본_부족: "거래 수가 적어 통계로 쓰기 어려워요",
  후반부_음수: "뒤쪽 구간에서 성과가 음수예요",
  거래_집중: "소수의 거래가 수익의 절반 이상을 만들었어요",
};

export function warningText(code) {
  return WARNING_TEXT[code] ?? "";
}

export function windowBars(windows) {
  return (windows ?? []).map((w) => ({
    index: w.index,
    pct: w.return_pct ?? 0,
    failed: w.return_pct === null || w.return_pct === undefined,
  }));
}

// 폴백을 'AI 분석' 이라 부르지 않는다 — 일일 챌린지에서 템플릿이 AI 로 둔갑한 적이 있다.
export function analysisLabel(source) {
  return source === "ai" ? "AI 분석" : "자동 요약";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && node --test "tests/validationView.test.js"`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/validationView.js frontend/tests/validationView.test.js
git commit -m "검증 결과 표시 로직 — 경고 문구, 구간 막대, 폴백 라벨"
```

---

### Task 11: 프론트 — `/studio/pro` 화면

**Files:**
- Create: `frontend/src/pages/StudioPro.jsx`
- Create: `frontend/src/pages/StudioPro.css`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/api.js`
- Test: `frontend/tests/studioProRoute.test.js`

**Interfaces:**
- Consumes: Task 10 (`warningText`, `windowBars`, `analysisLabel`), `/api/validate`,
  `/api/validate/explain`

- [ ] **Step 1: Write the failing test**

```js
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../src/pages/StudioPro.jsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/pages/StudioPro.css", import.meta.url), "utf8");
const appJsx = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");

test("라우트가 등록돼 있다", () => {
  assert.match(appJsx, /\/studio\/pro/);
});

test("폴백 라벨을 직접 쓰지 않고 공용 함수를 쓴다", () => {
  assert.match(page, /analysisLabel/);
  assert.doesNotMatch(page, /"AI 분석"/, "라벨 문자열을 화면에 박으면 폴백이 둔갑한다");
});

test("색은 토큰만 쓴다", () => {
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "하드코딩 색 대신 --c-* 토큰");
  assert.doesNotMatch(css, /\.dark\s/, "다크 규칙을 따로 두지 않는다");
});

test("'추천' 이라는 말을 쓰지 않는다", () => {
  assert.doesNotMatch(page, /추천/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && node --test "tests/studioProRoute.test.js"`
Expected: FAIL — ENOENT `StudioPro.jsx`

- [ ] **Step 3: Write the implementation**

`frontend/src/pages/StudioPro.jsx`:

```jsx
// 프로 빌더 — 빌더 본문 + 검증 탭 + 근거. 코치 패널 자리는 별도 계획에서 채운다.
import { useState } from "react";
import { postValidate, postValidateExplain } from "../api.js";
import { analysisLabel, warningText, windowBars } from "../lib/validationView.js";
import "./StudioPro.css";

export default function StudioPro() {
  const [macro, setMacro] = useState(null);      // 빌더 패널이 채운다
  const [report, setReport] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [openDate, setOpenDate] = useState("");
  const [busy, setBusy] = useState(false);

  async function runValidation() {
    if (!macro) return;
    setBusy(true);
    try {
      const next = await postValidate(macro, 4);
      setReport(next);
      setAnalysis(await postValidateExplain(macro, {
        ...next.result, windows: next.windows.map((w) => w.return_pct),
        warnings: next.warnings,
      }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pro">
      <section className="pro-build">
        {/* 기존 Studio 의 규칙·파라미터 패널을 여기에 재사용한다 */}
        <button className="pro-run" onClick={runValidation} disabled={!macro || busy}>
          {busy ? "검증 중…" : "검증하기"}
        </button>
      </section>

      {report && (
        <section className="pro-report">
          {report.warnings.length > 0 && (
            <ul className="pro-warnings">
              {report.warnings.map((code) => (
                <li key={code}>{warningText(code)}</li>
              ))}
            </ul>
          )}

          <h3>구간별 성과</h3>
          <ol className="pro-windows">
            {windowBars(report.windows).map((bar) => (
              <li key={bar.index} className={bar.failed ? "pro-window-failed" : ""}>
                <span>{bar.index}</span>
                <b>{bar.failed ? "데이터 없음" : `${bar.pct}%`}</b>
              </li>
            ))}
          </ol>

          <h3>월별 수익</h3>
          <ol className="pro-months">
            {report.monthly.map((row) => (
              <li key={row.month}><span>{row.month}</span><b>{row.pct}%</b></li>
            ))}
          </ol>

          {analysis && (
            <div className="pro-analysis">
              <h3>{analysisLabel(analysis.source)}</h3>
              <p>{analysis.text}</p>
              <ul className="pro-evidence">
                {(analysis.evidence ?? []).map((row) => (
                  <li key={row.date}>
                    <button onClick={() => setOpenDate(openDate === row.date ? "" : row.date)}>
                      {row.date} · {row.change_pct}% {row.verdict && `· ${row.verdict}`}
                    </button>
                    {openDate === row.date && (
                      <div className="pro-evidence-body">
                        {row.headlines?.found
                          ? row.headlines.items.map((item) => (
                              <a key={item.url} href={item.url} target="_blank" rel="noreferrer">
                                {item.title} <small>{item.source}</small>
                              </a>
                            ))
                          : <small>그 날짜의 근거를 찾지 못했어요.</small>}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
```

`frontend/src/pages/StudioPro.css` — 색은 `--c-*` 토큰만 쓰고 `.dark` 규칙을 두지 않는다:

```css
/* 프로 빌더. 색은 index.css 의 --c-* 토큰(rgb 삼중값)만 쓴다 —
   .dark 에서 토큰이 뒤집히므로 여기에 다크 규칙을 따로 두지 않는다. */
.pro { display: grid; gap: 16px; }
.pro-run { padding: 10px 16px; border: 0; border-radius: 10px;
           background: rgb(var(--c-brand)); color: rgb(var(--c-brand-ink)); cursor: pointer; }
.pro-run:disabled { opacity: .5; cursor: default; }
.pro-report { display: grid; gap: 12px; }
.pro-warnings { margin: 0; padding: 10px 12px 10px 28px; border-radius: 10px;
                border: 1px solid rgb(var(--c-amber-200)); background: rgb(var(--c-amber-50));
                color: rgb(var(--c-slate-900)); font-size: 13px; }
.pro-windows, .pro-months { display: flex; flex-wrap: wrap; gap: 8px;
                            margin: 0; padding: 0; list-style: none; }
.pro-windows li, .pro-months li { display: grid; gap: 2px; padding: 8px 12px;
  border: 1px solid rgb(var(--c-slate-200)); border-radius: 10px;
  background: rgb(var(--c-surface)); color: rgb(var(--c-slate-900));
  font-variant-numeric: tabular-nums; }
.pro-window-failed { color: rgb(var(--c-slate-500)); }
.pro-analysis { padding: 12px 14px; border-radius: 12px;
                background: rgb(var(--c-slate-100)); color: rgb(var(--c-slate-900)); }
.pro-evidence { margin: 8px 0 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.pro-evidence button { background: none; border: 0; padding: 0; cursor: pointer;
                       color: rgb(var(--c-indigo-700)); text-align: left; }
.pro-evidence-body { display: grid; gap: 4px; padding: 6px 0 2px 8px; font-size: 13px; }
@media (max-width: 480px) { .pro-windows li, .pro-months li { flex: 1 1 40%; } }
```

`frontend/src/App.jsx` 에 라우트를 더한다:

```jsx
<Route path="/studio/pro" element={<StudioPro />} />
```

`frontend/src/api.js` 에 두 함수를 더한다(파일의 기존 요청 헬퍼 모양을 따른다):

```js
export const postValidate = (macro, windows) =>
  post("/api/validate", { macro, windows });

export const postValidateExplain = (macro, summary) =>
  post("/api/validate/explain", { macro, summary });
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && node --test "tests/*.test.js" && npm run build`
Expected: PASS, build succeeds

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/StudioPro.jsx frontend/src/pages/StudioPro.css \
        frontend/src/App.jsx frontend/src/api.js frontend/tests/studioProRoute.test.js
git commit -m "프로 빌더 화면 — 검증 탭과 근거 펼침, 코치 자리는 비워 둔다"
```

---

## 마무리

- [ ] 백엔드 전체: `cd backend && .venv/Scripts/python.exe -m pytest -q`
  (prefect 미설치 2 건과 Windows cp949 2 건은 기존 실패다)
- [ ] 프론트 전체: `cd frontend && node --test "tests/*.test.js" && npm run build`
- [ ] 마이그레이션이 구 스키마에 붙는지 확인 (`SQLITE_PATH` 로 임시 DB 만들어 `init_db()`)
- [ ] `superpowers:finishing-a-development-branch` 로 병합 방식 결정
