"""국내 거래소(업비트 · 빗썸) 종목 목록의 한글 · 영문 이름 — 고정 사전에 없는 코인의 뉴스 검색 · 표시용.

news._COIN_KO · _COIN_ALIASES(직접 고른 이름과 동명이인 규칙)가 언제나 먼저다. 이 모듈은 거기에 없는 코인
(예: 2026-10-15 빗썸 신규 상장 KRW-ACU '아큐라스트 / Acurast')만 채운다 — 그 전에는 티커 'ACU' 로만 검색해
Asian Clearing Union 같은 엉뚱한 기사가 걸렸다.

이름은 종목 목록(data.krw.list_symbols — 60초 캐시)에서 읽는다. 기사 거르기는 메모리만 읽고(peek_names),
네트워크는 자산 준비 단계(refresh_names)에서만 쓴다. 실패해도 뉴스는 예전처럼 티커로 이어진다.
"""
from __future__ import annotations

import os
import re
import threading
import time

_REFRESH_SECONDS = 600
_RETRY_SECONDS = 120
_EXCHANGES = ("upbit", "bithumb")  # 같은 코인이 둘 다 있으면 업비트 이름을 쓴다
_SYMBOL = re.compile(r"[A-Z0-9]{1,20}\Z")

_names: dict[str, tuple[str, str]] = {}
_fetched_at = 0.0
_retry_after = 0.0
_refreshing = False
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("NEWS_EXCHANGE_NAMES_ENABLED", "true").strip().casefold() not in {"0", "false", "no", "off"}


def _clean(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if 2 <= len(text) <= 40 else ""


def peek_names(symbol: str) -> tuple[str, str]:
    """(한글 이름, 영문 이름). 모르면 빈 글자 둘 — I/O 없음."""
    key = str(symbol or "").strip().upper()
    with _lock:
        return _names.get(key, ("", ""))


def refresh_names() -> None:
    """종목 목록에서 이름을 다시 모은다(10분에 한 번, 실패하면 2분 뒤). 예외는 밖으로 내지 않는다."""
    global _names, _fetched_at, _retry_after, _refreshing
    if not enabled():
        return
    now = time.time()
    with _lock:
        if _refreshing or now - _fetched_at < _REFRESH_SECONDS or now < _retry_after:
            return
        _refreshing = True
    merged: dict[str, tuple[str, str]] = {}
    try:
        from .data import krw
        for exchange in _EXCHANGES:
            try:
                items = krw.list_symbols(exchange).get("items") or []
            except Exception:
                continue
            for item in items:
                base = str(item.get("base") or "").strip().upper()
                if not _SYMBOL.fullmatch(base) or base in merged:
                    continue
                korean, english = _clean(item.get("korean_name")), _clean(item.get("english_name"))
                if korean or english:
                    merged[base] = (korean, english)
    finally:
        with _lock:
            if merged:
                _names, _fetched_at = merged, time.time()
            else:
                _retry_after = time.time() + _RETRY_SECONDS
            _refreshing = False


def reset_for_tests() -> None:
    global _names, _fetched_at, _retry_after, _refreshing
    with _lock:
        _names, _fetched_at, _retry_after, _refreshing = {}, 0.0, 0.0, False
