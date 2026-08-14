"""Account auth: email/password signup & login, JWT session, current-user dep.

Our own auth (not Supabase Auth): passwords are PBKDF2-hashed (see security.py),
sessions are stateless JWTs signed with ``SECRET_KEY``. Supabase is used only as
the durable Postgres store. Keep this the single place that mints/verifies tokens.

Signup grants starter points atomically with account creation, so a new account
always has a matching ledger entry.
"""
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import jwt
from fastapi import Header, HTTPException
from sqlmodel import select

from . import email_service
from . import points as points_mod
from .db import User, get_session
from .security import hash_password, verify_password

# Dev fallback only (>=32 bytes to satisfy HS256). Deployments MUST set SECRET_KEY.
SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-insecure-secret-change-me-in-production-0123456789"
_JWT_ALGO = "HS256"
_TOKEN_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "168"))  # 7 days
_RESET_TTL_MIN = int(os.environ.get("RESET_TTL_MINUTES", "30"))
_RUNNER_SESSION_STREAM_TTL_SECONDS = max(
    15, min(int(os.environ.get("RUNNER_SESSION_STREAM_TTL_SECONDS", "60")), 300)
)
_RUNNER_SESSION_STREAM_PURPOSE = "runner_sessions_stream"
_FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "").rstrip("/")

# Google 간편 로그인(Google Identity Services). 설정되지 않으면 기능이 꺼진 채
# 이메일/비밀번호만 동작한다(프런트가 config 로 켬/끔을 확인).
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
_GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_가-힣]{2,20}$")


class AuthError(HTTPException):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(hours=_TOKEN_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=_JWT_ALGO)


def _decode(token: str) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[_JWT_ALGO])
        # Purpose-scoped tokens (password reset, websocket handoff, etc.) must
        # never be accepted as ordinary browser sessions.
        if payload.get("purpose") is not None:
            raise ValueError("purpose-scoped token")
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise AuthError(401, "세션이 만료됐거나 유효하지 않아요. 다시 로그인해 주세요.")


def make_runner_session_stream_token(user_id: int) -> dict:
    """Mint a short-lived, single-purpose token for the sessions websocket.

    Browser WebSocket constructors cannot set an Authorization header. The
    signed-in page therefore exchanges its normal bearer token for this short
    token and sends it as a websocket subprotocol. It is deliberately unusable
    on regular HTTP endpoints.
    """
    now = _now()
    expires = now + timedelta(seconds=_RUNNER_SESSION_STREAM_TTL_SECONDS)
    payload = {
        "sub": str(user_id),
        "purpose": _RUNNER_SESSION_STREAM_PURPOSE,
        "jti": secrets.token_urlsafe(12),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return {
        "token": jwt.encode(payload, SECRET_KEY, algorithm=_JWT_ALGO),
        "expires_in": _RUNNER_SESSION_STREAM_TTL_SECONDS,
    }


def decode_runner_session_stream_token(token: str) -> int:
    """Validate a sessions-stream token and return its account id."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[_JWT_ALGO])
        if payload.get("purpose") != _RUNNER_SESSION_STREAM_PURPOSE:
            raise ValueError("wrong purpose")
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise AuthError(401, "실시간 세션 연결 인증이 만료됐거나 유효하지 않아요.")


def user_view(user: User) -> dict:
    """Public view of an account — never includes the password hash."""
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "points_balance": user.points_balance,
        "created_at": user.created_at,
    }


def signup(email: str, username: str, password: str) -> dict:
    email = (email or "").strip().lower()
    username = (username or "").strip()
    if not _EMAIL_RE.match(email):
        raise AuthError(400, "이메일 형식이 올바르지 않아요.")
    if not _USERNAME_RE.match(username):
        raise AuthError(400, "아이디는 2~20자의 한글/영문/숫자/밑줄만 가능해요.")
    if len(password or "") < 8:
        raise AuthError(400, "비밀번호는 8자 이상이어야 해요.")

    with get_session() as db:
        if db.exec(select(User).where(User.email == email)).first():
            raise AuthError(409, "이미 가입된 이메일이에요.")
        if db.exec(select(User).where(User.username == username)).first():
            raise AuthError(409, "이미 사용 중인 아이디예요.")

        user = User(
            email=email,
            username=username,
            password_hash=hash_password(password),
            points_balance=0,
            created_at=_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        # Starter grant, atomically recorded in the ledger.
        points_mod.apply(db, user, points_mod.SIGNUP_GRANT, "signup_grant")
        db.commit()
        db.refresh(user)
        return {"token": make_token(user.id), "user": user_view(user)}


def login(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    with get_session() as db:
        user = db.exec(select(User).where(User.email == email)).first()
        if user is None or not verify_password(password or "", user.password_hash):
            raise AuthError(401, "이메일 또는 비밀번호가 올바르지 않아요.")
        return {"token": make_token(user.id), "user": user_view(user)}


def get_user_by_id(user_id: int) -> Optional[User]:
    with get_session() as db:
        return db.get(User, user_id)


# --- Google 간편 로그인 -------------------------------------------------
def google_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID)


def _verify_google_credential(credential: str) -> dict:
    """Google ID 토큰(GIS credential)을 검증하고 클레임을 돌려준다.

    Google 의 tokeninfo 엔드포인트가 서명·만료를 검증하므로, 우리는 대상(aud)이
    우리 클라이언트 ID 인지, 발급자·이메일 확인 여부만 추가로 확인한다.
    """
    if not GOOGLE_CLIENT_ID:
        raise AuthError(503, "구글 로그인이 아직 설정되지 않았어요. (서버에 GOOGLE_CLIENT_ID 필요)")
    credential = (credential or "").strip()
    if not credential:
        raise AuthError(400, "구글 인증 정보가 비어 있어요.")
    try:
        resp = httpx.get(_GOOGLE_TOKENINFO_URL, params={"id_token": credential}, timeout=10.0)
    except httpx.HTTPError:
        raise AuthError(502, "구글 인증 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.")
    if resp.status_code != 200:
        raise AuthError(401, "구글 인증에 실패했어요. 다시 시도해 주세요.")
    info = resp.json()
    if info.get("aud") != GOOGLE_CLIENT_ID:
        raise AuthError(401, "이 앱을 위한 구글 인증이 아니에요.")
    if info.get("iss") not in _GOOGLE_ISSUERS:
        raise AuthError(401, "구글 인증 발급자가 올바르지 않아요.")
    email = (info.get("email") or "").strip().lower()
    if not email:
        raise AuthError(401, "구글 계정에서 이메일을 가져오지 못했어요.")
    if str(info.get("email_verified", "")).lower() not in ("true", "1"):
        raise AuthError(401, "이메일이 확인되지 않은 구글 계정이에요.")
    return info


def _unique_username(db, base: str) -> str:
    """공개 아이디 규칙([A-Za-z0-9_가-힣]{2,20})에 맞춘 유일한 아이디를 만든다."""
    cleaned = re.sub(r"[^A-Za-z0-9_가-힣]", "", base or "")[:20]
    if len(cleaned) < 2:
        cleaned = (cleaned + "user")[:20]
    candidate = cleaned
    n = 0
    while db.exec(select(User).where(User.username == candidate)).first():
        n += 1
        suffix = str(n)
        candidate = cleaned[: 20 - len(suffix)] + suffix
    return candidate


def google_auth(credential: str) -> dict:
    """구글 ID 토큰으로 로그인(없으면 자동 가입).

    같은 이메일 계정이 이미 있으면 그대로 로그인 — 구글은 같은 지갑으로 들어가는
    또 하나의 경로일 뿐이다. 새 이메일이면 계정을 만들고(비밀번호 해시는 비워 둬서
    비밀번호 로그인은 재설정 전까지 막힘) 이메일 가입과 동일한 스타터 포인트를 준다.
    """
    info = _verify_google_credential(credential)
    email = info["email"].strip().lower()
    with get_session() as db:
        user = db.exec(select(User).where(User.email == email)).first()
        if user is not None:
            return {"token": make_token(user.id), "user": user_view(user)}
        username = _unique_username(db, info.get("name") or email.split("@")[0])
        user = User(
            email=email,
            username=username,
            password_hash="",  # 외부(구글) 인증 — 로컬 비밀번호 없음
            points_balance=0,
            created_at=_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        # 스타터 지급 — 이메일 가입과 동일하게 원장에 함께 기록.
        points_mod.apply(db, user, points_mod.SIGNUP_GRANT, "signup_grant")
        db.commit()
        db.refresh(user)
        return {"token": make_token(user.id), "user": user_view(user)}


# --- password reset -----------------------------------------------------
def _make_reset_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "purpose": "reset",
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=_RESET_TTL_MIN)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=_JWT_ALGO)


def _decode_reset_token(token: str) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[_JWT_ALGO])
        if payload.get("purpose") != "reset":
            raise ValueError("wrong purpose")
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise AuthError(400, "재설정 링크가 만료됐거나 유효하지 않아요. 다시 요청해 주세요.")


def request_password_reset(email: str) -> dict:
    """Email a reset link if the account exists. Always reports success (no
    account enumeration). Actual delivery requires RESEND_API_KEY to be set."""
    email = (email or "").strip().lower()
    with get_session() as db:
        user = db.exec(select(User).where(User.email == email)).first()
    if user is not None:
        token = _make_reset_token(user.id)
        link = f"{_FRONTEND_BASE_URL}/reset?token={token}"
        html = (
            "<div style='font-family:sans-serif'>"
            "<h2>🦜 GGparrot 비밀번호 재설정</h2>"
            f"<p>아래 버튼을 눌러 새 비밀번호를 설정하세요. (링크는 {_RESET_TTL_MIN}분간 유효)</p>"
            f"<p><a href='{link}' style='background:#4f46e5;color:#fff;padding:10px 16px;"
            "border-radius:8px;text-decoration:none'>비밀번호 재설정</a></p>"
            "<p style='color:#888;font-size:12px'>본인이 요청하지 않았다면 이 메일을 무시하세요.</p>"
            "</div>"
        )
        email_service.send_email(email, "[GGparrot] 비밀번호 재설정", html)
    return {
        "ok": True,
        "message": "가입된 이메일이면 재설정 링크를 보냈어요. 메일함(스팸함 포함)을 확인해 주세요.",
        "email_enabled": email_service.email_enabled(),
    }


def reset_password(token: str, new_password: str) -> dict:
    if len(new_password or "") < 8:
        raise AuthError(400, "비밀번호는 8자 이상이어야 해요.")
    user_id = _decode_reset_token(token)
    with get_session() as db:
        user = db.get(User, user_id)
        if user is None:
            raise AuthError(400, "계정을 찾을 수 없어요.")
        user.password_hash = hash_password(new_password)
        db.add(user)
        db.commit()
    return {"ok": True, "message": "비밀번호가 변경됐어요. 새 비밀번호로 로그인해 주세요."}


def current_user(authorization: Optional[str] = Header(default=None)) -> User:
    """FastAPI dependency: resolve the Bearer token to a User (401 otherwise)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError(401, "로그인이 필요해요.")
    user = get_user_by_id(_decode(authorization[7:].strip()))
    if user is None:
        raise AuthError(401, "계정을 찾을 수 없어요.")
    return user


def optional_user(authorization: Optional[str] = Header(default=None)) -> Optional[User]:
    """Like current_user but returns None instead of raising (for public+auth views)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        return get_user_by_id(_decode(authorization[7:].strip()))
    except HTTPException:
        return None
