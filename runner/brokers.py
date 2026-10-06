"""거래소 어댑터 — 봇은 돈과 수량으로만 말하고, 거래소 사정은 여기 안에 있다.

수수료 모델이 거래소마다 다르다: 바이낸스 현물은 매수 수수료를 기초자산에서 떼므로
실제 보유 수량이 체결 수량보다 적고, 업비트·빗썸은 원화에서 떼므로 체결 수량이 그대로
보유 수량이다. 이 차이를 봇 본문에서 분기하면 가장 예민한 코드에 거래소 지식이 번진다.
그래서 브로커가 acquired_qty 를 채워서 돌려준다.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

# 더 기다려도 바뀌지 않는 상태. OPEN 은 아직 끝나지 않은 주문이다.
TERMINAL_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED"})

# 바이낸스가 주문 조회에 돌려주는 '더 조회해도 같은' 상태. 위 집합보다 넓다 —
# EXPIRED·EXPIRED_IN_MATCH 는 시장가 주문이 체결된 만큼만 남기고 끝난 경우라서 다시 물어도 같다.
_BINANCE_SETTLED = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"})

# 주문 하나의 끝 상태를 확인하려고 조회하는 횟수. 실행기의 MAX_RETRIES 와 같은 수를 유지한다.
MAX_RETRIES = 3


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
    order_id: object = None  # 사용자 로그에 남기는 거래소 주문 번호. 응답이 없으면 None


@dataclass(frozen=True)
class OrderRules:
    """주문을 보내기 전에 알아야 하는 거래소 규격."""

    step: float          # 수량 최소 단위 (국내 시장가 매수는 금액으로 내므로 0)
    min_notional: float  # 1 회 최소 주문 금액 (호가 통화 기준)


def _positive_number(value):
    try:
        number = float(value or 0)
        return number if math.isfinite(number) and number > 0 else 0.0
    except (ValueError, TypeError):
        return 0.0


def _parse_filters(info: dict) -> tuple[float, float]:
    step, min_notional = 0.0, 0.0
    for f in (info or {}).get("filters", []):
        if f["filterType"] in ("LOT_SIZE", "MARKET_LOT_SIZE") and not step:
            step = float(f["stepSize"])
        elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            min_notional = float(f.get("minNotional", f.get("notional", 0)) or 0)
    return step, min_notional


def _fold_status(raw) -> str:
    """거래소 상태를 봇이 아는 네 가지로 접는다.

    EXPIRED·EXPIRED_IN_MATCH 는 '체결된 만큼으로 끝' 이므로 CANCELED 와 같게 다룬다.
    응답을 한 번도 받지 못한 경우는 열렸는지조차 모르므로 OPEN 이 아니라 UNKNOWN 이다.
    """
    status = str(raw or "")
    if not status:
        return "UNKNOWN"
    if status in ("EXPIRED", "EXPIRED_IN_MATCH"):
        return "CANCELED"
    if status in TERMINAL_STATUSES:
        return status
    return "OPEN"


class BinanceBroker:
    """지금 봇이 하던 바이낸스 호출을 그대로 감싼다. 로직 이동이지 변경이 아니다."""

    def __init__(self, raw, *, market, symbol, side, testnet, log, leverage=1):
        self.raw = raw
        self.market = market
        self.symbol = symbol
        self.side = side
        self.testnet = testnet
        self.log = log
        self.leverage = leverage
        self.base_asset = ""
        self._rules = OrderRules(step=0.0, min_notional=0.0)

    # --- 준비 ---------------------------------------------------
    def check_account(self) -> None:
        """계정이 이 시장에서 쓸 수 있는 키인지 확인하고 잔고를 알린다.

        ensure_ready() 와 합치지 않는다 — 여기서 터진 예외에는 호출자가 '키를 어디서
        발급했는지' 안내를 붙이고, 심볼 준비 단계의 실패와는 사용자에게 다르게 보고한다.
        """
        if self.market == "futures":
            bal = self._futures_usdt_balance()
            self.log(f"연결 성공 · 선물 USDT 증거금: {bal if bal is not None else '조회 실패'}")
        else:
            acc = self.raw.get_account()
            usdt = next((b for b in acc["balances"] if b["asset"] == "USDT"), None)
            self.log(f"연결 성공 · 현물 USDT 잔고: {usdt['free'] if usdt else '조회 실패'}")

    def ensure_ready(self) -> bool:
        """심볼 규격을 읽고 선물이면 마진·레버리지를 맞춘다. 심볼이 없으면 거짓."""
        if self.market == "futures":
            info = self._futures_symbol_info()
            if not info:
                return False
            self._rules = OrderRules(*_parse_filters(info))
            try:
                self.raw.futures_change_margin_type(symbol=self.symbol, marginType="ISOLATED")
            except Exception:
                pass  # 이미 ISOLATED 면 거래소가 거절한다 — 원하는 상태라 넘어간다
            try:
                self.raw.futures_change_leverage(symbol=self.symbol, leverage=self.leverage)
            except Exception as exc:
                self.log(f"⚠ 레버리지 {self.leverage}배 설정 실패({exc}). 계정 기본값으로 진행.")
        else:
            info = self.raw.get_symbol_info(self.symbol)
            if not info:
                return False
            self.base_asset = info.get("baseAsset", "")
            self._rules = OrderRules(*_parse_filters(info))
        return True

    def order_rules(self) -> OrderRules:
        return self._rules

    def _futures_symbol_info(self):
        try:
            for s in self.raw.futures_exchange_info().get("symbols", []):
                if s.get("symbol") == self.symbol:
                    return s
        except Exception as exc:
            self.log(f"선물 심볼정보 조회 실패: {exc}")
        return None

    def _futures_usdt_balance(self):
        try:
            for b in self.raw.futures_account_balance():
                if b.get("asset") == "USDT":
                    return float(b.get("balance", 0))
        except Exception:
            return None
        return 0.0

    # --- 시세 ---------------------------------------------------
    def price(self) -> float:
        if self.market == "futures":
            return float(self.raw.futures_symbol_ticker(symbol=self.symbol)["price"])
        return float(self.raw.get_symbol_ticker(symbol=self.symbol)["price"])

    # --- 주문 ---------------------------------------------------
    def submit(self, side_word, *, base_qty=None, notional=None, reduce_only=False,
               closing=None, client_id) -> Order:
        """시장가 주문 하나를 넣고 끝난 상태까지 확인해서 돌려준다.

        closing 은 이 주문이 보유를 줄이는 주문인지다. 현물은 매수 수수료만 기초자산에서
        빠지므로 수수료 계산이 이 값에 걸려 있다. 봇이 넘기지 않으면 주문 방향으로 판단한다.
        """
        if closing is None:
            closing = reduce_only or side_word != ("BUY" if self.side == "long" else "SELL")
        qty = base_qty  # 바이낸스는 수량으로 주문한다. notional 은 국내 시장가 매수용 — 여기서는 안 쓴다
        kwargs = dict(symbol=self.symbol, side=side_word, type="MARKET",
                      quantity=qty, newClientOrderId=client_id,
                      newOrderRespType="RESULT" if self.market == "futures" else "FULL")
        if reduce_only:
            kwargs["reduceOnly"] = "true"
        try:
            create = self.raw.futures_create_order if self.market == "futures" else self.raw.create_order
            order = create(**kwargs)
        except Exception:
            # 타임아웃은 거절의 증거가 아니다. 같은 client_id 로 조회할 뿐, 두 번째 시장가 주문은 없다.
            order = {}
        for attempt in range(MAX_RETRIES):
            if order.get("status") in _BINANCE_SETTLED:
                break
            try:
                query = self.raw.futures_get_order if self.market == "futures" else self.raw.get_order
                order = query(symbol=self.symbol, origClientOrderId=client_id)
            except Exception:
                pass  # 직전 응답에서 확인된 부분 체결을 유지한다
            if order.get("status") not in _BINANCE_SETTLED and attempt + 1 < MAX_RETRIES:
                time.sleep(0.25 * (attempt + 1))
        return self._normalize(order, closing=closing)

    def _normalize(self, order, *, closing) -> Order:
        """거래소 응답에서 체결 수량 · 평균가 · 실제 보유 수량을 뽑는다. 보정 순서를 바꾸지 않는다."""
        executed = _positive_number(order.get("executedQty"))
        average = _positive_number(order.get("avgPrice"))
        if not average and executed:
            average = _positive_number(order.get("cummulativeQuoteQty") or order.get("cumQuote")) / executed
        if not average and executed:
            fills = order.get("fills") or []
            quote = sum(_positive_number(fill.get("price")) * _positive_number(fill.get("qty")) for fill in fills)
            average = quote / executed

        acquired = executed
        fees_known = True
        if self.market == "spot" and not closing and executed:
            # ensure_ready() 전이라도 심볼에서 기초자산을 끌어낸다 — 이게 없으면 수수료가 조용히 안 빠진다.
            base_asset = self.base_asset or (self.symbol[:-4] if self.symbol.endswith(("USDT", "USDC")) else "")
            fills = order.get("fills") or []
            if not fills and order.get("orderId") is not None:
                try:
                    trades = self.raw.get_my_trades(symbol=self.symbol, orderId=order["orderId"], limit=1000)
                    fills = [trade for trade in trades if str(trade.get("orderId")) == str(order["orderId"])]
                except Exception:
                    fills = []
            fees_known = bool(base_asset and fills) and all("commissionAsset" in fill and "commission" in fill for fill in fills)
            fees_known = fees_known and math.isclose(sum(_positive_number(fill.get("qty")) for fill in fills), executed, rel_tol=1e-9, abs_tol=1e-12)
            if fees_known:
                base_fee = sum(_positive_number(fill.get("commission")) for fill in fills if fill["commissionAsset"] == base_asset)
                acquired = max(0.0, executed - base_fee)
        return Order(status=_fold_status(order.get("status")), executed_qty=executed,
                     avg_price=average, acquired_qty=acquired, fees_known=fees_known,
                     order_id=order.get("orderId"))
