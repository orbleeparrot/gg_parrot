"""웹 푸시 보내기 — 브라우저 구독(엔드포인트 · p256dh · auth)으로 알림 한 건을 밀어 넣는다.

표준 세 개만 쓴다: 본문 암호화 RFC 8291(aes128gcm, RFC 8188 한 레코드), 서버 인증 VAPID RFC 8292(ES256 JWT),
전송 RFC 8030(POST + TTL). 라이브러리(pywebpush)는 requests · aiohttp 를 끌어오므로 쓰지 않고, 암호는 이미 쓰는
``cryptography``, 전송은 공용 httpx 클라이언트로 한다. 암호화는 RFC 8291 부록 A 예시로 시험한다.

키: ``VAPID_PRIVATE_KEY`` (P-256 비밀키 32바이트 base64url — ``scripts/gen_vapid_keys.py`` 가 만든다).
없으면 푸시는 꺼진 것으로 보고(``enabled() == False``) 구독도 받지 않는다. 공개키는 비밀키에서 계산한다.
"""
from __future__ import annotations

import base64
import os
import struct
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# 푸시 서비스가 받는 본문 상한은 4096바이트(RFC 8030 §7.2) — 머리(86) · 구분자(1) · 태그(16)를 빼면 평문은 3993.
RECORD_SIZE = 4096
MAX_PLAINTEXT = RECORD_SIZE - 86 - 1 - 16
DEFAULT_SUBJECT = "https://gg-parrot.vercel.app"

# 서버가 POST 를 보낼 곳은 브라우저 회사의 푸시 서비스뿐이다 — 엔드포인트는 브라우저가 주는 값이라
# 아무 주소나 받으면 서버가 내부망으로 요청을 보내는 길(SSRF)이 된다.
_PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
               "web.push.apple.com")
_PUSH_HOST_SUFFIXES = (".notify.windows.com", ".push.apple.com", ".push.services.mozilla.com")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    raw = str(text or "").strip().replace("+", "-").replace("/", "_").rstrip("=")
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def allowed_endpoint(endpoint: str) -> bool:
    try:
        parts = urlsplit(str(endpoint or ""))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in (None, 443):
        return False
    return host in _PUSH_HOSTS or any(host.endswith(suffix) for suffix in _PUSH_HOST_SUFFIXES)


def valid_keys(p256dh: str, auth: str) -> bool:
    """브라우저가 준 공개키(65바이트, 0x04 로 시작하는 P-256 점)와 인증 비밀(16바이트)인지."""
    try:
        public = b64u_decode(p256dh)
        secret = b64u_decode(auth)
        if len(public) != 65 or public[0] != 4 or len(secret) != 16:
            return False
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
        return True
    except (ValueError, TypeError):
        return False


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _public_bytes(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(plaintext: bytes, p256dh: str, auth: str, *, salt: Optional[bytes] = None,
            server_key: Optional[ec.EllipticCurvePrivateKey] = None) -> bytes:
    """RFC 8291 — 받는 브라우저만 풀 수 있게 본문을 한 레코드(aes128gcm)로 싼다. salt · 서버 키는 시험용으로만 넘긴다."""
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError("push payload too large")
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    server_public = _public_bytes(server_key)
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + server_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    # 마지막(유일한) 레코드는 평문 뒤에 구분자 0x02 를 붙인다 — 채움 없음.
    body = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + struct.pack("!I", RECORD_SIZE) + bytes([len(server_public)]) + server_public + body


@dataclass(frozen=True)
class Vapid:
    private_key: ec.EllipticCurvePrivateKey
    public_key: str  # 브라우저 subscribe(applicationServerKey)에 넣는 값 — 65바이트 base64url
    subject: str


def load_private_key(text: str) -> ec.EllipticCurvePrivateKey:
    text = str(text or "").strip()
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
    else:
        key = ec.derive_private_key(int.from_bytes(b64u_decode(text), "big"), ec.SECP256R1())
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("VAPID key must be P-256")
    return key


@lru_cache(maxsize=4)
def _vapid_from(private_text: str, subject: str) -> Optional[Vapid]:
    if not private_text:
        return None
    key = load_private_key(private_text)
    return Vapid(private_key=key, public_key=b64u(_public_bytes(key)), subject=subject)


def vapid() -> Optional[Vapid]:
    """환경변수의 VAPID 키. 없거나 깨졌으면 None(푸시 꺼짐)."""
    try:
        return _vapid_from(os.environ.get("VAPID_PRIVATE_KEY", "").strip(),
                           os.environ.get("VAPID_SUBJECT", "").strip() or DEFAULT_SUBJECT)
    except (ValueError, TypeError):
        return None


def enabled() -> bool:
    return vapid() is not None


def vapid_authorization(endpoint: str, keys: Vapid, *, now: Optional[float] = None) -> str:
    import jwt

    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(now if now is not None else time.time()) + 12 * 3600,
              "sub": keys.subject}
    token = jwt.encode(claims, keys.private_key, algorithm="ES256", headers={"typ": "JWT"})
    return f"vapid t={token}, k={keys.public_key}"


# 보낸 결과 — gone 이면 구독이 끝났으니(404 · 410) 지운다. retry 는 잠깐 막힌 것(429 · 5xx · 연결 실패).
SENT, GONE, FAILED, RETRY = "sent", "gone", "failed", "retry"


def send(subscription: dict, payload: bytes, *, ttl: int = 3600, urgency: str = "normal", topic: str = "",
         client=None, keys: Optional[Vapid] = None) -> str:
    """구독 하나에 보낸다. 네트워크를 기다리므로 DB 연결을 쥔 채 부르지 않는다."""
    keys = keys or vapid()
    endpoint = str(subscription.get("endpoint") or "")
    if keys is None or not allowed_endpoint(endpoint):
        return FAILED
    try:
        body = encrypt(payload, subscription["p256dh"], subscription["auth"])
    except (ValueError, TypeError, KeyError):
        return FAILED
    headers = {
        "Authorization": vapid_authorization(endpoint, keys),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(max(0, int(ttl))),
        "Urgency": urgency,
    }
    if topic:
        headers["Topic"] = topic[:32]  # 아직 전해지지 않은 같은 주제 알림은 새것으로 바뀐다
    if client is None:
        from .http_runtime import get_http_client
        client = get_http_client()
    import httpx

    try:
        response = client.post(endpoint, content=body, headers=headers, timeout=10.0, follow_redirects=False)
    except httpx.HTTPError:
        return RETRY
    status = response.status_code
    if 200 <= status < 300:
        return SENT
    if status in (404, 410):
        return GONE
    if status == 429 or status >= 500:
        return RETRY
    return FAILED
