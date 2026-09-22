"""이 PC에 키 기억하기 — 바이낸스 API 키·시크릿·껄무새 회원 키를 로컬 파일 하나에 보관한다.

파일은 Windows DPAPI(CryptProtectData, 현재 사용자 범위)로 감싼다: 같은 Windows 계정에서만 풀리고,
다른 PC·다른 계정으로 복사하면 열리지 않는다. 서버로는 보내지 않는다(실행기는 원래 키를 서버에
보내지 않는다). 순수 함수만 두고 보호/해제 함수를 주입받아 테스트한다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

FIELDS = ("api_key", "api_secret", "member_key")
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
def save(path: Path, values: dict, *, protect: Callable[[bytes], bytes] = dpapi_protect) -> None:
    payload = {k: str(values.get(k) or "") for k in FIELDS}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(protect(raw))
    os.replace(tmp, path)


def load(path: Path, *, unprotect: Callable[[bytes], bytes] = dpapi_unprotect) -> Optional[dict]:
    """저장된 키. 파일이 없거나 못 풀면(다른 PC·손상) None — 호출자는 빈 칸으로 시작한다."""
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
    return {k: str(data.get(k) or "") for k in FIELDS}


def clear(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def apply_choice(path: Path, remember: bool, values: dict, *, protect: Callable[[bytes], bytes] = dpapi_protect) -> None:
    """'기억하기' 체크 상태대로: 켜져 있으면 지금 값을 저장, 꺼져 있으면 저장된 파일을 지운다."""
    if remember:
        save(path, values, protect=protect)
    else:
        clear(path)
