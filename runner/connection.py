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
PREFLIGHT_MAX_AGE = 120.0
_FINGERPRINT_SALT = secrets.token_bytes(32)


class PublicIPError(ValueError):
    pass


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
