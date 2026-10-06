# 포트폴리오 묶음 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 묶음 매크로에 종목별 비중 · 레그별 규칙 · 묶음 단위 한도를 열고, 묶음 매크로의 검증(워크포워드)을 가능하게 한다.

**Architecture:** `Macro` 에 `legs`(비중 + 규칙 덮어쓰기)와 `bundle_risk`(묶음 한도)를 더한다. 한도는 `CandleSim._entry_blocked` 한 자리에 꽂는 `BundleGate` 가 집행한다 — 진입 필터가 쓰는 바로 그 관문이라 "청산을 막지 않는다 · 정리성 주문을 막지 않는다" 가 공짜로 따라온다. 한도가 있을 때만 백테스트가 레그 동기(lockstep) 봉 루프로 돌아 백테스트와 실시간이 같은 답을 낸다.

**Tech Stack:** Python 3 / FastAPI / SQLModel / pydantic v2 / pandas (backend), React + Vite (frontend)

**Spec:** `docs/superpowers/specs/2026-10-07-portfolio-bundle-design.md`

## Global Constraints

- **시험 실행.** backend: `cd backend && .venv/Scripts/python.exe -m pytest tests/<file> -q`. 맨 `python` 은 `ModuleNotFoundError: korcen` 으로 죽는다. frontend: `cd frontend && node --test "tests/*.test.js"` — **글롭에 따옴표 필수**(Node v24.18 에서 `node --test tests/` 는 실패).
- **수집에서 실패하는 시험 파일 6개** — 전체 수트를 돌릴 때 `--ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py`.
- **기존 실패 4개는 회귀가 아니다** — `test_runner_release.py::test_all_release_surfaces_match_runner_source`, `test_openai_migration.py::test_no_old_provider_sdk_or_key_gates_remain`(cp949 로케일), `test_agent_collector_runner.py` 2개(prefect).
- **실행기는 건드리지 않는다.** `runner/` 아래 어떤 파일도 수정 금지. `RUNNER_VERSION = "10"`, `SIGNAL_MIN_VERSION = "8"`, `DOMESTIC_MIN_VERSION = "10"` 모두 그대로. 묶음 매크로 거절 네 곳(`PORTFOLIO_UNSUPPORTED_DETAIL` 3곳 + `PORTFOLIO_REFUSED_NOTE`)도 그대로 둔다.
- **DPAPI `_ENTROPY = b"ggparrot-runner-credentials-v1"` 는 절대 바꾸지 않는다** — 바꾸면 기존 자격증명 파일이 영구히 열리지 않는다.
- **`Macro` 에 칸이 늘면 모든 서명이 바뀐다.** Task 2 가 `SIG_VERSION = 4` 로 올리기 전에는 서명 시험이 깨진 채로 있어도 된다(Task 1 → Task 2 순서가 그 이유다).
- **한국어 문구.** 사용자에게 보이는 모든 문장·검증 오류는 한국어. 코드 주석도 이 저장소 관례대로 한국어.
- **관문은 진입만 막는다.** 어떤 Task 에서도 청산·안전주문·그리드 보충·K 방어 숏 경로에 관문을 넣지 않는다.
- **파일 인코딩**: UTF-8, LF. 파이썬 파일에 BOM 을 넣지 않는다.

## Review Focus

이 다섯 가지는 스펙이 함축하지만 어느 Task 의 시험도 자연히 건드리지 않는다. 각 줄의 시험을 그 코드를 가진 Task 에 넣어 뒀다.

1. **비중이 균등이 아닌 묶음을 기존(한도 없는) 경로로 돌렸을 때 자금이 비중대로 갈라지는가** — 균등 분배 코드가 한 군데라도 남으면 50/30/20 이 33/33/33 으로 돈다. (Task 5 Step 1)
2. **상장 시점이 다른 레그를 동기 루프에 넣었을 때** — 짧은 레그가 긴 레그의 봉에서 `on_candle` 을 받으면 없는 가격으로 거래한다. (Task 4 Step 5)
3. **v3 서명이 붙은 묶음 매크로 파일** — 범위 허용 때문에 v3 가 통과되면 `legs` 를 손으로 끼워 넣은 파일이 "원본" 으로 보인다. (Task 2 Step 5)
4. **재기동 복구 뒤 한도** — 1차에서 복구 경로를 빠뜨려 차단 결함을 냈다. 복구된 장부로 관문이 즉시 올바른 답을 내는지. (Task 7 Step 5)
5. **한도에 닿은 레그가 들고 있는 포지션을 청산할 수 있는가** — 원칙 1 이 깨지면 잘못 건 한도가 열린 손실을 키운다. (Task 3 Step 7)

---

### Task 1: 스키마 — `PortfolioLeg` · `BundleRisk` · `Macro` 칸과 검증

**Files:**
- Modify: `backend/app/engine/schema.py`
- Test: `backend/tests/test_portfolio_bundle_schema.py` (create)

**Interfaces:**
- Consumes: 기존 `EntryFilter`, `FILTERABLE_TYPES`, `RuleType`, `validate_symbol`, `Macro.MAX_SYMBOLS`
- Produces: `PortfolioLeg`, `BundleRisk`, `GATEABLE_TYPES`, `Macro.legs`, `Macro.bundle_risk`, `Macro.leg_specs()`, `Macro.leg_rule(leg)`, `Macro.leg_filter(leg)`, `Macro.for_leg(leg, initial_capital=None)`; `Macro.is_portfolio()`·`all_symbols()` 가 `legs` 를 본다

- [ ] **Step 1: 시험 파일을 쓴다 (전부 실패해야 한다)**

`backend/tests/test_portfolio_bundle_schema.py`:

```python
"""묶음 스키마 — 비중 · 레그 덮어쓰기 · 묶음 한도의 검증과 펼치기."""
import pytest
from pydantic import ValidationError

from app.engine.schema import BundleRisk, GATEABLE_TYPES, Macro, PortfolioLeg, RuleType

BASE = {
    "symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h",
    "period": {"preset": "3m"}, "params": {"k": 0.5, "initial_capital": 1000},
    "risk": {"invest_ratio": 1.0},
}


def macro(**over):
    return Macro(**{**BASE, **over})


def legs(*pairs):
    return [{"symbol": s, "weight": w} for s, w in pairs]


# --- 비중 -------------------------------------------------------------
def test_weights_must_sum_to_100():
    with pytest.raises(ValidationError, match="비중의 합"):
        macro(legs=legs(("BTCUSDT", 50), ("ETHUSDT", 30)))


def test_weights_sum_100_accepted_and_order_kept():
    m = macro(legs=legs(("ETHUSDT", 60), ("BTCUSDT", 40)))
    assert [leg.symbol for leg in m.legs] == ["ETHUSDT", "BTCUSDT"]
    assert m.symbol == "ETHUSDT"          # 첫 레그가 대표 종목
    assert m.is_portfolio() and m.all_symbols() == ["ETHUSDT", "BTCUSDT"]


def test_weight_rounding_tolerance():
    """33.33 × 3 = 99.99 는 받는다. 0.01 보다 더 벗어나면 거절."""
    macro(legs=legs(("BTCUSDT", 33.33), ("ETHUSDT", 33.33), ("SOLUSDT", 33.34)))
    with pytest.raises(ValidationError, match="비중의 합"):
        macro(legs=legs(("BTCUSDT", 33.3), ("ETHUSDT", 33.3), ("SOLUSDT", 33.3)))


def test_weight_must_be_positive_and_at_most_100():
    with pytest.raises(ValidationError):
        macro(legs=[{"symbol": "BTCUSDT", "weight": 0}, {"symbol": "ETHUSDT", "weight": 100}])


# --- 레그 목록 자체 ----------------------------------------------------
def test_single_leg_refused():
    with pytest.raises(ValidationError, match="2개 이상"):
        macro(legs=legs(("BTCUSDT", 100)))


def test_duplicate_symbol_refused():
    with pytest.raises(ValidationError, match="같은 종목"):
        macro(legs=legs(("BTCUSDT", 50), ("BTCUSDT", 50)))


def test_more_than_max_symbols_refused():
    many = [("BTCUSDT", 20), ("ETHUSDT", 20), ("SOLUSDT", 20),
            ("XRPUSDT", 20), ("ADAUSDT", 10), ("DOGEUSDT", 10)]
    with pytest.raises(ValidationError, match="최대"):
        macro(legs=legs(*many))


def test_legs_and_symbols_together_refused():
    with pytest.raises(ValidationError, match="함께"):
        macro(symbols=["BTCUSDT", "ETHUSDT"], legs=legs(("BTCUSDT", 50), ("ETHUSDT", 50)))


# --- leg_specs: symbols 형태를 균등 비중으로 ---------------------------
def test_leg_specs_from_symbols_is_even():
    m = macro(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    specs = m.leg_specs()
    assert [s.symbol for s in specs] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert all(abs(s.weight - 100 / 3) < 1e-9 for s in specs)


def test_leg_specs_single_symbol_is_empty():
    assert macro().leg_specs() == []


# --- 레그 규칙 덮어쓰기 ------------------------------------------------
def test_leg_rule_override_requires_params():
    with pytest.raises(ValidationError, match="세부값"):
        macro(legs=[{"symbol": "BTCUSDT", "weight": 50, "rule_type": "E"},
                    {"symbol": "ETHUSDT", "weight": 50}])


def test_leg_rule_override_expands():
    m = macro(legs=[
        {"symbol": "BTCUSDT", "weight": 70, "rule_type": "E",
         "params": {"trail_pct": 3.0, "dip_pct": 2.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 30},
    ])
    btc = m.for_leg(m.legs[0], 700.0)
    eth = m.for_leg(m.legs[1], 300.0)
    assert btc.rule_type is RuleType.E and btc.symbol == "BTCUSDT"
    assert btc.params["trail_pct"] == 3.0 and btc.params["initial_capital"] == 700.0
    assert eth.rule_type is RuleType.I and eth.params["k"] == 0.5
    assert eth.params["initial_capital"] == 300.0
    for leg_macro in (btc, eth):
        assert leg_macro.legs is None and leg_macro.symbols is None
        assert leg_macro.bundle_risk is None and not leg_macro.is_portfolio()


def test_leg_bad_params_names_the_leg():
    with pytest.raises(ValidationError, match="레그 ETHUSDT"):
        macro(legs=[{"symbol": "BTCUSDT", "weight": 50},
                    {"symbol": "ETHUSDT", "weight": 50, "rule_type": "E",
                     "params": {"trail_pct": -5.0, "initial_capital": 1000}}])


# --- 진입 조건 물려받기 ------------------------------------------------
MA_FILTER = {"kind": "ma", "params": {"period": 20, "side": "above"}}


def test_leg_inherits_bundle_filter_when_rule_unchanged():
    m = macro(entry_filter=MA_FILTER, legs=legs(("BTCUSDT", 50), ("ETHUSDT", 50)))
    assert m.leg_filter(m.legs[0]) is not None
    assert m.for_leg(m.legs[0], 500.0).entry_filter is not None


def test_leg_that_changes_rule_does_not_inherit_bundle_filter():
    """다른 규칙을 위해 쓴 조건을 물려받지 않는다 — 조용히 엉뚱한 관문이 붙는 것을 막는다."""
    m = macro(entry_filter=MA_FILTER, legs=[
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_pct": 3.0, "dip_pct": 2.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ])
    assert m.leg_filter(m.legs[0]) is None
    assert m.for_leg(m.legs[0], 500.0).entry_filter is None
    assert m.for_leg(m.legs[1], 500.0).entry_filter is not None   # 규칙 안 바꾼 레그는 물려받는다


def test_leg_own_filter_wins():
    m = macro(entry_filter=MA_FILTER, legs=[
        {"symbol": "BTCUSDT", "weight": 50,
         "entry_filter": {"kind": "rsi", "params": {"period": 14, "max": 70}}},
        {"symbol": "ETHUSDT", "weight": 50},
    ])
    assert m.leg_filter(m.legs[0]).kind.value == "rsi"


def test_leg_filter_on_unfilterable_leg_rule_refused():
    with pytest.raises(ValidationError):
        macro(legs=[
            {"symbol": "BTCUSDT", "weight": 50, "rule_type": "D",
             "params": {"lower_price": 100, "upper_price": 200, "grid_count": 5,
                        "per_grid_invest": 10, "initial_capital": 1000},
             "entry_filter": MA_FILTER},
            {"symbol": "ETHUSDT", "weight": 50},
        ])


# --- 묶음 한도 --------------------------------------------------------
def test_bundle_risk_needs_at_least_one_limit():
    with pytest.raises(ValidationError, match="최소 하나"):
        BundleRisk()


def test_bundle_risk_requires_portfolio():
    with pytest.raises(ValidationError, match="2개 이상"):
        macro(bundle_risk={"max_positions": 2})


def test_bundle_risk_accepted_on_gateable_legs():
    m = macro(legs=legs(("BTCUSDT", 50), ("ETHUSDT", 50)), bundle_risk={"max_positions": 1})
    assert m.bundle_risk.max_positions == 1 and m.bundle_risk.max_exposure_pct is None


def test_bundle_risk_refused_when_a_leg_is_not_gateable():
    """D(그리드)는 사다리 한 칸을 막으면 짝 없는 매수가 남는다 — 한도를 걸 수 없다."""
    with pytest.raises(ValidationError, match="E~K"):
        macro(legs=[
            {"symbol": "BTCUSDT", "weight": 50, "rule_type": "D",
             "params": {"lower_price": 100, "upper_price": 200, "grid_count": 5,
                        "per_grid_invest": 10, "initial_capital": 1000}},
            {"symbol": "ETHUSDT", "weight": 50},
        ], bundle_risk={"max_positions": 1})


def test_bundle_risk_refused_on_tick_driven_bundle_rule():
    with pytest.raises(ValidationError, match="E~K"):
        Macro(**{**BASE, "rule_type": "A", "params": {"take_profit_pct": 5, "initial_capital": 1000},
                 "legs": legs(("BTCUSDT", 50), ("ETHUSDT", 50)),
                 "bundle_risk": {"max_positions": 1}})


def test_max_positions_at_or_above_leg_count_refused():
    """레그 수와 같은 상한은 아무것도 막지 않는다 — 한도를 걸었다고 착각하게 둘 수 없다."""
    with pytest.raises(ValidationError, match="종목 수보다 작아야"):
        macro(legs=legs(("BTCUSDT", 50), ("ETHUSDT", 50)), bundle_risk={"max_positions": 2})


def test_mixed_rules_allowed_without_bundle_risk():
    """한도가 없으면 규칙을 섞어도 된다(관문이 없으니 D 의 짝 문제도 없다)."""
    m = macro(legs=[
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "D",
         "params": {"lower_price": 100, "upper_price": 200, "grid_count": 5,
                    "per_grid_invest": 10, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ])
    assert m.for_leg(m.legs[0], 500.0).rule_type is RuleType.D


def test_gateable_types_matches_filterable():
    from app.engine.schema import FILTERABLE_TYPES
    assert GATEABLE_TYPES == FILTERABLE_TYPES
    assert RuleType.D not in GATEABLE_TYPES and RuleType.E in GATEABLE_TYPES
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_schema.py -q`
Expected: 수집 단계에서 `ImportError: cannot import name 'BundleRisk'`.

- [ ] **Step 3: `PortfolioLeg` · `BundleRisk` · `GATEABLE_TYPES` 를 더한다**

`schema.py` 에서 `FILTERABLE_TYPES = frozenset({...})` 바로 **뒤**:

```python
# 진입 관문(진입 필터 · 묶음 한도)을 안전하게 끼울 수 있는 규칙. 같은 집합을 두 뜻으로
# 쓰므로 이름을 따로 둔다 — D(그리드)는 사다리 한 칸을 막으면 짝 없는 매수가 남고,
# A·B(틱 구동)·C(적립식)는 봉 관문을 지나지 않는다.
GATEABLE_TYPES = FILTERABLE_TYPES


class PortfolioLeg(BaseModel):
    """묶음의 한 다리. 규칙 관련 칸이 None 이면 묶음의 기본값을 쓴다."""

    symbol: str
    weight: float = Field(gt=0, le=100)      # 묶음 초기자금 중 이 레그의 몫(%)
    rule_type: Optional[RuleType] = None
    params: Optional[dict] = None            # rule_type 을 바꿨으면 반드시 같이 준다
    entry_filter: Optional[EntryFilter] = None


class BundleRisk(BaseModel):
    """묶음 전체에 거는 한도. 레그 자금 분배(비중)와는 다른 것이다."""

    # 동시에 포지션을 들고 있을 수 있는 종목 수.
    max_positions: Optional[int] = Field(default=None, ge=1, le=5)
    # 진입 기준 명목금액 합이 묶음 초기자금의 몇 %까지 갈 수 있는가.
    max_exposure_pct: Optional[float] = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> "BundleRisk":
        if self.max_positions is None and self.max_exposure_pct is None:
            raise ValueError("묶음 한도는 최소 하나를 정해야 합니다")
        return self
```

- [ ] **Step 4: `Macro` 에 칸 두 개와 접근자를 더한다**

`entry_filter: Optional[EntryFilter] = None` 과 `created_at` 사이:

```python
    # 묶음(포트폴리오) — 종목별 비중과 규칙 덮어쓰기. symbols 와 함께 쓸 수 없다.
    legs: Optional[list[PortfolioLeg]] = None
    bundle_risk: Optional[BundleRisk] = None
```

`is_portfolio`·`all_symbols` 를 고치고 접근자를 더한다(기존 `for_symbol` 바로 뒤):

```python
    def all_symbols(self) -> list[str]:
        """Every symbol this macro runs on (>=1). Portfolio when len > 1."""
        if self.legs:
            return [leg.symbol for leg in self.legs]
        return self.symbols if self.symbols else [self.symbol]

    def is_portfolio(self) -> bool:
        return bool(self.legs) or (bool(self.symbols) and len(self.symbols) > 1)

    def leg_specs(self) -> list["PortfolioLeg"]:
        """묶음을 레그 목록으로 정규화한다. symbols 형태는 균등 비중으로 바꿔 돌려준다.

        단일 종목 매크로는 빈 목록 — 호출부가 "묶음이 아니다" 로 읽는다.
        """
        if self.legs:
            return list(self.legs)
        syms = self.all_symbols()
        if len(syms) < 2:
            return []
        weight = 100.0 / len(syms)
        return [PortfolioLeg(symbol=s, weight=weight) for s in syms]

    def leg_rule(self, leg: "PortfolioLeg") -> RuleType:
        return leg.rule_type if leg.rule_type is not None else self.rule_type

    def leg_filter(self, leg: "PortfolioLeg") -> Optional[EntryFilter]:
        """레그의 실효 진입 조건.

        레그가 규칙을 바꿨으면 묶음의 조건은 물려받지 않는다 — 그 조건은 다른 규칙을 위해
        쓴 것이고, 조용히 엉뚱한 관문이 붙는 것이 사용자에게 가장 설명하기 어려운 결과다.
        """
        if leg.entry_filter is not None:
            return leg.entry_filter
        if leg.rule_type is not None and leg.rule_type is not self.rule_type:
            return None
        return self.entry_filter

    def for_leg(self, leg: "PortfolioLeg", initial_capital: Optional[float] = None) -> "Macro":
        """레그 하나를 단일 종목 매크로로 펼친다. 묶음 칸은 지워서 돌려준다."""
        data = self.model_dump()
        data["symbol"] = leg.symbol
        data["symbols"] = None
        data["legs"] = None
        data["bundle_risk"] = None
        data["rule_type"] = self.leg_rule(leg)
        if leg.params is not None:
            data["params"] = dict(leg.params)
        eff_filter = self.leg_filter(leg)
        data["entry_filter"] = eff_filter.model_dump() if eff_filter is not None else None
        if initial_capital is not None:
            params = dict(data.get("params") or {})
            if params.get("initial_capital") is not None:
                params["initial_capital"] = initial_capital
                data["params"] = params
        return Macro(**data)
```

- [ ] **Step 5: `Macro._validate` 에 묶음 검증을 더한다**

기존 `symbols` 정규화 블록(`if self.symbols:` … `self.symbols = None`) **바로 뒤**에 넣는다:

```python
        if self.legs is not None:
            if self.symbols:
                raise ValueError("symbols 와 legs 는 함께 쓸 수 없습니다")
            if len(self.legs) < 2:
                raise ValueError("묶음은 종목 2개 이상이어야 합니다")
            if len(self.legs) > self.MAX_SYMBOLS:
                raise ValueError(f"묶음은 종목 최대 {self.MAX_SYMBOLS}개까지예요")
            seen_legs: list[str] = []
            for leg in self.legs:
                leg.symbol = validate_symbol(leg.symbol, self.exchange)
                if leg.symbol in seen_legs:
                    raise ValueError(f"같은 종목을 두 번 넣었어요: {leg.symbol}")
                seen_legs.append(leg.symbol)
            total = sum(leg.weight for leg in self.legs)
            if abs(total - 100.0) > 0.01:
                raise ValueError(f"비중의 합이 100% 여야 합니다 (지금 {total:g}%)")
            # 첫 레그가 대표 종목 — 슬러그 · 요약 · 단일 종목 경로가 그대로 돈다.
            self.symbol = self.legs[0].symbol

        if self.bundle_risk is not None and not self.is_portfolio():
            raise ValueError("묶음 한도는 종목 2개 이상에서만 쓸 수 있습니다")
```

규칙별 params 분기(`if self.rule_type in _PARAMS_MODEL:`) **바로 앞**, 즉 기존
`entry_filter` 검사 뒤에 레그 펼치기 검증을 넣는다:

```python
        if self.is_portfolio():
            specs = self.leg_specs()
            if self.bundle_risk is not None:
                for leg in specs:
                    rule = self.leg_rule(leg)
                    if rule not in GATEABLE_TYPES:
                        raise ValueError(
                            f"묶음 한도는 규칙 {rule.value} 에 쓸 수 없습니다 — E~K 만 지원해요"
                        )
                cap = self.bundle_risk.max_positions
                if cap is not None and cap >= len(specs):
                    raise ValueError("동시 보유 상한이 종목 수보다 작아야 의미가 있어요")
            for leg in specs:
                if leg.rule_type is not None and leg.rule_type is not self.rule_type \
                        and leg.params is None:
                    raise ValueError(f"레그 {leg.symbol}: 규칙을 바꾸면 세부값도 함께 주세요")
                # 레그를 실제로 펼쳐 Macro 검증기를 통째로 돌린다 — 규칙별 params, 진입 조건
                # 적용 가능 여부, 국내 거래소 제약, 레버리지까지 한 번에 본다. 레그 검증
                # 로직을 두 벌로 베끼면 둘이 어긋나는 날이 온다.
                try:
                    self.for_leg(leg)
                except ValueError as exc:
                    raise ValueError(f"레그 {leg.symbol} 설정을 확인해 주세요") from exc
```

주의: `GATEABLE_TYPES` 가 `_validate` 안에서 보이도록 모듈 최상단에 정의돼 있어야 한다
(Step 3 에서 그렇게 했다). `legs` 가 있으면 `self.legs[0].symbol` 로 `self.symbol` 을
맞추는 일이 `validate_symbol(self.symbol, ...)` **뒤**에 일어난다 — 레그 종목은 자기
`validate_symbol` 을 이미 통과했으므로 괜찮다.

- [ ] **Step 6: 시험을 돌려 통과를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_schema.py -q`
Expected: PASS (24개 전부).

- [ ] **Step 7: 기존 스키마·포트폴리오 시험이 안 깨졌는지 본다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "schema or portfolio or macro" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py`
Expected: 서명 관련 시험만 실패한다(`Macro` 에 칸이 늘어 정규 바이트가 바뀌었다). 그 실패 목록을 보고서에 적는다 — Task 2 가 고친다. 그 밖의 실패가 있으면 그것은 이 Task 의 회귀다.

- [ ] **Step 8: 커밋**

```bash
git add backend/app/engine/schema.py backend/tests/test_portfolio_bundle_schema.py
git commit -m "feat(engine): 묶음 스키마 - 종목별 비중 · 레그 규칙 덮어쓰기 · 묶음 한도"
```

---

### Task 2: 서명 버전 4

**Files:**
- Modify: `backend/app/macro_signing.py`
- Test: `backend/tests/test_macro_signature_bundle.py` (create)

**Interfaces:**
- Consumes: Task 1 의 `Macro.legs`, `Macro.bundle_risk`
- Produces: `SIG_VERSION = 4`; `canonical_bytes(macro, version=)` 가 `version < 4` 에서 두 칸을 뺀다

`Macro` 에 칸이 늘면 `canonical_bytes` 가 내는 바이트가 **모든** 매크로에서 바뀐다. 1차에서 이것을 놓쳐 서명된 `.ggm.json` 전부가 "수정된 파일" 이 될 참이었다. `ACCEPTED_SIG_VERSIONS` 는 이미 `range(1, SIG_VERSION + 1)` 이라 저절로 따라온다.

- [ ] **Step 1: 고정 바이트 시험을 쓴다**

`backend/tests/test_macro_signature_bundle.py`:

```python
"""서명 버전 4 — legs · bundle_risk 를 넣고, 옛 버전은 바이트가 그대로여야 한다.

기대 바이트는 **머지 기준점의 코드가 실제로 낸 값**이다(`model_dump` 에서 되만드는 시험은
이 결함을 못 잡는다 — 되만들기는 오늘의 스키마를 쓰므로 언제나 통과한다).
"""
import json

import pytest

from app.engine.schema import Macro
from app.macro_signing import (
    ACCEPTED_SIG_VERSIONS, SIG_VERSION, canonical_bytes, sign, verify,
)

BASE = {
    "symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h",
    "period": {"preset": "3m"}, "params": {"k": 0.5, "initial_capital": 1000},
    "risk": {"invest_ratio": 1.0}, "created_at": "2026-01-01T00:00:00+00:00",
}


def test_sig_version_is_4():
    assert SIG_VERSION == 4
    assert ACCEPTED_SIG_VERSIONS == (1, 2, 3, 4)


def test_v3_bytes_have_no_bundle_keys():
    """v3 로 서명하던 매크로의 바이트에 새 칸이 끼면 기존 파일이 전부 '수정된 파일' 이 된다."""
    m = Macro(**BASE)
    v3 = json.loads(canonical_bytes(m, version=3).decode("utf-8"))
    assert "legs" not in v3 and "bundle_risk" not in v3
    assert "entry_filter" in v3            # v3 는 entry_filter 를 포함한다
    v2 = json.loads(canonical_bytes(m, version=2).decode("utf-8"))
    assert "entry_filter" not in v2 and "legs" not in v2
    v1 = json.loads(canonical_bytes(m, version=1).decode("utf-8"))
    assert "exchange" not in v1 and "quote_currency" not in v1 and "legs" not in v1


def test_v3_byte_length_frozen():
    """숫자를 손으로 못 박는다 — 어떤 칸이 들어오든 옛 바이트가 바뀌면 여기서 걸린다."""
    m = Macro(**BASE)
    assert len(canonical_bytes(m, version=3)) == 532
    assert len(canonical_bytes(m, version=2)) == 511
    assert len(canonical_bytes(m, version=1)) == 466


def test_v4_bytes_include_bundle_keys():
    m = Macro(**BASE)
    v4 = json.loads(canonical_bytes(m, version=4).decode("utf-8"))
    assert v4["legs"] is None and v4["bundle_risk"] is None


def test_bundle_macro_signs_and_verifies():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 60},
                                  {"symbol": "ETHUSDT", "weight": 40}],
                 "bundle_risk": {"max_positions": 1}})
    block = sign(m)
    assert block["v"] == 4
    assert verify(m, block) is True


def test_v3_signature_on_bundle_macro_refused():
    """v3 를 통과시키면 legs 를 손으로 끼워 넣은 파일이 '원본' 으로 보인다."""
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 60},
                                  {"symbol": "ETHUSDT", "weight": 40}]})
    import hashlib, hmac
    from app.macro_signing import _key
    forged = {
        "v": 3, "alg": "HMAC-SHA256",
        "hmac": hmac.new(_key(), canonical_bytes(m, version=3), hashlib.sha256).hexdigest(),
    }
    assert verify(m, forged) is False


def test_v3_signature_on_plain_macro_still_accepted():
    """묶음이 아닌 옛 파일은 그대로 통과해야 한다 — 이것이 버전 범위의 존재 이유다."""
    m = Macro(**BASE)
    import hashlib, hmac
    from app.macro_signing import _key
    old = {
        "v": 3, "alg": "HMAC-SHA256",
        "hmac": hmac.new(_key(), canonical_bytes(m, version=3), hashlib.sha256).hexdigest(),
    }
    assert verify(m, old) is True
```

- [ ] **Step 2: 고정 길이 세 개를 실제 값으로 맞춘다**

Step 1 의 `532`·`511`·`466` 은 **자리맡김이다.** 다음을 실행해 실제 값을 읽고 시험의 숫자를 그 값으로 고친다:

```bash
cd backend && .venv/Scripts/python.exe -c "
import io,sys
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
from app.engine.schema import Macro
from app.macro_signing import canonical_bytes
B={'symbol':'BTCUSDT','rule_type':'I','candle_interval':'1h','period':{'preset':'3m'},'params':{'k':0.5,'initial_capital':1000},'risk':{'invest_ratio':1.0},'created_at':'2026-01-01T00:00:00+00:00'}
m=Macro(**B)
for v in (1,2,3,4): print(v, len(canonical_bytes(m,version=v)))
"
```

**이 단계는 Step 3 을 한 뒤에 다시 한 번 돌려 값이 변하지 않았음을 확인한다** — v1·v2·v3 길이는 Step 3 전후로 같아야 한다. 달라지면 pop 이 빠진 것이다.

- [ ] **Step 3: `SIG_VERSION` 을 4 로 올리고 pop 을 더한다**

```python
# v1: exchange·quote_currency 가 없던 시절. v2: 그 둘을 포함. v3: entry_filter 를 포함.
# v4: legs·bundle_risk(묶음)를 포함.
# Macro 에 필드가 늘 때마다 서명 대상 JSON 이 바뀌므로, 올리고 옛 버전에서는 그 필드를 뺀다.
SIG_VERSION = 4
```

`canonical_bytes`:

```python
    if version < 3:
        data.pop("entry_filter", None)
    if version < 4:
        data.pop("legs", None)
        data.pop("bundle_risk", None)
```

`verify` 안의 거절 조건을 확장한다. 지금 모양(`version < 3 and macro.entry_filter is not None`) 옆에:

```python
    if version < 4 and (macro.legs is not None or macro.bundle_risk is not None):
        return False
```

- [ ] **Step 4: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_macro_signature_bundle.py tests/test_macro_signature_entry_filter.py tests/test_exchange_contracts.py -q`
Expected: PASS.

- [ ] **Step 5: 머지 기준점과 바이트를 대조한다 (Review Focus 3)**

v3 바이트가 정말 "예전과 같은지" 를 **역사 워크트리**로 확인한다. 자기 코드끼리 비교하는
시험은 이 질문에 답하지 않는다:

```bash
cd /c/Users/RHJ/Desktop/gg_parrot && git worktree add /c/Users/RHJ/AppData/Local/Temp/sigbase 83373ea
cd /c/Users/RHJ/AppData/Local/Temp/sigbase/backend && /c/Users/RHJ/Desktop/gg_parrot/backend/.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'.')
from app.engine.schema import Macro
from app.macro_signing import canonical_bytes, SIG_VERSION
B={'symbol':'BTCUSDT','rule_type':'I','candle_interval':'1h','period':{'preset':'3m'},'params':{'k':0.5,'initial_capital':1000},'risk':{'invest_ratio':1.0},'created_at':'2026-01-01T00:00:00+00:00'}
m=Macro(**B); print('base SIG_VERSION', SIG_VERSION)
for v in (1,2,3): print(v, len(canonical_bytes(m,version=v)))
"
```

세 길이가 Step 2 에서 읽은 값과 **정확히** 같아야 한다. 보고서에 양쪽 숫자를 적는다.
끝나면 `cd /c/Users/RHJ/Desktop/gg_parrot && git worktree remove /c/Users/RHJ/AppData/Local/Temp/sigbase`.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/macro_signing.py backend/tests/test_macro_signature_bundle.py
git commit -m "feat(api): 매크로 서명 v4 - 묶음 칸을 포함하고 옛 버전은 바이트를 지킨다"
```

---

### Task 3: 묶음 관문 `BundleGate`

**Files:**
- Create: `backend/app/engine/bundle.py`
- Modify: `backend/app/engine/candles.py` (`CandleSim.__init__` 한 줄, `_entry_blocked` 두 줄)
- Test: `backend/tests/test_bundle_gate.py` (create)

**Interfaces:**
- Consumes: Task 1 의 `BundleRisk`; `CandleSim.in_position()`, `total_qty()`, `avg_entry()`
- Produces: `BundleGate(risk, total_capital)` with `register(sim)`, `blocks(sim) -> bool`, `note() -> str`; `CandleSim.bundle_gate` 속성(기본 `None`)

- [ ] **Step 1: 시험을 쓴다**

`backend/tests/test_bundle_gate.py`:

```python
"""묶음 관문 — 한도에 닿으면 새 진입을 막고, 청산은 건드리지 않는다."""
import pytest

from app.engine.bundle import BundleGate
from app.engine.schema import BundleRisk


class _Sim:
    """심의 관문이 쓰는 면만 흉내낸다 — 수량과 평균 진입가."""

    def __init__(self, qty=0.0, entry=0.0):
        self.qty, self.entry = qty, entry

    def in_position(self):
        return self.qty > 0

    def total_qty(self):
        return self.qty

    def avg_entry(self):
        return self.entry


def gate(total=1000.0, **limits):
    g = BundleGate(BundleRisk(**limits), total)
    return g


# --- 동시 보유 상한 ---------------------------------------------------
def test_max_positions_blocks_a_flat_leg_when_cap_reached():
    g = gate(max_positions=1)
    held, flat = _Sim(qty=1.0, entry=100.0), _Sim()
    g.register(held); g.register(flat)
    assert g.blocks(flat) is True


def test_max_positions_lets_a_leg_that_already_holds_add_more():
    """추가 매수는 새 종목이 아니다 — 막으면 마틴게일 · 분할 진입이 반 토막 난다."""
    g = gate(max_positions=1)
    held, flat = _Sim(qty=1.0, entry=100.0), _Sim()
    g.register(held); g.register(flat)
    assert g.blocks(held) is False


def test_max_positions_passes_below_cap():
    g = gate(max_positions=2)
    a, b, c = _Sim(qty=1.0, entry=100.0), _Sim(), _Sim()
    for s in (a, b, c):
        g.register(s)
    assert g.blocks(b) is False


def test_max_positions_counts_only_registered_legs():
    g = gate(max_positions=1)
    flat = _Sim()
    g.register(flat)
    assert g.blocks(flat) is False          # 아무도 안 들고 있다


# --- 총 노출 한도 -----------------------------------------------------
def test_exposure_blocks_at_or_above_limit():
    """600 = 1000 × 60% — '닿으면 멈춘다'(원칙 7)이므로 같을 때도 막는다."""
    g = gate(total=1000.0, max_exposure_pct=60.0)
    a, b = _Sim(qty=6.0, entry=100.0), _Sim()
    g.register(a); g.register(b)
    assert g.blocks(b) is True


def test_exposure_passes_below_limit():
    g = gate(total=1000.0, max_exposure_pct=60.0)
    a, b = _Sim(qty=5.0, entry=100.0), _Sim()      # 500 < 600
    g.register(a); g.register(b)
    assert g.blocks(b) is False


def test_exposure_sums_every_leg():
    g = gate(total=1000.0, max_exposure_pct=60.0)
    a, b, c = _Sim(qty=3.0, entry=100.0), _Sim(qty=3.0, entry=100.0), _Sim()
    for s in (a, b, c):
        g.register(s)
    assert g.blocks(c) is True                     # 300 + 300 = 600


def test_exposure_blocks_a_holding_leg_too():
    """노출 한도는 '추가 매수' 도 막는다 — 보유 종목 수와 달리 금액은 더 늘어난다."""
    g = gate(total=1000.0, max_exposure_pct=60.0)
    a = _Sim(qty=6.0, entry=100.0)
    g.register(a)
    assert g.blocks(a) is True


# --- 두 한도 같이 -----------------------------------------------------
def test_both_limits_either_one_blocks():
    g = gate(total=1000.0, max_positions=2, max_exposure_pct=90.0)
    a, b, c = _Sim(qty=1.0, entry=100.0), _Sim(qty=1.0, entry=100.0), _Sim()
    for s in (a, b, c):
        g.register(s)
    assert g.blocks(c) is True                     # 보유 2종목 == 상한
    g2 = gate(total=1000.0, max_positions=3, max_exposure_pct=15.0)
    for s in (a, b, c):
        g2.register(s)
    assert g2.blocks(c) is True                    # 노출 200 >= 150


def test_both_limits_pass_when_neither_binds():
    g = gate(total=1000.0, max_positions=3, max_exposure_pct=90.0)
    a, b, c = _Sim(qty=1.0, entry=100.0), _Sim(), _Sim()
    for s in (a, b, c):
        g.register(s)
    assert g.blocks(b) is False


# --- 문구 -------------------------------------------------------------
def test_note_text():
    assert gate(max_positions=3).note() == "한 번에 3종목까지"
    assert gate(max_exposure_pct=60.0).note() == "총 노출 60% 까지"
    assert gate(max_positions=3, max_exposure_pct=60.0).note() == \
        "한 번에 3종목까지 · 총 노출 60% 까지"
    assert gate(max_exposure_pct=62.5).note() == "총 노출 62.5% 까지"


# --- LiveCandleSim 벗기기 ---------------------------------------------
def test_register_unwraps_live_sim():
    """실시간은 LiveCandleSim 으로 감싼 심을 준다 — 래퍼에는 total_qty 가 없다."""
    class _Wrapper:
        def __init__(self, inner):
            self.inner = inner

    inner = _Sim(qty=6.0, entry=100.0)
    g = gate(total=1000.0, max_exposure_pct=60.0)
    g.register(_Wrapper(inner))
    flat = _Sim()
    g.register(flat)
    assert g.blocks(flat) is True
    assert g.blocks(_Wrapper(flat)) is True        # 질문할 때도 벗긴다
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_bundle_gate.py -q`
Expected: `ModuleNotFoundError: No module named 'app.engine.bundle'`.

- [ ] **Step 3: `bundle.py` 를 쓴다**

```python
"""묶음 한도 — '지금 이 레그가 새로 들어가도 되는가' 를 묶음 전체를 보고 답한다.

진입 필터와 같은 자리(``CandleSim._entry_blocked``)에 꽂힌다. 그래서 청산 · 안전주문 ·
그리드 보충 · K 의 방어 숏은 이 관문을 지나지 않는다 — 설계 원칙 1 과 5 가 공짜로 따라온다.

상태를 따로 세지 않고 **레그 심에게 그때그때 물어본다.** 사건을 세면 재기동 복구 · 강제
청산 · 부분 청산마다 카운터를 맞춰야 하고, 1차에서 바로 그 종류의 어긋남이 차단 결함이
되었다. 지금 장부를 읽으면 어긋날 상태가 없다.
"""
from __future__ import annotations

from typing import List

from .schema import BundleRisk


def _inner(sim):
    """실시간은 ``LiveCandleSim`` 으로 감싼 심을 준다 — 장부는 ``.inner`` 에 있다."""
    return getattr(sim, "inner", sim)


class BundleGate:
    """묶음 한도의 집행자. 백테스트 루프와 ``paper.start_session`` 이 만들어 심에 꽂는다."""

    def __init__(self, risk: BundleRisk, total_capital: float) -> None:
        self.max_positions = risk.max_positions
        self.max_exposure_pct = risk.max_exposure_pct
        self.total_capital = float(total_capital)
        self._sims: List[object] = []

    def register(self, sim) -> None:
        self._sims.append(_inner(sim))

    def blocks(self, sim) -> bool:
        """True 면 이 레그의 **새 진입**을 막는다. 청산에는 영향이 없다."""
        me = _inner(sim)
        if self.max_positions is not None and not me.in_position():
            # 이미 들고 있는 레그는 묻지 않는다 — 추가 매수는 새 종목이 아니다.
            held = sum(1 for s in self._sims if s.in_position())
            if held >= self.max_positions:
                return True
        if self.max_exposure_pct is not None:
            # 진입 기준 명목금액(수량 × 평균 진입가). 시세를 끌어오지 않으므로 백테스트와
            # 실시간이 같은 값을 본다(원칙 4). 보유 중인 레그도 금액은 더 늘어나니 막는다.
            used = sum(s.total_qty() * s.avg_entry() for s in self._sims)
            if used >= self.total_capital * self.max_exposure_pct / 100.0:
                return True
        return False

    def note(self) -> str:
        parts = []
        if self.max_positions is not None:
            parts.append(f"한 번에 {self.max_positions}종목까지")
        if self.max_exposure_pct is not None:
            parts.append(f"총 노출 {self.max_exposure_pct:g}% 까지")
        return " · ".join(parts)
```

- [ ] **Step 4: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_bundle_gate.py -q`
Expected: PASS (15개).

- [ ] **Step 5: 심에 관문 자리를 만든다**

`candles.py` `CandleSim.__init__` 의 마지막 두 줄(`self.entry_filter = make_filter(macro)`) **뒤**:

```python
        # 묶음 한도 — 묶음을 조립하는 쪽(백테스트 루프 · paper.start_session)이 밖에서 꽂는다.
        # 심은 자기가 묶음의 일부인지 모른다.
        self.bundle_gate = None
```

`_entry_blocked` 의 진입 필터 검사 **바로 뒤**:

```python
        if self.bundle_gate is not None and self.bundle_gate.blocks(self):
            return True
```

- [ ] **Step 6: 관문이 실제로 진입을 막는 것을 심 수준에서 확인한다**

`tests/test_bundle_gate.py` 에 덧붙인다:

```python
# --- 심에 꽂았을 때 ---------------------------------------------------
from datetime import datetime, timezone

from app.engine.candles import make_candle_sim
from app.engine.schema import Macro

BREAKOUT = {
    "symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h",
    "period": {"preset": "3m"}, "params": {"k": 0.5, "initial_capital": 1000},
    "risk": {"invest_ratio": 1.0},
}


def _bars(sim, closes):
    """종가만 바꿔 가며 봉을 먹인다. 돌파 규칙이 사게 만드는 가장 단순한 모양."""
    out = []
    for i, c in enumerate(closes):
        ts = datetime(2026, 1, 1, i, tzinfo=timezone.utc)
        out.extend(sim.on_candle(c, c * 1.03, c * 0.99, c, ts, volume=1000.0))
    return out


def test_gate_none_by_default():
    assert make_candle_sim(Macro(**BREAKOUT)).bundle_gate is None


def test_gate_blocks_new_entry_in_the_sim():
    closes = [100, 101, 102, 103, 104, 105, 106]
    free = make_candle_sim(Macro(**BREAKOUT))
    baseline = _bars(free, closes)
    assert any(f.side == "buy" for f in baseline), "대조군이 안 샀다 — 시험이 무의미하다"

    blocked = make_candle_sim(Macro(**BREAKOUT))
    g = BundleGate(BundleRisk(max_positions=1), 1000.0)
    occupant = _Sim(qty=1.0, entry=100.0)      # 다른 레그가 자리를 차지했다
    g.register(occupant); g.register(blocked)
    blocked.bundle_gate = g
    assert not any(f.side == "buy" for f in _bars(blocked, closes))


def test_gate_does_not_block_the_exit(tmp_path):
    """Review Focus 5 — 한도에 닿아도 들고 있는 포지션은 팔 수 있어야 한다.

    원칙 1 이 깨지면 잘못 건 한도가 '못 사게' 가 아니라 '못 팔게' 가 되어 열린 손실을 키운다.
    """
    m = Macro(**{**BREAKOUT, "risk": {"invest_ratio": 1.0, "stop_loss_pct": 2.0}})
    sim = make_candle_sim(m)
    fills = _bars(sim, [100, 101, 102, 103, 104, 105, 106])
    assert sim.in_position(), "진입이 안 됐다 — 청산을 볼 수 없다"
    # 이제 한도를 꽉 채운 관문을 꽂는다. 이미 들고 있는 포지션은 손절로 나와야 한다.
    g = BundleGate(BundleRisk(max_positions=1, max_exposure_pct=1.0), 1000.0)
    g.register(_Sim(qty=100.0, entry=100.0)); g.register(sim)
    sim.bundle_gate = g
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    out = sim.on_candle(106, 106, 80, 82, ts, volume=1000.0)
    assert any(f.side == "sell" for f in out), "관문이 청산을 막았다 — 원칙 1 위반"
    assert not sim.in_position()
```

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_bundle_gate.py -q`
Expected: PASS.

- [ ] **Step 7: 돌연변이로 시험이 무는지 확인한다**

세 번 돌린다. **각각 시험이 실패해야 한다.** 실패하지 않으면 그 시험은 아무것도 증명하지 않으므로 고친다.

1. `bundle.py` 의 `if self.max_positions is not None and not me.in_position():` 에서 `and not me.in_position()` 을 지운다 → `test_max_positions_lets_a_leg_that_already_holds_add_more` 가 실패해야 한다.
2. `used >= self.total_capital * ...` 를 `used > ...` 로 바꾼다 → `test_exposure_blocks_at_or_above_limit` 가 실패해야 한다.
3. `candles.py` 의 `_entry_blocked` 에 넣은 두 줄을 지운다 → `test_gate_blocks_new_entry_in_the_sim` 이 실패해야 한다.

세 번 다 원래대로 되돌린 뒤 수트를 다시 돌려 통과를 확인한다. 보고서에 세 결과를 적는다.

- [ ] **Step 8: 커밋**

```bash
git add backend/app/engine/bundle.py backend/app/engine/candles.py backend/tests/test_bundle_gate.py
git commit -m "feat(engine): 묶음 한도 관문 - 진입만 막고 청산은 건드리지 않는다"
```

---

### Task 4: 레그 동기 백테스트 루프

**Files:**
- Create: `backend/app/engine/portfolio_backtest.py`
- Test: `backend/tests/test_portfolio_bundle_backtest.py` (create)

**Interfaces:**
- Consumes: Task 1 의 `leg_specs`·`for_leg`; Task 3 의 `BundleGate`; 기존 `make_candle_sim`, `backtest._metrics`, `backtest._iso`, `backtest._buy_hold_return_pct`, `backtest._PERIODS_PER_YEAR`, `EquityPoint`
- Produces: `run_bundle(macro, frames) -> list[tuple[str, BacktestResult]]`, `split_frames_by_time(frames, windows) -> list[dict[str, DataFrame]]`

- [ ] **Step 1: 시험을 쓴다**

`backend/tests/test_portfolio_bundle_backtest.py`:

```python
"""레그 동기 백테스트 — 한도가 레그를 가로질러 듣는가, 그리고 한도가 없을 때 기존 경로와 같은가."""
import pandas as pd
import pytest

from app.engine.backtest import run_backtest
from app.engine.portfolio_backtest import run_bundle, split_frames_by_time
from app.engine.schema import Macro

BREAKOUT_PARAMS = {"k": 0.5, "initial_capital": 1000}
BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": BREAKOUT_PARAMS, "risk": {"invest_ratio": 1.0},
}


def frame(closes, start="2026-01-01"):
    ts = pd.date_range(start, periods=len(closes), freq="1h")
    return pd.DataFrame({
        "timestamp": ts,
        "open": closes, "high": [c * 1.03 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1000.0] * len(closes),
    })


RISING = [100, 101, 102, 103, 104, 105, 106, 107]


def bundle(legs, **over):
    return Macro(**{**BASE, "legs": legs, **over})


# --- 한도가 없을 때 두 경로가 같은 답을 낸다 (§6) ----------------------
def test_lockstep_matches_per_leg_path_when_limit_never_binds():
    """노출 한도 100% 는 사실상 한도가 없다. 동기 루프가 기존 경로와 같은 답을 내야 한다."""
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    m = bundle(legs, bundle_risk={"max_exposure_pct": 100.0})
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}

    got = dict(run_bundle(m, frames))
    for sym in ("BTCUSDT", "ETHUSDT"):
        want = run_backtest(m.for_leg(next(l for l in m.legs if l.symbol == sym), 500.0),
                            frames[sym])
        assert got[sym].final_return_pct == want.final_return_pct
        assert got[sym].total_trades == want.total_trades
        assert [p.equity for p in got[sym].equity_curve] == [p.equity for p in want.equity_curve]


# --- 한도가 레그를 가로질러 듣는다 ------------------------------------
def test_max_positions_one_lets_only_one_leg_in():
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}

    free = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}), frames))
    assert free["BTCUSDT"].total_trades > 0 and free["ETHUSDT"].total_trades > 0, \
        "대조군에서 두 레그가 다 거래해야 한다 — 아니면 한도 시험이 무의미하다"

    capped = dict(run_bundle(bundle(legs, bundle_risk={"max_positions": 1}), frames))
    assert capped["BTCUSDT"].total_trades > 0
    assert capped["ETHUSDT"].total_trades == 0, "앞 레그가 자리를 잡았는데 뒤 레그가 들어갔다"


def test_leg_order_decides_who_gets_the_slot():
    """순서를 뒤집으면 자리를 잡는 레그가 바뀐다 — 결정적이고 사용자가 뜻을 표현할 수 있다."""
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}
    first = dict(run_bundle(bundle(
        [{"symbol": "ETHUSDT", "weight": 50}, {"symbol": "BTCUSDT", "weight": 50}],
        bundle_risk={"max_positions": 1}), frames))
    assert first["ETHUSDT"].total_trades > 0 and first["BTCUSDT"].total_trades == 0


def test_exposure_cap_stops_the_second_leg():
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}
    # 한 레그가 자기 자금(500) 전액을 넣으면 노출 50%. 한도를 40% 로 두면 둘째는 못 들어간다.
    capped = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 40.0}), frames))
    assert capped["ETHUSDT"].total_trades == 0


# --- 비중 ------------------------------------------------------------
def test_weights_decide_leg_capital():
    legs = [{"symbol": "BTCUSDT", "weight": 70}, {"symbol": "ETHUSDT", "weight": 30}]
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}), frames))
    assert got["BTCUSDT"].initial_capital == 700.0
    assert got["ETHUSDT"].initial_capital == 300.0


# --- 레그 길이가 다를 때 (Review Focus 2) -----------------------------
def test_shorter_leg_only_steps_on_its_own_bars():
    """상장이 늦은 종목이 없는 가격으로 거래하면 안 된다.

    짧은 레그의 자산곡선 점 수가 자기 봉 수와 같아야 한다 — 긴 레그의 시각에 끌려가면
    없는 봉에서 판정을 내렸다는 뜻이다.
    """
    long_f = frame(RISING)                                   # 8봉, 2026-01-01 00:00~
    short_f = frame(RISING[:4], start="2026-01-01 04:00")    # 4봉, 04:00~
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": long_f, "ETHUSDT": short_f}))
    assert len(got["BTCUSDT"].equity_curve) == 8
    assert len(got["ETHUSDT"].equity_curve) == 4
    # 짧은 레그의 첫 점은 자기 첫 봉 시각이다(긴 레그의 00:00 이 아니다).
    assert got["ETHUSDT"].equity_curve[0].t.startswith("2026-01-01T04:00")


def test_leg_with_no_rows_fails_that_leg_only():
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": frame(RISING), "ETHUSDT": frame([])}))
    assert got["BTCUSDT"].total_trades >= 0
    assert got["ETHUSDT"].total_trades == 0
    assert got["ETHUSDT"].initial_capital == 500.0


# --- 레그별 규칙 ------------------------------------------------------
def test_legs_can_run_different_rules():
    legs = [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_pct": 3.0, "dip_pct": 1.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}))
    assert set(got) == {"BTCUSDT", "ETHUSDT"}


# --- 시간 기준 창 자르기 ----------------------------------------------
def test_split_frames_by_time_uses_the_same_boundaries_for_every_leg():
    long_f = frame(list(range(100, 108)))
    short_f = frame(list(range(100, 104)), start="2026-01-01 04:00")
    parts = split_frames_by_time({"BTCUSDT": long_f, "ETHUSDT": short_f}, 2)
    assert len(parts) == 2
    # 앞 창은 00:00~03:00 — 짧은 레그는 그 구간에 봉이 없다.
    assert len(parts[0]["BTCUSDT"]) == 4 and len(parts[0]["ETHUSDT"]) == 0
    assert len(parts[1]["BTCUSDT"]) == 4 and len(parts[1]["ETHUSDT"]) == 4
    # 행이 빠지거나 겹치지 않는다.
    for sym, f in (("BTCUSDT", long_f), ("ETHUSDT", short_f)):
        assert sum(len(p[sym]) for p in parts) == len(f)


def test_split_frames_by_time_handles_too_few_rows():
    assert split_frames_by_time({"BTCUSDT": frame([100])}, 4) == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_backtest.py -q`
Expected: `ModuleNotFoundError: No module named 'app.engine.portfolio_backtest'`.

- [ ] **Step 3: `portfolio_backtest.py` 를 쓴다**

```python
"""묶음 백테스트 — 레그를 **봉 단위로 나란히** 돌린다.

왜 따로 있나: 기존 묶음 백테스트는 레그를 하나씩 끝까지 돌린 뒤 합산한다. 그 모양에서는
"지금 몇 종목 들고 있나" 를 물을 시점이 없어 묶음 한도를 표현할 수 없다. 실시간은 이미
``StrategyDriver`` 가 레그 전부를 한 객체에 들고 있으므로, 백테스트를 동기 루프로 맞추면
백테스트와 실시간이 같은 코드로 같은 답을 낸다(설계 원칙 4).

한도가 없는 묶음은 이 모듈을 쓰지 않는다 — 기존 경로를 그대로 둬서 기존 매크로의 결과가
한 바이트도 바뀌지 않게 한다. 두 경로가 같은 답을 내는 것은 시험으로 못 박았다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .backtest import (
    _PERIODS_PER_YEAR, BacktestResult, EquityPoint, _buy_hold_return_pct, _iso, _metrics,
)
from .bundle import BundleGate
from .candles import make_candle_sim
from .schema import Macro

TIME_COLUMN = "timestamp"
MIN_ROWS_PER_WINDOW = 2


class _Leg:
    """동기 루프가 레그 하나에 대해 들고 있는 것 전부."""

    __slots__ = ("symbol", "sim", "capital", "rows", "cursor", "curve", "closes")

    def __init__(self, symbol: str, sim, capital: float, df: pd.DataFrame) -> None:
        self.symbol = symbol
        self.sim = sim
        self.capital = capital
        self.rows = df
        self.cursor = 0
        self.curve: List[EquityPoint] = []
        self.closes = df["close"].to_numpy(dtype=float) if len(df) else None


def _volume_at(df: pd.DataFrame, i: int) -> Optional[float]:
    """NaN · inf 는 '모른다(None)' 로 — 0.0("거래가 없었다")과 다른 값이다.

    ``backtest._run_candle_engine`` 과 ``candle_feed._volume`` 이 같은 가드를 갖고 있다.
    """
    if "volume" not in df.columns:
        return None
    v = float(df["volume"].iloc[i])
    return v if math.isfinite(v) else None


def run_bundle(macro: Macro, frames: Dict[str, pd.DataFrame]) -> List[Tuple[str, BacktestResult]]:
    """레그를 봉 단위로 나란히 돌린다. 반환값은 ``portfolio.aggregate`` 가 먹는 모양이다.

    한 시각 안에서 레그 순서는 ``macro.legs`` 에 적힌 순서다. 한도가 걸릴 때 앞 레그가
    자리를 먼저 잡는다 — 결정적이고, 사용자가 순서를 바꿔 뜻을 표현할 수 있다.
    """
    specs = macro.leg_specs()
    if not specs:
        raise ValueError("묶음이 아닌 매크로입니다")
    total = macro.initial_capital or 1_000_000.0

    gate = BundleGate(macro.bundle_risk, total) if macro.bundle_risk is not None else None
    legs: List[_Leg] = []
    for spec in specs:
        df = frames.get(spec.symbol)
        if df is None:
            raise ValueError(f"레그 {spec.symbol} 의 캔들이 없습니다")
        df = df.sort_values(TIME_COLUMN).reset_index(drop=True) if len(df) else df
        capital = total * spec.weight / 100.0
        sim = make_candle_sim(macro.for_leg(spec, capital), initial_capital=capital)
        if gate is not None:
            gate.register(sim)
            sim.bundle_gate = gate
        legs.append(_Leg(spec.symbol, sim, capital, df))

    # 모든 레그의 시각 합집합. 레그는 **자기가 가진 봉에서만** 판정한다 — 상장이 늦은 종목이
    # 없는 가격으로 거래하면 안 된다.
    timeline = sorted({pd.Timestamp(t) for leg in legs for t in leg.rows[TIME_COLUMN]}) \
        if any(len(leg.rows) for leg in legs) else []

    for t in timeline:
        for leg in legs:
            i = leg.cursor
            rows = leg.rows
            if i >= len(rows) or pd.Timestamp(rows[TIME_COLUMN].iloc[i]) != t:
                continue
            ts = t.to_pydatetime()
            close = float(rows["close"].iloc[i])
            leg.sim.on_candle(
                float(rows["open"].iloc[i]), float(rows["high"].iloc[i]),
                float(rows["low"].iloc[i]), close, ts, volume=_volume_at(rows, i),
            )
            leg.curve.append(EquityPoint(t=_iso(rows[TIME_COLUMN].iloc[i]),
                                         equity=round(leg.sim.equity(close), 4)))
            leg.cursor = i + 1

    periods = _PERIODS_PER_YEAR.get(macro.candle_interval, 365.0)
    out: List[Tuple[str, BacktestResult]] = []
    for leg in legs:
        out.append((leg.symbol, _metrics(
            leg.curve,
            leg.sim.closed_trades,
            len(leg.sim.closed_trades),
            leg.sim.initial_capital,
            same_bar_sl_bars=leg.sim.same_bar_sl,
            liquidation_count=leg.sim.liquidations,
            liquidated_loss=leg.sim.liquidated_loss,
            buy_hold_return_pct=(_buy_hold_return_pct(leg.closes)
                                 if leg.closes is not None and len(leg.closes) else None),
            periods_per_year=periods,
        )))
    return out


def split_frames_by_time(frames: Dict[str, pd.DataFrame], windows: int) -> List[Dict[str, pd.DataFrame]]:
    """레그 프레임들을 **같은 시각 경계**로 자른다.

    행 수로 자르면(``walkforward.split_frame``) 레그마다 봉 수가 달라 경계가 어긋난다 —
    묶음에서는 모든 레그가 같은 기간을 보아야 한도가 뜻을 가진다.
    """
    timeline = sorted({pd.Timestamp(t) for df in frames.values() for t in df.get(TIME_COLUMN, [])})
    if len(timeline) < MIN_ROWS_PER_WINDOW:
        return []
    count = max(1, min(int(windows), len(timeline) // MIN_ROWS_PER_WINDOW))
    size = len(timeline) // count
    bounds = []
    for i in range(count):
        lo = timeline[i * size]
        hi = None if i == count - 1 else timeline[(i + 1) * size]
        bounds.append((lo, hi))

    parts: List[Dict[str, pd.DataFrame]] = []
    for lo, hi in bounds:
        cut = {}
        for sym, df in frames.items():
            if TIME_COLUMN not in df.columns or not len(df):
                cut[sym] = df
                continue
            col = pd.to_datetime(df[TIME_COLUMN])
            mask = col >= lo if hi is None else (col >= lo) & (col < hi)
            cut[sym] = df[mask].reset_index(drop=True)
        parts.append(cut)
    return parts
```

- [ ] **Step 4: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_backtest.py -q`
Expected: PASS (11개).

- [ ] **Step 5: 돌연변이로 시험이 무는지 확인한다**

1. 레그가 자기 봉에서만 돈다는 가드(`pd.Timestamp(rows[TIME_COLUMN].iloc[i]) != t` 의 `!= t` 를 `False` 로 바꿔 항상 통과시킨다) → `test_shorter_leg_only_steps_on_its_own_bars` 가 실패해야 한다 (Review Focus 2).
2. `capital = total * spec.weight / 100.0` 를 `capital = total / len(specs)` 로 바꾼다 → `test_weights_decide_leg_capital` 이 실패해야 한다.
3. `sim.bundle_gate = gate` 를 지운다 → `test_max_positions_one_lets_only_one_leg_in` 이 실패해야 한다.

세 번 다 되돌린 뒤 수트를 다시 돌린다. 보고서에 세 결과를 적는다.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/engine/portfolio_backtest.py backend/tests/test_portfolio_bundle_backtest.py
git commit -m "feat(engine): 레그 동기 묶음 백테스트 - 한도가 레그를 가로질러 듣는다"
```

---

### Task 5: 백테스트 API 가 비중과 한도를 쓴다

**Files:**
- Modify: `backend/app/main.py` (묶음 백테스트 분기, ~228-248)
- Test: `backend/tests/test_portfolio_bundle_api.py` (create)

**Interfaces:**
- Consumes: Task 1 의 `leg_specs`·`for_leg`; Task 4 의 `run_bundle`
- Produces: 응답 모양은 바뀌지 않는다(`aggregate` 결과 + `per_symbol`)

- [ ] **Step 1: 시험을 쓴다**

`backend/tests/test_portfolio_bundle_api.py`:

```python
"""묶음 백테스트 경로 — 비중대로 자금이 갈라지고, 한도가 있으면 동기 루프를 쓴다."""
import pandas as pd
import pytest

from app.engine.schema import Macro

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}
RISING = [100, 101, 102, 103, 104, 105, 106, 107]


def frame(closes):
    ts = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    return pd.DataFrame({"timestamp": ts, "open": closes,
                         "high": [c * 1.03 for c in closes], "low": [c * 0.99 for c in closes],
                         "close": closes, "volume": [1000.0] * len(closes)})


@pytest.fixture
def patched(monkeypatch):
    """캔들 조회를 막고 같은 프레임을 돌려준다 — 이 시험은 분배와 경로 선택만 본다."""
    import app.main as main

    calls = []

    def fake_fetch(macro, start_ms, end_ms):
        calls.append(macro)
        return frame(RISING), "test"

    monkeypatch.setattr(main, "fetch_klines_for_macro", fake_fetch)
    return main, calls


# --- Review Focus 1: 비중이 균등이 아닐 때 자금이 비중대로 갈라진다 ----
def test_uneven_weights_split_capital_on_the_plain_path(patched):
    """한도가 없는(기존) 경로에도 비중이 들어야 한다 — 균등 분배 코드가 남으면 여기서 걸린다."""
    main, _ = patched
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                  {"symbol": "ETHUSDT", "weight": 30}]})
    _agg, per_symbol, _src, _label = main._run_portfolio_backtest(m, 0, 1)
    caps = {row["symbol"]: row["final_equity"] for row in per_symbol}
    assert set(caps) == {"BTCUSDT", "ETHUSDT"}
    # 두 레그가 같은 가격 데이터를 받았으므로 최종 자산의 비는 자금의 비와 같다.
    assert caps["BTCUSDT"] / caps["ETHUSDT"] == pytest.approx(70 / 30, rel=1e-6)


def test_even_symbols_form_unchanged(patched):
    """symbols 형태(옛 모양)는 균등 분배 그대로."""
    main, _ = patched
    m = Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]})
    _agg, per_symbol, _src, _label = main._run_portfolio_backtest(m, 0, 1)
    caps = {row["symbol"]: row["final_equity"] for row in per_symbol}
    assert caps["BTCUSDT"] == pytest.approx(caps["ETHUSDT"], rel=1e-9)


def test_bundle_risk_takes_the_lockstep_path(patched, monkeypatch):
    main, _ = patched
    seen = {}
    real = main.portfolio_backtest_mod.run_bundle

    def spy(macro, frames):
        seen["called"] = True
        return real(macro, frames)

    monkeypatch.setattr(main.portfolio_backtest_mod, "run_bundle", spy)
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    main._run_portfolio_backtest(m, 0, 1)
    assert seen.get("called") is True


def test_no_bundle_risk_does_not_take_the_lockstep_path(patched, monkeypatch):
    """한도가 없으면 기존 경로 그대로 — 기존 매크로의 결과를 건드리지 않는다."""
    main, _ = patched
    seen = {}
    monkeypatch.setattr(main.portfolio_backtest_mod, "run_bundle",
                        lambda *a, **k: seen.setdefault("called", True) or [])
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}]})
    main._run_portfolio_backtest(m, 0, 1)
    assert "called" not in seen


def test_each_leg_is_fetched_with_its_own_rule(patched):
    main, calls = patched
    m = Macro(**{**BASE, "legs": [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_pct": 3.0, "dip_pct": 1.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]})
    main._run_portfolio_backtest(m, 0, 1)
    got = {c.symbol: c.rule_type.value for c in calls}
    assert got == {"BTCUSDT": "E", "ETHUSDT": "I"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_api.py -q`
Expected: `AttributeError: module 'app.main' has no attribute '_run_portfolio_backtest'`.

- [ ] **Step 3: 묶음 분기를 함수로 빼고 두 경로를 고르게 한다**

고치는 것은 `main.py` 의 `_run_any(macro)` 다(현재 ~222-247). 지금 모양:

```python
    if macro.is_portfolio():
        syms = macro.all_symbols()
        base = macro.initial_capital
        per_cap = (base / len(syms)) if base else None
        results: list = []
        source = ""
        for sym in syms:
            leg = macro.for_symbol(sym, per_cap)
            df, source = fetch_klines_for_macro(leg, start_ms, end_ms)
            results.append((sym, run_backtest(leg, df)))
        agg, per_symbol = portfolio_mod.aggregate(results, candle_interval=macro.candle_interval)
        return agg, per_symbol, source, label
```

이 블록을 `return _run_portfolio_backtest(macro, start_ms, end_ms, label)` 한 줄로 바꾸고,
`_run_any` **앞**에 새 함수를 둔다. import 블록(`portfolio as portfolio_mod` 옆)에
`from .engine import portfolio_backtest as portfolio_backtest_mod` 를 더한다.

```python
def _run_portfolio_backtest(
    macro: Macro, start_ms: int, end_ms: int, label: str = ""
) -> tuple[BacktestResult, list, str, str]:
    """묶음 매크로의 백테스트. 반환은 (집계 결과, 종목별, 데이터 출처, 기간 라벨).

    한도(``bundle_risk``)가 있으면 레그를 봉 단위로 나란히 돌린다 — 그래야 "한 번에 몇
    종목" 을 물을 시점이 생기고, 실시간과 같은 답이 나온다. 한도가 없으면 기존 경로를
    그대로 쓴다(기존 묶음 매크로의 결과를 한 바이트도 바꾸지 않는다).

    ``source`` 는 지금처럼 마지막 레그의 값이다 — 레그가 서로 다른 출처를 쓰는 일은
    없으므로(같은 거래소 · 같은 시장) 뜻이 같다.
    """
    specs = macro.leg_specs()
    base = macro.initial_capital
    source = ""
    frames: dict = {}
    leg_macros: list = []
    for spec in specs:
        cap = (base * spec.weight / 100.0) if base else None
        leg = macro.for_leg(spec, cap)
        df, source = fetch_klines_for_macro(leg, start_ms, end_ms)
        frames[spec.symbol] = df
        leg_macros.append((spec.symbol, leg))

    if macro.bundle_risk is not None:
        results = portfolio_backtest_mod.run_bundle(macro, frames)
    else:
        results = [(sym, run_backtest(leg, frames[sym])) for sym, leg in leg_macros]

    agg, per_symbol = portfolio_mod.aggregate(results, candle_interval=macro.candle_interval)
    return agg, per_symbol, source, label
```

`_run_any` 의 docstring 에서 "capital split evenly" 를 고친다 — 더 이상 사실이 아니다:
`"Portfolio => each leg runs its own rule with capital split by weight."`

- [ ] **Step 4: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_api.py -q`
Expected: PASS (5개).

- [ ] **Step 5: 기존 백테스트·포트폴리오 시험이 안 깨졌는지 본다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "backtest or portfolio" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py`
Expected: PASS.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/main.py backend/tests/test_portfolio_bundle_api.py
git commit -m "feat(api): 묶음 백테스트가 비중을 쓰고 한도가 있으면 동기 루프를 돈다"
```

---

### Task 6: 묶음 검증을 연다

**Files:**
- Modify: `backend/app/main.py` (`/api/validate` ~1207, `/api/explain` ~1358)
- Modify: `backend/app/engine/walkforward.py` (묶음 창 실행 함수 추가)
- Modify: `frontend/src/pages/StudioPro.jsx` (`PORTFOLIO_MESSAGE` 분기 제거)
- Test: `backend/tests/test_portfolio_bundle_validate.py` (create)

**Interfaces:**
- Consumes: Task 4 의 `run_bundle`·`split_frames_by_time`; Task 5 의 `_run_portfolio_backtest`
- Produces: `walkforward.run_bundle_windows(macro, frames, windows) -> list[dict]`; `/api/validate` 와 `/api/explain` 이 묶음 매크로를 422 하지 않는다

- [ ] **Step 1: 시험을 쓴다**

`backend/tests/test_portfolio_bundle_validate.py`:

```python
"""묶음 검증 — 창을 레그마다 같은 시각으로 자르고, 한도가 있으면 창마다 동기 루프를 돈다."""
import pandas as pd
import pytest

from app.engine import walkforward as wf
from app.engine.schema import Macro

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}


def frame(closes, start="2026-01-01"):
    ts = pd.date_range(start, periods=len(closes), freq="1h")
    return pd.DataFrame({"timestamp": ts, "open": closes,
                         "high": [c * 1.03 for c in closes], "low": [c * 0.99 for c in closes],
                         "close": closes, "volume": [1000.0] * len(closes)})


LONG = list(range(100, 116))


def test_bundle_windows_returns_one_row_per_window():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame(LONG)}
    rows = wf.run_bundle_windows(m, frames, 4)
    assert [r["index"] for r in rows] == [1, 2, 3, 4]
    assert all(r["start"] and r["end"] for r in rows)
    assert all(r["error"] == "" for r in rows)


def test_bundle_windows_return_is_the_whole_bundle_not_one_leg():
    """창 수익률은 묶음 합산이어야 한다 — 한 레그만 보면 한도의 효과가 안 보인다."""
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame(LONG)}
    capped = wf.run_bundle_windows(m, frames, 2)
    free = wf.run_bundle_windows(
        m.model_copy(update={"bundle_risk": m.bundle_risk.model_copy(
            update={"max_positions": None, "max_exposure_pct": 100.0})}), frames, 2)
    assert [r["return_pct"] for r in capped] != [r["return_pct"] for r in free], \
        "한도가 창 수익률을 바꾸지 않았다 — 합산이 아니라 한 레그만 보고 있을 수 있다"


def test_bundle_window_failure_keeps_its_slot():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame([])}
    rows = wf.run_bundle_windows(m, frames, 3)
    assert len(rows) == 3
    assert all("return_pct" in r for r in rows)


def test_bundle_windows_too_few_rows_is_empty():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    assert wf.run_bundle_windows(m, {"BTCUSDT": frame([100]), "ETHUSDT": frame([100])}, 4) == []


# --- 끝점이 더 이상 거절하지 않는다 ------------------------------------
def test_validate_endpoint_no_longer_refuses_portfolio():
    import app.main as main
    src = __import__("inspect").getsource(main)
    assert "여러 종목 포트폴리오 매크로는 아직 검증할 수 없습니다" not in src
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_validate.py -q`
Expected: `AttributeError: module 'app.engine.walkforward' has no attribute 'run_bundle_windows'`.

- [ ] **Step 3: `run_bundle_windows` 를 쓴다**

`walkforward.py` 의 `run_windows` **뒤**에 붙인다:

```python
def run_bundle_windows(macro: Macro, frames: dict, windows: int = DEFAULT_WINDOWS) -> list[dict]:
    """묶음의 구간별 성과. 창은 **시각 기준**으로 자른다.

    행 수로 자르면 레그마다 봉 수가 달라 경계가 어긋난다 — 묶음에서는 모든 레그가 같은
    기간을 보아야 한도가 뜻을 가진다. 수익률은 레그 합산(``portfolio.aggregate``)이다.
    """
    from . import portfolio as portfolio_mod
    from .portfolio_backtest import run_bundle, split_frames_by_time

    rows = []
    for index, part in enumerate(split_frames_by_time(frames, windows), start=1):
        times = [pd.Timestamp(t) for df in part.values()
                 for t in (df[TIME_COLUMN] if TIME_COLUMN in df.columns else [])]
        start = _label(min(times)) if times else ""
        end = _label(max(times)) if times else ""
        try:
            results = run_bundle(macro, part)
            agg, _per = portfolio_mod.aggregate(results, candle_interval=macro.candle_interval)
        except Exception as exc:  # noqa: BLE001 — 한 구간 실패가 전체를 막지 않는다
            logger.warning("bundle walk-forward window failed: index=%d reason=%s",
                           index, type(exc).__name__)
            rows.append(_failed(index, start, end, type(exc).__name__))
            continue
        value = float(agg.final_return_pct)
        if not math.isfinite(value):
            rows.append(_failed(index, start, end, "NonFiniteReturn"))
            continue
        rows.append({"index": index, "start": start, "end": end,
                     "return_pct": round(value, 2),
                     "trades": int(agg.total_trades), "error": ""})
    return rows
```

- [ ] **Step 4: `/api/validate` 의 거절을 걷어낸다**

`main.py` ~1207 의 두 줄

```python
    if macro.is_portfolio():
        raise HTTPException(status_code=422, detail="여러 종목 포트폴리오 매크로는 아직 검증할 수 없습니다. 종목 하나로 나눠 검증해 주세요.")
```

을 지우고, 그 아래 `df, _source = fetch_klines_for_macro(macro, start_ms, end_ms)` / `result = run_backtest(macro, df)` / `windows = walkforward_mod.run_windows(macro, df, body.windows)` 를 묶음 분기로 감싼다:

```python
    try:
        if macro.is_portfolio():
            # 묶음은 레그마다 캔들을 받아 합산한다. 창도 같은 프레임으로 자른다 —
            # 백테스트와 검증이 같은 답을 내야 한다.
            result, _per_symbol, _source, _lbl = _run_portfolio_backtest(macro, start_ms, end_ms)
            frames = {}
            base = macro.initial_capital
            for spec in macro.leg_specs():
                cap = (base * spec.weight / 100.0) if base else None
                leg_df, _s = fetch_klines_for_macro(macro.for_leg(spec, cap), start_ms, end_ms)
                frames[spec.symbol] = leg_df
            windows = walkforward_mod.run_bundle_windows(macro, frames, body.windows)
        else:
            df, _source = fetch_klines_for_macro(macro, start_ms, end_ms)
            result = run_backtest(macro, df)
            windows = walkforward_mod.run_windows(macro, df, body.windows)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
```

기존 코드에서 `windows = ...` 가 `try` 밖에 있었다면 위처럼 안으로 들인다. `result.equity_curve` 를 쓰는 아래 코드는 그대로 둔다 — 묶음도 같은 `BacktestResult` 모양이다.

- [ ] **Step 5: `/api/explain` 의 거절을 걷어낸다**

~1358 의 같은 두 줄을 지운다. 그 아래가 `daily` 매크로로 캔들 하나를 받으므로, 묶음일 때는 대표 종목(첫 레그)의 일봉으로 근거를 만든다 — 근거는 "일간 변동" 설명이고 종목별 합산 곡선이 아니라 가격 흐름을 읽는다:

```python
    # 묶음은 대표 종목(첫 레그)의 일봉으로 근거를 만든다. 근거는 가격 흐름 해설이라
    # 레그별로 나누면 읽을 수 없게 길어진다.
    base_macro = macro
    if macro.is_portfolio():
        specs = macro.leg_specs()
        base_macro = macro.for_leg(specs[0], macro.initial_capital)
    daily = base_macro if base_macro.candle_interval == "1d" \
        else base_macro.model_copy(update={"candle_interval": "1d"})
```

아래의 `fetch_klines_for_macro(daily, ...)` 는 그대로다.

- [ ] **Step 6: 프런트의 거절 분기를 지운다**

`frontend/src/pages/StudioPro.jsx`:
- 19행의 `const PORTFOLIO_MESSAGE = ...` 를 지운다.
- 그 상수를 쓰는 분기(검증 전에 묶음을 막는 자리)를 지운다. `grep -n PORTFOLIO_MESSAGE` 로 전부 찾아 없앤다.

- [ ] **Step 7: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_validate.py -q`
Expected: PASS (5개).

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS.

- [ ] **Step 8: 돌연변이 — 창이 시각 기준인지**

`run_bundle_windows` 가 `split_frames_by_time` 대신 레그 하나를 `split_frame` 으로 자르도록 바꿔 본다 → `test_bundle_window_failure_keeps_its_slot` 또는 `test_bundle_windows_returns_one_row_per_window` 가 실패해야 한다. 되돌린 뒤 수트를 다시 돌린다.

- [ ] **Step 9: 커밋**

```bash
git add backend/app/main.py backend/app/engine/walkforward.py frontend/src/pages/StudioPro.jsx backend/tests/test_portfolio_bundle_validate.py
git commit -m "feat(api): 묶음 매크로 검증을 연다 - 창을 시각 기준으로 자른다"
```

---

### Task 7: 모의 · 실시간이 비중과 한도를 쓴다

**Files:**
- Modify: `backend/app/paper.py` (`start_session` ~193-215)
- Test: `backend/tests/test_portfolio_bundle_paper.py` (create)

**Interfaces:**
- Consumes: Task 1 의 `leg_specs`·`for_leg`; Task 3 의 `BundleGate`
- Produces: `paper.start_session` 이 비중대로 자금을 나누고, `bundle_risk` 가 있으면 관문 하나를 모든 레그 심에 꽂는다

- [ ] **Step 1: 시험을 쓴다**

`backend/tests/test_portfolio_bundle_paper.py`:

```python
"""모의 · 실시간의 묶음 — 비중 분배, 레그가 공유하는 관문, 복구 뒤에도 듣는 한도."""
import pytest

from app.engine.bundle import BundleGate
from app.engine.schema import BundleRisk, Macro
from app.engine.stepper import make_sim

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}


def bundle(**over):
    return Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                     {"symbol": "ETHUSDT", "weight": 30}], **over})


def test_build_legs_splits_by_weight():
    from app.paper import build_bundle_legs

    m = bundle()
    legs, gate = build_bundle_legs(m, 1000.0)
    caps = {leg.symbol: leg.initial for leg in legs}
    assert caps == {"BTCUSDT": 700.0, "ETHUSDT": 300.0}
    assert gate is None                     # 한도가 없으면 관문도 없다


def test_build_legs_even_for_symbols_form():
    from app.paper import build_bundle_legs

    m = Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]})
    legs, _gate = build_bundle_legs(m, 1000.0)
    assert {leg.initial for leg in legs} == {500.0}


def test_build_legs_attaches_one_shared_gate():
    from app.paper import build_bundle_legs

    m = bundle(bundle_risk={"max_positions": 1})
    legs, gate = build_bundle_legs(m, 1000.0)
    assert gate is not None
    inners = [getattr(leg.sim, "inner", leg.sim) for leg in legs]
    assert all(s.bundle_gate is gate for s in inners), "레그마다 다른 관문을 꽂았다"


def test_build_legs_uses_each_leg_rule():
    from app.paper import build_bundle_legs

    m = Macro(**{**BASE, "legs": [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_pct": 3.0, "dip_pct": 1.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]})
    legs, _gate = build_bundle_legs(m, 1000.0)
    kinds = {leg.symbol: type(getattr(leg.sim, "inner", leg.sim)).__name__ for leg in legs}
    assert kinds["BTCUSDT"] != kinds["ETHUSDT"], "레그별 규칙이 반영되지 않았다"


# --- Review Focus 4: 재기동 복구 뒤에도 한도가 듣는다 ------------------
def test_gate_reads_restored_books():
    """1차에서 복구 경로를 빠뜨려 차단 결함을 냈다. 관문은 상태를 따로 들지 않으므로
    복구된 장부를 그 즉시 올바르게 읽어야 한다 — 그것을 여기서 못 박는다."""
    from app.paper import build_bundle_legs

    m = bundle(bundle_risk={"max_positions": 1})
    legs, gate = build_bundle_legs(m, 1000.0)
    btc, eth = legs[0], legs[1]
    assert gate.blocks(eth.sim) is False, "아무도 안 들고 있는데 막았다"

    # 재기동: BTC 레그가 포지션을 들고 있던 상태로 되살아난다.
    btc.sim.restore(700.0, in_position=True, qty=5.0, entry_price=100.0, last_price=100.0)
    assert gate.blocks(eth.sim) is True, "복구된 장부를 관문이 못 읽었다"
    assert gate.blocks(btc.sim) is False, "들고 있는 레그의 추가 매수를 막았다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_paper.py -q`
Expected: `ImportError: cannot import name 'build_bundle_legs' from 'app.paper'`.

- [ ] **Step 3: `build_bundle_legs` 를 쓴다**

`paper.py` 에서 `start_session` **앞**에 넣는다:

```python
def build_bundle_legs(macro: Macro, initial: float) -> tuple[List[Leg], Optional[BundleGate]]:
    """묶음 매크로의 레그와(필요하면) 공유 관문을 만든다.

    자금은 비중대로 나눈다. 한도가 있으면 관문 **하나**를 만들어 모든 레그 심에 꽂는다 —
    레그마다 따로 만들면 서로를 못 보고 한도가 레그별 한도가 되어 버린다.

    관문은 심의 현재 장부를 그때그때 읽으므로 복구할 상태가 없다. 재기동 뒤
    ``driver.restore`` 가 장부를 되살리면 관문은 그 즉시 올바른 답을 낸다.
    """
    specs = macro.leg_specs()
    gate = BundleGate(macro.bundle_risk, initial) if macro.bundle_risk is not None else None
    legs: List[Leg] = []
    for spec in specs:
        cap = initial * spec.weight / 100.0
        sim = make_sim(macro.for_leg(spec, cap), initial_capital=cap)
        if gate is not None:
            gate.register(sim)
            inner = getattr(sim, "inner", sim)
            inner.bundle_gate = gate
        legs.append(Leg(spec.symbol, sim, cap))
    return legs, gate
```

import 를 더한다: `from .engine.bundle import BundleGate`.

- [ ] **Step 4: `start_session` 이 그 함수를 쓰게 한다**

기존

```python
    initial = _session_initial(macro)
    per_leg = initial / len(symbols)
    legs: List[Leg] = []
    for sym in symbols:
        leg_macro = macro.for_symbol(sym, per_leg) if len(symbols) > 1 else macro
        legs.append(Leg(sym, make_sim(leg_macro, initial_capital=per_leg), per_leg))
```

을

```python
    initial = _session_initial(macro)
    if macro.is_portfolio():
        legs, _gate = build_bundle_legs(macro, initial)
        symbols = [leg.symbol for leg in legs]   # 레그 순서가 곧 자리 우선순위다
    else:
        legs = [Leg(symbols[0], make_sim(macro, initial_capital=initial), initial)]
```

로 바꾼다. 위쪽의 `for sym in symbols: ensure_spot_available(...)` 와 `replay_prices` 는 그대로 쓴다 — `symbols` 가 레그 순서와 같아지므로 영향이 없다. `ensure_spot_available` 호출이 `legs` 생성보다 먼저 와야 한다는 순서는 유지한다.

- [ ] **Step 5: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_paper.py -q`
Expected: PASS (5개).

- [ ] **Step 6: 기존 모의 시험이 안 깨졌는지 본다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "paper or driver or runner_engine" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py`
Expected: PASS.

- [ ] **Step 7: 돌연변이**

1. `gate = BundleGate(...)` 를 레그 루프 **안**으로 옮겨 레그마다 새로 만든다 → `test_build_legs_attaches_one_shared_gate` 가 실패해야 한다.
2. `cap = initial * spec.weight / 100.0` 를 `cap = initial / len(specs)` 로 바꾼다 → `test_build_legs_splits_by_weight` 가 실패해야 한다.

되돌린 뒤 수트를 다시 돌린다.

- [ ] **Step 8: 커밋**

```bash
git add backend/app/paper.py backend/tests/test_portfolio_bundle_paper.py
git commit -m "feat(paper): 모의 세션이 비중대로 나누고 묶음 한도를 레그가 공유한다"
```

---

### Task 8: 요약 · 해설 문구

**Files:**
- Modify: `backend/app/engine/summary.py`
- Modify: `backend/app/engine/explain.py`
- Test: `backend/tests/test_portfolio_bundle_summary.py` (create)

**Interfaces:**
- Consumes: Task 1 의 `leg_specs`·`bundle_risk`; Task 3 의 `BundleGate.note()`
- Produces: `human_summary` 가 비중 · 한도를 한 줄씩 담는다

- [ ] **Step 1: 시험을 쓴다**

`backend/tests/test_portfolio_bundle_summary.py`:

```python
"""묶음 요약 문구 — 비중과 한도가 요약에 들어간다."""
import pytest

from app.engine.schema import Macro
from app.engine.summary import human_summary

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}


def test_even_weights_say_even():
    s = human_summary(Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]}))
    assert "균등" in s


def test_uneven_weights_are_listed():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                                {"symbol": "ETHUSDT", "weight": 30}]}))
    assert "BTC 70%" in s and "ETH 30%" in s


def test_bundle_limit_is_named():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                                {"symbol": "ETHUSDT", "weight": 50}],
                               "bundle_risk": {"max_positions": 1, "max_exposure_pct": 60}}))
    assert "묶음 한도" in s and "한 번에 1종목까지" in s and "총 노출 60% 까지" in s


def test_single_symbol_summary_unchanged():
    s = human_summary(Macro(**BASE))
    assert "묶음 한도" not in s and "균등" not in s


def test_leg_rules_are_named_when_they_differ():
    s = human_summary(Macro(**{**BASE, "legs": [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_pct": 3.0, "dip_pct": 1.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]}))
    assert "종목별 규칙" in s
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_summary.py -q`
Expected: FAIL (`"BTC 70%" not in s` 등).

- [ ] **Step 3: `summary.py` 에 묶음 줄을 더한다**

`human_summary` 에서 진입 조건 줄(`진입 조건: ...`)을 붙이는 자리 **바로 뒤**에 넣는다. 종목 base 는 이 파일이 이미 쓰는 방식(`symbol` 에서 quote 를 떼는 기존 헬퍼)을 그대로 쓴다 — 없으면 `sym.replace("USDT", "").replace("KRW", "")`.

```python
    if macro.is_portfolio():
        specs = macro.leg_specs()
        weights = [spec.weight for spec in specs]
        if max(weights) - min(weights) < 0.02:
            parts.append(f"종목 {len(specs)}개 · 자금 균등")
        else:
            shares = " · ".join(
                f"{_base_of(spec.symbol)} {spec.weight:g}%" for spec in specs)
            parts.append(f"종목 비중: {shares}")
        if any(spec.rule_type is not None for spec in specs):
            parts.append("종목별 규칙을 따로 정했어요")
        if macro.bundle_risk is not None:
            from .bundle import BundleGate
            note = BundleGate(macro.bundle_risk, macro.initial_capital or 1.0).note()
            parts.append(f"묶음 한도: {note}")
```

`parts` 는 이 함수가 요약 조각을 모으는 기존 리스트 이름이다 — 실제 이름을 읽고 맞춘다. `_base_of` 가 없으면 파일 안에 작은 헬퍼로 만든다:

```python
def _base_of(symbol: str) -> str:
    """BTCUSDT -> BTC. 요약에서 종목을 짧게 부른다."""
    for quote in ("USDT", "KRW", "BUSD"):
        if symbol.endswith(quote):
            return symbol[: -len(quote)]
    return symbol
```

- [ ] **Step 4: `explain.py` 에 한 줄 더한다**

`_points` 의 `return pts` **앞**:

```python
    if macro.bundle_risk is not None:
        from .bundle import BundleGate
        note = BundleGate(macro.bundle_risk, macro.initial_capital or 1.0).note()
        pts.append(f"묶음 한도를 걸어서 {note} 만 들어가. 한도에 닿으면 새로 안 사고, 들고 있는 건 그대로 팔아.")
```

- [ ] **Step 5: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_portfolio_bundle_summary.py tests/test_entry_filter_summary.py tests/test_entry_filter_explain.py -q`
Expected: PASS.

- [ ] **Step 6: 기존 요약 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "summary or explain" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py`
Expected: PASS. 기존 묶음 요약 문구를 못 박은 시험이 있으면 그 기대값을 새 문구로 맞추고, 바꾼 이유를 커밋 메시지에 적는다.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/engine/summary.py backend/app/engine/explain.py backend/tests/test_portfolio_bundle_summary.py
git commit -m "feat(engine): 요약 · 해설이 종목 비중과 묶음 한도를 말한다"
```

---

### Task 9: 프런트 — `macro.js` 의 비중 · 레그 규칙 · 묶음 한도

**Files:**
- Modify: `frontend/src/lib/macro.js`
- Test: `frontend/tests/macroBundle.test.js` (create)

**Interfaces:**
- Consumes: 기존 `buildEntryFilter`, `withTypeDefaults`, `macroToForm`, `validateDetailed`
- Produces: 폼 키 `leg_weights`(쉼표 문자열), `leg_rules`(객체), `use_bundle_risk`, `bundle_max_positions`, `bundle_max_exposure_pct`; `buildLegs(form)`, `buildBundleRisk(form)`, `evenWeights(count)`

- [ ] **Step 1: 시험을 쓴다**

`frontend/tests/macroBundle.test.js`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBundleRisk, buildLegs, evenWeights, macroToForm, toMacro, validateDetailed } from "../src/lib/macro.js";

const FORM = {
  exchange: "binance", symbol: "BTCUSDT, ETHUSDT", rule_type: "I",
  candle_interval: "1h", preset: "3m", k: "0.5", initial_capital: "1000",
  invest_ratio: "1",
};

test("evenWeights 는 100 을 균등하게 쪼개고 합이 정확히 100 이다", () => {
  assert.deepEqual(evenWeights(2), [50, 50]);
  assert.deepEqual(evenWeights(4), [25, 25, 25, 25]);
  const three = evenWeights(3);
  assert.equal(three.length, 3);
  assert.equal(three.reduce((a, b) => a + b, 0), 100);
});

test("비중을 안 건드리면 legs 를 만들지 않는다 (옛 symbols 모양 유지)", () => {
  assert.equal(buildLegs(FORM), null);
});

test("비중을 건드리면 legs 를 만든다", () => {
  const legs = buildLegs({ ...FORM, leg_weights: "70, 30" });
  assert.deepEqual(legs, [
    { symbol: "BTCUSDT", weight: 70 },
    { symbol: "ETHUSDT", weight: 30 },
  ]);
});

test("레그 규칙을 바꾸면 rule_type 과 params 가 함께 들어간다", () => {
  const legs = buildLegs({
    ...FORM, leg_weights: "50, 50",
    leg_rules: { BTCUSDT: { rule_type: "E", trail_pct: "3", dip_pct: "1", initial_capital: "1000" } },
  });
  assert.equal(legs[0].rule_type, "E");
  assert.equal(legs[0].params.trail_pct, 3);
  assert.equal(legs[1].rule_type, undefined);
});

test("묶음 한도는 체크를 켰을 때만 만들어진다", () => {
  assert.equal(buildBundleRisk({ ...FORM }), null);
  assert.equal(buildBundleRisk({ ...FORM, use_bundle_risk: true }), null, "둘 다 비면 null");
  assert.deepEqual(
    buildBundleRisk({ ...FORM, use_bundle_risk: true, bundle_max_positions: "1" }),
    { max_positions: 1 },
  );
  assert.deepEqual(
    buildBundleRisk({ ...FORM, use_bundle_risk: true, bundle_max_exposure_pct: "60" }),
    { max_exposure_pct: 60 },
  );
});

test("toMacro 가 legs 와 bundle_risk 를 싣는다", () => {
  const macro = toMacro({ ...FORM, leg_weights: "70, 30", use_bundle_risk: true, bundle_max_positions: "1" });
  assert.equal(macro.legs.length, 2);
  assert.equal(macro.bundle_risk.max_positions, 1);
  assert.equal(macro.symbols, undefined, "legs 를 쓰면 symbols 를 같이 보내지 않는다");
});

test("macroToForm 왕복", () => {
  const macro = toMacro({ ...FORM, leg_weights: "70, 30", use_bundle_risk: true, bundle_max_exposure_pct: "60" });
  const back = macroToForm(macro);
  assert.equal(back.symbol, "BTCUSDT, ETHUSDT");
  assert.equal(back.leg_weights, "70, 30");
  assert.equal(back.use_bundle_risk, true);
  assert.equal(String(back.bundle_max_exposure_pct), "60");
});

test("macroToForm 은 비중이 균등하면 leg_weights 를 비워 둔다", () => {
  const back = macroToForm({ ...toMacro(FORM), symbols: ["BTCUSDT", "ETHUSDT"] });
  assert.ok(!back.leg_weights, "균등이면 비중 입력을 안 켠다");
});

test("비중 합이 100 이 아니면 검증이 잡는다", () => {
  const errs = validateDetailed({ ...FORM, leg_weights: "70, 20" });
  assert.ok(errs.some((e) => /비중/.test(e.message)), JSON.stringify(errs));
});

test("비중 개수가 종목 수와 다르면 검증이 잡는다", () => {
  const errs = validateDetailed({ ...FORM, leg_weights: "50, 30, 20" });
  assert.ok(errs.some((e) => /비중/.test(e.message)));
});

test("동시 보유 상한은 정수여야 하고 종목 수보다 작아야 한다", () => {
  assert.ok(validateDetailed({ ...FORM, use_bundle_risk: true, bundle_max_positions: "1.5" })
    .some((e) => /동시 보유/.test(e.message)));
  assert.ok(validateDetailed({ ...FORM, use_bundle_risk: true, bundle_max_positions: "2" })
    .some((e) => /종목 수/.test(e.message)));
});

test("한도는 종목 2개 이상에서만", () => {
  const errs = validateDetailed({ ...FORM, symbol: "BTCUSDT", use_bundle_risk: true, bundle_max_positions: "1" });
  assert.ok(errs.some((e) => /2개 이상|묶음/.test(e.message)));
});

test("총 노출 한도 범위", () => {
  assert.ok(validateDetailed({ ...FORM, use_bundle_risk: true, bundle_max_exposure_pct: "0" })
    .some((e) => /노출/.test(e.message)));
  assert.ok(validateDetailed({ ...FORM, use_bundle_risk: true, bundle_max_exposure_pct: "101" })
    .some((e) => /노출/.test(e.message)));
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && node --test "tests/macroBundle.test.js"`
Expected: `buildLegs is not a function`.

- [ ] **Step 3: `macro.js` 에 헬퍼를 더한다**

```js
// 균등 비중 — 합이 정확히 100 이 되게 마지막 몫이 나머지를 받는다. 서버 검증이 합을
// 0.01 오차로 보므로 떠돌이 소수점을 남기지 않는다.
export function evenWeights(count) {
  const n = Math.max(1, Number(count) || 1);
  const each = Math.round((100 / n) * 100) / 100;
  const out = Array.from({ length: n - 1 }, () => each);
  out.push(Math.round((100 - each * (n - 1)) * 100) / 100);
  return out;
}

function symbolList(form) {
  return String(form.symbol || "").split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
}

// 비중을 건드리지 않았으면 null — 옛 `symbols` 모양을 그대로 보낸다. 서버가 둘을 함께
// 받지 않으므로 "둘 중 하나" 를 여기서 정한다.
export function buildLegs(form) {
  const symbols = symbolList(form);
  if (symbols.length < 2) return null;
  const weights = String(form.leg_weights || "").split(",").map((s) => s.trim()).filter(Boolean);
  const rules = form.leg_rules || {};
  const touched = weights.length > 0 || Object.keys(rules).length > 0;
  if (!touched) return null;
  const even = evenWeights(symbols.length);
  return symbols.map((symbol, i) => {
    const leg = { symbol, weight: weights[i] !== undefined ? Number(weights[i]) : even[i] };
    const rule = rules[symbol];
    if (rule && rule.rule_type) {
      leg.rule_type = rule.rule_type;
      leg.params = buildParams({ ...rule });   // 규칙별 params 를 만드는 기존 함수
      const filter = buildEntryFilter(rule);
      if (filter) leg.entry_filter = filter;
    }
    return leg;
  });
}

export function buildBundleRisk(form) {
  if (!form.use_bundle_risk) return null;
  const out = {};
  if (form.bundle_max_positions !== "" && form.bundle_max_positions != null) {
    out.max_positions = Number(form.bundle_max_positions);
  }
  if (form.bundle_max_exposure_pct !== "" && form.bundle_max_exposure_pct != null) {
    out.max_exposure_pct = Number(form.bundle_max_exposure_pct);
  }
  return Object.keys(out).length ? out : null;
}
```

`buildParams` 는 `macro.js` 가 규칙별 params 를 만드는 **기존 함수의 실제 이름**으로 바꿔 쓴다 — 파일을 읽고 확인한다.

- [ ] **Step 4: `toMacro` 와 `macroToForm` 을 잇는다**

`toMacro` 에서 `symbols` 를 싣는 자리:

```js
  const legs = buildLegs(form);
  if (legs) {
    macro.legs = legs;
    delete macro.symbols;          // 서버가 둘을 함께 받지 않는다
  }
  const bundleRisk = buildBundleRisk(form);
  if (bundleRisk) macro.bundle_risk = bundleRisk;
```

`macroToForm`:

```js
  if (Array.isArray(macro.legs) && macro.legs.length > 1) {
    form.symbol = macro.legs.map((leg) => leg.symbol).join(", ");
    const even = evenWeights(macro.legs.length);
    const isEven = macro.legs.every((leg, i) => Math.abs(leg.weight - even[i]) < 0.02);
    // 균등이면 비중 입력을 켜지 않는다 — 사용자가 손대지 않은 것을 손댄 것처럼 보이지 않게.
    form.leg_weights = isEven ? "" : macro.legs.map((leg) => String(leg.weight)).join(", ");
    form.leg_rules = {};
    for (const leg of macro.legs) {
      if (!leg.rule_type) continue;
      form.leg_rules[leg.symbol] = {
        rule_type: leg.rule_type,
        ...macroToForm({ ...macro, rule_type: leg.rule_type, params: leg.params || {},
                         entry_filter: leg.entry_filter || null, legs: null, symbols: null }),
      };
    }
  }
  if (macro.bundle_risk) {
    form.use_bundle_risk = true;
    form.bundle_max_positions = macro.bundle_risk.max_positions ?? "";
    form.bundle_max_exposure_pct = macro.bundle_risk.max_exposure_pct ?? "";
  }
```

- [ ] **Step 5: `validateDetailed` 에 검사를 더한다**

```js
  const symbols = symbolList(form);
  const weightsRaw = String(form.leg_weights || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (weightsRaw.length) {
    if (symbols.length < 2) {
      errors.push({ key: "leg_weights", message: "비중은 종목 2개 이상에서만 정해요." });
    } else if (weightsRaw.length !== symbols.length) {
      errors.push({ key: "leg_weights", message: `비중을 종목 수(${symbols.length}개)만큼 적어 주세요.` });
    } else {
      const nums = weightsRaw.map(Number);
      if (nums.some((n) => !Number.isFinite(n) || n <= 0 || n > 100)) {
        errors.push({ key: "leg_weights", message: "비중은 0보다 크고 100 이하인 숫자여야 해요." });
      } else {
        const total = nums.reduce((a, b) => a + b, 0);
        if (Math.abs(total - 100) > 0.01) {
          errors.push({ key: "leg_weights", message: `비중의 합이 ${Number(total.toFixed(2))}% 예요 · 100% 로 맞춰 주세요.` });
        }
      }
    }
  }
  if (form.use_bundle_risk) {
    if (symbols.length < 2) {
      errors.push({ key: "use_bundle_risk", message: "묶음 한도는 종목 2개 이상에서만 쓸 수 있어요." });
    }
    const mp = form.bundle_max_positions;
    if (mp !== "" && mp != null) {
      const n = Number(mp);
      if (!Number.isInteger(n) || n < 1) {
        errors.push({ key: "bundle_max_positions", message: "동시 보유 종목 수는 1 이상의 정수예요." });
      } else if (symbols.length >= 2 && n >= symbols.length) {
        errors.push({ key: "bundle_max_positions", message: `동시 보유 상한은 종목 수(${symbols.length}개)보다 작아야 의미가 있어요.` });
      }
    }
    const ex = form.bundle_max_exposure_pct;
    if (ex !== "" && ex != null) {
      const n = Number(ex);
      if (!Number.isFinite(n) || n <= 0 || n > 100) {
        errors.push({ key: "bundle_max_exposure_pct", message: "총 노출 한도는 0보다 크고 100 이하예요." });
      }
    }
    if ((mp === "" || mp == null) && (ex === "" || ex == null)) {
      errors.push({ key: "use_bundle_risk", message: "묶음 한도를 켰으면 최소 하나를 정해 주세요." });
    }
  }
```

`errors` 와 각 오류 객체의 모양(`{ key, message }`)은 **이 파일의 기존 모양을 읽고 맞춘다.**

- [ ] **Step 6: 시험을 돌린다**

Run: `cd frontend && node --test "tests/macroBundle.test.js"`
Expected: PASS (13개).

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS (기존 전부).

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/lib/macro.js frontend/tests/macroBundle.test.js
git commit -m "feat(web): 폼이 종목 비중 · 레그 규칙 · 묶음 한도를 다룬다"
```

---

### Task 10: 프런트 — 비중 입력과 묶음 한도 칸

**Files:**
- Modify: `frontend/src/components/Builder.jsx` (`SymbolPicker` 비중 입력, 위험관리에 묶음 한도)
- Modify: `frontend/src/components/Builder.css` (비중 입력 칸 — 파일명은 실제 스타일 파일을 확인해 맞춘다)
- Test: `frontend/tests/builderBundle.test.js` (create)

**Interfaces:**
- Consumes: Task 9 의 `evenWeights`; 기존 `num`/`sel`/`chk` 헬퍼, `portfolioWeight`
- Produces: 비중 입력이 `form.leg_weights` 를 쓰고, 묶음일 때만 묶음 한도 칸이 보인다

`Builder.jsx` 의 주의점: 입력 헬퍼 `num(key, label, {step, hint, term, unit, wide})`·`sel(...)`·`chk(key, label, {term})` 는 **함수로 호출한다**(JSX 컴포넌트로 쓰면 입력 포커스가 날아간다 — `Builder.jsx:328` 주석). **`chk` 는 `hint` 를 받지 않는다.**

- [ ] **Step 1: 시험을 쓴다**

`frontend/tests/builderBundle.test.js`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";

const FORM = {
  exchange: "binance", symbol: "BTCUSDT, ETHUSDT, SOLUSDT", rule_type: "I",
  candle_interval: "1h", preset: "3m", k: "0.5", initial_capital: "1000", invest_ratio: "1",
};

function render(form) {
  return renderComponent("src/components/Builder.jsx", { form, setForm: () => {} });
}

test("묶음이면 비중 입력이 보인다", async () => {
  const html = await render(FORM);
  assert.match(html, /leg_weights|비중/);
});

test("묶음이면 묶음 한도 칸이 보인다", async () => {
  const html = await render({ ...FORM, use_bundle_risk: true });
  const text = textOf(html);
  assert.match(text, /묶음 동시 보유 종목 수/);
  assert.match(text, /묶음 총 노출 한도/);
});

test("종목이 하나면 묶음 한도 칸이 없다", async () => {
  const text = textOf(await render({ ...FORM, symbol: "BTCUSDT", use_bundle_risk: true }));
  assert.doesNotMatch(text, /묶음 동시 보유/);
});

test("비중 합이 100 이 아니면 한 줄 알린다", async () => {
  const text = textOf(await render({ ...FORM, leg_weights: "50, 30, 10" }));
  assert.match(text, /비중의 합이 90% 예요/);
});

test("비중 합이 맞으면 경고가 없다", async () => {
  const text = textOf(await render({ ...FORM, leg_weights: "50, 30, 20" }));
  assert.doesNotMatch(text, /비중의 합이/);
});

test("균등하게 버튼이 있다", async () => {
  const text = textOf(await render(FORM));
  assert.match(text, /균등하게/);
});

test("묶음 한도 라벨이 레그 위험관리와 섞이지 않는다", async () => {
  const text = textOf(await render({ ...FORM, use_bundle_risk: true }));
  // '묶음' 접두어 없이 '동시 보유 종목 수' 만 있으면 사용자가 레그 설정으로 읽는다.
  assert.doesNotMatch(text, /(?<!묶음 )동시 보유 종목 수/);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && node --test "tests/builderBundle.test.js"`
Expected: FAIL (묶음 한도 문구 없음).

- [ ] **Step 3: `SymbolPicker` 의 비중 칸을 입력으로 바꾼다**

`SymbolPicker` 가 `value`(종목 문자열)와 `onChange` 만 받고 있다. 비중을 받도록 두 prop 을 더한다: `weights`(쉼표 문자열), `onWeights`.

행 목록(`bd-symrow`)의 비중 `<span>` 을 입력으로 바꾼다:

```jsx
                <input
                  className="bd-symrow-w num"
                  type="number"
                  min="0.01" max="100" step="0.01"
                  value={weightAt(index)}
                  aria-label={`${baseOf(symbol)} 비중(%)`}
                  onChange={(event) => setWeightAt(index, event.target.value)}
                />
                <span className="bd-symrow-pct" aria-hidden="true">%</span>
```

컴포넌트 안에 헬퍼를 둔다:

```jsx
  // 비중 문자열이 비어 있으면 균등값을 보여 준다 — 사용자가 손대기 전에는 '균등' 이 사실이다.
  const weightList = String(weights || "").split(",").map((s) => s.trim()).filter(Boolean);
  const even = evenWeights(symbols.length);
  const weightAt = (i) => (weightList[i] !== undefined ? weightList[i] : String(even[i] ?? ""));
  const setWeightAt = (i, value) => {
    const next = symbols.map((_, idx) => (idx === i ? value : weightAt(idx)));
    onWeights(next.join(", "));
  };
  const weightTotal = symbols.map((_, i) => Number(weightAt(i)))
    .reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
  const weightsOff = symbols.length > 1 && Math.abs(weightTotal - 100) > 0.01;
```

행 목록 아래 안내·버튼:

```jsx
      {symbols.length > 1 && (
        <div className="bd-weights-foot">
          {weightsOff && (
            <div className="bd-error" role="alert">
              비중의 합이 {Number(weightTotal.toFixed(2))}% 예요 · 100% 로 맞춰 주세요
            </div>
          )}
          <button type="button" className="bd-weights-even" onClick={() => onWeights("")}>
            균등하게
          </button>
        </div>
      )}
```

맨 아래 `bd-hint` 문구를 고친다 — 비중을 손댔으면 균등이라고 말하면 안 된다:

```jsx
      <div className="bd-hint">
        {symbols.length > 1
          ? `${symbols.length}종목 · ${weightList.length ? "종목마다 비중을 정했어요" : "자금을 종목 수만큼 균등하게 나눠요"} · 최대 ${MAX_SYMBOLS}개`
          : `여러 종목을 넣으면 자금을 나눠요 · 최대 ${MAX_SYMBOLS}개`}
      </div>
```

`Builder` 본체에서 `SymbolPicker` 를 쓰는 자리(`Builder.jsx:388` 근처)에 두 prop 을 넘긴다:

```jsx
        weights={form.leg_weights || ""}
        onWeights={(value) => setForm((f) => ({ ...f, leg_weights: value }))}
```

`evenWeights` 를 `../lib/macro.js` 에서 import 한다.

- [ ] **Step 4: 위험관리 블록에 묶음 한도를 더한다**

위험관리 칸들을 그리는 자리의 **끝**, 묶음일 때만:

```jsx
      {symbolCount > 1 && (
        <>
          {chk("use_bundle_risk", "묶음 한도 쓰기", { term: "bundle_risk" })}
          {form.use_bundle_risk && (
            <div className={g2}>
              {num("bundle_max_positions", "묶음 동시 보유 종목 수", {
                step: 1,
                hint: `한 번에 포지션을 들고 있을 종목 수. 종목 수(${symbolCount}개)보다 작아야 의미가 있어요.`,
              })}
              {num("bundle_max_exposure_pct", "묶음 총 노출 한도", {
                step: 0.1, unit: "%",
                hint: "진입 기준 금액 합이 이 비율에 닿으면 새로 안 사요. 들고 있는 건 그대로 팔아요.",
              })}
            </div>
          )}
        </>
      )}
```

`symbolCount` 는 `Builder` 안에서 계산한다:

```jsx
  const symbolCount = String(form.symbol || "").split(",").map((s) => s.trim()).filter(Boolean).length;
```

`g2` 는 이 파일이 쓰는 격자 클래스 변수다(이미 있다).

- [ ] **Step 5: CSS**

`bd-symrow-w` 가 `<span>` 에서 `<input>` 이 됐으므로 폭·정렬을 맞춘다. 실제 스타일 파일을 찾아(`grep -rn "bd-symrow-w" frontend/src`) 그 규칙 옆에 더한다:

```css
/* 비중 입력 — 숫자 두세 자리와 소수점이 들어갈 만큼만. 숫자 증감 화살표는 숨긴다. */
input.bd-symrow-w { width: 4.5rem; text-align: right; }
input.bd-symrow-w::-webkit-outer-spin-button,
input.bd-symrow-w::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.bd-symrow-pct { opacity: .6; margin-left: .15rem; }
.bd-weights-foot { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.bd-weights-even { font-size: .8rem; text-decoration: underline; opacity: .75; }
```

- [ ] **Step 6: 시험을 돌린다**

Run: `cd frontend && node --test "tests/builderBundle.test.js"`
Expected: PASS (7개).

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS.

- [ ] **Step 7: 돌연변이**

1. 비중 합 경고(`weightsOff &&` 블록)를 지운다 → `test("비중 합이 100 이 아니면 한 줄 알린다")` 가 실패해야 한다.
2. `묶음 동시 보유 종목 수` 라벨에서 `묶음 ` 을 뗀다 → 마지막 시험이 실패해야 한다.

되돌린 뒤 수트를 다시 돌린다.

- [ ] **Step 8: 커밋**

```bash
git add frontend/src/components/Builder.jsx frontend/src/components/Builder.css frontend/tests/builderBundle.test.js
git commit -m "feat(web): 종목 비중을 입력으로 바꾸고 묶음 한도 칸을 더한다"
```

---

### Task 11: 프런트 — 레그마다 규칙 바꾸기 (프로 빌더 전용)

**Files:**
- Create: `frontend/src/components/LegRuleEditor.jsx`
- Modify: `frontend/src/components/Builder.jsx` (레그 행에 펼치기 버튼, `variant` 로 프로 전용)
- Test: `frontend/tests/legRuleEditor.test.js` (create)

**Interfaces:**
- Consumes: Task 9 의 `form.leg_rules`; 기존 `RULE_TYPES` 라벨, `withTypeDefaults`
- Produces: `<LegRuleEditor symbol rule onChange onClear />`; `Builder` 가 `variant === "dense"`(프로) 일 때만 그린다

레그 규칙 편집은 **프로 빌더에서만** 보인다. 기본 빌더는 지금 모양 그대로(균등 · 단일 규칙)다 — 기본 빌더에 규칙 N개를 넣으면 그 화면의 뜻이 사라진다.

- [ ] **Step 1: 시험을 쓴다**

`frontend/tests/legRuleEditor.test.js`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";

const FORM = {
  exchange: "binance", symbol: "BTCUSDT, ETHUSDT", rule_type: "I",
  candle_interval: "1h", preset: "3m", k: "0.5", initial_capital: "1000", invest_ratio: "1",
};

test("프로 빌더에는 규칙 바꾸기가 있다", async () => {
  const text = textOf(await renderComponent("src/components/Builder.jsx",
    { form: FORM, setForm: () => {}, variant: "dense" }));
  assert.match(text, /규칙 바꾸기/);
});

test("기본 빌더에는 규칙 바꾸기가 없다", async () => {
  const text = textOf(await renderComponent("src/components/Builder.jsx",
    { form: FORM, setForm: () => {} }));
  assert.doesNotMatch(text, /규칙 바꾸기/);
});

test("종목이 하나면 규칙 바꾸기가 없다", async () => {
  const text = textOf(await renderComponent("src/components/Builder.jsx",
    { form: { ...FORM, symbol: "BTCUSDT" }, setForm: () => {}, variant: "dense" }));
  assert.doesNotMatch(text, /규칙 바꾸기/);
});

test("레그 규칙이 정해져 있으면 그 규칙 이름이 보인다", async () => {
  const text = textOf(await renderComponent("src/components/Builder.jsx", {
    form: { ...FORM, leg_rules: { BTCUSDT: { rule_type: "E", trail_pct: "3", dip_pct: "1", initial_capital: "1000" } } },
    setForm: () => {}, variant: "dense",
  }));
  assert.match(text, /BTC/);
  assert.doesNotMatch(text, /규칙 바꾸기(?![\s\S]*BTC)/);
});

test("LegRuleEditor 는 규칙 선택과 되돌리기를 그린다", async () => {
  const text = textOf(await renderComponent("src/components/LegRuleEditor.jsx", {
    symbol: "BTCUSDT",
    rule: { rule_type: "E", trail_pct: "3", dip_pct: "1", initial_capital: "1000" },
    onChange: () => {}, onClear: () => {},
  }));
  assert.match(text, /묶음 기본 규칙으로/);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && node --test "tests/legRuleEditor.test.js"`
Expected: FAIL.

- [ ] **Step 3: `LegRuleEditor.jsx` 를 쓴다**

```jsx
// 레그 하나의 규칙 판. 묶음의 기본 규칙 대신 이 종목만 다른 규칙으로 돌릴 때 쓴다.
// 규칙 세부값은 Builder 의 조건 판을 그대로 재사용한다 — 규칙별 칸을 두 번 구현하지 않는다.
import Builder from "./Builder.jsx";
import Icon from "./Icon.jsx";

export default function LegRuleEditor({ symbol, rule, onChange, onClear }) {
  // 레그 판은 종목 · 기간 · 거래소를 다시 묻지 않는다 — 그건 묶음이 정한다.
  const form = { ...rule, symbol };
  return (
    <div className="bd-legrule" aria-label={`${symbol} 규칙`}>
      <Builder
        form={form}
        setForm={(next) => onChange(typeof next === "function" ? next(form) : next)}
        variant="dense"
        scope="leg"
      />
      <button type="button" className="bd-legrule-clear" onClick={onClear}>
        <Icon name="x" size={12} strokeWidth={2.25} /> 묶음 기본 규칙으로
      </button>
    </div>
  );
}
```

`Builder` 에 `scope` prop 을 더한다 — `scope === "leg"` 일 때 종목 고르기 · 기간 · 거래소 · 위험관리 · 묶음 한도 블록을 그리지 않는다(규칙과 진입 조건만). 기존 블록들을 `scope !== "leg" && (...)` 로 감싼다.

- [ ] **Step 4: 레그 행에 펼치기를 더한다**

`SymbolPicker` 에 prop 두 개를 더한다: `legRules`(객체), `onLegRule(symbol, next)`, 그리고 `showLegRules`(불리언 — 프로에서만 true). 행 목록의 `bd-symrow-x` 버튼 **앞**:

```jsx
                {showLegRules && (
                  <button
                    type="button"
                    className={"bd-symrow-rule" + (legRules[symbol] ? " is-on" : "")}
                    onClick={() => setOpenRule(openRule === symbol ? "" : symbol)}
                    aria-expanded={openRule === symbol}
                  >
                    {legRules[symbol] ? RULE_TYPES[legRules[symbol].rule_type]?.label || "규칙 바뀜" : "규칙 바꾸기"}
                  </button>
                )}
```

행 아래 펼친 판:

```jsx
              {showLegRules && openRule === symbol && (
                <LegRuleEditor
                  symbol={symbol}
                  rule={legRules[symbol] || withTypeDefaults({ rule_type: "E" })}
                  onChange={(next) => onLegRule(symbol, next)}
                  onClear={() => { onLegRule(symbol, null); setOpenRule(""); }}
                />
              )}
```

`const [openRule, setOpenRule] = useState("");` 를 `SymbolPicker` 상태에 더한다. `<li>` 안에 블록 요소를 넣게 되므로 `bd-symrow` 를 감싸는 구조를 `<li>` → `<li><div className="bd-symrow-main">…</div>{펼친 판}</li>` 로 바꾸고 CSS 를 맞춘다.

`Builder` 본체에서 넘긴다:

```jsx
        showLegRules={dense && scope !== "leg"}
        legRules={form.leg_rules || {}}
        onLegRule={(symbol, next) => setForm((f) => {
          const rules = { ...(f.leg_rules || {}) };
          if (next === null) delete rules[symbol]; else rules[symbol] = next;
          return { ...f, leg_rules: rules };
        })}
```

- [ ] **Step 5: CSS**

```css
/* 레그 규칙 — 행 아래로 펼친다. 묶음 판 안의 판이므로 테두리로 깊이를 한 단 준다. */
.bd-symrow-main { display: flex; align-items: center; gap: .4rem; }
.bd-symrow-rule { font-size: .75rem; padding: .1rem .4rem; border: 1px solid currentColor;
                  border-radius: .3rem; opacity: .7; }
.bd-symrow-rule.is-on { opacity: 1; font-weight: 600; }
.bd-legrule { margin: .4rem 0 .6rem; padding: .6rem; border-left: 2px solid; opacity: .95; }
.bd-legrule-clear { font-size: .75rem; opacity: .7; display: inline-flex;
                    align-items: center; gap: .2rem; }
```

- [ ] **Step 6: 시험을 돌린다**

Run: `cd frontend && node --test "tests/legRuleEditor.test.js"`
Expected: PASS (5개).

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS.

- [ ] **Step 7: 무한 재귀가 없는지 확인한다**

`LegRuleEditor` 가 `Builder` 를 쓰고 `Builder` 가 `LegRuleEditor` 를 쓴다. `scope="leg"` 가 레그 판 안에서 종목 고르기를 끄므로 재귀가 끊긴다. 다음으로 확인한다:

```bash
cd frontend && node --test "tests/legRuleEditor.test.js" 2>&1 | grep -ci "Maximum call stack"
```

Expected: `0`. 1 이상이면 `scope === "leg"` 가드가 종목 고르기를 못 끄고 있다.

- [ ] **Step 8: 커밋**

```bash
git add frontend/src/components/LegRuleEditor.jsx frontend/src/components/Builder.jsx frontend/src/components/Builder.css frontend/tests/legRuleEditor.test.js
git commit -m "feat(web): 프로 빌더에서 종목마다 규칙을 따로 정한다"
```

---

### Task 12: 매크로 카드가 비중과 한도를 보여 준다

**Files:**
- Modify: `frontend/src/components/MacroCard.jsx` (`macroFacts`, ~44-75)
- Modify: `frontend/src/lib/portfolio.js` (`weightPhrase` 추가)
- Test: `frontend/tests/macroCardBundle.test.js` (create)

**Interfaces:**
- Consumes: `macro.legs`, `macro.bundle_risk`
- Produces: `weightPhrase(legs)`; 카드에 `종목 비중` · `묶음 한도` 행

1차에서 배운 것 — 서버 요약 끝에 붙은 문구는 모든 화면에서 **가장 먼저 잘린다.** 그래서 구조화된 칸이 본진이다.

- [ ] **Step 1: 시험을 쓴다**

`frontend/tests/macroCardBundle.test.js`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";
import { weightPhrase } from "../src/lib/portfolio.js";

const MACRO = {
  exchange: "binance", symbol: "BTCUSDT", rule_type: "I", candle_interval: "1h",
  params: { k: 0.5, initial_capital: 1000 }, risk: { invest_ratio: 1 },
  period: { preset: "3m" },
};

test("weightPhrase 는 base 티커와 비중을 잇는다", () => {
  assert.equal(
    weightPhrase([{ symbol: "BTCUSDT", weight: 50 }, { symbol: "ETHUSDT", weight: 30 },
                  { symbol: "SOLUSDT", weight: 20 }]),
    "BTC 50% · ETH 30% · SOL 20%",
  );
  assert.equal(weightPhrase([{ symbol: "BTCUSDT", weight: 33.33 }, { symbol: "ETHUSDT", weight: 66.67 }]),
    "BTC 33.33% · ETH 66.67%");
  assert.equal(weightPhrase([]), "");
  assert.equal(weightPhrase(null), "");
});

test("비중이 다르면 카드가 비중을 적는다", async () => {
  const text = textOf(await renderComponent("src/components/MacroCard.jsx", {
    macro: { ...MACRO, legs: [{ symbol: "BTCUSDT", weight: 70 }, { symbol: "ETHUSDT", weight: 30 }] },
    symbols: ["BTCUSDT", "ETHUSDT"],
  }));
  assert.match(text, /종목 비중/);
  assert.match(text, /BTC 70% · ETH 30%/);
});

test("균등이면 기존 문구 그대로", async () => {
  const text = textOf(await renderComponent("src/components/MacroCard.jsx", {
    macro: { ...MACRO, symbols: ["BTCUSDT", "ETHUSDT"] },
    symbols: ["BTCUSDT", "ETHUSDT"],
  }));
  assert.match(text, /자금 균등/);
  assert.doesNotMatch(text, /종목 비중/);
});

test("묶음 한도가 있으면 한 행으로 보인다", async () => {
  const text = textOf(await renderComponent("src/components/MacroCard.jsx", {
    macro: { ...MACRO, legs: [{ symbol: "BTCUSDT", weight: 50 }, { symbol: "ETHUSDT", weight: 50 }],
             bundle_risk: { max_positions: 1, max_exposure_pct: 60 } },
    symbols: ["BTCUSDT", "ETHUSDT"],
  }));
  assert.match(text, /묶음 한도/);
  assert.match(text, /한 번에 1종목까지/);
  assert.match(text, /총 노출 60% 까지/);
});

test("단일 종목 카드에는 묶음 행이 없다", async () => {
  const text = textOf(await renderComponent("src/components/MacroCard.jsx",
    { macro: MACRO, symbols: ["BTCUSDT"] }));
  assert.doesNotMatch(text, /묶음 한도|종목 비중/);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && node --test "tests/macroCardBundle.test.js"`
Expected: `weightPhrase is not a function`.

- [ ] **Step 3: `portfolio.js` 에 두 함수를 더한다**

```js
// 서버의 summary._base_of 와 같은 규칙으로 티커를 짧게 부른다.
const QUOTES = ["USDT", "KRW", "BUSD"];

export function baseTicker(symbol) {
  const s = String(symbol || "").toUpperCase();
  for (const q of QUOTES) if (s.endsWith(q)) return s.slice(0, -q.length);
  return s;
}

// "BTC 50% · ETH 30%" — 비중이 균등하지 않은 묶음을 한 줄로 보여 준다.
export function weightPhrase(legs) {
  if (!Array.isArray(legs) || legs.length === 0) return "";
  return legs.map((leg) => `${baseTicker(leg.symbol)} ${Number(leg.weight)}%`).join(" · ");
}

// 서버 bundle.BundleGate.note() 와 같은 문구를 낸다. 한쪽만 고치면 두 화면이 달라진다.
export function bundleLimitPhrase(risk) {
  if (!risk) return "";
  const parts = [];
  if (risk.max_positions != null) parts.push(`한 번에 ${risk.max_positions}종목까지`);
  if (risk.max_exposure_pct != null) parts.push(`총 노출 ${Number(risk.max_exposure_pct)}% 까지`);
  return parts.join(" · ");
}
```

- [ ] **Step 4: `macroFacts` 에 두 행을 더한다**

지금 `symbols.length > 1` 일 때 `종목 N개 · 자금 균등` 행을 넣는 자리(`MacroCard.jsx:52`)를 갈라 준다:

```js
  const legs = Array.isArray(macro.legs) && macro.legs.length > 1 ? macro.legs : null;
  if (legs) {
    facts.push({ k: `종목 비중 ${legs.length}개`, v: weightPhrase(legs), num: true, wide: true });
  } else if (symbols.length > 1) {
    facts.push({ k: `종목 ${symbols.length}개 · 자금 균등`, v: symbols.join(" · "), num: true, wide: true });
  }
  if (macro.bundle_risk) {
    facts.push({ k: "묶음 한도", v: bundleLimitPhrase(macro.bundle_risk), wide: true });
  }
```

`MacroCard.jsx:62` 의 `종목당 {portfolioWeight(...).fraction}` 문구는 비중이 다를 때 거짓이 된다 — `legs` 가 있으면 그 조각을 빈 문자열로 둔다.

import 를 더한다: `import { bundleLimitPhrase, portfolioTitle, portfolioWeight, weightPhrase } from "../lib/portfolio.js";`

- [ ] **Step 5: 서버와 문구가 같은지 대조한다**

`bundleLimitPhrase`(JS)와 `BundleGate.note()`(파이썬)가 같은 문구를 두 번 구현한다. 1차의 `entryFilterPhrase`/`note()` 와 같은 상황이고, 같은 방식으로 **대조해 둔다.** 아래를 돌려 **불일치 0** 을 확인하고 그 결과를 보고서에 적는다:

```bash
cd /c/Users/RHJ/Desktop/gg_parrot && backend/.venv/Scripts/python.exe -c "
import io,sys,json
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
sys.path.insert(0,'backend')
from app.engine.bundle import BundleGate
from app.engine.schema import BundleRisk
cases=[{'max_positions':1},{'max_positions':3},{'max_exposure_pct':60},{'max_exposure_pct':62.5},
       {'max_positions':2,'max_exposure_pct':80},{'max_exposure_pct':100}]
print(json.dumps([[c, BundleGate(BundleRisk(**c), 1000.0).note()] for c in cases], ensure_ascii=False))
" > /tmp/py_notes.json
cd frontend && node -e "
const fs=require('fs');
import('./src/lib/portfolio.js').then(({bundleLimitPhrase})=>{
  const rows=JSON.parse(fs.readFileSync('/tmp/py_notes.json','utf8'));
  let bad=0;
  for(const [c,want] of rows){ const got=bundleLimitPhrase(c);
    if(got!==want){bad++;console.log('MISMATCH',JSON.stringify(c),'py=',want,'js=',got);} }
  console.log('불일치',bad,'/',rows.length);
});
"
```

**인코딩 주의:** 파이썬 쪽 출력을 UTF-8 로 강제했다. 이 래퍼 없이 돌리면 cp949 파이프가 한글을 깨뜨려 전부 "불일치" 로 보인다(1차에서 겪었다).

- [ ] **Step 6: 시험을 돌린다**

Run: `cd frontend && node --test "tests/macroCardBundle.test.js"`
Expected: PASS (5개).

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS.

- [ ] **Step 7: 돌연변이**

`macroFacts` 의 `묶음 한도` 행을 지운다 → `test("묶음 한도가 있으면 한 행으로 보인다")` 가 실패해야 한다. 되돌린다.

- [ ] **Step 8: 커밋**

```bash
git add frontend/src/components/MacroCard.jsx frontend/src/lib/portfolio.js frontend/tests/macroCardBundle.test.js
git commit -m "feat(web): 매크로 카드가 종목 비중과 묶음 한도를 보여 준다"
```

---

## 마지막 확인 (모든 Task 뒤)

- [ ] 백엔드 전체 수트

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py
```

Expected: 기존 실패 4개만 남는다(Global Constraints 참조).

- [ ] 프런트 전체 수트

```bash
cd frontend && node --test "tests/*.test.js"
```

- [ ] 실행기 불변 확인

```bash
cd /c/Users/RHJ/Desktop/gg_parrot && git diff --stat main -- runner/
grep -n 'RUNNER_VERSION = ' backend/app/runner.py
grep -c 'PORTFOLIO_UNSUPPORTED_DETAIL\|PORTFOLIO_REFUSED_NOTE' backend/app/runner.py backend/app/runner_engine.py
```

Expected: `runner/` 변경 0줄, `RUNNER_VERSION = "10"`, 묶음 거절 상수가 그대로 쓰인다.

- [ ] 프런트→백엔드 왕복: 프로 빌더가 만든 묶음 매크로가 서버 검증을 통과하는지 손으로 한 번 확인하고 결과를 적는다.
