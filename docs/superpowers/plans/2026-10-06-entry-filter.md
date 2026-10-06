# 진입 필터 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 규칙의 새 진입에 조건 하나를 덧씌우는 진입 필터를 E·F·G·H·I·J·K 일곱 규칙에 추가한다.

**Architecture:** 판정은 새 파일 `engine/entry_filter.py` 의 `FilterEval` 하나에 모은다. 배선은 두 줄 — `CandleSim.on_candle` 이 봉이 끝난 뒤 필터를 갱신하고, 이미 있는 공용 관문 `CandleSim._entry_blocked` 가 필터를 묻는다. 지표 계산은 기존 `RSIState`·`BollingerState`·`MAState` 를 그대로 재사용한다.

**Tech Stack:** Python 3.12 / pydantic v2 / pytest (백엔드), React + 평문 JS / `node:test` (프런트)

**Spec:** [docs/superpowers/specs/2026-10-06-entry-filter-design.md](../specs/2026-10-06-entry-filter-design.md)

## Global Constraints

- 필터 대상 규칙: `FILTERABLE_TYPES = frozenset({RuleType.E, RuleType.F, RuleType.G, RuleType.H, RuleType.I, RuleType.J, RuleType.K})`. A·B·C·D 는 스키마가 거부한다.
- **기존 심(sim) 테스트는 한 줄도 바뀌지 않아야 한다.** 바뀌면 필터가 기존 동작을 건드린 것이다.
- 지표가 아직 값을 못 내면 `allows()` 는 `False` — 진입을 막는다.
- 필터는 진입만 막는다. 청산·세이프티오더·그리드 보충은 건드리지 않는다.
- 필터 갱신은 `_strategy` **뒤**에. 진행 중인 봉으로 자기 자신을 판정하면 미래 참조다.
- 규칙 안에 이미 있는 조건(`ParamsI.ma_filter_period`, `ParamsG.squeeze_filter`)은 그대로 두고 **둘 다 AND** 로 적용한다.
- 기간 상한은 400 (`WARMUP_CANDLES = 500` 이 덮는다). RSI 는 200.
- `RSIState`·`BollingerState`·`MAState` 를 `candles.py` 에서 **옮기지 않는다** — F·G·J 가 쓰고 있다. `entry_filter.py` 가 import 한다.
- `needs_signals` 와 실행기 코드는 건드리지 않는다. 대상 일곱 규칙은 이미 전부 서버 신호 경로다.
- 백엔드 시험: `cd backend && .venv/Scripts/python.exe -m pytest tests/<파일> -q`
- 프런트 시험: `cd frontend && node --test tests/<파일>.test.js`

## Review Focus

- **지표가 안 데워진 구간** — `period` 가 받은 봉 수보다 크면 진입이 **0** 이어야 한다. "모르면 막는다"가 반대로 구현되면 아무 조건 없이 사게 된다. (Task 2 · Task 3)
- **거래량 없는 봉** — `volume=None` 인 봉만 받은 거래량 필터는 막아야 한다. 통과시키면 필터를 켠 사용자가 필터 없이 거래한다. (Task 2 · Task 4)
- **`entry_filter` 가 없는 옛 매크로** — 저장된 매크로·리더보드 매크로 전부가 `None` 경로로 지금과 **완전히 같게** 돌아야 한다. (Task 3)
- **필터 가능 → 불가능 규칙으로 바꿀 때** — 화면이 필터를 버려야 한다. 남겨 두면 스키마가 `ValueError` 를 내고 사용자는 "왜 안 되는지" 알 수 없다. (Task 7)
- **규칙 안 조건과 동시 적용** — I 의 `ma_filter_period` 와 `entry_filter` 가 둘 다 걸렸을 때 각각 독립적으로 막아야 한다. 한쪽이 다른 쪽을 덮으면 사용자가 건 조건이 조용히 사라진다. (Task 3)

---

### Task 1: 필터 스키마

**Files:**
- Modify: `backend/app/engine/schema.py`
- Test: `backend/tests/test_entry_filter_schema.py` (새 파일)

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces: `FilterKind`, `MAFilterParams`, `RSIFilterParams`, `BollingerFilterParams`, `VolumeFilterParams`, `EntryFilter`, `FILTERABLE_TYPES`, `Macro.entry_filter: Optional[EntryFilter]`

- [ ] **Step 1: 실패하는 시험 작성**

`backend/tests/test_entry_filter_schema.py`:

```python
"""진입 필터 스키마 — kind 별 params 검증, 못 쓰는 규칙 거부."""
import pytest

from app.engine.schema import FILTERABLE_TYPES, Macro, RuleType

BREAKOUT = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
            "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}
MA20 = {"kind": "ma", "params": {"period": 20, "side": "above"}}


def test_filterable_types_are_the_seven_candle_rules():
    assert FILTERABLE_TYPES == frozenset({RuleType.E, RuleType.F, RuleType.G,
                                          RuleType.H, RuleType.I, RuleType.J, RuleType.K})


def test_no_filter_stays_none():
    assert Macro(**BREAKOUT).entry_filter is None


def test_ma_params_are_canonicalized():
    m = Macro(**{**BREAKOUT, "entry_filter": MA20})
    assert m.entry_filter.params == {"ma_type": "SMA", "period": 20, "side": "above"}


def test_unknown_param_keys_are_dropped_like_rule_params():
    m = Macro(**{**BREAKOUT, "entry_filter": {"kind": "ma", "params": {**MA20["params"], "nope": 1}}})
    assert "nope" not in m.entry_filter.params


@pytest.mark.parametrize("rule_type, params, extra", [
    ("A", {"take_profit_pct": 3.0, "initial_capital": 1000}, {}),
    ("B", {"buy_price": 1, "sell_price": 2, "initial_capital": 1000}, {}),
    ("C", {"amount_per_buy": 100, "interval_days": 1, "initial_capital": 1000}, {"candle_interval": "1d"}),
    ("D", {"lower_price": 1, "upper_price": 2, "grid_count": 3, "initial_capital": 1000}, {}),
])
def test_filter_on_unfilterable_rule_is_rejected(rule_type, params, extra):
    body = {**BREAKOUT, "rule_type": rule_type, "params": params, **extra, "entry_filter": MA20}
    with pytest.raises(ValueError, match="entry_filter"):
        Macro(**body)


@pytest.mark.parametrize("bad", [
    {"kind": "ma", "params": {"period": 1, "side": "above"}},        # period < 2
    {"kind": "ma", "params": {"period": 401, "side": "above"}},      # period > 400
    {"kind": "ma", "params": {"period": 20, "side": "sideways"}},    # 모르는 side
    {"kind": "ma", "params": {"period": 20}},                        # side 없음
    {"kind": "rsi", "params": {"period": 14}},                       # min·max 둘 다 없음
    {"kind": "rsi", "params": {"period": 14, "min": 70, "max": 30}}, # min > max
    {"kind": "bb", "params": {"period": 20, "zone": "middle"}},      # 모르는 zone
    {"kind": "volume", "params": {"period": 20}},                    # multiple 없음
    {"kind": "volume", "params": {"period": 20, "multiple": 0}},     # multiple <= 0
    {"kind": "nope", "params": {}},                                  # 모르는 kind
])
def test_bad_filter_params_are_rejected(bad):
    with pytest.raises(ValueError):
        Macro(**{**BREAKOUT, "entry_filter": bad})


@pytest.mark.parametrize("good", [
    {"kind": "ma", "params": {"ma_type": "EMA", "period": 400, "side": "below"}},
    {"kind": "rsi", "params": {"period": 14, "max": 70}},
    {"kind": "rsi", "params": {"period": 14, "min": 30}},
    {"kind": "bb", "params": {"period": 20, "num_std": 2.0, "zone": "below_lower"}},
    {"kind": "volume", "params": {"period": 20, "multiple": 2.0}},
])
def test_good_filter_params_are_accepted(good):
    assert Macro(**{**BREAKOUT, "entry_filter": good}).entry_filter is not None


def test_filter_survives_a_round_trip():
    m = Macro(**{**BREAKOUT, "entry_filter": MA20})
    assert Macro.model_validate_json(m.model_dump_json()).entry_filter.params["period"] == 20
```

- [ ] **Step 2: 실패 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_schema.py -q
```
기대: `ImportError: cannot import name 'FILTERABLE_TYPES'`

- [ ] **Step 3: 구현**

`backend/app/engine/schema.py` — `_PARAMS_MODEL` 정의 **뒤**, `Macro` 클래스 **앞**에 넣는다:

```python
# --- 진입 필터 --------------------------------------------------------
# 기존 규칙의 새 진입에만 걸리는 관문 하나. 스스로 사거나 팔지 않는다.
class FilterKind(str, enum.Enum):   # 이 파일은 `enum` 을 import 한다 — `Enum` 은 NameError
    MA = "ma"            # 종가가 이동평균 위/아래
    RSI = "rsi"          # RSI 가 구간 안
    BOLLINGER = "bb"     # 볼린저 밴드 기준 위치
    VOLUME = "volume"    # 거래량이 평균의 N배 이상


class MAFilterParams(BaseModel):
    ma_type: Literal["SMA", "EMA"] = "SMA"
    period: int = Field(ge=2, le=400)      # WARMUP_CANDLES=500 이 덮는 상한
    side: Literal["above", "below"]


class RSIFilterParams(BaseModel):
    period: int = Field(default=14, ge=2, le=200)
    min: Optional[float] = Field(default=None, ge=0, le=100)
    max: Optional[float] = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> "RSIFilterParams":
        if self.min is None and self.max is None:
            raise ValueError("rsi filter requires min or max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("rsi filter min must not exceed max")
        return self


class BollingerFilterParams(BaseModel):
    period: int = Field(default=20, ge=2, le=400)
    num_std: float = Field(default=2.0, gt=0, le=5)
    zone: Literal["below_lower", "above_upper", "inside"]


class VolumeFilterParams(BaseModel):
    period: int = Field(default=20, ge=2, le=400)
    multiple: float = Field(gt=0, le=100)


_FILTER_PARAMS_MODEL: dict[FilterKind, type[BaseModel]] = {
    FilterKind.MA: MAFilterParams,
    FilterKind.RSI: RSIFilterParams,
    FilterKind.BOLLINGER: BollingerFilterParams,
    FilterKind.VOLUME: VolumeFilterParams,
}


class EntryFilter(BaseModel):
    kind: FilterKind
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "EntryFilter":
        # Macro._validate_new_type 과 같은 모양 — kind 별 모델로 다시 검증하고
        # model_dump() 로 정규화해 되담는다(기본값 적용·모르는 키 탈락).
        self.params = _FILTER_PARAMS_MODEL[self.kind](**self.params).model_dump()
        return self


# 실시간에서 캔들을 받는 규칙만 필터를 걸 수 있다. A·B(PositionSim)·C(DcaSim) 는 틱 기반이라
# 지표를 계산할 데이터가 없고, D 그리드는 사다리 중간을 막으면 팔 짝 없는 매수가 남는다.
FILTERABLE_TYPES = frozenset({
    RuleType.E, RuleType.F, RuleType.G, RuleType.H, RuleType.I, RuleType.J, RuleType.K,
})
```

`Macro` 에 필드 한 줄 — `fees` 아래, `created_at` 위:

```python
    entry_filter: Optional[EntryFilter] = None
```

`Macro._validate` 안, `if self.rule_type in _PARAMS_MODEL:` 분기 **앞**에:

```python
        if self.entry_filter is not None and self.rule_type not in FILTERABLE_TYPES:
            raise ValueError(
                f"rule_type {self.rule_type.value} does not support entry_filter"
            )
```

- [ ] **Step 4: 통과 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_schema.py -q
```
기대: 전부 통과.

- [ ] **Step 5: 기존 스키마 시험이 그대로인지 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "schema or macro or engine or backtest" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py
```
기대: 새로 깨지는 것 없음. (`test_runner_release.py` 의 `cp949` 실패와 `test_agent_collector_runner.py` 2건은 이 작업 전부터 있던 것이다 — 손대지 말 것.)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/engine/schema.py backend/tests/test_entry_filter_schema.py
git commit -m "feat(engine): 진입 필터 스키마 — kind 별 params 검증과 규칙 제한"
```

---

### Task 2: 필터 평가기

**Files:**
- Create: `backend/app/engine/entry_filter.py`
- Test: `backend/tests/test_entry_filter_eval.py` (새 파일)

**Interfaces:**
- Consumes: Task 1 의 `EntryFilter`, `FilterKind`, `Macro.entry_filter`
- Produces: `FilterEval` (메서드 `update(close, volume=None) -> None`, `allows() -> bool`, `note() -> str`), `make_filter(macro) -> Optional[FilterEval]`

- [ ] **Step 1: 실패하는 시험 작성**

`backend/tests/test_entry_filter_eval.py`:

```python
"""필터 평가기 — 네 종류의 판정, 그리고 '모르면 막는다'."""
from app.engine.entry_filter import make_filter
from app.engine.schema import Macro

BREAKOUT = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
            "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}


def _eval(kind, params):
    return make_filter(Macro(**{**BREAKOUT, "entry_filter": {"kind": kind, "params": params}}))


def test_no_filter_returns_none():
    assert make_filter(Macro(**BREAKOUT)) is None


def test_unwarmed_filter_blocks():
    f = _eval("ma", {"period": 20, "side": "above"})
    assert f.allows() is False            # 봉을 하나도 안 먹였다
    for close in range(10):               # 20봉이 필요한데 10봉만
        f.update(float(100 + close))
    assert f.allows() is False


def test_ma_above_allows_when_close_is_over_the_average():
    f = _eval("ma", {"period": 3, "side": "above"})
    for close in (100.0, 100.0, 100.0):
        f.update(close)
    assert f.allows() is False            # 종가 == 평균, "위" 가 아니다
    f.update(200.0)
    assert f.allows() is True


def test_ma_below_is_the_mirror():
    f = _eval("ma", {"period": 3, "side": "below"})
    for close in (100.0, 100.0, 100.0, 50.0):
        f.update(close)
    assert f.allows() is True


def test_rsi_max_blocks_when_overbought():
    f = _eval("rsi", {"period": 2, "max": 70})
    for close in (100.0, 110.0, 120.0, 130.0, 140.0):   # 계속 오름 -> RSI 100 근처
        f.update(close)
    assert f.allows() is False


def test_rsi_min_blocks_when_oversold():
    f = _eval("rsi", {"period": 2, "min": 30})
    for close in (140.0, 130.0, 120.0, 110.0, 100.0):   # 계속 내림 -> RSI 0 근처
        f.update(close)
    assert f.allows() is False


def test_bollinger_zones():
    below = _eval("bb", {"period": 3, "num_std": 1.0, "zone": "below_lower"})
    for close in (100.0, 100.0, 100.0, 50.0):
        below.update(close)
    assert below.allows() is True
    inside = _eval("bb", {"period": 3, "num_std": 1.0, "zone": "inside"})
    for close in (100.0, 101.0, 100.0):
        inside.update(close)
    assert inside.allows() is True


def test_volume_multiple():
    f = _eval("volume", {"period": 3, "multiple": 2.0})
    for close in (100.0, 100.0, 100.0):
        f.update(close, volume=10.0)
    assert f.allows() is False            # 평균과 같다
    f.update(100.0, volume=100.0)
    assert f.allows() is True


def test_volume_filter_blocks_without_volume():
    """거래량을 못 받으면 막는다 — 통과시키면 필터를 켠 사용자가 필터 없이 거래한다."""
    f = _eval("volume", {"period": 2, "multiple": 2.0})
    for _ in range(5):
        f.update(100.0)                   # volume 없음
    assert f.allows() is False


def test_note_describes_the_filter_in_korean():
    assert "20" in _eval("ma", {"period": 20, "side": "above"}).note()
    for kind, params in (("ma", {"period": 20, "side": "above"}),
                         ("rsi", {"period": 14, "max": 70}),
                         ("bb", {"period": 20, "zone": "inside"}),
                         ("volume", {"period": 20, "multiple": 2.0})):
        note = _eval(kind, params).note()
        assert note and note.strip() == note
```

- [ ] **Step 2: 실패 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_eval.py -q
```
기대: `ModuleNotFoundError: No module named 'app.engine.entry_filter'`

- [ ] **Step 3: 구현**

`backend/app/engine/entry_filter.py`:

```python
"""진입 필터 판정 — 마감봉 종가를 하나씩 먹고 '지금 진입해도 되는가'를 답한다.

심(sim) 클래스가 candles.py 와 stepper.py 에 나뉘어 있어 판정을 여기 한 곳에 둔다.
지표 계산은 candles.py 의 상태 기계를 그대로 재사용한다 — 옮기지 않는다(F·G·J 가 쓴다).
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional

from .candles import BollingerState, MAState, RSIState
from .schema import EntryFilter, FilterKind, Macro


class _VolumeState:
    """고정 창 평균 거래량. 창이 안 차면 None."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._win: Deque[float] = deque(maxlen=period)

    def update(self, volume: Optional[float]) -> Optional[float]:
        if volume is None:
            return None
        self._win.append(float(volume))
        if len(self._win) < self.period:
            return None
        return sum(self._win) / self.period


class FilterEval:
    """필터 하나의 상태와 판정. ``allows()`` 는 값이 없으면 항상 False."""

    def __init__(self, spec: EntryFilter) -> None:
        self.kind = spec.kind
        self.p = dict(spec.params)
        self._allows = False
        if self.kind is FilterKind.MA:
            self._state = MAState(self.p["ma_type"], int(self.p["period"]))
        elif self.kind is FilterKind.RSI:
            self._state = RSIState(int(self.p["period"]))
        elif self.kind is FilterKind.BOLLINGER:
            self._state = BollingerState(int(self.p["period"]), float(self.p["num_std"]))
        else:
            self._state = _VolumeState(int(self.p["period"]))

    def update(self, close: float, volume: Optional[float] = None) -> None:
        if self.kind is FilterKind.VOLUME:
            avg = self._state.update(volume)
            # 평균을 못 구했거나 이 봉의 거래량을 모르면 막는다.
            self._allows = (
                avg is not None and volume is not None
                and float(volume) >= avg * float(self.p["multiple"])
            )
            return

        value = self._state.update(close)
        if value is None:
            self._allows = False          # 아직 모른다 -> 막는다
            return

        if self.kind is FilterKind.MA:
            self._allows = close > value if self.p["side"] == "above" else close < value
        elif self.kind is FilterKind.RSI:
            low, high = self.p.get("min"), self.p.get("max")
            self._allows = (low is None or value >= low) and (high is None or value <= high)
        else:
            _mid, upper, lower = value
            zone = self.p["zone"]
            if zone == "below_lower":
                self._allows = close < lower
            elif zone == "above_upper":
                self._allows = close > upper
            else:
                self._allows = lower <= close <= upper

    def allows(self) -> bool:
        return self._allows

    def note(self) -> str:
        p = self.p
        if self.kind is FilterKind.MA:
            where = "위" if p["side"] == "above" else "아래"
            return f"{p['period']}봉 {p['ma_type']} 이동평균 {where}"
        if self.kind is FilterKind.RSI:
            low, high = p.get("min"), p.get("max")
            if low is not None and high is not None:
                return f"RSI({p['period']}) {low:g}~{high:g}"
            if high is not None:
                return f"RSI({p['period']}) {high:g} 이하"
            return f"RSI({p['period']}) {low:g} 이상"
        if self.kind is FilterKind.BOLLINGER:
            zones = {"below_lower": "하단 밖", "above_upper": "상단 밖", "inside": "밴드 안"}
            return f"볼린저({p['period']}, {p['num_std']:g}σ) {zones[p['zone']]}"
        return f"거래량이 {p['period']}봉 평균의 {p['multiple']:g}배 이상"


def make_filter(macro: Macro) -> Optional[FilterEval]:
    """필터가 없으면 None — 호출부는 None 을 '관문 없음' 으로 읽는다."""
    return None if macro.entry_filter is None else FilterEval(macro.entry_filter)
```

- [ ] **Step 4: 통과 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_eval.py -q
```
기대: 전부 통과.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/engine/entry_filter.py backend/tests/test_entry_filter_eval.py
git commit -m "feat(engine): 진입 필터 평가기 — 네 종류 판정과 '모르면 막는다'"
```

---

### Task 3: 캔들 심 배선

**Files:**
- Modify: `backend/app/engine/candles.py` (`CandleSim.__init__`, `CandleSim.on_candle`, `CandleSim._entry_blocked`)
- Test: `backend/tests/test_entry_filter_sims.py` (새 파일)

**Interfaces:**
- Consumes: Task 2 의 `make_filter`, `FilterEval`
- Produces: `CandleSim.entry_filter` 속성 (`Optional[FilterEval]`). 일곱 규칙이 자동으로 필터를 따른다.

**주의:** 이 작업은 `_strategy` 를 하나도 고치지 않는다. 세 줄만 더한다. 규칙별 심을 고치고 있다면 잘못 가고 있는 것이다.

- [ ] **Step 1: 실패하는 시험 작성**

`backend/tests/test_entry_filter_sims.py`:

```python
"""필터가 일곱 규칙의 진입을 막는다 — 그리고 청산·세이프티오더는 막지 않는다."""
import pytest
from datetime import datetime, timedelta, timezone

from app.engine.candles import make_candle_sim
from app.engine.schema import Macro

START = datetime(2026, 1, 1, tzinfo=timezone.utc)

BASE = {"symbol": "BTCUSDT", "candle_interval": "1h", "period": {"preset": "3m"},
        "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0, "slippage_pct": 0}}
RULES = {
    "E": {"trail_percent": 3.0, "initial_capital": 1000},
    "F": {"rsi_period": 2, "entry_threshold": 90, "exit_threshold": 95, "initial_capital": 1000},
    "G": {"bb_period": 3, "bb_std": 1.0, "initial_capital": 1000},
    "H": {"base_order_size": 100, "safety_order_size": 100, "price_deviation": 1.0,
          "take_profit": 1.0, "max_safety_orders": 2, "initial_capital": 1000},
    "I": {"k": 0.1, "initial_capital": 1000},
    "J": {"fast_period": 2, "slow_period": 3, "initial_capital": 1000},
    "K": {"drop_trigger_pct": 5, "short_take_profit_pct": 3, "short_stop_loss_pct": 2,
          "initial_capital": 1000},
}
# 절대 통과하지 못하는 필터: 400봉 이평은 40봉만 주면 영원히 안 데워지고, 안 데워진 필터는
# 막는다(원칙 2). 가격 열과 무관하게 결정적이라 일곱 규칙을 같은 필터로 쓸 수 있다.
BLOCKS = {"kind": "ma", "params": {"period": 400, "side": "above"}}


def _macro(rule_type, entry_filter=None):
    body = {**BASE, "rule_type": rule_type, "params": RULES[rule_type]}
    if rule_type == "K":
        body["market"] = "futures"
    if entry_filter is not None:
        body["entry_filter"] = entry_filter
    return Macro(**body)


def _run(macro, closes):
    sim = make_candle_sim(macro)
    fills = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        fills += sim.on_candle(o, max(o, c) * 1.01, min(o, c) * 0.99, c, START + timedelta(hours=i))
    return fills


RISING = [100.0 + i * 5 for i in range(40)]
# 삼각파 — 올랐다 내린다. F(RSI)·G(볼린저 역추세) 는 계속 오르는 열에서는 필터가 없어도
# 진입하지 않는다(RSI 가 100 에 붙고, 역추세는 하단을 뚫을 일이 없다). 그래서 따로 준다.
TRIANGLE = ([80.0, 100.0, 120.0, 140.0, 120.0, 100.0] * 7)[:40]
SERIES = {"F": TRIANGLE, "G": TRIANGLE}


def _series(rule_type):
    return SERIES.get(rule_type, RISING)


@pytest.mark.parametrize("rule_type", sorted(RULES))
def test_filter_blocks_every_entry_on_each_filterable_rule(rule_type):
    closes = _series(rule_type)
    bare = _run(_macro(rule_type), closes)
    filtered = _run(_macro(rule_type, BLOCKS), closes)
    assert not [f for f in filtered if f.side in ("buy", "short")], rule_type
    # 필터 없이는 뭔가 사야 한다 — 아니면 이 시험은 아무것도 증명하지 않는다.
    assert [f for f in bare if f.side in ("buy", "short")], rule_type


@pytest.mark.parametrize("rule_type", sorted(RULES))
def test_macro_without_filter_is_byte_for_byte_unchanged(rule_type):
    closes = _series(rule_type)
    before = [(f.side, round(f.price, 6), round(f.qty, 8)) for f in _run(_macro(rule_type), closes)]
    again = [(f.side, round(f.price, 6), round(f.qty, 8)) for f in _run(_macro(rule_type), closes)]
    assert before == again and before  # 결정적이고, 비어 있지 않다


def test_filter_does_not_block_exits():
    """포지션을 들고 있고 필터가 거짓인 동안에도 손절이 일어난다."""
    macro = Macro(**{**BASE, "rule_type": "E", "params": RULES["E"],
                     "risk": {"invest_ratio": 1.0, "stop_loss_pct": 2.0},
                     "entry_filter": {"kind": "ma", "params": {"period": 3, "side": "above"}}})
    # 오르는 구간에서 진입(필터 통과) -> 급락 구간에서 필터는 거짓이지만 손절은 나가야 한다.
    closes = [100.0 + i * 5 for i in range(10)] + [100.0 - i * 5 for i in range(10)]
    fills = _run(macro, closes)
    assert [f for f in fills if f.side == "buy"]
    assert [f for f in fills if f.side == "sell"]


def test_filter_does_not_block_safety_orders():
    """H 첫 진입 뒤 필터가 거짓으로 바뀌어도 세이프티오더는 나간다."""
    macro = _macro("H", {"kind": "ma", "params": {"period": 3, "side": "above"}})
    closes = [100.0 + i * 5 for i in range(8)] + [135.0 - i * 3 for i in range(14)]
    buys = [f for f in _run(macro, closes) if f.side == "buy"]
    assert len(buys) >= 2, buys  # 첫 진입 + 세이프티오더 최소 하나


def test_rule_local_filter_and_entry_filter_both_apply():
    """I 의 ma_filter_period 와 entry_filter 가 각각 독립적으로 막는다."""
    # entry_filter 만 거짓 -> 안 산다
    only_new = _macro("I", {"kind": "ma", "params": {"period": 3, "side": "below"}})
    assert not [f for f in _run(only_new, RISING) if f.side == "buy"]
    # ma_filter_period 만 거짓(내리는 봉) -> 안 산다
    falling = [200.0 - i * 5 for i in range(40)]
    only_old = Macro(**{**BASE, "rule_type": "I", "market": "spot",
                        "params": {**RULES["I"], "ma_filter_period": 3},
                        "entry_filter": {"kind": "ma", "params": {"period": 3, "side": "below"}}})
    assert not [f for f in _run(only_old, falling) if f.side == "buy"]


def test_unwarmed_filter_blocks_until_the_period_is_reached():
    macro = _macro("I", {"kind": "ma", "params": {"period": 30, "side": "above"}})
    assert not [f for f in _run(macro, RISING[:12]) if f.side == "buy"]
    # 같은 필터, 충분한 봉 -> 데워지고 통과한다. 이 줄이 없으면 위 단정은 "필터가 아예 망가져도 통과" 다.
    assert [f for f in _run(macro, RISING) if f.side == "buy"]
```

- [ ] **Step 2: 실패 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_sims.py -q
```
기대: `test_filter_blocks_every_entry_on_each_filterable_rule` 이 일곱 규칙 전부 실패 (필터가 아직 배선되지 않았으므로 매수가 그대로 일어난다).

**봉 열이 안 맞아 실패하면:** `assert [f for f in bare ...]` 가 어느 규칙에서 터지면 그 규칙은
주어진 봉 열에서 필터 없이도 진입하지 않는 것이다. `SERIES` 에 그 규칙의 열을 추가해 진입이
일어나게 만든다. **단정을 약화시키거나 그 규칙을 `RULES` 에서 빼지 말 것** — 그러면 이 시험이
아무것도 증명하지 않는다.

- [ ] **Step 3: 구현 — `CandleSim.__init__` 끝에 한 줄**

`backend/app/engine/candles.py`, `CandleSim.__init__` 의 `self.stopped = False` 줄 **다음**:

```python
        # 진입 필터 — 없으면 None. on_candle 이 봉이 끝난 뒤 갱신하고 _entry_blocked 가 묻는다.
        from .entry_filter import make_filter  # 늦은 import: entry_filter -> candles 순환 방지
        self.entry_filter = make_filter(macro)
```

- [ ] **Step 4: 구현 — `on_candle` 에 갱신 한 줄**

```python
    def on_candle(self, o: float, h: float, l: float, c: float, ts: datetime) -> List[Fill]:
        fills: List[Fill] = []
        # Funding on held shorts (per bar, prorated only for 1d; kept simple).
        if self.side is PositionSide.SHORT and self.in_position() and self.funding > 0:
            self.cash -= self.total_qty() * c * self.funding / 100.0
        self._strategy(o, h, l, c, ts, fills)
        # 전략 '뒤' 에 갱신한다 — 그래야 이 봉의 진입 판단은 직전 마감봉의 필터 값을 본다.
        # 순서를 바꾸면 진행 중인 봉으로 자기 자신을 판정하는 미래 참조가 된다.
        if self.entry_filter is not None:
            self.entry_filter.update(c)
        return fills
```

- [ ] **Step 5: 구현 — `_entry_blocked` 에 관문 한 줄**

```python
    def _entry_blocked(self, ts: datetime) -> bool:
        if self.stopped:
            return True
        if self._halted_day is not None and self._halted_day == self._day:
            return True
        if self._cooldown_until is not None and ts < self._cooldown_until:
            return True
        if self.entry_filter is not None and not self.entry_filter.allows():
            return True
        return False
```

- [ ] **Step 6: 통과 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_sims.py -q
```
기대: 전부 통과.

- [ ] **Step 7: 기존 엔진 시험이 그대로인지 확인 — 이 작업의 핵심 관문**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "candle or engine or backtest or paper or sim or runner" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py
```
기대: 새로 깨지는 것 **없음**. 하나라도 깨지면 필터가 기존 동작을 건드린 것이다 — 시험을 고치지 말고 구현을 고칠 것.

- [ ] **Step 8: 커밋**

```bash
git add backend/app/engine/candles.py backend/tests/test_entry_filter_sims.py
git commit -m "feat(engine): 캔들 심에 진입 필터 배선 — 갱신 한 곳, 관문 한 곳"
```

---

### Task 4: 거래량을 봉과 함께 심까지 보낸다

**Files:**
- Modify: `backend/app/engine/candles.py` (`CandleSim.on_candle`, `CandleSim.warmup`, `LiveCandleSim.on_candle`, `LiveCandleSim.warmup`)
- Modify: `backend/app/engine/backtest.py` (봉 루프)
- Modify: `backend/app/engine/driver.py` (`StrategyDriver.push_candle`)
- Test: `backend/tests/test_entry_filter_volume.py` (새 파일)

**Interfaces:**
- Consumes: Task 3 의 `CandleSim.entry_filter`
- Produces: `on_candle(o, h, l, c, ts, volume=None)` — 모든 호출 지점에서 **선택 인자**. 기본값이 있으므로 기존 호출은 그대로 돈다.

**주의:** `push_candle` 은 지금 `t_ms, o, h, l, c = candle` 로 **정확히 5개**를 언패킹한다. 테스트가 튜플 더미를 쓰므로(`driver.py:203` 주석) 길이 변화에 관대해야 한다 — 5개 봉도 6개 봉도 받아야 한다.

- [ ] **Step 1: 실패하는 시험 작성**

`backend/tests/test_entry_filter_volume.py`:

```python
"""거래량 필터 — 봉의 거래량이 심까지 와야 작동하고, 안 오면 막는다."""
from datetime import datetime, timedelta, timezone

from app.engine.candles import make_candle_sim
from app.engine.driver import Leg, StrategyDriver
from app.engine.schema import Macro

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
VOL2X = {"kind": "volume", "params": {"period": 3, "multiple": 2.0}}
BODY = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"k": 0.1, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
        "fees": {"commission_pct": 0, "slippage_pct": 0}}


def test_on_candle_accepts_volume_and_the_filter_uses_it():
    sim = make_candle_sim(Macro(**{**BODY, "entry_filter": VOL2X}))
    closes = [100.0 + i * 5 for i in range(12)]
    volumes = [10.0] * 8 + [500.0] * 4          # 뒤에서 거래량이 터진다
    fills = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        fills += sim.on_candle(o, c * 1.02, o * 0.98, c, START + timedelta(hours=i), volume=volumes[i])
    assert [f for f in fills if f.side == "buy"]


def test_without_volume_the_filter_blocks_everything():
    sim = make_candle_sim(Macro(**{**BODY, "entry_filter": VOL2X}))
    closes = [100.0 + i * 5 for i in range(12)]
    fills = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        fills += sim.on_candle(o, c * 1.02, o * 0.98, c, START + timedelta(hours=i))
    assert not [f for f in fills if f.side == "buy"]


def test_warmup_passes_volume_when_the_candle_carries_it():
    sim = make_candle_sim(Macro(**{**BODY, "entry_filter": VOL2X}))
    # (t_ms, o, h, l, c, v) 6칸 봉
    candles = [(START.timestamp() * 1000 + i * 3_600_000, 100.0, 101.0, 99.0, 100.0, 10.0)
               for i in range(5)]
    sim.warmup(candles)
    assert sim.entry_filter.allows() is False   # 평균과 같으니 2배가 아니다


def test_warmup_still_accepts_five_column_candles():
    """거래량 없는 옛 봉 모양도 그대로 받는다."""
    sim = make_candle_sim(Macro(**BODY))
    candles = [(START.timestamp() * 1000 + i * 3_600_000, 100.0, 101.0, 99.0, 100.0)
               for i in range(5)]
    sim.warmup(candles)   # 예외가 없으면 통과


def test_push_candle_accepts_both_candle_shapes():
    macro = Macro(**{**BODY, "entry_filter": VOL2X})
    driver = StrategyDriver([Leg("BTCUSDT", make_candle_sim(macro), 1000.0)], 1000.0, macro=macro)
    t = int(START.timestamp() * 1000)
    assert driver.push_candle("BTCUSDT", (t, 100.0, 101.0, 99.0, 100.0)) == 0
    assert driver.push_candle("BTCUSDT", (t + 3_600_000, 100.0, 101.0, 99.0, 100.0, 10.0)) == 0
```

- [ ] **Step 2: 실패 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_volume.py -q
```
기대: `TypeError: on_candle() got an unexpected keyword argument 'volume'`

- [ ] **Step 3: 구현 — `CandleSim.on_candle` 에 선택 인자**

```python
    def on_candle(self, o: float, h: float, l: float, c: float, ts: datetime,
                  volume: Optional[float] = None) -> List[Fill]:
        ...
        if self.entry_filter is not None:
            self.entry_filter.update(c, volume=volume)
        return fills
```

- [ ] **Step 4: 구현 — `CandleSim.warmup` 이 6칸 봉을 받는다**

```python
    def warmup(self, candles) -> None:
        """과거 마감봉으로 지표·전략 상태를 채운다. (t, o, h, l, c) 또는 (t, o, h, l, c, v)."""
        for row in candles:
            t_ms, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
            volume = float(row[5]) if len(row) > 5 and row[5] is not None else None
            ts = datetime.fromtimestamp(int(t_ms) / 1000, timezone.utc)
            self.on_candle(float(o), float(h), float(l), float(c), ts, volume=volume)
        self.reset_book()
```

- [ ] **Step 5: 구현 — `LiveCandleSim` 이 거래량을 그대로 넘긴다**

```python
    def on_candle(self, o: float, h: float, l: float, c: float, ts: datetime,
                  volume: Optional[float] = None) -> int:
        fills = self.inner.on_candle(o, h, l, c, ts, volume=volume)
        self._queue.extend(fills)
        return len(fills)
```

`LiveCandleSim.warmup` 은 `self.inner.warmup(candles)` 로 넘기므로 바뀌지 않는다.

- [ ] **Step 6: 구현 — `StrategyDriver.push_candle` 이 두 모양을 다 받는다**

`backend/app/engine/driver.py`:

```python
    def push_candle(self, symbol: str, candle) -> int:
        leg = self.leg_for(symbol)
        on_candle = getattr(leg.sim, "on_candle", None)
        if on_candle is None:
            return 0
        # (t, o, h, l, c) 와 (t, o, h, l, c, v) 를 모두 받는다 — 테스트 더미가 튜플이고,
        # 거래량을 싣지 않는 옛 호출이 남아 있다.
        t_ms, o, h, l, c = candle[0], candle[1], candle[2], candle[3], candle[4]
        volume = float(candle[5]) if len(candle) > 5 and candle[5] is not None else None
        ts = datetime.fromtimestamp(int(t_ms) / 1000, timezone.utc) if t_ms else datetime.now(timezone.utc)
        return int(on_candle(float(o), float(h), float(l), float(c), ts, volume=volume))
```

- [ ] **Step 7: 구현 — 백테스트 봉 루프가 거래량을 넘긴다**

`backend/app/engine/backtest.py` 의 `_run_candle_engine`. `run_backtest` 의 독스트링이 `df` 열을
`timestamp, open, high, low, close, volume` 로 적어 두었지만, 열이 없는 데이터프레임도 올 수 있으니
있을 때만 읽는다:

```python
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    # 거래량 필터만 쓰는 열. 없으면 None 으로 넘겨 필터가 막게 한다(원칙 2).
    volumes = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else None
    times = df["timestamp"].tolist()

    sim = make_candle_sim(macro)
    equity_curve: List[EquityPoint] = []
    for i in range(len(closes)):
        ts = pd.Timestamp(times[i]).to_pydatetime()
        sim.on_candle(opens[i], highs[i], lows[i], closes[i], ts,
                      volume=None if volumes is None else float(volumes[i]))
        equity_curve.append(EquityPoint(t=_iso(times[i]), equity=round(sim.equity(closes[i]), 4)))
```

- [ ] **Step 8: 통과 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_volume.py tests/test_entry_filter_sims.py -q
```

- [ ] **Step 9: 기존 시험 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "candle or engine or backtest or paper or driver or runner" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py
```
기대: 새로 깨지는 것 없음.

- [ ] **Step 10: 커밋**

```bash
git add backend/app/engine/candles.py backend/app/engine/driver.py backend/app/engine/backtest.py backend/tests/test_entry_filter_volume.py
git commit -m "feat(engine): 봉의 거래량을 심까지 보낸다 — 거래량 필터 작동"
```

---

### Task 5: 설명에 필터 한 줄

**Files:**
- Modify: `backend/app/engine/explain.py` (`_points`)
- Test: `backend/tests/test_entry_filter_explain.py` (새 파일)

**Interfaces:**
- Consumes: Task 2 의 `FilterEval.note()`, Task 1 의 `Macro.entry_filter`
- Produces: 없음 (화면용 문구)

- [ ] **Step 1: 실패하는 시험 작성**

```python
"""필터가 걸린 매크로의 설명에 필터가 보인다 — 없으면 '왜 안 샀는지' 를 설명에서 못 찾는다."""
from app.engine.backtest import BacktestResult
from app.engine.explain import explain_result
from app.engine.schema import Macro

BODY = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}
RESULT = BacktestResult(final_return_pct=1.0, win_rate_pct=50.0, mdd_pct=5.0, total_trades=4,
                        initial_capital=1000.0, final_equity=1010.0, equity_curve=[])


def test_filter_appears_in_the_points():
    macro = Macro(**{**BODY, "entry_filter": {"kind": "ma", "params": {"period": 20, "side": "above"}}})
    text = " ".join(explain_result(macro, RESULT).points)
    assert "20" in text and "이동평균" in text


def test_no_filter_adds_no_line():
    bare = explain_result(Macro(**BODY), RESULT).points
    filtered = explain_result(
        Macro(**{**BODY, "entry_filter": {"kind": "volume", "params": {"period": 20, "multiple": 2.0}}}),
        RESULT,
    ).points
    assert len(filtered) == len(bare) + 1
```

`BacktestResult` 의 필수 필드는 위에 넘긴 일곱 개가 전부다 ([backtest.py:37](../../../backend/app/engine/backtest.py)) — `final_return_pct` · `win_rate_pct` · `mdd_pct` · `total_trades` · `initial_capital` · `final_equity` · `equity_curve`. `buy_hold_return_pct` 를 주지 않았으므로 `_points` 의 홀딩 비교 대신 "이 기간 최종 수익률은" 줄이 나오고, `total_trades=4` 라서 MDD·승률 줄까지 세 줄이 된다. 필터가 붙으면 네 줄이다.

- [ ] **Step 2: 실패 확인** → `assert "20" in text` 실패

- [ ] **Step 3: 구현**

`_points(macro, r)` 는 `pts: List[str]` 를 쌓아 돌려준다. **함수 맨 끝 `return pts` 바로 앞**에
한 줄을 더한다 — 앞쪽 문구들(홀딩 대비·MDD·승률)의 순서를 건드리지 않는다:

```python
    # 필터는 '왜 안 샀나' 의 근거다 — 수익률 이야기 뒤에 붙인다.
    if macro.entry_filter is not None:
        from .entry_filter import make_filter
        pts.append(
            f"진입 조건을 하나 더 걸었어 — {make_filter(macro).note()}. "
            "조건이 아닐 때는 사지 않아."
        )

    return pts
```

말투는 그 파일의 기존 문구와 같은 반말이다(`_points` 의 다른 줄들이 "앞섰어" · "빠졌어" 다).

- [ ] **Step 4: 통과 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_explain.py tests/ -q -k "explain or entry_filter"
```

- [ ] **Step 5: 커밋**

```bash
git add backend/app/engine/explain.py backend/tests/test_entry_filter_explain.py
git commit -m "feat(engine): 설명에 진입 필터 한 줄"
```

---

### Task 6: 한 줄 요약에 필터

**Files:**
- Modify: `backend/app/engine/summary.py` (`human_summary`)
- Test: `backend/tests/test_entry_filter_summary.py` (새 파일)

**Interfaces:**
- Consumes: Task 2 의 `FilterEval.note()`, Task 1 의 `Macro.entry_filter`
- Produces: 없음

**왜 이 작업이 필요한가:** `human_summary(macro)` 는 리더보드 전략 칸·공유 카드·실행 독·AI 해설이
**모두 쓰는 한 줄**이다. 프런트의 [`leaderboardStrategy`](../../../frontend/src/lib/leaderboardStrategy.js)
는 매크로 필드를 직접 읽지 않고 서버가 만든 `human_summary` 를 잘라 쓴다. 그래서 여기 안 넣으면
리더보드에서 필터가 **보이지 않는다** — 필터가 걸린 전략을 복사해 온 사람이 왜 거래가 적은지 알 수 없다.

- [ ] **Step 1: 실패하는 시험 작성**

`backend/tests/test_entry_filter_summary.py`:

```python
"""한 줄 요약에 필터가 보인다 — 리더보드·카드·실행 독이 이 문장을 쓴다."""
from app.engine.schema import Macro
from app.engine.summary import human_summary

BODY = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}


def test_filter_is_appended_to_the_summary():
    macro = Macro(**{**BODY, "entry_filter": {"kind": "ma", "params": {"period": 20, "side": "above"}}})
    text = human_summary(macro)
    assert "20봉" in text and "이동평균" in text and "위" in text


def test_summary_without_filter_is_unchanged():
    assert human_summary(Macro(**BODY)) == human_summary(
        Macro(**{**BODY, "entry_filter": None})
    )


def test_filter_comes_after_the_capital_part():
    macro = Macro(**{**BODY, "entry_filter": {"kind": "volume", "params": {"period": 20, "multiple": 2.0}}})
    parts = human_summary(macro).split(" · ")
    assert "투입" in parts[-2] and "거래량" in parts[-1]


def test_every_filter_kind_reads_as_a_condition():
    for kind, params in (("ma", {"period": 20, "side": "below"}),
                         ("rsi", {"period": 14, "max": 70}),
                         ("bb", {"period": 20, "zone": "inside"}),
                         ("volume", {"period": 20, "multiple": 2.0})):
        macro = Macro(**{**BODY, "entry_filter": {"kind": kind, "params": params}})
        tail = human_summary(macro).split(" · ")[-1]
        assert tail.startswith("진입 조건:"), (kind, tail)
```

- [ ] **Step 2: 실패 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_summary.py -q
```
기대: `assert "20봉" in text` 실패.

- [ ] **Step 3: 구현**

`human_summary` 의 레버리지 블록 **다음**, 거래소 삽입 **앞**에 넣는다. 자금·레버리지 뒤에 와야
요약의 기존 순서가 그대로 읽힌다:

```python
    if macro.entry_filter is not None:
        from .entry_filter import make_filter
        parts.append(f"진입 조건: {make_filter(macro).note()}")

    if macro.exchange != "binance":
        from ..exchanges import capabilities
        parts.insert(0, f"{capabilities(macro.exchange)['label']} · KRW 현물")
    return " · ".join(parts)
```

- [ ] **Step 4: 통과 확인**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_entry_filter_summary.py -q
```

- [ ] **Step 5: 요약 문장을 고정한 기존 시험 확인 — 중요**

`human_summary` 출력을 글자 그대로 고정한 시험이 여러 파일에 있다 (`test_api.py`,
`test_admin.py`, `test_exchange_contracts.py`, `test_leaderboard_carryover.py`,
`test_chat_members.py` 등). 필터가 없는 매크로는 문장이 바뀌지 않아야 한다:

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "summary or api or admin or leaderboard or exchange_contracts or chat_members or card" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py
```
기대: 새로 깨지는 것 **없음**. 하나라도 깨지면 필터 없는 매크로의 문장을 건드린 것이다.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/engine/summary.py backend/tests/test_entry_filter_summary.py
git commit -m "feat(engine): 한 줄 요약에 진입 필터 — 리더보드·카드·실행 독이 함께 본다"
```

---

### Task 7: 프런트 매크로 계층

**Files:**
- Modify: `frontend/src/lib/macro.js` (`defaultForm`, `buildMacro`, `macroToForm`, `withTypeDefaults`, `validateDetailed`)
- Test: `frontend/tests/entryFilter.test.js` (새 파일)

**Interfaces:**
- Consumes: Task 1 의 `entry_filter` 매크로 모양
- Produces: `FILTERABLE_RULE_TYPES`, `FILTER_KINDS` (export), 평평한 폼 키 — `use_entry_filter`, `filter_kind`, `filter_ma_type`, `filter_ma_period`, `filter_ma_side`, `filter_rsi_period`, `filter_rsi_min`, `filter_rsi_max`, `filter_bb_period`, `filter_bb_num_std`, `filter_bb_zone`, `filter_vol_period`, `filter_vol_multiple`

- [ ] **Step 1: 실패하는 시험 작성**

`frontend/tests/entryFilter.test.js`:

```javascript
import assert from "node:assert/strict";
import { test } from "node:test";
import { FILTERABLE_RULE_TYPES, buildMacro, defaultForm, macroToForm, validateDetailed, withTypeDefaults }
  from "../src/lib/macro.js";

const breakout = () => withTypeDefaults({ ...defaultForm(), symbol: "BTCUSDT" }, "I");

test("필터를 끄면 매크로에 entry_filter 가 없다", () => {
  assert.equal(buildMacro(breakout()).entry_filter, null);
});

test("이동평균 필터를 켜면 매크로에 실린다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "ma", filter_ma_period: 20, filter_ma_side: "above" };
  assert.deepEqual(buildMacro(form).entry_filter, { kind: "ma", params: { ma_type: "SMA", period: 20, side: "above" } });
});

test("RSI 필터는 비워 둔 쪽을 보내지 않는다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "rsi", filter_rsi_period: 14, filter_rsi_min: "", filter_rsi_max: 70 };
  assert.deepEqual(buildMacro(form).entry_filter.params, { period: 14, max: 70 });
});

test("거래량 필터가 실린다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "volume", filter_vol_period: 20, filter_vol_multiple: 2 };
  assert.deepEqual(buildMacro(form).entry_filter.params, { period: 20, multiple: 2 });
});

test("필터를 못 쓰는 규칙으로 바꾸면 필터를 버린다", () => {
  const on = { ...breakout(), use_entry_filter: true, filter_kind: "ma", filter_ma_period: 20, filter_ma_side: "above" };
  for (const rt of ["A", "B", "C", "D"]) {
    assert.equal(withTypeDefaults(on, rt).use_entry_filter, false, rt);
    assert.equal(buildMacro(withTypeDefaults(on, rt)).entry_filter, null, rt);
  }
});

test("필터를 쓰는 규칙끼리 바꾸면 필터가 남는다", () => {
  const on = { ...breakout(), use_entry_filter: true, filter_kind: "ma", filter_ma_period: 20, filter_ma_side: "above" };
  for (const rt of FILTERABLE_RULE_TYPES) {
    assert.equal(withTypeDefaults(on, rt).use_entry_filter, true, rt);
  }
});

test("일곱 규칙만 필터를 쓴다", () => {
  assert.deepEqual([...FILTERABLE_RULE_TYPES].sort(), ["E", "F", "G", "H", "I", "J", "K"]);
});

test("검증이 서버와 같은 규칙으로 먼저 막는다", () => {
  const bad = { ...breakout(), use_entry_filter: true, filter_kind: "ma", filter_ma_period: 1, filter_ma_side: "above" };
  assert.equal(validateDetailed(bad).field, "filter_ma_period");
  const noBound = { ...breakout(), use_entry_filter: true, filter_kind: "rsi", filter_rsi_period: 14, filter_rsi_min: "", filter_rsi_max: "" };
  assert.ok(validateDetailed(noBound).field.startsWith("filter_rsi"));
  const crossed = { ...breakout(), use_entry_filter: true, filter_kind: "rsi", filter_rsi_period: 14, filter_rsi_min: 70, filter_rsi_max: 30 };
  assert.ok(validateDetailed(crossed));
});

test("켠 필터가 왕복한다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "bb", filter_bb_period: 20, filter_bb_num_std: 2, filter_bb_zone: "inside" };
  const back = macroToForm(buildMacro(form));
  assert.equal(back.use_entry_filter, true);
  assert.equal(back.filter_kind, "bb");
  assert.equal(back.filter_bb_zone, "inside");
});

test("필터 없는 옛 매크로를 읽으면 꺼진 상태다", () => {
  const macro = buildMacro(breakout());
  delete macro.entry_filter;
  assert.equal(macroToForm(macro).use_entry_filter, false);
});
```

- [ ] **Step 2: 실패 확인**

```bash
cd frontend && node --test tests/entryFilter.test.js
```
기대: `FILTERABLE_RULE_TYPES` import 실패.

- [ ] **Step 3: 구현 — `macro.js`**

`RULE_TYPES` 정의 뒤에:

```javascript
// 서버 FILTERABLE_TYPES 와 같아야 한다 — A·B·C 는 실시간에서 캔들을 안 받고, D 그리드는
// 사다리 중간을 막으면 팔 짝 없는 매수가 남는다.
export const FILTERABLE_RULE_TYPES = Object.freeze(["E", "F", "G", "H", "I", "J", "K"]);
export const FILTER_KINDS = Object.freeze([
  { value: "ma", label: "이동평균 위/아래" },
  { value: "rsi", label: "RSI 구간" },
  { value: "bb", label: "볼린저 위치" },
  { value: "volume", label: "거래량 배수" },
]);
```

`defaultForm()` 에 기본값을 더한다:

```javascript
  use_entry_filter: false, filter_kind: "ma",
  filter_ma_type: "SMA", filter_ma_period: 20, filter_ma_side: "above",
  filter_rsi_period: 14, filter_rsi_min: "", filter_rsi_max: 70,
  filter_bb_period: 20, filter_bb_num_std: 2, filter_bb_zone: "inside",
  filter_vol_period: 20, filter_vol_multiple: 2,
```

필터 조립 함수 (`buildParams` 옆):

```javascript
function buildEntryFilter(form) {
  if (!form.use_entry_filter || !FILTERABLE_RULE_TYPES.includes(form.rule_type)) return null;
  switch (form.filter_kind) {
    case "ma":
      return { kind: "ma", params: { ma_type: form.filter_ma_type, period: num(form.filter_ma_period), side: form.filter_ma_side } };
    case "rsi": {
      const params = { period: num(form.filter_rsi_period) };
      // 비워 둔 쪽은 보내지 않는다 — 서버가 null 과 '없음' 을 다르게 읽는다.
      if (optNum(form.filter_rsi_min) !== null) params.min = num(form.filter_rsi_min);
      if (optNum(form.filter_rsi_max) !== null) params.max = num(form.filter_rsi_max);
      return { kind: "rsi", params };
    }
    case "bb":
      return { kind: "bb", params: { period: num(form.filter_bb_period), num_std: num(form.filter_bb_num_std), zone: form.filter_bb_zone } };
    default:
      return { kind: "volume", params: { period: num(form.filter_vol_period), multiple: num(form.filter_vol_multiple) } };
  }
}
```

`buildMacro` 의 돌려주는 객체에 한 줄 — `params: buildParams(rt, form),` 다음:

```javascript
    entry_filter: buildEntryFilter(form),
```

`withTypeDefaults` 에 — `if (rt === "C") next.leverage = 1;` 다음:

```javascript
  // 필터를 못 쓰는 규칙으로 옮기면 필터를 버린다. 남겨 두면 서버가 거부하고
  // 사용자는 왜 안 되는지 알 수 없다.
  if (!FILTERABLE_RULE_TYPES.includes(rt)) next.use_entry_filter = false;
```

`macroToForm` 에:

```javascript
  const ef = macro.entry_filter;
  f.use_entry_filter = !!ef;
  if (ef) {
    f.filter_kind = ef.kind;
    const p = ef.params || {};
    if (ef.kind === "ma") Object.assign(f, { filter_ma_type: p.ma_type ?? "SMA", filter_ma_period: p.period ?? 20, filter_ma_side: p.side ?? "above" });
    else if (ef.kind === "rsi") Object.assign(f, { filter_rsi_period: p.period ?? 14, filter_rsi_min: p.min ?? "", filter_rsi_max: p.max ?? "" });
    else if (ef.kind === "bb") Object.assign(f, { filter_bb_period: p.period ?? 20, filter_bb_num_std: p.num_std ?? 2, filter_bb_zone: p.zone ?? "inside" });
    else Object.assign(f, { filter_vol_period: p.period ?? 20, filter_vol_multiple: p.multiple ?? 2 });
  }
```

`validateDetailed` 에, 규칙별 검증 뒤:

```javascript
  if (form.use_entry_filter && FILTERABLE_RULE_TYPES.includes(rt)) {
    const k = form.filter_kind;
    if (k === "ma") {
      const period = num(form.filter_ma_period);
      if (!(period >= 2 && period <= 400)) return fail("filter_ma_period", "이동평균 기간은 2~400 봉이에요.");
    } else if (k === "rsi") {
      const period = num(form.filter_rsi_period);
      if (!(period >= 2 && period <= 200)) return fail("filter_rsi_period", "RSI 기간은 2~200 봉이에요.");
      const lo = optNum(form.filter_rsi_min), hi = optNum(form.filter_rsi_max);
      if (lo === null && hi === null) return fail("filter_rsi_max", "RSI 위 또는 아래 한쪽은 정해 주세요.");
      if (lo !== null && hi !== null && lo > hi) return fail("filter_rsi_min", "RSI 아래 값이 위 값보다 클 수 없어요.");
      for (const [key, v] of [["filter_rsi_min", lo], ["filter_rsi_max", hi]]) {
        if (v !== null && !(v >= 0 && v <= 100)) return fail(key, "RSI 는 0~100 사이예요.");
      }
    } else if (k === "bb") {
      const period = num(form.filter_bb_period);
      if (!(period >= 2 && period <= 400)) return fail("filter_bb_period", "볼린저 기간은 2~400 봉이에요.");
      const sd = num(form.filter_bb_num_std);
      if (!(sd > 0 && sd <= 5)) return fail("filter_bb_num_std", "표준편차 배수는 0 보다 크고 5 이하예요.");
    } else {
      const period = num(form.filter_vol_period);
      if (!(period >= 2 && period <= 400)) return fail("filter_vol_period", "거래량 평균 기간은 2~400 봉이에요.");
      const mult = num(form.filter_vol_multiple);
      if (!(mult > 0 && mult <= 100)) return fail("filter_vol_multiple", "거래량 배수는 0 보다 크고 100 이하예요.");
    }
  }
```

- [ ] **Step 4: 통과 확인**

```bash
cd frontend && node --test tests/entryFilter.test.js
```

- [ ] **Step 5: 기존 프런트 시험 확인**

```bash
cd frontend && node --test tests/
```
기대: 새로 깨지는 것 없음.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib/macro.js frontend/tests/entryFilter.test.js
git commit -m "feat(builder): 매크로 계층에 진입 필터 — 조립·왕복·검증"
```

---

### Task 8: 빌더 화면의 필터 칸

**Files:**
- Modify: `frontend/src/components/Builder.jsx`
- Modify: `frontend/src/components/Builder.css` (필요하면)
- Test: `frontend/tests/entryFilterBuilder.test.js` (새 파일)

**Interfaces:**
- Consumes: Task 7 의 `FILTERABLE_RULE_TYPES`, `FILTER_KINDS`, 폼 키들
- Produces: 없음 (마지막 작업)

**주의:** `num`·`sel`·`chk` 는 JSX 컴포넌트가 아니라 **호출하는 함수**다 (`Builder.jsx:328` 주석 — 그래야 입력칸이 타이핑 중 포커스를 잃지 않는다). 새 칸도 같은 방식으로 만들 것.

- [ ] **Step 1: 실패하는 시험 작성**

`frontend/tests/entryFilterBuilder.test.js`:

```javascript
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const jsx = readFileSync(new URL("../src/components/Builder.jsx", import.meta.url), "utf8");

test("필터 칸이 FILTERABLE_RULE_TYPES 로 보일지 정한다", () => {
  assert.match(jsx, /FILTERABLE_RULE_TYPES/);
  assert.match(jsx, /FILTER_KINDS/);
});

test("필터 켜기 체크와 종류 고르기가 있다", () => {
  assert.match(jsx, /use_entry_filter/);
  assert.match(jsx, /filter_kind/);
});

test("네 종류의 칸이 모두 있다", () => {
  for (const key of ["filter_ma_period", "filter_rsi_period", "filter_bb_period", "filter_vol_multiple"]) {
    assert.match(jsx, new RegExp(key), key);
  }
});

test("필터 칸이 위험관리보다 앞에 온다", () => {
  assert.ok(jsx.indexOf("use_entry_filter") < jsx.indexOf("use_daily_max_loss"));
});
```

위 네 시험은 소스에 글자가 있느냐만 본다 — 그것만으로는 **그려지는지** 증명하지 못한다.
`frontend/tests/renderHelper.js` 로 실제 렌더해서 확인한다(이미 있다 · 새 의존성 없음).
`renderComponent(componentPath, props, opts)` 는 HTML 을 돌려주고, `textOf(html)` 이 글자만 남긴다:

```javascript
import { renderComponent, textOf } from "./renderHelper.js";
import { defaultForm, withTypeDefaults } from "../src/lib/macro.js";

const render = (rt, extra = {}) => renderComponent(
  "src/components/Builder.jsx",
  { form: { ...withTypeDefaults({ ...defaultForm(), symbol: "BTCUSDT" }, rt), ...extra }, setForm: () => {} },
);

test("필터를 쓰는 규칙에서는 체크가 보이고, 켜기 전에는 세부 칸이 없다", async () => {
  const text = textOf(await render("I"));
  assert.match(text, /진입 조건 더 달기/);
  assert.doesNotMatch(text, /이동평균 기간/);
});

test("켜면 종류와 세부 칸이 나온다", async () => {
  const text = textOf(await render("I", { use_entry_filter: true, filter_kind: "ma" }));
  assert.match(text, /진입 조건 종류/);
  assert.match(text, /이동평균 기간/);
});

test("종류를 바꾸면 그 종류의 칸만 나온다", async () => {
  const text = textOf(await render("I", { use_entry_filter: true, filter_kind: "volume" }));
  assert.match(text, /평균의 몇 배 이상/);
  assert.doesNotMatch(text, /이동평균 기간/);
});

test("필터를 못 쓰는 네 규칙에서는 칸이 아예 없다", async () => {
  for (const rt of ["A", "B", "C", "D"]) {
    assert.doesNotMatch(textOf(await render(rt)), /진입 조건 더 달기/, rt);
  }
});
```

`Builder` 의 props 는 `{ form, setForm, chartSlot, variant, intervalOptions, fieldError }`
([Builder.jsx:282](../../../frontend/src/components/Builder.jsx)) — `form` 과 `setForm` 만 필수다.
렌더가 라우터를 요구하면 세 번째 인자로 `{ router: true }` 를 준다.

- [ ] **Step 2: 실패 확인**

```bash
cd frontend && node --test tests/entryFilterBuilder.test.js
```

- [ ] **Step 3: 구현**

`Builder.jsx` 의 import 에 `FILTERABLE_RULE_TYPES, FILTER_KINDS` 를 더하고, 규칙별 `params` 칸 **다음**, 위험관리 **앞**에 필터 묶음을 그린다. 규칙이 필터를 못 쓰면 **아무것도 그리지 않는다**:

```jsx
      {FILTERABLE_RULE_TYPES.includes(rt) && (
        <div className={g2full}>
          {/* chk 는 opts.term 만 받는다(Builder.jsx:360) — 설명은 종류 칸의 hint 로 붙인다. */}
          {chk("use_entry_filter", "진입 조건 더 달기")}
          {form.use_entry_filter && sel("filter_kind", "진입 조건 종류", FILTER_KINDS,
            { hint: "이 조건이 아닐 때는 사지 않아요. 청산은 그대로예요" })}
          {form.use_entry_filter && form.filter_kind === "ma" && (
            <>
              {sel("filter_ma_type", "이동평균 종류", [{ value: "SMA", label: "단순(SMA)" }, { value: "EMA", label: "지수(EMA)" }])}
              {num("filter_ma_period", "이동평균 기간 (봉)", { step: "1" })}
              {sel("filter_ma_side", "어느 쪽일 때 살까", [{ value: "above", label: "이평선 위" }, { value: "below", label: "이평선 아래" }])}
            </>
          )}
          {form.use_entry_filter && form.filter_kind === "rsi" && (
            <>
              {num("filter_rsi_period", "RSI 기간 (봉)", { step: "1" })}
              {num("filter_rsi_min", "RSI 아래 한도", { hint: "비워두면 아래 한도 없음" })}
              {num("filter_rsi_max", "RSI 위 한도", { hint: "비워두면 위 한도 없음" })}
            </>
          )}
          {form.use_entry_filter && form.filter_kind === "bb" && (
            <>
              {num("filter_bb_period", "볼린저 기간 (봉)", { step: "1" })}
              {num("filter_bb_num_std", "표준편차 배수")}
              {sel("filter_bb_zone", "어느 자리일 때 살까", [
                { value: "inside", label: "밴드 안" },
                { value: "below_lower", label: "하단 밖" },
                { value: "above_upper", label: "상단 밖" },
              ])}
            </>
          )}
          {form.use_entry_filter && form.filter_kind === "volume" && (
            <>
              {num("filter_vol_period", "거래량 평균 기간 (봉)", { step: "1" })}
              {num("filter_vol_multiple", "평균의 몇 배 이상")}
            </>
          )}
        </div>
      )}
```

세 도우미의 인자 모양 (`Builder.jsx:330~368`):
`num(key, label, { step, hint, term, unit, wide })` ·
`sel(key, label, [{ value, label }], { hint, term, wide })` ·
`chk(key, label, { term })` — **`chk` 는 `hint` 를 받지 않는다**(넘기면 조용히 버려진다).

- [ ] **Step 4: 통과 확인**

```bash
cd frontend && node --test tests/entryFilterBuilder.test.js
```

- [ ] **Step 5: 전체 프런트 시험**

```bash
cd frontend && node --test tests/
```
기대: 새로 깨지는 것 없음. 특히 `Builder` 를 렌더하는 기존 시험들.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/components/Builder.jsx frontend/src/components/Builder.css frontend/tests/entryFilterBuilder.test.js
git commit -m "feat(builder): 진입 조건 칸 — 필터를 쓰는 일곱 규칙에서만 보인다"
```
