"""이 PC에 키 기억하기 — 거래소별 API 키·시크릿과 껄무새 회원 키를 로컬 파일 하나에 보관한다.

파일은 Windows DPAPI(CryptProtectData, 현재 사용자 범위)로 감싼다: 같은 Windows 계정에서만 풀리고,
다른 PC·다른 계정으로 복사하면 열리지 않는다. 서버로는 보내지 않는다(실행기는 원래 키를 서버에
보내지 않는다). 순수 함수만 두고 보호/해제 함수를 주입받아 테스트한다.

파일 모양(v2): {"version": 2, "member_key": str, "exchanges": {거래소: {"api_key": str, "api_secret": str}}}.
v1(바이낸스 한 쌍만, version 없음)은 읽을 때 binance 로 옮겨 담는다. 되돌아가지 않는다(저장하면 v2 로 다시 쓴다).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

EXCHANGES = ("binance", "upbit", "bithumb")
FIELDS = ("api_key", "api_secret", "member_key")  # v1 호환 — 이사에만 쓴다
_PAIR = ("api_key", "api_secret")
# 절대 바꾸지 않는다: DPAPI 가 이 값을 암호화에 섞어서, 바꾸면 기존 파일이 영영 안 열린다(이사도 불가).
# 이름의 v1 은 파일 형식 버전(payload 의 "version")과 무관한 옛 이름일 뿐이다.
_ENTROPY = b"ggparrot-runner-credentials-v1"


def default_path(local_app_data: Optional[str] = None) -> Path:
    base = local_app_data or os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "GGParrot" / "credentials.dat"


def supported() -> bool:
    """DPAPI 는 Windows 에만 있다 — 다른 OS 에선 '기억하기' UI 를 숨긴다."""
    return sys.platform == "win32"


# --- DPAPI (ctypes, 추가 의존성 없음) ---------------------------------------
def _blob_roundtrip(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    src = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    entropy = DATA_BLOB(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_protect(data: bytes) -> bytes:
    return _blob_roundtrip(data, True)


def dpapi_unprotect(data: bytes) -> bytes:
    return _blob_roundtrip(data, False)


# --- 파일 -------------------------------------------------------------------
def _text(value: object) -> str:
    """문자열만 믿는다. 숫자·dict 같은 깨진 값을 str() 로 키처럼 둔갑시키지 않는다."""
    return value if isinstance(value, str) else ""


def _pair(raw: object) -> Optional[dict]:
    """키 한 쌍. dict 가 아니거나 둘 다 비면 None — 빈 칸 항목은 '저장된 키 없음' 과 같다.
    한쪽만 있어도 버리지 않는다: 사용자가 칸에 적은 그대로 돌려줘야 v1 때와 같게 동작한다."""
    if not isinstance(raw, dict):
        return None
    pair = {k: _text(raw.get(k)) for k in _PAIR}
    return pair if any(pair.values()) else None


def _exchanges(raw: object) -> dict:
    """아는 거래소(EXCHANGES)만, 고정된 순서로. 모르는 이름·깨진 항목은 조용히 버린다."""
    if not isinstance(raw, dict):
        return {}
    found = {}
    for name in EXCHANGES:
        pair = _pair(raw.get(name))
        if pair:
            found[name] = pair
    return found


def save(path: Path, values: dict, *, protect: Callable[[bytes], bytes] = dpapi_protect) -> None:
    payload = {
        "version": 2,
        "member_key": _text(values.get("member_key")),
        "exchanges": _exchanges(values.get("exchanges")),
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(protect(raw))
    os.replace(tmp, path)


def load(path: Path, *, unprotect: Callable[[bytes], bytes] = dpapi_unprotect) -> Optional[dict]:
    """저장된 키. 파일이 없거나 못 풀면(다른 PC·손상) None — 호출자는 빈 칸으로 시작한다.
    돌려주는 모양은 항상 v2: {"version": 2, "member_key": str, "exchanges": {...}}."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        data = json.loads(unprotect(raw).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if "version" not in data:
        # v1: 바이낸스 한 쌍뿐이었다. 회원 키는 그 자리에 둔다 — 옛 실행기로 돌아가도 회원 키는 살아 있게.
        pair = _pair(data)
        exchanges = {"binance": pair} if pair else {}
    else:
        exchanges = _exchanges(data.get("exchanges"))
    return {"version": 2, "member_key": _text(data.get("member_key")), "exchanges": exchanges}


def clear(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def apply_choice(path: Path, remember: bool, values: dict, *, protect: Callable[[bytes], bytes] = dpapi_protect) -> None:
    """'기억하기' 체크 상태대로: 켜져 있으면 지금 값(v2 모양)을 저장, 꺼져 있으면 저장된 파일을 지운다."""
    if remember:
        save(path, values, protect=protect)
    else:
        clear(path)
