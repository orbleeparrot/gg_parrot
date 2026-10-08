"""Local connection preparation, independent of Tk and server/member sessions."""
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import json
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
}
PREFLIGHT_MAX_AGE = 120.0
_FINGERPRINT_SALT = secrets.token_bytes(32)


class PublicIPError(ValueError):
    pass


def api_management_url(exchange: str) -> str:
    try:
        return API_MANAGEMENT_PAGES[exchange]
    except (KeyError, TypeError):
        raise ValueError("이 거래소의 공식 API 관리 페이지를 확인하지 못했어요.") from None


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

    def matches(self, fingerprint: str, *, now=None) -> bool:
        elapsed = (time.monotonic() if now is None else now) - self.checked_at
        return self.ok and self.fingerprint == fingerprint and 0 <= elapsed < PREFLIGHT_MAX_AGE
