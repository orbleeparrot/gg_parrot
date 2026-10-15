"""Local connection preparation, independent of Tk and server/member sessions."""
from dataclasses import dataclass, field
import copy
import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import time

PUBLIC_IPV4_URL = "https://api4.ipify.org?format=json"
PUBLIC_IP_MAX_AGE = 120.0
PUBLIC_IP_CAVEAT = (
    "이 주소는 ipify 조회 당시의 공인 IPv4예요. 고정 IP 여부와 거래소 허용 IP 등록 여부는 확인하지 않습니다. "
    "VPN·프록시·분할 라우팅을 사용하면 조회 서비스와 거래소 API의 외부 주소가 다를 수 있어요. "
    "주소가 바뀌었거나 네트워크를 바꿨으면 다시 확인·등록하고 연결 검사를 진행하세요."
)
API_MANAGEMENT_PAGES = {
    "upbit": "https://www.upbit.com/mypage/open_api_management",
    "bithumb": "https://www.bithumb.com/react/api-support/management-api",
    # 바이낸스는 실행 환경마다 키를 만드는 곳이 다르다(binance_env). 테스트넷은 첫 화면이 곧 API Keys 목록이다.
    "binance:testnet": "https://testnet.binance.vision/",
    "binance:demo": "https://demo.binance.com/en/my/settings/api-management",
    "binance:live": "https://www.binance.com/en/my/settings/api-management",
}
# 연결 도우미의 모든 단계 맨 위에 두는 '거래소 열기' 바로가기. 정적 허용 목록 — 다른 주소는 열지 않는다.
EXCHANGE_HOME_PAGES = {
    "upbit": "https://upbit.com/",
    "bithumb": "https://www.bithumb.com/",
    "binance:testnet": "https://testnet.binance.vision/",
    "binance:demo": "https://demo.binance.com/en/futures/BTCUSDT",
    "binance:live": "https://www.binance.com/en",
}
# 바이낸스 실행 환경 — 키는 환경끼리 통하지 않는다(2026-10-15 조사).
#   testnet: 현물 테스트넷 testnet.binance.vision(GitHub 로그인, 바이낸스 계정과 무관)
#   demo:    선물 데모 demo.binance.com(바이낸스 계정 로그인 · demo-fapi). 옛 testnet.binancefuture.com 웹은 여기로 넘어간다.
#   live:    binance.com 실거래 — IP 제한 없는 키는 'Enable Reading' 만 켤 수 있어 허용 IP 가 꼭 필요하다(2023-01-30 규칙).
BINANCE_ENVS = ("testnet", "demo", "live")


def binance_env(mode: str, market: str) -> str:
    """실행 모드 · 시장으로 바이낸스 키를 만들 곳을 고른다. 실전이 아니면 연습 환경이다."""
    if mode == "live":
        return "live"
    return "demo" if market == "futures" else "testnet"


def _page_key(exchange: str, env: str) -> str:
    return f"{exchange}:{env}" if exchange == "binance" else exchange
PREFLIGHT_MAX_AGE = 120.0
_FINGERPRINT_SALT = secrets.token_bytes(32)


class PublicIPError(ValueError):
    pass


def api_management_url(exchange: str, env: str = "") -> str:
    try:
        return API_MANAGEMENT_PAGES[_page_key(exchange, env)]
    except (KeyError, TypeError):
        raise ValueError("이 거래소의 공식 API 관리 페이지를 확인하지 못했어요.") from None


def exchange_home_url(exchange: str, env: str = "") -> str:
    try:
        return EXCHANGE_HOME_PAGES[_page_key(exchange, env)]
    except (KeyError, TypeError):
        raise ValueError("이 거래소의 공식 홈페이지를 확인하지 못했어요.") from None


# --- 연결 검사 결과를 세 줄로 -----------------------------------------------
# 도우미는 '키 인증 · 허용 IP · 주문 권한' 을 따로 보여 준다. 거래소는 한 번의 검사에서 막힌 곳 하나만
# 오류 낱말로 알려 주므로, 그 낱말로 어디까지 통과했는지를 거꾸로 읽는다(모르는 낱말은 지어내지 않는다).
CHECK_OK, CHECK_BAD, CHECK_WAIT, CHECK_NA = "ok", "bad", "wait", "na"
# 키는 받아들였지만 호출한 IP 가 허용 목록에 없다 — 업비트 no_authorization_ip, 빗썸 NotAllowIP.
_IP_REJECTED = frozenset({"no_authorization_ip", "NotAllowIP"})
# 키 자체를 받아들이지 않았다(틀린 키 · 시크릿 짝 불일치 · 만료).
_KEY_REJECTED = frozenset({"invalid_access_key", "jwt_verification", "no_authorization_token", "expired_access_key"})
# 키와 IP 는 통과했고 주문하기 권한이 없다.
_SCOPE_REJECTED = frozenset({"out_of_scope"})
# 거래소가 주문 내용(잔고 · 최소 금액)까지 따져 본 오류 — 키 · IP · 주문 권한은 이미 통과했다는 뜻이다.
_ORDER_EVALUATED = frozenset({"insufficient_funds_bid", "insufficient_funds_ask", "insufficient_funds",
                              "over_krw_funds_bid", "under_min_total"})


# 바이낸스(BinanceBroker.last_error_name) — 오류 코드는 '키 · IP · 권한' 을 한 코드(-2015)로 뭉쳐 돌려주므로,
# 실거래는 조회가 통과한 뒤 키 권한 조회(apiRestrictions)로 IP 제한 · 주문 권한을 따로 읽는다(테스트넷 · 데모에는 그 조회가 없다).
_BINANCE_VIEWS = {
    # 키 형식 · 서명(시크릿 짝) · 없는 키 — 키를 다시 복사하면 된다.
    "binance_key": ({"auth": "bad", "ip": "wait", "order": "wait"}, "auth", "키 확인 필요", {}),
    # 테스트넷 · 데모의 -2015 — IP 제한이 없는 곳이라 키(또는 다른 환경의 키)다.
    "binance_access_test": ({"auth": "bad", "ip": "na", "order": "wait"}, "auth", "키 확인 필요", {"ip": "필요 없음"}),
    # 실거래 -2015 — 방금 복사한 키라면 대개 허용 IP 다. 거래소가 둘을 가르지 않아 IP 쪽으로 안내하고 키도 다시 보게 한다.
    "binance_access": ({"auth": "wait", "ip": "bad", "order": "wait"}, "ip", "허용 IP 확인 필요", {"ip": "IP 또는 키 불일치"}),
    # 주문 권한이 꺼져 있고 IP 제한도 없다 — IP 를 걸어야 주문 권한을 켤 수 있다.
    "binance_ip_unrestricted": ({"auth": "ok", "ip": "bad", "order": "bad"}, "ip", "허용 IP 등록 필요",
                                {"ip": "IP 제한 없음", "order": "주문 권한 꺼짐"}),
    "binance_no_trade": ({"auth": "ok", "ip": "ok", "order": "bad"}, "order", "주문 권한 확인 필요",
                         {"order": "Spot & Margin & Stock Trading 꺼짐"}),
    "binance_no_futures": ({"auth": "ok", "ip": "ok", "order": "bad"}, "order", "주문 권한 확인 필요",
                           {"order": "Enable Futures 꺼짐"}),
}


def check_view(exchange: str, ok: bool, error_name: str = "") -> dict:
    """검사 결과 하나를 도우미의 세 줄(키 인증 · 허용 IP · 주문 권한)과 제목으로 옮긴다.

    connected 는 '키 · IP · 권한이 통하는가' 다 — 잔고 부족처럼 주문 내용 때문에 실패한 검사도 연결은 된 것이다.
    빗썸 · 바이낸스는 주문 없이 주문 권한을 확인할 길이 없어 성공해도 그 줄은 '확인 불가'(na)로 둔다.
    """
    name = str(error_name or "")
    order_ok = CHECK_OK if exchange == "upbit" else CHECK_NA
    if exchange == "binance":
        if ok and name == "binance_verified":
            # 실거래: 키 권한 조회로 IP 제한 · 주문 권한까지 확인했다.
            return {"auth": CHECK_OK, "ip": CHECK_OK, "order": CHECK_OK, "connected": True, "problem": "",
                    "headline": "연결 완료", "notes": {}}
        if ok and name == "binance_verified_open":
            return {"auth": CHECK_OK, "ip": CHECK_NA, "order": CHECK_OK, "connected": True, "problem": "",
                    "headline": "연결 완료", "notes": {"ip": "IP 제한 없음"}}
        if ok and name == "binance_test_ok":
            return {"auth": CHECK_OK, "ip": CHECK_NA, "order": CHECK_NA, "connected": True, "problem": "",
                    "headline": "연결 완료", "notes": {"ip": "필요 없음"}}
        if name in _BINANCE_VIEWS:
            rows, problem, headline, notes = _BINANCE_VIEWS[name]
            return {**rows, "connected": False, "problem": problem, "headline": headline, "notes": dict(notes)}
    if ok or name in _ORDER_EVALUATED or name.startswith("under_min_total"):
        rows = {"auth": CHECK_OK, "ip": CHECK_OK, "order": order_ok}
        return {**rows, "connected": True, "problem": "" if ok else "order_size", "headline": "연결 완료"}
    if name in _IP_REJECTED:
        return {"auth": CHECK_OK, "ip": CHECK_BAD, "order": CHECK_WAIT, "connected": False,
                "problem": "ip", "headline": "허용 IP 확인 필요"}
    if name in _KEY_REJECTED:
        return {"auth": CHECK_BAD, "ip": CHECK_WAIT, "order": CHECK_WAIT, "connected": False,
                "problem": "auth", "headline": "키 확인 필요"}
    if name in _SCOPE_REJECTED:
        return {"auth": CHECK_OK, "ip": CHECK_OK, "order": CHECK_BAD, "connected": False,
                "problem": "order", "headline": "주문 권한 확인 필요"}
    return {"auth": CHECK_WAIT, "ip": CHECK_WAIT, "order": CHECK_WAIT, "connected": False,
            "problem": "network" if name == "network" else "other", "headline": "연결 확인 필요"}


# 거래소 발급 창의 [복사] 로 클립보드에 들어온 값이 키처럼 생겼는가. 공백 · 점 · 콜론이 없고 길다 —
# IP 주소 · 주소창 URL · 문장은 걸리지 않는다. 키 한 쌍이 같은 모양이라 어느 칸인지는 들어온 순서로 정한다.
_KEY_TEXT = re.compile(r"[A-Za-z0-9+/=_-]{20,200}")


def clipboard_key(text) -> str:
    value = str(text or "").strip()
    return value if _KEY_TEXT.fullmatch(value) else ""


@dataclass(frozen=True)
class PublicIPResult:
    ok: bool
    address: str
    reason: str
    checked_at: float = field(default_factory=time.monotonic)
    observed_at: float = field(default_factory=time.time)
    changed: bool = False

    def fresh(self, *, now=None) -> bool:
        elapsed = (time.monotonic() if now is None else now) - self.checked_at
        return 0 <= elapsed < PUBLIC_IP_MAX_AGE


class PublicIPCache:
    """Main-thread-owned, PC-scoped short cache and singleflight request generation."""
    def __init__(self):
        self.result = None
        self.generation = 0
        self._sequence = 0
        self._active = None
        self._previous_address = ""

    def seed_previous(self, history) -> bool:
        """Restore an observation only, never a current or registered address."""
        observation = valid_ip_observation(history)
        if observation is None:
            return False
        self._previous_address = observation["public_ipv4"]
        return True

    @property
    def busy(self):
        return self._active is not None

    def begin(self, *, force=False, now=None):
        if self.busy:
            return "shared", self._active
        if not force and self.result is not None and self.result.fresh(now=now):
            return "cached", None
        self.result = None
        self._sequence += 1
        self._active = (self.generation, self._sequence)
        return "request", self._active

    def finish(self, token, ok, value, *, now=None):
        if token != self._active:
            return None
        self._active = None
        if token[0] != self.generation:
            return None
        try:
            address = validate_public_ipv4(value) if ok else ""
        except PublicIPError:
            ok, address = False, ""
        changed = bool(ok and self._previous_address and address != self._previous_address)
        if ok:
            self._previous_address = address
        # Failure strings from transports/proxies are never cached or rendered.
        reason = "조회 당시 공인 IPv4를 확인했어요." if ok else "공인 IPv4 확인에 실패했어요. VPN·인터넷 연결을 확인한 뒤 다시 눌러 주세요."
        self.result = PublicIPResult(bool(ok), address, reason, checked_at=time.monotonic() if now is None else now, changed=changed)
        return self.result

    def invalidate(self):
        self.generation += 1
        self.result = None
        # Keep singleflight locked until the old request returns. Its generation
        # will be rejected by finish; no parallel external requests are spawned.


def public_ip_status(result, *, now=None) -> str:
    if result is None:
        return "공인 IPv4 확인 전 — 허용 IP 등록은 거래소 공식 페이지에서 직접 합니다."
    if not result.fresh(now=now):
        return "공인 IPv4 조회 결과가 만료됐어요. 다시 확인한 현재 주소만 복사할 수 있습니다."
    if not result.ok:
        return result.reason
    observed = time.strftime("%H:%M:%S", time.localtime(result.observed_at))
    changed = " · 이전 조회 주소와 달라요. 거래소 허용 IP를 확인하고 연결 검사를 다시 진행하세요." if result.changed else ""
    return f"{result.address} · {observed} 조회 당시 주소 (120초 유효). 고정 여부·허용 등록은 확인하지 않았어요.{changed}"


def validate_public_ipv4(value) -> str:
    try:
        address = ipaddress.ip_address(str(value).strip())
        if address.version != 4 or not address.is_global or address.is_multicast:
            raise ValueError
        return str(address)
    except ValueError:
        raise PublicIPError("공인 IPv4를 확인하지 못했어요. 네트워크를 확인한 뒤 다시 눌러 주세요.") from None


def detect_public_ipv4(http) -> str:
    """One explicit, unauthenticated request; no fallback/retry/key-bearing session."""
    try:
        response = http.get(PUBLIC_IPV4_URL, timeout=(3, 5), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError
        return validate_public_ipv4(response.json().get("ip"))
    except Exception:
        # Never echo network errors: proxy URLs may contain passwords.
        raise PublicIPError("공인 IPv4 확인에 실패했어요. VPN·인터넷 연결을 확인하고 다시 눌러 주세요.") from None


def valid_ip_observation(history):
    """Untrusted local metadata cannot smuggle private IPs or non-finite times."""
    if not isinstance(history, dict):
        return None
    observed_at = history.get("observed_at")
    if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)):
        return None
    if not 0 < observed_at <= time.time() + 300 or not math.isfinite(observed_at):
        return None
    try:
        address = validate_public_ipv4(history.get("public_ipv4"))
    except PublicIPError:
        return None
    return {"public_ipv4": address, "observed_at": observed_at}


def with_successful_ip_history(remembered, macro, mode, current_credentials, ip_result, preflight):
    """Prepare encrypted metadata without saving changed/unsaved keys implicitly.

    The IP is an ipify observation at a successful check, not the exchange's
    registered IP or proof that both requests followed the same route.
    """
    if mode != "live" or not remembered or not ip_result or not preflight:
        return None
    exchange = str(macro.get("exchange") or "binance").lower()
    if exchange not in API_MANAGEMENT_PAGES:
        return None
    saved_pair = (remembered.get("exchanges") or {}).get(exchange)
    current_pair = (current_credentials.get("exchanges") or {}).get(exchange)
    if not saved_pair or saved_pair != current_pair or not all(saved_pair.get(key) for key in ("api_key", "api_secret")):
        return None
    if not ip_result.ok or not ip_result.fresh() or not preflight.matches(preflight_fingerprint(macro, mode, current_credentials)):
        return None
    observation = valid_ip_observation({"public_ipv4": ip_result.address, "observed_at": ip_result.observed_at})
    if observation is None:
        return None
    previous_history = remembered.get("connection_history") or {}
    if previous_history.get(exchange) == observation:
        return None
    updated = copy.deepcopy(remembered)
    updated.setdefault("connection_history", {})[exchange] = observation
    return updated


def preflight_fingerprint(macro: dict, mode: str, credentials: dict) -> str:
    exchange = str(macro.get("exchange") or "binance").lower()
    selected_pair = (credentials.get("exchanges") or {}).get(exchange) or {}
    payload = json.dumps([macro, mode, selected_pair], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hmac.new(_FINGERPRINT_SALT, payload.encode(), hashlib.sha256).hexdigest()


def safe_preflight_reason(reason: str, credentials: dict) -> str:
    """Mask any supplied credentials before a reason can be rendered or logged."""
    text = str(reason)
    # Broker libraries sometimes fold network exceptions into their returned reason.
    # Never render their signed URLs, proxy credentials or authentication headers.
    if re.search(r"https?://|authorization|bearer\s|signature\s*[=:]|proxy|[^\s/:@]+:[^\s/@]+@|eyJ[A-Za-z0-9_-]+\.", text, re.IGNORECASE):
        return "네트워크 또는 인증 응답을 확인하지 못했어요. 인터넷 연결·허용 IP·API 권한을 확인한 뒤 다시 눌러 주세요."
    values = [credentials.get("member_key")]
    for pair in (credentials.get("exchanges") or {}).values():
        values.extend((pair.get("api_key"), pair.get("api_secret")))
    for value in sorted({str(v) for v in values if v}, key=len, reverse=True):
        text = text.replace(value, "[키 숨김]")
    return text[:600]


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reason: str
    fingerprint: str
    checked_at: float = field(default_factory=time.monotonic)
    broker: object = field(default=None, repr=False, compare=False)
    # 거래소가 돌려준 오류 낱말(예: no_authorization_ip). 사람에게 보일 문장은 reason 이고, 이건 check_view 가 읽는다.
    error_name: str = ""

    def matches(self, fingerprint: str, *, now=None) -> bool:
        elapsed = (time.monotonic() if now is None else now) - self.checked_at
        return self.ok and self.fingerprint == fingerprint and 0 <= elapsed < PREFLIGHT_MAX_AGE
