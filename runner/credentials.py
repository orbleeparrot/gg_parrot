"""이 PC에 키 기억하기 — 거래소별 API 키·시크릿과 껄무새 회원 키를 로컬 파일 하나에 보관한다.

파일은 Windows DPAPI(CryptProtectData, 현재 사용자 범위)로 감싼다: 보통 같은 Windows 계정·PC에서
풀린다(Windows 로밍 프로필은 예외). 서버로는 보내지 않는다(실행기는 원래 키를 서버에
보내지 않는다). 순수 함수만 두고 보호/해제 함수를 주입받아 테스트한다.

파일 모양(v2): {"version": 2, "member_key": str, "exchanges": {거래소: {"api_key": str, "api_secret": str}}}.
v1(바이낸스 한 쌍만, version 없음)은 읽을 때 binance 로 옮겨 담는다. 되돌아가지 않는다(저장하면 v2 로 다시 쓴다).
"""
from __future__ import annotations

import json
import ipaddress
import math
import os
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

EXCHANGES = ("binance", "upbit", "bithumb")
FIELDS = ("api_key", "api_secret", "member_key")  # v1 호환 — 이사에만 쓴다
_PAIR = ("api_key", "api_secret")
# 절대 바꾸지 않는다: DPAPI 가 이 값을 암호화에 섞어서, 바꾸면 기존 파일이 영영 안 열린다(이사도 불가).
# 이름의 v1 은 파일 형식 버전(payload 의 "version")과 무관한 옛 이름일 뿐이다.
_ENTROPY = b"ggparrot-runner-credentials-v1"
HISTORY_MAX_FUTURE_SKEW_SECONDS = 300


@dataclass(frozen=True)
class CredentialsLoadResult:
    values: Optional[dict] = field(repr=False)
    status: str


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


def _connection_history(raw: object) -> dict:
    """Past observations only, never proof of registered IP or current auth."""
    if not isinstance(raw, dict):
        return {}
    history = {}
    latest = time.time() + HISTORY_MAX_FUTURE_SKEW_SECONDS
    for exchange in EXCHANGES:
        item = raw.get(exchange)
        if not isinstance(item, dict) or not isinstance(item.get("public_ipv4"), str):
            continue
        observed = item.get("observed_at")
        if (isinstance(observed, bool) or not isinstance(observed, (int, float))
                or not 0 < observed <= latest or not math.isfinite(observed)):
            continue
        try:
            address = ipaddress.ip_address(item["public_ipv4"].strip())
        except ValueError:
            continue
        if address.version == 4 and address.is_global and not address.is_multicast:
            history[exchange] = {"public_ipv4": str(address), "observed_at": observed}
    return history


def _payload(values: dict) -> dict:
    payload = {"version": 2, "member_key": _text(values.get("member_key")),
               "exchanges": _exchanges(values.get("exchanges"))}
    history = _connection_history(values.get("connection_history"))
    if history:
        payload["connection_history"] = history
    return payload


def save(path: Path, values: dict, *, protect: Callable[[bytes], bytes] = dpapi_protect) -> None:
    payload = _payload(values)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    protected = protect(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(protected)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def load_result(path: Path, *, unprotect: Callable[[bytes], bytes] = dpapi_unprotect) -> CredentialsLoadResult:
    """No raw OS/crypto errors or secrets in the user-facing load status."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return CredentialsLoadResult(None, "missing")
    except OSError:
        return CredentialsLoadResult(None, "read_error")
    try:
        decrypted = unprotect(raw)
    except Exception:
        return CredentialsLoadResult(None, "decrypt_error")
    try:
        data = json.loads(decrypted.decode("utf-8"))
    except Exception:
        return CredentialsLoadResult(None, "invalid_data")
    if not isinstance(data, dict):
        return CredentialsLoadResult(None, "invalid_data")
    if "version" not in data:
        # v1: 바이낸스 한 쌍뿐이었다. 회원 키는 그 자리에 둔다 — 옛 실행기로 돌아가도 회원 키는 살아 있게.
        pair = _pair(data)
        exchanges = {"binance": pair} if pair else {}
        data = {"version": 2, "member_key": data.get("member_key"), "exchanges": exchanges,
                "connection_history": data.get("connection_history")}
    return CredentialsLoadResult(_payload(data), "loaded")


def load(path: Path, *, unprotect: Callable[[bytes], bytes] = dpapi_unprotect) -> Optional[dict]:
    """Backward-compatible values-only view; new UI uses load_result statuses."""
    return load_result(path, unprotect=unprotect).values


def clear(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def backup_unreadable(path: Path) -> Path:
    """After explicit user consent only: move one unreadable store, never delete it.

    The same-directory destination is exclusively reserved, so an existing
    backup is never overwritten. We do not accept symlinks or directory targets.
    If the move fails the original stays in place and the empty reservation is
    removed. The encrypted bytes are not decrypted or rewritten by this helper.
    """
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("연결된 경로는 백업하지 않았어요. 저장 위치를 먼저 확인하세요.")
    source = path.lstat()
    if not stat.S_ISREG(source.st_mode):
        raise ValueError("키 저장 파일 하나만 백업할 수 있어요.")
    loaded = load_result(path)
    if loaded.status not in ("read_error", "decrypt_error", "invalid_data"):
        raise ValueError("저장 파일 상태가 바뀌었어요. 저장된 키를 다시 불러온 뒤 확인하세요.")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    descriptor, name = tempfile.mkstemp(prefix=f"{path.name}.backup-{stamp}-", dir=path.parent)
    backup = Path(name)
    try:
        os.close(descriptor)
        current = path.lstat()
        if (current.st_ino, current.st_dev, current.st_mode, current.st_size, current.st_mtime_ns) != (
                source.st_ino, source.st_dev, source.st_mode, source.st_size, source.st_mtime_ns):
            raise ValueError("저장 파일 상태가 바뀌었어요. 저장된 키를 다시 불러온 뒤 확인하세요.")
        os.replace(path, backup)
    except Exception:
        try:
            backup.unlink()
        except OSError:
            pass
        raise
    return backup


def apply_choice(path: Path, remember: bool, values: dict, *, protect: Callable[[bytes], bytes] = dpapi_protect) -> None:
    """'기억하기' 체크 상태대로: 켜져 있으면 지금 값(v2 모양)을 저장, 꺼져 있으면 저장된 파일을 지운다."""
    if remember:
        save(path, values, protect=protect)
    else:
        clear(path)


def apply_exchange_choice(path: Path, exchange: str, remember: bool, values: dict,
                          *, previous: Optional[dict] = None,
                          protect: Callable[[bytes], bytes] = dpapi_protect) -> Optional[dict]:
    """Change only this exchange; preserve other saved keys and the member key.

    A member-only DPAPI file is deliberately retained after deleting the last
    exchange: removing an exchange must not also forget the user's membership.
    Unknown/unreadable stores are never overwritten. Return only after I/O
    succeeds, without modifying either input dictionary on failure.
    """
    if exchange not in EXCHANGES or not isinstance(values, dict):
        raise ValueError("저장할 거래소와 키 입력을 다시 확인하세요.")
    if previous is not None and not isinstance(previous, dict):
        raise ValueError("기존 저장 키 상태를 다시 확인하세요.")
    # The disk is authoritative: another runner can change/delete its keys after
    # the UI loaded them. Never resurrect deleted keys or overwrite unreadable
    # files from a stale `previous` cache (the argument remains source-compatible).
    loaded = load_result(path)
    if loaded.status not in ("missing", "loaded"):
        raise ValueError("기존 저장 키를 읽지 못해 변경하지 않았어요. 저장 복구 안내를 먼저 확인하세요.")
    before = _payload(loaded.values or {})
    result = _payload(before)
    history = dict(result.get("connection_history") or {})
    if remember:
        selected = _exchanges(values.get("exchanges")).get(exchange)
        if not selected or not all(value.strip() for value in selected.values()):
            raise ValueError("저장하려는 거래소의 API 키와 시크릿을 모두 입력하세요.")
        result["exchanges"][exchange] = selected
        member_key = _text(values.get("member_key"))
        if member_key:
            result["member_key"] = member_key
        incoming = values.get("connection_history")
        if isinstance(incoming, dict) and exchange in incoming:
            observation = _connection_history(incoming).get(exchange)
            if observation:
                history[exchange] = observation
            else:
                history.pop(exchange, None)
    else:
        result["exchanges"].pop(exchange, None)
        history.pop(exchange, None)
    if history:
        result["connection_history"] = history
    else:
        result.pop("connection_history", None)
    if result["exchanges"] or result["member_key"] or history:
        save(path, result, protect=protect)
        return result
    clear(path)
    return None
