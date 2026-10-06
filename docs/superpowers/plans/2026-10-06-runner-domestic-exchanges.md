# 실행기 국내 거래소(업비트 · 빗썸) 실거래 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GUI 매크로 실행기가 업비트 · 빗썸 원화 현물 주문을 내게 하되, 바이낸스 실거래 경로는 동작이 한 가지도 바뀌지 않는다.

**Architecture:** 실행기가 거래소를 부르는 11 곳을 `runner/brokers.py` 의 브로커 인터페이스 뒤로 모은다. `BinanceBroker` 는 지금 호출을 그대로 감싸고, `DomesticBroker` 하나가 업비트 · 빗썸을 베이스 URL 로 가른다. 테스트넷이 없는 자리는 `MockBroker` 데코레이터와 업비트 `POST /v1/orders/test` 리허설이 채운다. 서버는 거래소 차단을 실행기 버전 조건으로 바꾸고 신호 시세에 거래소를 넘긴다.

**Tech Stack:** Python 3 (실행기 · 백엔드), `requests`, 표준 라이브러리 `hmac`/`hashlib`/`base64` 로 만든 JWT(HS512), tkinter, PyInstaller. 프론트엔드는 React(문구만).

**Spec:** `docs/superpowers/specs/2026-10-06-runner-domestic-exchanges-design.md`

## Global Constraints

- **새 의존성을 넣지 않는다.** `runner/requirements.txt` 는 `python-binance>=1.0.19` 와 `requests>=2.31.0` 그대로. JWT HS512 는 `hmac` · `hashlib` · `base64` 로 직접 만든다.
- **`runner/test_macro_runner_execution.py` 의 시험 본문 20 개를 고치지 않는다.** `bot()` 도우미 한 곳만 바뀐다. 본문을 고쳐야 하면 멈추고 이유를 적는다.
- **거래소 비밀키는 서버로 가지 않는다.** `_build_start_payload` 가 보내는 것에 `api_key` · `api_secret` 이 어떤 거래소의 것이든 들어가면 안 된다.
- 봇은 `self.broker` 로 브로커를 쥔다. `self.client` 라는 이름은 쓰지 않는다 — 기존 시험이 그 이름을 날 `python-binance` 클라이언트로 알고 있다.
- 국내 거래소는 **현물 · 롱 · 1 배**만이다. `backend/app/engine/schema.py` 가 이미 막는다. 국내 경로에서 선물 메서드가 불리면 예외를 던진다.
- 주문 상한 상수: `MAX_ORDER_USDT = 100`, `MAX_ORDER_KRW = 150_000`.
- 실행기 버전: `runner/installation.py` 의 `RUNNER_VERSION` 을 `"10"` 으로. 서버는 `DOMESTIC_MIN_VERSION = "10"`.
- 거래소 이름은 `binance` · `upbit` · `bithumb` 셋뿐. 국내 두 개의 베이스 URL 은 `https://api.upbit.com` · `https://api.bithumb.com`.

## Review Focus

이 다섯 가지는 스펙이 요구하지만 어느 과제의 시험도 저절로 짚지 않는다. 각 줄의 시험을 그 코드를 가진 과제에 넣었다.

1. **원화 주문이 1 회 상한에 걸려 전부 거절된다** — `MAX_ORDER_USDT = 100` 을 KRW 에 쓰면 100 원이 되어 최소 주문 금액 미만이다. → Task 2
2. **모의 모드가 주문을 진짜로 낸다** — 데코레이터가 한 메서드라도 그대로 넘기면 실제 돈이 나간다. 세 거래소 모두에서 주문 엔드포인트가 한 번도 불리지 않아야 한다. → Task 6
3. **타임아웃 뒤 같은 주문을 두 번 낸다** — 국내 경로에서 응답이 없을 때 재주문하면 포지션이 두 배가 된다. `identifier` 로 조회해야 한다. → Task 4
4. **국내 매수에서 보유 수량을 수수료만큼 깎는다** — 업비트는 수수료를 원화에서 떼므로 차감하면 실제보다 적게 들고 있다고 믿고 청산이 남는다. → Task 4
5. **구버전 실행기가 국내 매크로를 받아 조용히 바이낸스로 주문한다** — 서버가 버전을 보지 않으면 v9 실행기가 `KRW-BTC` 를 바이낸스에 보낸다. → Task 9

---

### Task 1: 브로커 인터페이스와 정규화된 주문

브로커가 주고받을 자료형만 만든다. 아직 아무도 쓰지 않는다.

**Files:**
- Create: `runner/brokers.py`
- Test: `runner/test_brokers.py`

**Interfaces:**
- Consumes: 없음
- Produces: `Order(status, executed_qty, avg_price, acquired_qty, fees_known)`, `OrderRules(step, min_notional)`, `TERMINAL_STATUSES`, `Broker` (프로토콜)

- [ ] **Step 1: 실패하는 시험을 쓴다**

`runner/test_brokers.py`:

```python
import unittest

from runner import brokers


class OrderShapeTests(unittest.TestCase):
    def test_order_carries_fill_and_fee_confidence(self):
        order = brokers.Order(status="FILLED", executed_qty=2.0, avg_price=105.0,
                              acquired_qty=1.998, fees_known=True)
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(order.acquired_qty, 1.998)
        self.assertTrue(order.fees_known)

    def test_unknown_order_is_not_terminal(self):
        self.assertIn("FILLED", brokers.TERMINAL_STATUSES)
        self.assertIn("CANCELED", brokers.TERMINAL_STATUSES)
        self.assertIn("REJECTED", brokers.TERMINAL_STATUSES)
        self.assertNotIn("OPEN", brokers.TERMINAL_STATUSES)

    def test_order_rules_default_to_no_minimum(self):
        rules = brokers.OrderRules(step=0.001, min_notional=0.0)
        self.assertEqual(rules.step, 0.001)
        self.assertEqual(rules.min_notional, 0.0)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest runner/test_brokers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.brokers'`

- [ ] **Step 3: 최소 구현**

`runner/brokers.py`:

```python
"""거래소 어댑터 — 봇은 돈과 수량으로만 말하고, 거래소 사정은 여기 안에 있다.

수수료 모델이 거래소마다 다르다: 바이낸스 현물은 매수 수수료를 기초자산에서 떼므로
실제 보유 수량이 체결 수량보다 적고, 업비트·빗썸은 원화에서 떼므로 체결 수량이 그대로
보유 수량이다. 이 차이를 봇 본문에서 분기하면 가장 예민한 코드에 거래소 지식이 번진다.
그래서 브로커가 acquired_qty 를 채워서 돌려준다.
"""
from __future__ import annotations

from dataclasses import dataclass

# 더 기다려도 바뀌지 않는 상태. OPEN 은 아직 끝나지 않은 주문이다.
TERMINAL_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED"})


@dataclass(frozen=True)
class Order:
    """어느 거래소에서 왔든 봇이 같은 모양으로 읽는 주문 결과.

    acquired_qty 는 수수료를 뺀 '실제로 손에 남는' 수량이다.
    fees_known 이 거짓이면 수수료를 확정하지 못한 것이고, 봇은 포지션을 불확실로 본다.
    """

    status: str
    executed_qty: float
    avg_price: float
    acquired_qty: float
    fees_known: bool


@dataclass(frozen=True)
class OrderRules:
    """주문을 보내기 전에 알아야 하는 거래소 규격."""

    step: float          # 수량 최소 단위 (국내 시장가 매수는 금액으로 내므로 0)
    min_notional: float  # 1 회 최소 주문 금액 (호가 통화 기준)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest runner/test_brokers.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add runner/brokers.py runner/test_brokers.py
git commit -m "feat(runner): 거래소 브로커의 정규화된 주문 자료형"
```

---

### Task 2: 통화별 주문 상한

`MAX_ORDER_USDT` 를 원화에 쓰면 1 회 주문이 100 원이 되어 업비트 최소 주문 금액(5,000 원) 미만으로 전부 거절된다. 상한을 통화와 함께 다니게 한다.

**Files:**
- Modify: `runner/macro_runner.py` (상수 부근 99-101 행, `_order_qty`, `BotThread.__init__` 의 `self.capital`, `RiskGuard.__init__` 의 `self.base`, 시작 로그)
- Test: `runner/test_order_cap.py`

**Interfaces:**
- Consumes: 없음
- Produces: `macro_runner.MAX_ORDER_KRW`, `macro_runner.order_cap(quote: str) -> float`, `macro_runner.quote_of(symbol: str) -> str`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`runner/test_order_cap.py`:

```python
import unittest

from runner.test_macro_runner_single_instance import macro_runner


class OrderCapTests(unittest.TestCase):
    def test_krw_cap_is_not_the_usdt_number(self):
        # 100 원은 업비트 최소 주문 금액(5,000 원) 미만이라 전부 거절된다.
        self.assertEqual(macro_runner.order_cap("USDT"), macro_runner.MAX_ORDER_USDT)
        self.assertEqual(macro_runner.order_cap("KRW"), macro_runner.MAX_ORDER_KRW)
        self.assertGreater(macro_runner.order_cap("KRW"), 5000)

    def test_quote_is_read_from_the_symbol(self):
        self.assertEqual(macro_runner.quote_of("BTCUSDT"), "USDT")
        self.assertEqual(macro_runner.quote_of("KRW-BTC"), "KRW")

    def test_unknown_quote_falls_back_to_usdt_cap(self):
        self.assertEqual(macro_runner.order_cap("???"), macro_runner.MAX_ORDER_USDT)

    def test_krw_entry_quantity_uses_the_krw_cap(self):
        # 15,000,000 원짜리 코인을 사면 수량은 작지만 0 이 아니어야 한다.
        qty, notional = macro_runner._order_qty(
            15_000_000.0, 1e-8, 0, 1_000_000.0, 1, "spot", quote="KRW")
        self.assertGreater(qty, 0)
        self.assertLessEqual(notional, macro_runner.MAX_ORDER_KRW)

    def test_usdt_entry_quantity_is_unchanged(self):
        qty, notional = macro_runner._order_qty(100.0, 0.001, 0, 1000.0, 1, "spot")
        self.assertEqual(qty, 1.0)
        self.assertEqual(notional, 100.0)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest runner/test_order_cap.py -v`
Expected: FAIL — `AttributeError: module 'macro_runner' has no attribute 'order_cap'`

- [ ] **Step 3: 최소 구현**

`runner/macro_runner.py` 의 상수 자리(99 행 부근)에 더한다:

```python
MAX_ORDER_USDT = float(os.environ.get("MAX_ORDER_USDT", "100"))   # 1회 주문 상한(USDT)
MAX_ORDER_KRW = float(os.environ.get("MAX_ORDER_KRW", "150000"))  # 1회 주문 상한(KRW) — 100 USDT 와 비슷한 자리수
ORDER_CAP_BASIS = os.environ.get("ORDER_CAP_BASIS", "notional").lower()  # notional | margin
MAX_RETRIES = 3


def quote_of(symbol: str) -> str:
    """호가 통화 — 국내는 `KRW-BTC`, 바이낸스는 `BTCUSDT`."""
    return "KRW" if str(symbol).upper().startswith("KRW-") else "USDT"


def order_cap(quote: str) -> float:
    """1 회 주문 상한. 상한은 늘 통화와 함께 다닌다 — 떼어 두면 다음에 또 틀린다."""
    return MAX_ORDER_KRW if str(quote).upper() == "KRW" else MAX_ORDER_USDT
```

`_order_qty` 에 `quote` 를 받는다:

```python
def _order_qty(price, step, min_notional, budget, leverage, market, *, quote="USDT") -> tuple[float, float]:
    cap = min(budget, order_cap(quote))
    if market == "futures" and ORDER_CAP_BASIS == "margin":
        notional = cap * leverage
    else:
        notional = cap
    qty = _round_step(notional / price, step)
    return qty, qty * price
```

`BotThread.__init__` 에서 `self.quote` 를 두고 자본 기본값을 고친다(`self.symbol` 을 정한 뒤):

```python
        self.quote = quote_of(self.symbol)
```

```python
        self.capital = float((macro.get("params") or {}).get("initial_capital") or 0) or order_cap(self.quote)
```

`RiskGuard.__init__` 가 상한을 받게 한다:

```python
    def __init__(self, risk: dict, base_capital: float, *, cap: float = MAX_ORDER_USDT) -> None:
        ...
        self.base = base_capital if base_capital > 0 else cap
```

`RiskGuard` 를 만드는 자리에서 `cap=order_cap(self.quote)` 를 넘기고, 시작 로그(875 행 부근)의 `f"... 주문 상한 {MAX_ORDER_USDT} USDT ..."` 를 `f"... 주문 상한 {order_cap(self.quote):,.0f} {self.quote} ..."` 로 바꾼다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest runner/test_order_cap.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 기존 실행기 시험이 그대로 녹색인지 본다**

Run: `python -m pytest runner/ -v`
Expected: 모두 PASS. `_order_qty` 의 `quote` 는 키워드 기본값이라 기존 호출이 바뀌지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add runner/macro_runner.py runner/test_order_cap.py
git commit -m "fix(runner): 1회 주문 상한을 통화별로 — 원화에 USDT 상한을 쓰면 전부 거절된다"
```

---

### Task 3: BinanceBroker — 이동이지 변경이 아니다

지금 `_place` 안에 있는 제출 · 재조정 · 평균가 · 수수료 계산을 `BinanceBroker` 로 옮긴다. 호출 인자 · 재시도 횟수 · 터미널 상태 · 평균가 보정 순서를 바꾸지 않는다.

**Files:**
- Modify: `runner/brokers.py`
- Modify: `runner/macro_runner.py` (`_connect`, `_prepare`, `_price`, `_place`, `_fut_symbol_info`, `_fut_usdt_balance`)
- Modify: `runner/test_macro_runner_execution.py` (**`bot()` 도우미 한 곳만**)
- Test: `runner/test_brokers.py`

**Interfaces:**
- Consumes: Task 1 의 `Order` · `OrderRules` · `TERMINAL_STATUSES`, Task 2 의 `order_cap`
- Produces: `brokers.BinanceBroker(raw, *, market, symbol, side, testnet, log)` 와 메서드 `ensure_ready() -> bool` · `price() -> float` · `order_rules() -> OrderRules` · `submit(side_word, *, base_qty=None, notional=None, reduce_only=False, client_id) -> Order`. 봇은 `self.broker` 로 쥔다.

- [ ] **Step 1: 실패하는 시험을 쓴다**

`runner/test_brokers.py` 에 더한다:

```python
from unittest.mock import Mock


class BinanceBrokerTests(unittest.TestCase):
    def broker(self, market="spot"):
        self.raw = Mock()
        return brokers.BinanceBroker(self.raw, market=market, symbol="BTCUSDT",
                                     side="long", testnet=True, log=Mock())

    def test_spot_base_fee_is_deducted_from_acquired(self):
        broker = self.broker()
        self.raw.create_order.return_value = {
            "orderId": 7, "status": "FILLED", "executedQty": "2", "avgPrice": "100",
            "fills": [{"price": "100", "qty": "2", "commission": "0.002", "commissionAsset": "BTC"}],
        }
        order = broker.submit("BUY", base_qty=2.0, client_id="ggp-1")
        self.assertEqual(order.executed_qty, 2.0)
        self.assertAlmostEqual(order.acquired_qty, 1.998)
        self.assertTrue(order.fees_known)

    def test_timeout_is_reconciled_by_client_id_without_a_second_order(self):
        broker = self.broker(market="futures")
        self.raw.futures_create_order.side_effect = TimeoutError()
        self.raw.futures_get_order.return_value = {"status": "FILLED", "executedQty": "2", "avgPrice": "105"}
        order = broker.submit("SELL", base_qty=2.0, client_id="ggp-2")
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(self.raw.futures_create_order.call_count, 1)
        self.raw.futures_get_order.assert_called_with(symbol="BTCUSDT", origClientOrderId="ggp-2")

    def test_missing_fee_confirmation_is_reported_not_guessed(self):
        broker = self.broker()
        self.raw.create_order.return_value = {
            "orderId": 8, "status": "FILLED", "executedQty": "2", "avgPrice": "100", "fills": [],
        }
        self.raw.get_my_trades.return_value = []
        order = broker.submit("BUY", base_qty=2.0, client_id="ggp-3")
        self.assertFalse(order.fees_known)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest runner/test_brokers.py -v`
Expected: FAIL — `AttributeError: module 'runner.brokers' has no attribute 'BinanceBroker'`

- [ ] **Step 3: BinanceBroker 를 만든다**

`runner/brokers.py` 에 더한다. **`macro_runner._place` 의 601-730 행에 있는 제출 · 재조정 · `positive_number` · `executed`/`average`/`acquired`/`fees_known` 계산을 그대로 옮긴다.** 숫자와 순서를 바꾸지 않는다.

파일 맨 위 import 에 `math` 와 `time` 을 더한다 (Task 1 은 `dataclass` 만 들여왔다).

```python
MAX_RETRIES = 3


def _positive_number(value):
    try:
        number = float(value or 0)
        return number if math.isfinite(number) and number > 0 else 0.0
    except (ValueError, TypeError):
        return 0.0


class BinanceBroker:
    """지금 봇이 하던 바이낸스 호출을 그대로 감싼다. 로직 이동이지 변경이 아니다."""

    def __init__(self, raw, *, market, symbol, side, testnet, log):
        self.raw = raw
        self.market = market
        self.symbol = symbol
        self.side = side
        self.testnet = testnet
        self.log = log
        self.base_asset = ""

    def price(self) -> float:
        if self.market == "futures":
            return float(self.raw.futures_symbol_ticker(symbol=self.symbol)["price"])
        return float(self.raw.get_symbol_ticker(symbol=self.symbol)["price"])

    def submit(self, side_word, *, base_qty=None, notional=None, reduce_only=False, client_id):
        qty = base_qty
        kwargs = dict(symbol=self.symbol, side=side_word, type="MARKET",
                      quantity=qty, newClientOrderId=client_id,
                      newOrderRespType="RESULT" if self.market == "futures" else "FULL")
        if reduce_only:
            kwargs["reduceOnly"] = "true"
        try:
            create = self.raw.futures_create_order if self.market == "futures" else self.raw.create_order
            order = create(**kwargs)
        except Exception:
            # 타임아웃은 거절의 증거가 아니다. 같은 client_id 로 조회할 뿐 재주문하지 않는다.
            order = {}
        terminal = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"}
        for attempt in range(MAX_RETRIES):
            if order.get("status") in terminal:
                break
            try:
                query = self.raw.futures_get_order if self.market == "futures" else self.raw.get_order
                order = query(symbol=self.symbol, origClientOrderId=client_id)
            except Exception:
                pass  # 직전 응답에서 확인된 부분 체결을 유지한다
            if order.get("status") not in terminal and attempt + 1 < MAX_RETRIES:
                time.sleep(0.25 * (attempt + 1))
        return self._normalize(order, closing=reduce_only or side_word != ("BUY" if self.side == "long" else "SELL"))
```

`_normalize` 는 `macro_runner._place` 의 `executed`/`average`/`acquired`/`fees_known` 블록을 그대로 옮기고 `Order` 로 싼다. `status` 는 바이낸스의 `EXPIRED`·`EXPIRED_IN_MATCH` 를 `CANCELED` 로 접고 그 밖의 비터미널을 `OPEN` 으로 접는다. `self.raw.get_order`·`get_my_trades`·`futures_*` 호출 인자를 바꾸지 않는다.

`ensure_ready()` 는 `_connect` 의 계정 확인과 `_prepare` 의 심볼정보 · 마진 · 레버리지 설정을 그대로 옮기고, `order_rules()` 는 `_parse_filters` 결과를 `OrderRules` 로 돌려준다.

- [ ] **Step 4: 봇이 브로커를 쓰게 한다**

`macro_runner.BotThread` 에서:
- `self.client = None` → `self.broker = None`
- `_connect` 가 `Client(...)` 를 만들어 `BinanceBroker` 로 감싸 `self.broker` 에 둔다
- `_price` 는 `return self.broker.price()`
- `_place` 는 제출 · 재조정 블록을 지우고 `order = self.broker.submit(side_word, base_qty=qty, reduce_only=reduce_only, client_id=client_id)` 한 줄로 바꾼다. **그 아래의 포지션 갱신 · 먼지 · `position_uncertain` · `raise` 는 그대로 둔다.** `order.get("status")` 를 읽던 자리는 `order.status` 로, `executed`/`average`/`acquired`/`fees_known` 은 `order.executed_qty` / `order.avg_price` / `order.acquired_qty` / `order.fees_known` 으로 읽는다
- `_fut_symbol_info` · `_fut_usdt_balance` · `_prepare` 의 거래소 호출은 브로커로 옮기고 봇에서 지운다

- [ ] **Step 5: 기존 시험의 `bot()` 도우미만 고친다**

`runner/test_macro_runner_execution.py` 의 `bot()` 에서 `bot.client = Mock()` 한 줄을 셋으로 바꾼다. **다른 줄과 다른 시험은 건드리지 않는다.**

```python
        bot.client = Mock()   # 기존 시험 본문이 쓰는 이름 — 날 python-binance 클라이언트
        bot.broker = macro_runner.brokers.BinanceBroker(
            bot.client, market="futures", symbol="BTCUSDT", side="long", testnet=True, log=Mock())
```

`bot.market`/`bot.symbol`/`bot.side` 를 바꾸는 시험이 있으면 브로커의 같은 속성도 따라가야 한다 — 도우미에서 브로커를 만들 때 이 값들을 쓰고, 시험이 뒤에 바꾸는 경우는 `bot.broker.market` 등을 함께 맞춘다.

- [ ] **Step 6: 시험 본문이 안 바뀌었음을 확인한다**

Run: `git diff runner/test_macro_runner_execution.py`
Expected: `bot()` 도우미 안의 줄만 바뀌었다. `def test_` 로 시작하는 함수 본문은 한 줄도 바뀌지 않았다.

- [ ] **Step 7: 전체 실행기 시험**

Run: `python -m pytest runner/ -v`
Expected: 모두 PASS. 특히 `test_macro_runner_execution.py` 20 개.

- [ ] **Step 8: 커밋**

```bash
git add runner/brokers.py runner/macro_runner.py runner/test_brokers.py runner/test_macro_runner_execution.py
git commit -m "refactor(runner): 바이낸스 거래소 호출을 BinanceBroker 뒤로 — 동작은 그대로"
```

---

### Task 4: DomesticBroker — 업비트 · 빗썸

**Files:**
- Modify: `runner/brokers.py`
- Test: `runner/test_brokers_domestic.py`

**Interfaces:**
- Consumes: Task 1 의 `Order` · `OrderRules`
- Produces: `brokers.DomesticBroker(access_key, secret_key, *, exchange, symbol, log, session=None)` — `BinanceBroker` 와 같은 메서드 이름. `brokers.DOMESTIC_BASES`, `brokers.jwt_token(access_key, secret_key, query=None)`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`runner/test_brokers_domestic.py`:

```python
import json
import unittest
from unittest.mock import Mock

from runner import brokers


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class DomesticBrokerTests(unittest.TestCase):
    def broker(self, exchange="upbit"):
        self.session = Mock()
        return brokers.DomesticBroker("acc", "sec", exchange=exchange,
                                      symbol="KRW-BTC", log=Mock(), session=self.session)

    def test_market_buy_sends_krw_total_not_volume(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u1", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01",
             "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]})
        broker.submit("BUY", notional=1_000_000.0, client_id="ggp-1")
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["side"], "bid")
        self.assertEqual(body["ord_type"], "price")
        self.assertEqual(float(body["price"]), 1_000_000.0)
        self.assertNotIn("volume", body)

    def test_market_sell_sends_volume_not_price(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u2", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01",
             "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]})
        broker.submit("SELL", base_qty=0.01, client_id="ggp-2")
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["side"], "ask")
        self.assertEqual(body["ord_type"], "market")
        self.assertEqual(float(body["volume"]), 0.01)
        self.assertNotIn("price", body)

    def test_timeout_reconciles_by_identifier_without_a_second_order(self):
        broker = self.broker()
        self.session.post.side_effect = TimeoutError()
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01",
             "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]})
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-3")
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(self.session.get.call_args.kwargs["params"], {"identifier": "ggp-3"})

    def test_acquired_is_not_reduced_by_fees(self):
        # 업비트는 수수료를 원화에서 뗀다 — 기초자산을 깎으면 실제보다 적게 들고 있다고 믿는다.
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u4", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01", "paid_fee": "500",
             "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]})
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-4")
        self.assertEqual(order.acquired_qty, order.executed_qty)
        self.assertEqual(order.acquired_qty, 0.01)

    def test_average_price_comes_from_trades(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u5", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.02",
             "trades": [{"price": "100", "volume": "0.01", "funds": "1.0"},
                        {"price": "300", "volume": "0.01", "funds": "3.0"}]})
        order = broker.submit("BUY", notional=4.0, client_id="ggp-5")
        self.assertEqual(order.avg_price, 200.0)   # (1.0 + 3.0) / 0.02

    def test_partial_trade_coverage_is_reported_as_unknown_fees(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u6", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.02",
             "trades": [{"price": "100", "volume": "0.01", "funds": "1.0"}]})
        order = broker.submit("BUY", notional=2.0, client_id="ggp-6")
        self.assertFalse(order.fees_known)

    def test_bithumb_uses_its_own_base_url_with_the_same_path(self):
        broker = self.broker(exchange="bithumb")
        self.session.post.return_value = FakeResponse({"uuid": "u7", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01",
             "trades": [{"price": "100", "volume": "0.01", "funds": "1.0"}]})
        broker.submit("BUY", notional=1.0, client_id="ggp-7")
        url = self.session.post.call_args.args[0]
        self.assertTrue(url.startswith("https://api.bithumb.com"))
        self.assertTrue(url.endswith("/v1/orders"))

    def test_futures_methods_refuse_loudly(self):
        broker = self.broker()
        with self.assertRaises(Exception):
            broker.set_leverage(3)


class JwtTests(unittest.TestCase):
    def test_token_has_three_parts_and_a_fresh_nonce(self):
        first = brokers.jwt_token("acc", "sec")
        second = brokers.jwt_token("acc", "sec")
        self.assertEqual(len(first.split(".")), 3)
        self.assertNotEqual(first, second)   # nonce 가 요청마다 새로워야 한다

    def test_query_hash_is_present_only_with_a_query(self):
        import base64

        def payload(token):
            raw = token.split(".")[1]
            raw += "=" * (-len(raw) % 4)
            return json.loads(base64.urlsafe_b64decode(raw))

        self.assertNotIn("query_hash", payload(brokers.jwt_token("acc", "sec")))
        body = payload(brokers.jwt_token("acc", "sec", {"market": "KRW-BTC"}))
        self.assertIn("query_hash", body)
        self.assertEqual(body["query_hash_alg"], "SHA512")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest runner/test_brokers_domestic.py -v`
Expected: FAIL — `AttributeError: module 'runner.brokers' has no attribute 'DomesticBroker'`

- [ ] **Step 3: JWT 와 DomesticBroker 를 만든다**

`runner/brokers.py` 에 더한다. import 에 `base64` · `hashlib` · `hmac` · `json` · `uuid` 와 `requests` 를 더한다 — **PyJWT 를 넣지 않는다.**

```python
DOMESTIC_BASES = {"upbit": "https://api.upbit.com", "bithumb": "https://api.bithumb.com"}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def jwt_token(access_key: str, secret_key: str, query: dict | None = None) -> str:
    """HS512 JWT — PyJWT 를 넣지 않는다. 실행기는 exe 라 의존성 하나가 빌드·서명에 걸린다."""
    header = _b64(json.dumps({"alg": "HS512", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = {"access_key": access_key, "nonce": str(uuid.uuid4())}
    if query:
        # 업비트는 쿼리 스트링(정렬 없이 보낸 순서)의 SHA512 를 요구한다.
        qs = "&".join(f"{k}={v}" for k, v in query.items())
        payload["query_hash"] = hashlib.sha512(qs.encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    signature = hmac.new(secret_key.encode(), signing_input, hashlib.sha512).digest()
    return f"{header}.{body}.{_b64(signature)}"
```

`DomesticBroker` 는 `BinanceBroker` 와 같은 메서드 이름을 갖는다.

- `price()` — 공개 시세 `GET /v1/ticker?markets=`. 키가 없어도 된다
- `order_rules()` — `GET /v1/orders/chance?market=` 의 `bid.min_total` 을 `min_notional` 로, `step` 은 `0.0`(시장가 매수를 금액으로 내므로 수량 단위가 필요 없다). 수수료율 `bid_fee`/`ask_fee` 는 `self.fees` 에 둔다
- `submit(side_word, *, base_qty, notional, reduce_only, client_id)` — 매수면 `{"market", "side": "bid", "ord_type": "price", "price": notional, "identifier": client_id}`, 매도면 `{"market", "side": "ask", "ord_type": "market", "volume": base_qty, "identifier": client_id}` 로 `POST /v1/orders`. 예외가 나면 **재주문하지 않고** `GET /v1/order?identifier=` 로 `MAX_RETRIES` 번까지 조회한다
- `_normalize(payload)` — `state` 를 `done → FILLED` · `cancel → CANCELED` · 그 밖 → `OPEN`. `executed_qty = float(executed_volume)`, `avg_price = Σ funds / Σ volume`(수량이 0 이면 0.0), `acquired_qty = executed_qty`(수수료가 원화에서 빠진다), `fees_known = Σ trades.volume ≈ executed_qty`
- `set_leverage` · `set_margin_type` 등 선물 메서드는 `RuntimeError("국내 현물에는 선물 설정이 없습니다")` 를 던진다

모든 사설 호출은 `Authorization: Bearer <jwt_token(...)>` 를 붙이고 베이스 URL 은 `DOMESTIC_BASES[self.exchange]` 를 쓴다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest runner/test_brokers_domestic.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
git add runner/brokers.py runner/test_brokers_domestic.py
git commit -m "feat(runner): 업비트·빗썸 주문 브로커 — 같은 경로, 다른 베이스 URL"
```

---

### Task 5: 리허설

**Files:**
- Modify: `runner/brokers.py`
- Test: `runner/test_brokers_domestic.py`

**Interfaces:**
- Consumes: Task 4 의 `DomesticBroker`, Task 3 의 `BinanceBroker`
- Produces: 두 브로커의 `rehearse(*, notional) -> tuple[bool, str]` — `(통과 여부, 사람이 읽을 이유)`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`runner/test_brokers_domestic.py` 에 더한다:

```python
class RehearsalTests(DomesticBrokerTests):
    def test_rehearsal_uses_the_test_endpoint_not_the_real_one(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "t1", "state": "wait"}, status=201)
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertTrue(ok, reason)
        url = self.session.post.call_args.args[0]
        self.assertTrue(url.endswith("/v1/orders/test"))

    def test_rehearsal_translates_ip_allowlist_failure_first(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse(
            {"error": {"name": "no_authorization_ip", "message": "허용되지 않은 IP"}}, status=401)
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertFalse(ok)
        self.assertIn("IP", reason)

    def test_rehearsal_reports_minimum_order_amount(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse(
            {"error": {"name": "under_min_total_bid", "message": "최소 주문 금액"}}, status=400)
        ok, reason = broker.rehearse(notional=1_000.0)
        self.assertFalse(ok)
        self.assertIn("최소 주문 금액", reason)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest runner/test_brokers_domestic.py -k Rehearsal -v`
Expected: FAIL — `AttributeError: 'DomesticBroker' object has no attribute 'rehearse'`

- [ ] **Step 3: 구현**

`DomesticBroker.rehearse` 는 `submit` 과 **같은 본문**으로 `POST /v1/orders/test` 를 친다. 201 이면 통과. 실패면 응답의 `error.name` 을 사람 말로 옮긴다 — 인증 · IP 계열(`no_authorization_ip`, `invalid_access_key`, `jwt_verification`)은 **허용 IP 를 먼저 말하고**, `under_min_total*` 은 최소 주문 금액을 말한다. 모르는 오류는 거래소 메시지를 그대로 붙인다.

`BinanceBroker.rehearse` 는 리허설 호출이 없으므로 `ensure_ready()` 와 `order_rules()` 가 성공하는지로 갈음한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest runner/test_brokers_domestic.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: 커밋**

```bash
git add runner/brokers.py runner/test_brokers_domestic.py
git commit -m "feat(runner): 주문 전 리허설 — 돈 없이 키·IP·권한·최소 금액을 확인한다"
```

---

### Task 6: MockBroker — 테스트넷이 없는 자리

**Files:**
- Modify: `runner/brokers.py`
- Test: `runner/test_brokers_mock.py`

**Interfaces:**
- Consumes: Task 1 의 `Order` · `OrderRules`, Task 5 의 `rehearse`
- Produces: `brokers.MockBroker(inner, *, log)` — `inner` 와 같은 메서드. `submit` 은 네트워크로 나가지 않는다

- [ ] **Step 1: 실패하는 시험을 쓴다**

`runner/test_brokers_mock.py`:

```python
import unittest
from unittest.mock import Mock

from runner import brokers


class MockBrokerTests(unittest.TestCase):
    def inner(self):
        inner = Mock()
        inner.price.return_value = 100.0
        inner.order_rules.return_value = brokers.OrderRules(step=0.001, min_notional=0.0)
        inner.fees = {"bid": 0.0005, "ask": 0.0005}
        inner.rehearse.return_value = (True, "")
        return inner

    def test_mock_never_submits_to_the_exchange(self):
        inner = self.inner()
        broker = brokers.MockBroker(inner, log=Mock())
        broker.submit("BUY", notional=1000.0, client_id="ggp-1")
        broker.submit("SELL", base_qty=1.0, client_id="ggp-2")
        inner.submit.assert_not_called()

    def test_mock_fills_at_the_current_price(self):
        broker = brokers.MockBroker(self.inner(), log=Mock())
        order = broker.submit("BUY", notional=1000.0, client_id="ggp-3")
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(order.avg_price, 100.0)
        self.assertTrue(order.fees_known)

    def test_mock_applies_the_fee_rate_to_acquired(self):
        broker = brokers.MockBroker(self.inner(), log=Mock())
        order = broker.submit("BUY", notional=1000.0, client_id="ggp-4")
        self.assertLess(order.acquired_qty, order.executed_qty * 1.0 + 1e-12)
        self.assertGreater(order.acquired_qty, 0)

    def test_mock_still_reads_price_from_the_real_exchange(self):
        inner = self.inner()
        brokers.MockBroker(inner, log=Mock()).price()
        inner.price.assert_called_once()

    def test_mock_tolerates_a_failed_rehearsal(self):
        inner = self.inner()
        inner.rehearse.return_value = (False, "허용 IP 가 등록되지 않았어요")
        log = Mock()
        ok, reason = brokers.MockBroker(inner, log=log).rehearse(notional=1000.0)
        self.assertTrue(ok, "모의는 키 없이도 돌 수 있어야 연습이 된다")
        self.assertTrue(any("허용 IP" in str(c) for c in log.call_args_list))

    def test_mock_falls_back_when_order_rules_need_keys(self):
        inner = self.inner()
        inner.order_rules.side_effect = RuntimeError("401")
        rules = brokers.MockBroker(inner, log=Mock()).order_rules()
        self.assertGreaterEqual(rules.min_notional, 0.0)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest runner/test_brokers_mock.py -v`
Expected: FAIL — `AttributeError: module 'runner.brokers' has no attribute 'MockBroker'`

- [ ] **Step 3: 구현**

`MockBroker` 는 `inner` 를 감싸고 읽기는 넘기되 주문만 삼킨다.

- `price()` · `ensure_ready()` → `inner` 에 그대로
- `order_rules()` → `inner` 를 쓰되 예외가 나면(키가 없으면) 기본값 `OrderRules(step=0.0, min_notional=0.0)` 을 돌려주고 그 사실을 한 번 기록한다
- `submit(...)` → **`inner.submit` 을 부르지 않는다.** `price()` 로 체결가를 잡고, 매수는 `executed_qty = notional / price`, 매도는 `executed_qty = base_qty`. 수수료율(`inner.fees` 가 없으면 0.0005)로 `acquired_qty` 를 깎는다. `status="FILLED"`, `fees_known=True`
- `rehearse(...)` → `inner.rehearse` 를 부르되 **실패해도 `(True, ...)` 로 돌려주고 이유를 기록한다.** 모의는 키 없이도 돌 수 있어야 연습이 된다

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest runner/test_brokers_mock.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add runner/brokers.py runner/test_brokers_mock.py
git commit -m "feat(runner): 모의 모드 — 주문만 삼키고 읽기는 진짜로"
```

---

### Task 7: credentials v2 — 거래소별 키

**Files:**
- Modify: `runner/credentials.py`
- Modify: `runner/test_credentials.py`

**Interfaces:**
- Consumes: 없음
- Produces: `credentials.save(path, values, *, protect=...)` 와 `credentials.load(path, *, unprotect=...)` 가 `{"version": 2, "member_key": str, "exchanges": {name: {"api_key": str, "api_secret": str}}}` 를 다룬다. `credentials.EXCHANGES = ("binance", "upbit", "bithumb")`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`runner/test_credentials.py` 에 더한다:

```python
class CredentialsV2Tests(unittest.TestCase):
    def roundtrip(self, values):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.dat"
            credentials.save(path, values, protect=lambda b: b)
            return credentials.load(path, unprotect=lambda b: b)

    def test_each_exchange_keeps_its_own_pair(self):
        loaded = self.roundtrip({
            "member_key": "mk",
            "exchanges": {"binance": {"api_key": "bk", "api_secret": "bs"},
                          "upbit": {"api_key": "uk", "api_secret": "us"}},
        })
        self.assertEqual(loaded["exchanges"]["binance"]["api_key"], "bk")
        self.assertEqual(loaded["exchanges"]["upbit"]["api_secret"], "us")
        self.assertEqual(loaded["member_key"], "mk")

    def test_v1_file_moves_into_binance_and_keeps_member_key(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.dat"
            path.write_bytes(json.dumps(
                {"api_key": "old", "api_secret": "olds", "member_key": "mk"}).encode())
            loaded = credentials.load(path, unprotect=lambda b: b)
        self.assertEqual(loaded["exchanges"]["binance"]["api_key"], "old")
        self.assertEqual(loaded["exchanges"]["binance"]["api_secret"], "olds")
        self.assertEqual(loaded["member_key"], "mk")
        self.assertEqual(loaded["version"], 2)

    def test_unknown_exchange_names_are_dropped(self):
        loaded = self.roundtrip({
            "member_key": "mk",
            "exchanges": {"binance": {"api_key": "bk", "api_secret": "bs"},
                          "kraken": {"api_key": "x", "api_secret": "y"}},
        })
        self.assertNotIn("kraken", loaded["exchanges"])

    def test_entropy_is_unchanged_so_old_files_still_open(self):
        self.assertEqual(credentials._ENTROPY, b"ggparrot-runner-credentials-v1")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest runner/test_credentials.py -v`
Expected: FAIL — v1 파일이 `exchanges` 로 옮겨지지 않는다

- [ ] **Step 3: 구현**

`credentials.py` 에서:

```python
EXCHANGES = ("binance", "upbit", "bithumb")
FIELDS = ("api_key", "api_secret", "member_key")  # v1 호환 — 이사에만 쓴다
```

`save` 는 `member_key` 와 `exchanges`(아는 거래소만, 각 `api_key`/`api_secret` 문자열)를 `{"version": 2, ...}` 로 쓴다. `load` 는 읽은 뒤 `version` 이 없으면 v1 로 보고 `api_key`/`api_secret` 을 `exchanges.binance` 로 옮기고 `version: 2` 를 붙인다. `_ENTROPY` 는 **바꾸지 않는다** — 바꾸면 기존 파일이 열리지 않아 이사 자체가 불가능하다.

`apply_choice` 도 v2 모양을 받게 고친다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest runner/test_credentials.py -v`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add runner/credentials.py runner/test_credentials.py
git commit -m "feat(runner): 자격증명 v2 — 거래소별 키 한 쌍씩, v1 은 바이낸스로 이사"
```

---

### Task 8: 실행기가 거래소를 고른다

봇이 매크로의 `exchange` 로 브로커를 만들고, GUI 가 거래소별 키 칸과 실행 모드를 보인다.

**Files:**
- Modify: `runner/macro_runner.py` (`BotThread.__init__`, `_connect`, 실행 모드 · 키 칸 GUI, `_build_start_payload`)
- Modify: `runner/installation.py` (`RUNNER_VERSION`)
- Test: `runner/test_macro_runner_exchange.py`

**Interfaces:**
- Consumes: Task 3·4·6 의 브로커들, Task 7 의 자격증명 v2
- Produces: `BotThread(macro, credentials_values, mode, server, on_log, on_status, on_finish)` — `mode` 는 `"mock" | "testnet" | "live"`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`runner/test_macro_runner_exchange.py`:

```python
import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner


class ExchangeSelectionTests(unittest.TestCase):
    def bot(self, exchange, symbol, mode="live", keys=None):
        bot = object.__new__(macro_runner.BotThread)
        bot.macro = {"exchange": exchange, "symbol": symbol, "rule_type": "A",
                     "params": {"initial_capital": 1_000_000}, "risk": {}}
        bot.symbol, bot.side, bot.leverage = symbol, "long", 1
        bot.exchange, bot.mode = exchange, mode
        bot.quote = macro_runner.quote_of(symbol)
        bot.credentials = keys if keys is not None else {
            "exchanges": {exchange: {"api_key": "k", "api_secret": "s"}}}
        bot.log = Mock()
        return bot

    def test_domestic_macro_builds_the_domestic_broker(self):
        bot = self.bot("upbit", "KRW-BTC")
        self.assertTrue(bot._connect())
        self.assertIsInstance(bot.broker, macro_runner.brokers.DomesticBroker)

    def test_mock_mode_wraps_whichever_broker(self):
        for exchange, symbol in (("upbit", "KRW-BTC"), ("bithumb", "KRW-ETH")):
            bot = self.bot(exchange, symbol, mode="mock")
            self.assertTrue(bot._connect())
            self.assertIsInstance(bot.broker, macro_runner.brokers.MockBroker)

    def test_missing_keys_for_that_exchange_block_the_start(self):
        bot = self.bot("upbit", "KRW-BTC", keys={"exchanges": {"binance": {"api_key": "k", "api_secret": "s"}}})
        self.assertFalse(bot._connect())
        self.assertTrue(any("업비트" in str(c) for c in bot.log.call_args_list))

    def test_exchange_secrets_never_reach_the_server_payload(self):
        # 거래소가 셋이 되어도 비밀키는 이 PC를 떠나지 않는다.
        secrets = {"exchanges": {name: {"api_key": f"KEY-{name}", "api_secret": f"SECRET-{name}"}
                                 for name in ("binance", "upbit", "bithumb")}}
        for exchange, symbol in (("binance", "BTCUSDT"), ("upbit", "KRW-BTC"), ("bithumb", "KRW-ETH")):
            bot = self.bot(exchange, symbol, keys=secrets)
            bot.member_key = Mock(get=Mock(return_value="mk"))
            blob = repr(macro_runner.BotThread._build_start_payload(bot, False))
            for name in ("binance", "upbit", "bithumb"):
                self.assertNotIn(f"SECRET-{name}", blob)
                self.assertNotIn(f"KEY-{name}", blob)

    def test_runner_version_is_ten(self):
        from runner import installation
        self.assertEqual(installation.RUNNER_VERSION, "10")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest runner/test_macro_runner_exchange.py -v`
Expected: FAIL — `BotThread` 가 아직 `exchange`/`mode` 를 모른다

- [ ] **Step 3: 구현**

- `BotThread.__init__` 가 `api_key`/`api_secret`/`testnet` 대신 `credentials`(v2 모양)와 `mode` 를 받는다. `self.exchange = str(macro.get("exchange", "binance")).lower()`
- `_connect` 가 거래소로 갈라 브로커를 만든다. `binance` → `BinanceBroker(Client(...))`, 그 밖 → `DomesticBroker(...)`. 해당 거래소 키가 없으면 거래소 이름을 넣어 기록하고 `False`
- `mode == "mock"` 이면 만든 브로커를 `MockBroker` 로 감싼다. `mode == "testnet"` 은 바이낸스에서만 뜻이 있다(국내는 `mock`·`live` 둘뿐)
- 세션 시작에서 `self.broker.rehearse(notional=...)` 를 한 번 부른다. 실패하면 `live` 에서는 주문 없이 멈추고, `mock` 에서는 기록만 남긴다
- GUI: 실행 모드 라디오를 `모의`·`테스트넷`·`실전` 으로 두고 매크로의 거래소가 국내면 `테스트넷` 을 숨긴다. 기본값은 늘 `모의`. 키 칸은 거래소별로 셋
- `installation.RUNNER_VERSION` 을 `"10"` 으로

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest runner/test_macro_runner_exchange.py -v`
Expected: PASS

- [ ] **Step 5: 전체 실행기 시험**

Run: `python -m pytest runner/ -v`
Expected: 모두 PASS. `test_macro_runner_execution.py` 20 개 포함.

- [ ] **Step 6: 커밋**

```bash
git add runner/macro_runner.py runner/installation.py runner/test_macro_runner_exchange.py
git commit -m "feat(runner): 매크로의 거래소로 브로커를 고르고 모의·실전 모드를 가른다"
```

---

### Task 9: 서버가 문을 연다

**Files:**
- Modify: `backend/app/runner.py` (`_require_binance_macro` 와 호출 세 곳, `claim` 의 방어적 검사, 버전 상수)
- Modify: `backend/app/runner_engine.py` (신호 시세)
- Test: `backend/tests/test_runner_domestic.py`

**Interfaces:**
- Consumes: 없음
- Produces: `runner.DOMESTIC_MIN_VERSION`, `runner.supports_domestic(version: str) -> bool`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`backend/tests/test_runner_domestic.py`:

```python
import pytest

from app import runner as runner_mod


def test_version_gate_mirrors_the_signal_gate():
    assert runner_mod.DOMESTIC_MIN_VERSION == "10"
    assert runner_mod.supports_domestic("10") is True
    assert runner_mod.supports_domestic("11") is True
    assert runner_mod.supports_domestic("9") is False
    assert runner_mod.supports_domestic("") is False
    assert runner_mod.supports_domestic("abc") is False


def test_old_runner_cannot_take_a_domestic_macro(domestic_macro):
    with pytest.raises(Exception) as caught:
        runner_mod._require_supported_exchange(domestic_macro, "9")
    assert "실행기" in str(caught.value)


def test_new_runner_takes_a_domestic_macro(domestic_macro):
    runner_mod._require_supported_exchange(domestic_macro, "10")


def test_binance_macro_is_unaffected_by_version(binance_macro):
    runner_mod._require_supported_exchange(binance_macro, "9")
    runner_mod._require_supported_exchange(binance_macro, "10")
```

신호 시세 시험 (`backend/tests/test_runner_engine_exchange.py`):

```python
import asyncio
import json
from unittest.mock import Mock

from app import runner_engine


def _live(symbol, exchange):
    driver = Mock()
    driver.symbol = symbol
    driver.tick.return_value = None
    driver.state.return_value = {}
    live = runner_engine._Live(session_id=1, driver=driver, exchange=exchange)
    live.unsaved = []
    return live


def _run(live, monkeypatch):
    seen = {}

    def fake_price(symbol, ttl=2.0, *, exchange="binance"):
        seen["symbol"], seen["exchange"] = symbol, exchange
        return 100_000_000.0

    monkeypatch.setattr(runner_engine, "get_ticker_price_cached", fake_price)
    asyncio.run(runner_engine._tick_once(live))
    return seen


def test_signal_price_is_fetched_from_the_macro_exchange(monkeypatch):
    seen = _run(_live("KRW-BTC", "upbit"), monkeypatch)
    assert seen == {"symbol": "KRW-BTC", "exchange": "upbit"}


def test_binance_sessions_still_ask_binance(monkeypatch):
    seen = _run(_live("BTCUSDT", "binance"), monkeypatch)
    assert seen == {"symbol": "BTCUSDT", "exchange": "binance"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && python -m pytest tests/test_runner_domestic.py -v`
Expected: FAIL — `AttributeError: module 'app.runner' has no attribute 'DOMESTIC_MIN_VERSION'`

- [ ] **Step 3: 구현**

`backend/app/runner.py`:

```python
DOMESTIC_MIN_VERSION = os.environ.get("RUNNER_DOMESTIC_MIN_VERSION", "10").strip() or "10"
DOMESTIC_REQUIRED_DETAIL = "국내 거래소 매크로는 실행기 v10 이상이 필요해요. 실행기를 업데이트해 주세요."


def supports_domestic(version: str) -> bool:
    """실행기가 업비트·빗썸 주문을 낼 줄 아는가. 지표형 신호(v8)와는 다른 능력이라 따로 센다."""
    v = (version or "").strip()
    return v.isascii() and v.isdigit() and len(v) <= 6 and int(v) >= int(DOMESTIC_MIN_VERSION)
```

`_require_binance_macro(macro)` 를 `_require_supported_exchange(macro, runner_version)` 로 바꾼다 — 국내 매크로이고 `supports_domestic` 이 거짓이면 426 과 `DOMESTIC_REQUIRED_DETAIL`. 호출 세 곳(세션 시작 · 티켓 발급 · 티켓 청구)에 실행기 버전을 넘긴다. 티켓 **발급** 시점에는 실행기 버전을 모르므로 그 자리에서는 거래소를 막지 않고 **청구 시점**에 판단한다.

`claim` 의 방어적 검사(`payload.get("exchange") != "binance" or symbol.startswith("KRW-")`)를 같은 기준으로 바꾼다.

`backend/app/runner_engine.py` 의 `_Live` 는 `__slots__` 라 거래소를 담을 자리를 먼저 만들어야 한다.

```python
class _Live:
    __slots__ = ("session_id", "driver", "task", "subs", "stop_flag",
                 "last_checkpoint", "unsaved", "exchange")

    def __init__(self, session_id: int, driver: StrategyDriver, exchange: str = "binance") -> None:
        ...
        self.exchange = exchange
```

`_Live` 를 만드는 자리(`start_driver`)에서 매크로의 거래소를 넘기고, `_tick_once` 의 시세 호출이 그것을 쓴다:

```python
        price = await asyncio.to_thread(
            get_ticker_price_cached, live.driver.symbol, exchange=live.exchange)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && python -m pytest tests/test_runner_domestic.py tests/test_runner_engine_exchange.py -v`
Expected: PASS

- [ ] **Step 5: 백엔드 전체 시험**

Run: `cd backend && python -m pytest -q`
Expected: 기존 실패 외에 새 실패 없음

- [ ] **Step 6: 커밋**

```bash
git add backend/app/runner.py backend/app/runner_engine.py backend/tests/test_runner_domestic.py backend/tests/test_runner_engine_exchange.py
git commit -m "feat(server): 국내 매크로 실행을 실행기 버전으로 연다"
```

---

### Task 10: 웹 문구

**Files:**
- Modify: `frontend/src/components/PaperPanel.jsx`
- Modify: `frontend/src/components/HeroGuideScreens.jsx`
- Test: `frontend/tests/domesticRunner.test.js`

**Interfaces:**
- Consumes: 없음
- Produces: 없음

- [ ] **Step 1: 실패하는 시험을 쓴다**

`frontend/tests/domesticRunner.test.js`:

```javascript
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const paper = readFileSync(new URL("../src/components/PaperPanel.jsx", import.meta.url), "utf8");
const hero = readFileSync(new URL("../src/components/HeroGuideScreens.jsx", import.meta.url), "utf8");

test("국내 거래소 빠른 실행을 막지 않는다", () => {
  assert.doesNotMatch(paper, /국내 거래소 실행기 직접 연결은 아직 지원하지 않아요/);
  assert.doesNotMatch(hero, /국내 거래소 실행기 직접 연결은 아직 지원하지 않아요/);
});

test("국내 현물 제약 문구는 남는다", () => {
  assert.match(hero, /숏·선물·레버리지는 사용할 수 없어요|원화 현물 전용/);
});

test("봇 번들은 바이낸스 전용임을 '아직' 이 아니라 범위로 말한다", () => {
  // '아직 지원하지 않아요' 는 곧 된다는 약속이다. 번들은 범위 밖이므로 그렇게 쓰지 않는다.
  assert.match(paper, /매크로 파일[\s\S]{0,120}바이낸스/);
  assert.doesNotMatch(paper, /국내 거래소 실거래 실행기 파일은 아직 지원하지 않아요/);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && node --test --test-isolation=none tests/domesticRunner.test.js`
Expected: FAIL — 세 시험 모두

- [ ] **Step 3: 문구를 고친다**

- `PaperPanel.jsx` 의 빠른 실행 분기에서 `"국내 거래소 실행기 직접 연결은 아직 지원하지 않아요."` 와 그 가드를 걷는다
- `PaperPanel.jsx` 의 매크로 파일 내려받기 문구를 `"매크로 파일(봇)은 바이낸스 전용이에요. 업비트·빗썸은 매크로 실행기로 돌려 주세요."` 로 바꾼다
- `HeroGuideScreens.jsx:196` 에서 `", 국내 거래소 실행기 직접 연결은 아직 지원하지 않아요"` 를 걷고 현물·롱·1배 제약 문구는 남긴다

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && node --test --test-isolation=none tests/domesticRunner.test.js`
Expected: PASS (3 passed)

- [ ] **Step 5: 프론트 전체 시험과 빌드**

Run: `cd frontend && node --test --test-isolation=none tests/*.test.js && npm run build`
Expected: 전부 PASS, 빌드 통과

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/components/PaperPanel.jsx frontend/src/components/HeroGuideScreens.jsx frontend/tests/domesticRunner.test.js
git commit -m "feat(web): 국내 매크로의 실행기 연결을 열고 봇 번들 범위를 정확히 쓴다"
```
