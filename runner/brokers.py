"""거래소 어댑터 — 봇은 돈과 수량으로만 말하고, 거래소 사정은 여기 안에 있다.

수수료 모델이 거래소마다 다르다: 바이낸스 현물은 매수 수수료를 기초자산에서 떼므로
실제 보유 수량이 체결 수량보다 적고, 업비트·빗썸은 원화에서 떼므로 체결 수량이 그대로
보유 수량이다. 이 차이를 봇 본문에서 분기하면 가장 예민한 코드에 거래소 지식이 번진다.
그래서 브로커가 acquired_qty 를 채워서 돌려준다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

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
    raw_status: str = ""     # 거래소가 쓴 낱말 그대로. 사람에게 보고할 때는 접은 status 가 아니라 이걸 쓴다


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

    def rehearse(self, *, notional) -> tuple[bool, str]:
        """바이낸스에는 주문 없이 검증만 하는 호출이 없다. 계정 확인과 주문 규격 읽기가 되는지로 갈음한다.

        통과 못 했을 때 세션을 멈출지는 부르는 쪽이 정한다 — 이 메서드는 결과만 돌려준다.
        """
        try:
            self.check_account()
            if not self.ensure_ready():
                return False, f"{self.symbol} 심볼을 바이낸스에서 찾지 못했어요. 심볼 이름을 확인하세요."
            minimum = self.order_rules().min_notional
        except Exception as exc:
            # -2015 는 '키 · 허용 IP · 권한' 중 무엇인지 거래소가 가르지 않고 한 코드로 돌려준다.
            if getattr(exc, "code", None) in (-2014, -2015):
                return False, f"바이낸스가 키를 받아주지 않았어요({exc}). 키 · 허용 IP · 권한 설정을 확인하세요."
            return False, f"바이낸스 확인에 실패했어요: {exc}"
        if minimum and _positive_number(notional) < minimum:
            return False, f"최소 주문 금액보다 적어요. 바이낸스 최소 주문 금액은 {minimum:g} 인데 이번 주문은 {_positive_number(notional):g} 입니다."
        return True, "바이낸스 계정 확인과 주문 규격 읽기에 성공했습니다. 주문 권한은 첫 주문에서야 드러납니다"

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
                     order_id=order.get("orderId"),
                     raw_status=str(order.get("status", "unknown")))


# --- 국내 거래소 (업비트 · 빗썸 원화 현물) ------------------------------
DOMESTIC_BASES = {"upbit": "https://api.upbit.com", "bithumb": "https://api.bithumb.com"}

# 두 거래소는 읽기 경로(/v1/...)가 같지만 쓰기·인증은 다르다 — 공식 문서를 거래소별로 확인한 값이다.
#  - 업비트: JWT HS512, 주문은 POST /v1/orders, 종류는 ord_type, 클라이언트 주문번호는 identifier(최대 64자)
#  - 빗썸:  JWT HS256 + payload 에 timestamp(ms), 주문은 POST /v2/orders, 종류는 order_type,
#           클라이언트 주문번호는 client_order_id(최대 36자). 조회 GET /v1/order 는 client_order_id 로 찾는다.
_DOMESTIC_SPECS = {
    "upbit": {"alg": "HS512", "timestamp": False, "order_path": "/v1/orders",
              "type_key": "ord_type", "id_key": "identifier", "id_max": 64},
    "bithumb": {"alg": "HS256", "timestamp": True, "order_path": "/v2/orders",
                "type_key": "order_type", "id_key": "client_order_id", "id_max": 36},
}
DOMESTIC_LABELS = {"upbit": "업비트", "bithumb": "빗썸"}
_JWT_HASHES = {"HS512": hashlib.sha512, "HS256": hashlib.sha256}

_DOMESTIC_TIMEOUT = 10  # 초. 이 안에 답이 없어도 주문이 들어갔을 수 있다 — 재주문이 아니라 조회로 확인한다
_VOLUME_DECIMALS = Decimal("0.00000001")  # 국내 수량 소수 한계(8자리). 넘으면 거래소가 매도를 거절한다


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def jwt_token(access_key: str, secret_key: str, query: dict | None = None, *,
              alg: str = "HS512", timestamp: bool = False) -> str:
    """HMAC JWT — PyJWT 를 넣지 않는다. 실행기는 exe 라 의존성 하나가 빌드·서명에 걸린다.

    alg 와 timestamp 는 거래소마다 다르다(업비트 HS512, 빗썸 HS256 + timestamp). 기본값이 업비트다.
    """
    digest = _JWT_HASHES[alg]
    header = _b64(json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode())
    payload = {"access_key": access_key, "nonce": str(uuid.uuid4())}
    if timestamp:
        payload["timestamp"] = int(time.time() * 1000)
    if query:
        # 쿼리 스트링(정렬 없이 보낸 순서, URL 인코딩 없이)의 SHA512. POST 는 본문의 키=값을 같은 방식으로 잇는다.
        qs = "&".join(f"{k}={v}" for k, v in query.items())
        payload["query_hash"] = hashlib.sha512(qs.encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    signature = hmac.new(secret_key.encode(), signing_input, digest).digest()
    return f"{header}.{body}.{_b64(signature)}"


def _plain(number: Decimal) -> str:
    """지수 표기 없이 쓴 십진수 문자열. 본문 값과 query_hash 가 같은 글자여야 서명이 맞는다."""
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _fold_domestic_status(state, executed_volume) -> str:
    """업비트·빗썸 state 를 봇이 아는 상태로 접는다. 답이 없으면 열렸는지조차 모르므로 UNKNOWN.

    cancel 은 체결량으로 가른다. 체결이 있었다면 남은 주문(예: 시장가 매수에서 쓰지 못한 원화)만
    취소된 것이라 주문은 체결된 것이다 — 이걸 CANCELED 로 접으면 정상 체결에 봇이 멈춘다.
    체결이 없으면 말 그대로 취소다. 문서가 정하지 않은 거래소 동작을 사실로 적지 않으려고,
    '체결량이 있는 cancel' 이 실제로 오는지와 무관하게 안전한 쪽으로 접는다.
    """
    word = str(state or "")
    if not word:
        return "UNKNOWN"
    if word == "done":
        return "FILLED"
    if word == "cancel":
        return "FILLED" if _positive_number(executed_volume) > 0 else "CANCELED"
    return "OPEN"


class DomesticApiError(RuntimeError):
    """거래소가 HTTP 오류로 답했다. name 은 거래소가 쓴 오류 낱말(예: insufficient_funds_bid)."""

    def __init__(self, status: int, name: str, message: str):
        super().__init__(f"HTTP {status} {name}: {message}" if name else f"HTTP {status}")
        self.status = status
        self.name = name
        self.message = message  # 모르는 오류를 사람에게 보일 때 거래소 말을 그대로 쓰려고 남긴다


# 허용 IP 를 먼저 말해야 하는 오류들. 두 거래소 모두 호출 IP 를 등록해야 하는데 집 IP 는 바뀐다.
# 그런데 "키가 틀렸다" 로만 알리면 사용자가 키를 다시 발급하며 헛수고를 한다.
# 이름은 거래소마다 다르다 — 업비트 no_authorization_ip, 빗썸 NotAllowIP (각 공식 문서의 오류 표).
_IP_FIRST_ERRORS = frozenset({"no_authorization_ip", "NotAllowIP", "invalid_access_key", "jwt_verification",
                              "invalid_query_payload", "no_authorization_token"})
_EXPIRED_KEY_ERRORS = frozenset({"expired_access_key", "expired_jwt"})
_NO_FUNDS_ERRORS = frozenset({"insufficient_funds_bid", "insufficient_funds_ask"})
_MARKET_ERRORS = frozenset({"notfoundmarket", "invalid_market", "market_offline"})


def _explain_domestic_error(exchange: str, status: int, name: str, message: str) -> str:
    """거래소 오류를 사람이 다음에 할 일이 보이는 문장으로 옮긴다. 모르는 오류는 거래소 말을 그대로 둔다."""
    label = DOMESTIC_LABELS.get(exchange, exchange)
    if name in _IP_FIRST_ERRORS:
        return (f"{label} 가 요청을 받아주지 않았어요({name}). 먼저 이 컴퓨터의 현재 IP 가 {label} 의 "
                f"허용 IP 에 등록돼 있는지 확인하세요 — 집 IP 는 바뀝니다. IP 가 맞다면 "
                f"API 키(액세스 키·시크릿 키)를 다시 복사해 넣으세요.")
    if name == "out_of_scope":
        return f"이 API 키에 주문 권한이 없어요({name}). {label} API 관리에서 주문하기 권한을 켠 키를 쓰세요."
    if name in _EXPIRED_KEY_ERRORS:
        return f"API 키가 만료됐어요({name}). {label} API 관리에서 키를 연장하거나 새로 발급하세요."
    if name.startswith("under_min_total"):
        return f"최소 주문 금액보다 적어요({name}). 주문 금액을 {label} 최소 주문 금액 이상으로 올리세요."
    if name in _NO_FUNDS_ERRORS:
        return f"주문할 원화 잔고가 모자라요({name}). 잔고를 채우거나 주문 금액을 줄이세요."
    if name == "over_krw_funds_bid":
        return f"1회 최대 주문 금액을 넘었어요({name}). 주문 금액을 줄이세요."
    if name in _MARKET_ERRORS:
        return f"이 마켓은 지금 주문할 수 없어요({name}). 마켓 이름이 맞는지, 거래소 점검 중인지 확인하세요."
    detail = f"{name}: {message}" if name and message else (name or message or f"HTTP {status}")
    return f"{label} 가 거절했어요({detail})"


class DomesticBroker:
    """업비트 · 빗썸 원화 현물. BinanceBroker 와 같은 메서드 이름으로 봇이 거래소를 구분하지 않게 한다.

    현물 · 롱 · 1배만 있다. 수수료는 원화에서 빠지므로 체결 수량이 곧 보유 수량이다.
    """

    market = "spot"
    side = "long"

    def __init__(self, access_key, secret_key, *, exchange, symbol, log, session=None):
        if exchange not in _DOMESTIC_SPECS:
            raise ValueError(f"지원하지 않는 국내 거래소입니다: {exchange}")
        self.access_key = access_key
        self.secret_key = secret_key
        self.exchange = exchange
        self.symbol = symbol
        self.log = log
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self._spec = _DOMESTIC_SPECS[exchange]
        self._base = DOMESTIC_BASES[exchange]
        self.base_asset = symbol.split("-", 1)[1] if "-" in symbol else ""
        self.fees = {"bid": 0.0, "ask": 0.0}
        self._rules = None

    # --- 호출 ---------------------------------------------------
    def _auth(self, query):
        token = jwt_token(self.access_key, self.secret_key, query,
                          alg=self._spec["alg"], timestamp=self._spec["timestamp"])
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _raise_for_error(resp):
        if resp.status_code < 400:
            return
        try:
            error = (resp.json() or {}).get("error") or {}
        except Exception:
            error = {}
        raise DomesticApiError(resp.status_code, str(error.get("name", "")), str(error.get("message", "")))

    def _get(self, path, params=None, *, private=True):
        resp = self.session.get(self._base + path, params=params,
                                headers=self._auth(params) if private else None,
                                timeout=_DOMESTIC_TIMEOUT)
        self._raise_for_error(resp)
        return resp.json()

    # --- 준비 ---------------------------------------------------
    def check_account(self) -> None:
        """키가 유효한지 원화 잔고 조회로 확인한다. 실패하면 예외 — 호출자가 키 안내를 붙인다."""
        accounts = self._get("/v1/accounts")
        krw = next((a for a in accounts if a.get("currency") == "KRW"), None)
        self.log(f"연결 성공 · 원화 잔고: {krw['balance'] if krw else '조회 실패'}")

    def ensure_ready(self) -> bool:
        """주문 규격과 수수료율을 읽는다. 마켓이 없으면 거짓, 인증·네트워크 문제는 예외."""
        try:
            self._load_chance()
        except DomesticApiError as exc:
            if exc.status in (400, 404):
                return False
            raise
        return True

    def _load_chance(self):
        chance = self._get("/v1/orders/chance", {"market": self.symbol})
        self.fees = {"bid": _positive_number(chance.get("bid_fee")),
                     "ask": _positive_number(chance.get("ask_fee"))}
        # step 이 0 인 이유: 시장가 매수는 원화 금액으로 내므로 수량 단위가 필요 없다.
        self._rules = OrderRules(step=0.0, min_notional=_positive_number((chance.get("bid") or {}).get("min_total")))

    def order_rules(self) -> OrderRules:
        if self._rules is None:
            self._load_chance()
        return self._rules

    # --- 리허설 -------------------------------------------------
    def rehearse(self, *, notional) -> tuple[bool, str]:
        """돈을 쓰지 않고 키 · 허용 IP · 주문 권한 · 최소 주문 금액이 통하는지 본다. (통과, 사람이 읽을 이유).

        업비트에는 주문을 만들지 않고 검증만 하는 POST /v1/orders/test 가 있다(성공 201,
        돌려준 번호는 조회·취소에 못 쓴다). 빗썸 문서에는 그런 경로가 없다 — 없는 경로를 치면
        404 가 나서 늘 실패로 읽히므로, 빗썸은 이미 쓰는 읽기 호출로 갈음한다.
        통과 못 했을 때 세션을 멈출지는 부르는 쪽이 정한다 — 모의 모드는 키 없이도 돌아야 해서
        여기서 정책을 갖지 않고 결과만 돌려준다.
        """
        try:
            if self.exchange == "upbit":
                return self._rehearse_by_test_order(notional)
            return self._rehearse_by_reads(notional)
        except DomesticApiError as exc:
            return False, _explain_domestic_error(self.exchange, exc.status, exc.name, exc.message)
        except ValueError as exc:
            return False, f"리허설 주문을 만들지 못했어요: {exc}"
        except Exception as exc:
            # 네트워크 끊김 · 시간 초과. 거래소 응답이 아니므로 키 탓으로 읽히지 않게 따로 말한다.
            return False, f"{DOMESTIC_LABELS[self.exchange]} 에 연결하지 못했어요: {exc}"

    def _rehearse_by_test_order(self, notional) -> tuple[bool, str]:
        # 진짜 주문 경로(order_path)를 쓰지 않는다 — 리허설이 주문을 넣으면 안 된다.
        body = self._order_body("BUY", base_qty=None, notional=notional, reduce_only=False,
                                client_id=f"ggp-rehearsal-{uuid.uuid4().hex[:12]}")
        resp = self.session.post(self._base + self._spec["order_path"] + "/test", json=body,
                                 headers=self._auth(body), timeout=_DOMESTIC_TIMEOUT)
        self._raise_for_error(resp)
        if resp.status_code != 201:
            return False, f"업비트 주문 검증이 예상 밖 응답을 줬어요(HTTP {resp.status_code})"
        return True, "업비트 주문 검증 통과 — 키 · 허용 IP · 주문 권한 · 최소 주문 금액이 맞습니다"

    def _rehearse_by_reads(self, notional) -> tuple[bool, str]:
        # 키와 허용 IP 는 비공개 읽기 두 번(잔고 · 주문 가능 정보)이 서명까지 확인해 준다.
        # 주문 권한은 주문을 내기 전에는 확인할 방법이 없어, 통과 문구에서 확인했다고 말하지 않는다.
        self._get("/v1/accounts")
        self._load_chance()
        minimum = self._rules.min_notional
        if minimum and _positive_number(notional) < minimum:
            return False, (f"최소 주문 금액보다 적어요. 빗썸 최소 주문 금액은 {_plain(Decimal(repr(minimum)))}원인데 "
                           f"이번 주문은 {_plain(Decimal(repr(_positive_number(notional))))}원입니다.")
        return True, ("빗썸 키 · 허용 IP · 최소 주문 금액은 확인했습니다. 빗썸에는 주문 검증 경로가 없어 "
                      "주문 권한은 첫 주문에서야 드러납니다")

    # --- 시세 ---------------------------------------------------
    def price(self) -> float:
        rows = self._get("/v1/ticker", {"markets": self.symbol}, private=False)
        value = _positive_number(rows[0].get("trade_price")) if rows else 0.0
        if not value:
            raise RuntimeError(f"{self.symbol} 현재가를 받지 못했습니다")
        return value

    # --- 주문 ---------------------------------------------------
    def _order_body(self, side_word, *, base_qty, notional, reduce_only, client_id) -> dict:
        """시장가 주문 본문. submit 과 리허설이 같은 함수로 만든다 — 리허설이 통과했는데
        실제 주문만 본문 모양 때문에 거절되는 일을 막으려는 것이다."""
        word = str(side_word).upper()
        if word not in ("BUY", "SELL"):
            raise ValueError(f"알 수 없는 주문 방향: {side_word!r}")
        if reduce_only and word == "BUY":
            raise ValueError("국내 현물은 롱만 있어 reduce_only 매수가 없습니다")
        if not client_id or len(client_id) > self._spec["id_max"]:
            raise ValueError(f"client_id 는 1~{self._spec['id_max']}자여야 합니다")
        body = {"market": self.symbol}
        if word == "BUY":
            total = _positive_number(notional)
            if not total:
                raise ValueError("시장가 매수에는 양수 notional(원화 금액)이 필요합니다")
            body.update(side="bid", **{self._spec["type_key"]: "price"}, price=_plain(Decimal(repr(total))))
        else:
            qty = _positive_number(base_qty)
            # 8자리 아래로 내림 — 올리면 가진 것보다 많이 팔려는 주문이 된다.
            volume = Decimal(repr(qty)).quantize(_VOLUME_DECIMALS, rounding=ROUND_DOWN)
            if not volume:
                raise ValueError("시장가 매도에는 양수 base_qty(수량)가 필요합니다")
            body.update(side="ask", **{self._spec["type_key"]: "market"}, volume=_plain(volume))
        body[self._spec["id_key"]] = client_id
        return body

    def submit(self, side_word, *, base_qty=None, notional=None, reduce_only=False,
               closing=None, client_id) -> Order:
        """시장가 주문 하나를 넣고 끝난 상태까지 확인해서 돌려준다.

        매수는 원화 금액(notional)으로, 매도는 수량(base_qty)으로 낸다. 그 쪽 인자가 없으면
        잘못된 주문을 보내는 대신 예외를 낸다 — 반대쪽 인자는 쓰지 않는다.
        closing 은 받기만 한다. 수수료가 원화에서 빠져 acquired_qty 가 늘 executed_qty 이므로
        청산 여부로 달라지는 것이 없고, 방향에서 끌어내는 대체 계산도 두지 않는다.
        """
        body = self._order_body(side_word, base_qty=base_qty, notional=notional,
                                reduce_only=reduce_only, client_id=client_id)

        rejection = ""
        try:
            resp = self.session.post(self._base + self._spec["order_path"], json=body,
                                     headers=self._auth(body), timeout=_DOMESTIC_TIMEOUT)
            if 400 <= resp.status_code < 500:
                # 거래소가 오류 이름을 달아 돌려준 4xx 만 '받지 않았다' 로 본다. 이름이 없는 4xx 는
                # 중간 장비(게이트웨이 · WAF)가 거래소가 주문을 받은 뒤에 낸 것일 수 있어, 타임아웃이나
                # 5xx 와 같이 아래에서 끝까지 조회로 확인한다.
                try:
                    rejection = str(((resp.json() or {}).get("error") or {}).get("name") or "")
                except Exception:
                    pass
        except Exception:
            # 타임아웃은 거절의 증거가 아니다. 같은 client_id 로 조회할 뿐, 두 번째 시장가 주문은 없다.
            pass

        order = {}
        for attempt in range(1 if rejection else MAX_RETRIES):
            try:
                order = self._get("/v1/order", {self._spec["id_key"]: client_id})
            except Exception:
                pass  # 직전 응답에서 확인된 부분 체결을 유지한다
            if _fold_domestic_status(order.get("state"), order.get("executed_volume")) in TERMINAL_STATUSES:
                break
            if attempt + 1 < MAX_RETRIES and not rejection:
                time.sleep(0.25 * (attempt + 1))
        if rejection and not order:
            return Order(status="REJECTED", executed_qty=0.0, avg_price=0.0, acquired_qty=0.0,
                         fees_known=True, order_id=None, raw_status=rejection)
        return self._normalize(order)

    def _normalize(self, order) -> Order:
        """거래소 응답에서 체결 수량 · 평균가를 뽑는다. 수수료는 원화에서 빠지므로 보유 수량은 체결 수량이다."""
        executed = _positive_number(order.get("executed_volume"))
        funds = volume = 0.0
        for trade in order.get("trades") or []:
            qty = _positive_number(trade.get("volume"))
            volume += qty
            funds += _positive_number(trade.get("funds")) or _positive_number(trade.get("price")) * qty
        average = funds / volume if volume else 0.0
        # 체결 내역이 체결 수량을 다 덮지 못하면 평균가를 믿을 수 없다 — 봇이 포지션을 불확실로 본다.
        fees_known = math.isclose(volume, executed, rel_tol=1e-9, abs_tol=1e-12)
        return Order(status=_fold_domestic_status(order.get("state"), executed), executed_qty=executed,
                     avg_price=average, acquired_qty=executed, fees_known=fees_known,
                     order_id=order.get("uuid") or order.get("order_id"),
                     raw_status=str(order.get("state") or "unknown"))

    # --- 선물 전용: 국내 현물에는 없다 ---------------------------
    def set_leverage(self, *_args, **_kwargs):
        raise RuntimeError("국내 현물에는 선물 설정이 없습니다")

    def set_margin_type(self, *_args, **_kwargs):
        raise RuntimeError("국내 현물에는 선물 설정이 없습니다")
