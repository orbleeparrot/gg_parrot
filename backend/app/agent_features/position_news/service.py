"""Service layer for position-aware news alerts."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque

from ... import news as news_mod
from . import classifier

FEATURE_KEY = "position_news"
_CACHE_SECONDS = max(300, int(os.environ.get("POSITION_NEWS_ANALYSIS_CACHE_SECONDS", "21600")))
_DEGRADED_CACHE_SECONDS = max(30, int(os.environ.get("POSITION_NEWS_DEGRADED_CACHE_SECONDS", "60")))
_CACHE_MAX_ENTRIES = max(8, int(os.environ.get("POSITION_NEWS_ANALYSIS_CACHE_MAX_ENTRIES", "256")))
_analysis_cache: dict[str, tuple[dict, float]] = {}
_cache_guard = threading.Lock()


class _KeyLock:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


_analysis_locks: dict[str, _KeyLock] = {}
_AI_RATE_WINDOW_SECONDS = max(60, int(os.environ.get("POSITION_NEWS_AI_RATE_WINDOW_SECONDS", "3600")))
_AI_USER_MAX_ANALYSES = max(1, int(os.environ.get("POSITION_NEWS_AI_USER_MAX_ANALYSES", "12")))
_AI_GLOBAL_MAX_ANALYSES = max(1, int(os.environ.get("POSITION_NEWS_AI_GLOBAL_MAX_ANALYSES", "120")))
_rate_guard = threading.Lock()
_user_ai_usage: dict[int, deque[float]] = {}
_global_ai_usage: deque[float] = deque()

_DISCLAIMER = (
    "뉴스 영향은 실제 가격 반응과 다를 수 있습니다. 헤드라인을 바탕으로 한 정보 제공이며 "
    "매수·매도 등 매매 지시가 아닙니다. 반드시 원문을 확인하세요."
)


def _fingerprint(asset_symbol: str, items: list[dict]) -> str:
    material = {
        "asset_symbol": asset_symbol,
        "prompt_version": classifier.PROMPT_VERSION,
        "model": os.environ.get("ANTHROPIC_MODEL", ""),
        "ai_enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "items": [
            {
                "title": item.get("title") or "",
                "source": item.get("source") or "",
            }
            for item in items
        ],
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _consume_ai_budget(requester_id: int | None) -> bool:
    """Process-local cost guard; cached analyses never consume this budget."""
    if requester_id is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return True
    now = time.time()
    cutoff = now - _AI_RATE_WINDOW_SECONDS
    with _rate_guard:
        while _global_ai_usage and _global_ai_usage[0] <= cutoff:
            _global_ai_usage.popleft()
        if len(_user_ai_usage) > _AI_GLOBAL_MAX_ANALYSES * 2:
            for user_id, timestamps in list(_user_ai_usage.items()):
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()
                if not timestamps:
                    _user_ai_usage.pop(user_id, None)
        if len(_global_ai_usage) >= _AI_GLOBAL_MAX_ANALYSES:
            return False
        usage = _user_ai_usage.setdefault(int(requester_id), deque())
        while usage and usage[0] <= cutoff:
            usage.popleft()
        if len(usage) >= _AI_USER_MAX_ANALYSES:
            return False
        usage.append(now)
        _global_ai_usage.append(now)
        return True


def _analysis_for(
    asset_symbol: str,
    coin_name: str,
    items: list[dict],
    requester_id: int | None = None,
) -> dict:
    key = _fingerprint(asset_symbol, items)
    now = time.time()
    with _cache_guard:
        expired = [cache_key for cache_key, (_value, expires_at) in _analysis_cache.items() if expires_at <= now]
        for cache_key in expired:
            _analysis_cache.pop(cache_key, None)
            lock = _analysis_locks.get(cache_key)
            if lock is not None and lock.users == 0:
                _analysis_locks.pop(cache_key, None)
        hit = _analysis_cache.get(key)
        if hit and hit[1] > now:
            return hit[0]
        analysis_lock = _analysis_locks.setdefault(key, _KeyLock())
        analysis_lock.users += 1

    # Only identical headline snapshots wait for one another. Calls for other
    # symbols continue independently while this key is analyzed.
    try:
        with analysis_lock.lock:
            now = time.time()
            with _cache_guard:
                hit = _analysis_cache.get(key)
                if hit and hit[1] > now:
                    return hit[0]

            analysis = classifier.analyze_headlines(
                items,
                coin_name,
                allow_ai=_consume_ai_budget(requester_id),
            )
            if analysis.get("analysis_status") == "rate_limited":
                return analysis
            with _cache_guard:
                while len(_analysis_cache) >= _CACHE_MAX_ENTRIES:
                    evicted = next(iter(_analysis_cache))
                    _analysis_cache.pop(evicted, None)
                    evicted_lock = _analysis_locks.get(evicted)
                    if evicted_lock is not None and evicted_lock.users == 0:
                        _analysis_locks.pop(evicted, None)
                ttl = _DEGRADED_CACHE_SECONDS if analysis.get("analysis_status") == "degraded" else _CACHE_SECONDS
                _analysis_cache[key] = (analysis, time.time() + ttl)
            return analysis
    finally:
        with _cache_guard:
            analysis_lock.users -= 1
            if analysis_lock.users == 0 and key not in _analysis_cache:
                if _analysis_locks.get(key) is analysis_lock:
                    _analysis_locks.pop(key, None)


def _article_id(item: dict) -> str:
    identity = "|".join((str(item.get("title") or ""), str(item.get("source") or ""))).strip("|")
    if not identity:
        identity = str(item.get("url") or "")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def build_position_news(session: dict, news_payload: dict, analysis: dict) -> dict:
    """Build the public contract from authoritative session and raw news data."""
    side = str(session.get("position_side") or "").strip().lower()
    if side not in {"long", "short"}:
        raise ValueError("등록 매크로의 포지션 방향이 올바르지 않아요.")

    raw_items = list(news_payload.get("items") or [])
    analyzed_items = list(analysis.get("items") or [])
    items = []
    for index, raw in enumerate(raw_items):
        assessed = analyzed_items[index] if index < len(analyzed_items) else classifier.classify_headline(raw.get("title", ""))
        sentiment = assessed.get("sentiment") if assessed.get("sentiment") in classifier.SENTIMENTS else "unclear"
        effect = classifier.position_effect(sentiment, side)
        items.append({
            "id": _article_id(raw),
            "title": str(raw.get("title") or ""),
            "source": str(raw.get("source") or ""),
            "url": str(raw.get("url") or ""),
            "published": raw.get("published"),
            "asset_sentiment": sentiment,
            "position_effect": effect,
            "position_label": classifier.position_label(effect, side),
            "reason": str(assessed.get("reason") or "")[:180],
            "confidence": assessed.get("confidence") if assessed.get("confidence") in {"low", "medium", "high"} else "low",
        })

    market_symbol = str(session.get("symbol") or "").upper()
    asset_symbol = str(news_payload.get("symbol") or market_symbol).upper()
    coin_name = str(news_payload.get("coin_name") or asset_symbol or "선택 종목")
    snapshot_id = _fingerprint(asset_symbol, raw_items)[:20]
    return {
        "feature_key": FEATURE_KEY,
        "feature_version": classifier.FEATURE_VERSION,
        "context": {
            "session_id": session.get("session_id"),
            "user_macro_id": session.get("user_macro_id"),
            "market_symbol": market_symbol,
            "asset_symbol": asset_symbol,
            "coin_name": coin_name,
            "position_side": side,
            "in_position": bool(session.get("in_position", False)),
            "position_basis": "macro_configuration",
        },
        "overview": {
            "text": str(analysis.get("overview") or ""),
            "scope": "headlines_only",
        },
        "snapshot_id": snapshot_id,
        "items": items,
        "analysis_status": analysis.get("analysis_status") or "degraded",
        "analysis_source": analysis.get("analysis_source") or "rule",
        "ai": bool(analysis.get("ai", False)),
        "updated_at": news_payload.get("updated_at"),
        "refresh_seconds": int(news_payload.get("refresh_seconds") or _CACHE_SECONDS),
        "disclaimer": _DISCLAIMER,
    }


def get_position_news(session: dict, *, requester_id: int | None = None) -> dict:
    """Fetch headline data, analyze it once, then apply this session's side."""
    news_payload = news_mod.get_coin_news(str(session.get("symbol") or ""))
    items = list(news_payload.get("items") or [])
    asset_symbol = str(news_payload.get("symbol") or session.get("symbol") or "").upper()
    coin_name = str(news_payload.get("coin_name") or asset_symbol or "선택 종목")
    analysis = _analysis_for(asset_symbol, coin_name, items, requester_id)
    return build_position_news(session, news_payload, analysis)
