"""매크로 파일(.ggm.json) 서명 — "웹에서 받은 원본 그대로인가"를 서버가 판별한다.

피드백: 사용자가 내려받은 파일을 로컬에서 고쳐 실행한 뒤 "껄무새 매크로가 이상하다"고
문의하면, 운영자는 원본인지 수정본인지 알 길이 없었다. 파일을 내려줄 때 정규화된
매크로에 HMAC 서명(``_sig``)을 동봉하고, 실행기가 시작할 때 그 서명을 같이 올리면
서버가 다시 계산해 세션에 출처(``macro_origin``)를 남긴다.

서명은 정규화된 매크로(Macro.model_dump) 위에 걸리므로 ``human_summary`` 같은
표시용 필드를 바꾸는 건 무관하고, 레버리지·손절·조건 등 실제 동작 값을 바꾸면
불일치가 난다. 실행기는 비밀키가 없으니 검증은 언제나 서버가 한다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Optional

from .engine import Macro

# v1: exchange·quote_currency 가 없던 시절. v2: 그 둘을 포함. v3: entry_filter 를 포함.
# v4: legs·bundle_risk(묶음)를 포함.
# Macro 에 필드가 늘 때마다 서명 대상 JSON 이 바뀌므로, 올리고 옛 버전에서는 그 필드를 뺀다.
SIG_VERSION = 4
# 받아들이는 서명 버전 — SIG_VERSION 이하 전부. 손으로 적은 목록(`(1, 2, SIG_VERSION)`)은 올릴 때마다
# 직전 버전을 빠뜨리는 함정이었다(v1→v2 때 `(1, SIG_VERSION)` 이라 v2 파일이 전부 '수정된 파일' 이 될 참이었다).
# 범위로 만들어 두면 다음 bump 가 옛 파일을 떨어뜨리지 않는다.
ACCEPTED_SIG_VERSIONS = tuple(range(1, SIG_VERSION + 1))

# 세션에 남는 출처 값과 화면 표기.
ORIGIN_WEB = "web"  # 웹 '빠른 실행' 티켓 — 서버가 매크로를 직접 넘김
ORIGIN_FILE_VERIFIED = "file_verified"  # 파일 서명 일치 — 웹에서 받은 원본 그대로
ORIGIN_FILE_MODIFIED = "file_modified"  # 파일 서명 불일치 — 로컬에서 손댄 파일
ORIGIN_FILE_UNSIGNED = "file_unsigned"  # 서명 없는 파일(옛 파일·옛 실행기)
ORIGIN_LABELS = {
    ORIGIN_WEB: "웹에서 바로 실행",
    ORIGIN_FILE_VERIFIED: "원본 파일(서명 확인)",
    ORIGIN_FILE_MODIFIED: "수정된 파일",
    ORIGIN_FILE_UNSIGNED: "서명 없는 파일",
}


def _key() -> bytes:
    secret = os.environ.get("MACRO_SIGNING_SECRET", "").strip()
    if not secret:
        from .auth import SECRET_KEY  # 지연 import — auth 가 db 를 끌어와 순환을 피한다.

        secret = SECRET_KEY
    return hashlib.sha256(b"ggparrot-macro-file:" + secret.encode("utf-8")).digest()


def canonical_bytes(macro: Macro, *, version: int = SIG_VERSION) -> bytes:
    """서명 대상 — 정규화된 매크로를 키 정렬 JSON 으로 직렬화한 바이트."""
    data = macro.model_dump(mode="json")
    if version == 1:
        data.pop("exchange", None)
        data.pop("quote_currency", None)
    if version < 3:
        data.pop("entry_filter", None)
    if version < 4:
        data.pop("legs", None)
        data.pop("bundle_risk", None)
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(macro: Macro) -> str:
    """짧은 지문(sha256 앞 12자리) — 로그에서 어떤 매크로였는지 대조하는 용도."""
    return hashlib.sha256(canonical_bytes(macro)).hexdigest()[:12]


def _mac(macro: Macro, *, version: int = SIG_VERSION) -> str:
    return hmac.new(_key(), canonical_bytes(macro, version=version), hashlib.sha256).hexdigest()


def sign(macro: Macro) -> dict:
    """파일에 동봉할 ``_sig`` 블록."""
    return {
        "v": SIG_VERSION,
        "alg": "HMAC-SHA256",
        "hmac": _mac(macro),
        "issued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def verify(macro: Macro, sig: Optional[dict]) -> bool:
    if not isinstance(sig, dict):
        return False
    given = sig.get("hmac")
    # compare_digest accepts ASCII strings only. A locally edited file can
    # contain any JSON value; classify malformed signatures without a 500.
    if (not isinstance(given, str) or len(given) != 64
            or any(char not in "0123456789abcdef" for char in given)
            or sig.get("v") not in ACCEPTED_SIG_VERSIONS):
        return False
    version = sig["v"]
    if version == 1 and (macro.exchange != "binance" or macro.quote_currency != "USDT"):
        return False
    # 옛 서명은 entry_filter 를 모른다. 필터가 붙은 매크로가 옛 서명으로 "원본" 이 되면
    # 파일에 필터를 손으로 끼워 넣고도 검증을 통과하므로 거절한다.
    if version < 3 and macro.entry_filter is not None:
        return False
    # 옛 서명은 legs·bundle_risk 도 모른다. 묶음 매크로가 v3 이하 서명으로 "원본" 이 되면
    # 파일에 레그를 손으로 끼워 넣고도 통과하므로 거절한다.
    if version < 4 and (macro.legs is not None or macro.bundle_risk is not None):
        return False
    return hmac.compare_digest(given, _mac(macro, version=version))


def classify_origin(macro: Optional[Macro], *, user_macro_id: Optional[int], sig: Optional[dict]) -> str:
    """세션의 매크로 출처를 정한다. 티켓 경로가 우선, 그다음 파일 서명."""
    if user_macro_id is not None:
        return ORIGIN_WEB
    if macro is None or not isinstance(sig, dict) or not sig:
        return ORIGIN_FILE_UNSIGNED
    return ORIGIN_FILE_VERIFIED if verify(macro, sig) else ORIGIN_FILE_MODIFIED
