"""Read-only service for applying one session's side to shared ticker news."""
from __future__ import annotations

import hashlib
import os
import time

from sqlmodel import Session

from ... import news as news_mod
from . import classifier

FEATURE_KEY = "position_news"
_STALE_SECONDS = max(
    60,
    int(os.environ.get("POSITION_NEWS_STALE_SECONDS", "900")),
)
_REFRESH_SECONDS = max(
    60,
    int(os.environ.get("POSITION_NEWS_COLLECTION_SECONDS", "300")),
)
_DISCLAIMER = (
    "뉴스 영향은 실제 가격 반응과 다를 수 있습니다. 헤드라인을 바탕으로 한 정보 제공이며 "
    "매수·매도 등 매매 지시가 아닙니다. 반드시 원문을 확인하세요."
)


def _article_id(item: dict) -> str:
    identity = "|".join(
        (
            str(item.get("title") or ""),
            str(item.get("source") or ""),
        )
    ).strip("|")
    if not identity:
        identity = str(item.get("url") or "")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _snapshot_id(asset_symbol: str, items: list[dict]) -> str:
    identity = "|".join(
        [
            asset_symbol,
            *[
                f"{item.get('title') or ''}|{item.get('source') or ''}"
                for item in items
            ],
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _session_context(
    session: dict,
    *,
    asset_symbol: str,
    coin_name: str,
    side: str,
) -> dict:
    return {
        "session_id": session.get("session_id"),
        "user_macro_id": session.get("user_macro_id"),
        "market_symbol": str(session.get("symbol") or "").upper(),
        "asset_symbol": asset_symbol,
        "coin_name": coin_name,
        "position_side": side,
        "in_position": bool(session.get("in_position", False)),
        "position_basis": "macro_configuration",
    }


def build_position_news(
    session: dict,
    news_payload: dict,
    analysis: dict,
    *,
    snapshot_id: str = "",
) -> dict:
    """Project one shared analysis into an authoritative session direction."""
    side = str(session.get("position_side") or "").strip().lower()
    if side not in {"long", "short"}:
        raise ValueError("등록 매크로의 포지션 방향이 올바르지 않아요.")

    raw_items = list(news_payload.get("items") or [])
    analyzed_items = list(analysis.get("items") or [])
    items = []
    for index, raw in enumerate(raw_items):
        assessed = (
            analyzed_items[index]
            if index < len(analyzed_items)
            else classifier.classify_headline(raw.get("title", ""))
        )
        sentiment = (
            assessed.get("sentiment")
            if assessed.get("sentiment") in classifier.SENTIMENTS
            else "unclear"
        )
        effect = classifier.position_effect(sentiment, side)
        items.append(
            {
                "id": _article_id(raw),
                "title": str(raw.get("title") or ""),
                "source": str(raw.get("source") or ""),
                "url": str(raw.get("url") or ""),
                "published": raw.get("published"),
                "asset_sentiment": sentiment,
                "position_effect": effect,
                "summary": str(
                    assessed.get("summary") or raw.get("excerpt") or ""
                )[:180],
                "confidence": (
                    assessed.get("confidence")
                    if assessed.get("confidence") in {"low", "medium", "high"}
                    else "low"
                ),
            }
        )

    market_symbol = str(session.get("symbol") or "").upper()
    payload_asset = news_mod.canonical_asset_symbol(
        str(news_payload.get("symbol") or "")
    )
    asset_symbol = payload_asset or news_mod.asset_from_market_symbol(
        market_symbol
    )
    coin_name = str(
        news_payload.get("coin_name") or asset_symbol or "선택 종목"
    )
    return {
        "feature_key": FEATURE_KEY,
        "feature_version": classifier.FEATURE_VERSION,
        "context": _session_context(
            session,
            asset_symbol=asset_symbol,
            coin_name=coin_name,
            side=side,
        ),
        "overview": {
            "text": str(analysis.get("overview") or ""),
            "scope": "articles",
        },
        "snapshot_id": (
            snapshot_id[:20]
            if snapshot_id
            else _snapshot_id(asset_symbol, raw_items)
        ),
        "items": items,
        "analysis_status": analysis.get("analysis_status") or "degraded",
        "analysis_source": analysis.get("analysis_source") or "rule",
        "ai": bool(analysis.get("ai", False)),
        "updated_at": news_payload.get("updated_at"),
        "refresh_seconds": int(
            news_payload.get("refresh_seconds") or _REFRESH_SECONDS
        ),
        "disclaimer": _DISCLAIMER,
    }


def build_pending_position_news(session: dict) -> dict:
    side = str(session.get("position_side") or "").strip().lower()
    if side not in {"long", "short"}:
        raise ValueError("등록 매크로의 포지션 방향이 올바르지 않아요.")
    market_symbol = str(session.get("symbol") or "").upper()
    asset = news_mod.asset_from_market_symbol(market_symbol)
    return {
        "feature_key": FEATURE_KEY,
        "feature_version": classifier.FEATURE_VERSION,
        "context": _session_context(
            session,
            asset_symbol=asset,
            coin_name=asset or "선택 종목",
            side=side,
        ),
        "overview": {
            "text": (
                f"{asset or '선택 종목'} 공용 뉴스 수집을 준비하고 있어요. "
                "첫 중앙 수집이 끝나면 자동으로 표시됩니다."
            ),
            "scope": "headlines_only",
        },
        "snapshot_id": "",
        "items": [],
        "analysis_status": "pending",
        "analysis_source": "central_collector",
        "ai": False,
        "updated_at": None,
        "refresh_seconds": _REFRESH_SECONDS,
        "collection": {
            "status": "pending",
            "freshness": "pending",
            "stale_after_seconds": _STALE_SECONDS,
        },
        "disclaimer": _DISCLAIMER,
    }


def _load_latest_snapshot(symbol: str, db: Session | None = None) -> dict | None:
    from .repository import get_latest_snapshot

    return get_latest_snapshot(symbol, db=db)


def get_position_news(session: dict, db: Session | None = None) -> dict:
    """Read the central snapshot only; never fetch RSS or invoke AI here."""
    asset = news_mod.asset_from_market_symbol(str(session.get("symbol") or ""))
    # Preserve the one-argument seam used by small unit fakes when no request
    # session is supplied; authenticated routes pass their shared session.
    stored = _load_latest_snapshot(asset) if db is None else _load_latest_snapshot(asset, db)
    if stored is None:
        return build_pending_position_news(session)

    payload = build_position_news(
        session,
        stored["news_payload"],
        stored["analysis"],
        snapshot_id=str(stored.get("snapshot_id") or ""),
    )
    collection = dict(stored.get("collection") or {})
    last_success_ms = int(collection.get("last_success_ms") or 0)
    age_ms = max(0, int(time.time() * 1000) - last_success_ms)
    collection["freshness"] = (
        "stale"
        if not last_success_ms or age_ms > _STALE_SECONDS * 1000
        else "fresh"
    )
    collection["age_seconds"] = age_ms // 1000 if last_success_ms else None
    collection["stale_after_seconds"] = _STALE_SECONDS
    payload["collection"] = collection
    return payload
