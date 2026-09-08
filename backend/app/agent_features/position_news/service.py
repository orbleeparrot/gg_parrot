"""Read-only service for applying one session's side to shared ticker news."""
from __future__ import annotations

import hashlib
import os
import re
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
    if item.get("content_type") == "community":
        identity = "|".join((
            "community", str(item.get("source") or "Binance Square"),
            str(item.get("community_post_id") or item.get("url") or ""),
        ))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    identity = "|".join(
        (
            str(item.get("original_title") or item.get("title") or ""),
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
                _article_id(item) if item.get("content_type") == "community"
                else f"{item.get('title') or ''}|{item.get('source') or ''}"
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
    _include_community_body: bool = False,
) -> dict:
    """Project one shared analysis into an authoritative session direction."""
    side = str(session.get("position_side") or "").strip().lower()
    if side not in {"long", "short"}:
        raise ValueError("등록 매크로의 포지션 방향이 올바르지 않아요.")

    raw_items = list(news_payload.get("items") or [])
    analyzed_items = list(analysis.get("items") or [])
    items = []
    for index, raw in enumerate(raw_items):
        is_community = raw.get("content_type") == "community"
        is_historical = bool(raw.get("published")) and not news_mod._within_live_news_window(raw)
        assessed = {} if is_community else (
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
        if is_historical or is_community:
            # History and unverified community opinions do not establish a
            # directional observation about the user's current position.
            sentiment, effect = "unclear", "unclear"
        items.append(
            {
                "id": _article_id(raw),
                "title": str(raw.get("title") or ""),
                **(
                    {"original_title": str(raw.get("original_title"))}
                    if raw.get("original_title")
                    else {}
                ),
                "source": str(raw.get("source") or ""),
                **({
                    "content_type": "community",
                    "community_post_id": str(raw.get("community_post_id") or ""),
                    "author": str(raw.get("author") or ""),
                    **{key: raw[key] for key in (
                        "community_summary", "community_summary_status", "community_summary_partial",
                    ) if key in raw},
                    **({key: raw[key] for key in news_mod._COMMUNITY_BODY_FIELDS if key in raw}
                       if _include_community_body else {}),
                } if is_community else {}),
                "url": str(raw.get("url") or ""),
                "published": raw.get("published"),
                "is_historical": is_historical,
                "asset_sentiment": sentiment,
                "position_effect": effect,
                "summary": str(
                    "커뮤니티 작성자의 의견이며 포지션 영향은 확인되지 않았어요."
                    if is_community else assessed.get("summary") or raw.get("excerpt") or ""
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
    community_count = sum(item.get("content_type") == "community" for item in items)
    article_count = len(items) - community_count
    only_community = bool(community_count) and not article_count
    overview = {"text": str(analysis.get("overview") or ""), "scope": "articles"}
    if community_count:
        overview = {
            "text": (
                f"뉴스 {article_count}건과 커뮤니티 게시글 {community_count}건을 확인했어요. "
                if article_count else f"커뮤니티 게시글 {community_count}건을 확인했어요. "
            ) + "커뮤니티 글은 작성자의 의견입니다.",
            "scope": "mixed_sources" if article_count else "community_posts",
        }
    return {
        "feature_key": FEATURE_KEY,
        "feature_version": classifier.FEATURE_VERSION,
        "context": _session_context(
            session,
            asset_symbol=asset_symbol,
            coin_name=coin_name,
            side=side,
        ),
        "overview": overview,
        "snapshot_id": (
            snapshot_id[:20]
            if snapshot_id
            else _snapshot_id(asset_symbol, raw_items)
        ),
        "items": items,
        "analysis_status": analysis.get("analysis_status") or "degraded",
        "analysis_source": "community" if only_community else analysis.get("analysis_source") or "rule",
        "ai": bool(analysis.get("ai", False)) and not only_community,
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
    """Read the central snapshot and localize its deduplicated titles."""
    asset = news_mod.asset_from_market_symbol(str(session.get("symbol") or ""))
    # Preserve the one-argument seam used by small unit fakes when no request
    # session is supplied; authenticated routes pass their shared session.
    stored = _load_latest_snapshot(asset) if db is None else _load_latest_snapshot(asset, db)
    if stored is None:
        payload = build_pending_position_news(session)
        from .repository import get_collection_state
        collection = get_collection_state(asset, db)
        if collection:
            payload["collection"].update(collection)
            status = collection["status"]
            if status in {"empty", "error"}:
                payload["analysis_status"] = status
                payload["overview"]["text"] = (
                    f"{asset} 관련 뉴스를 검색했지만 아직 기사를 찾지 못했어요. 자동으로 다시 확인합니다."
                    if status == "empty" else "뉴스 소스 연결에 실패했어요. 자동으로 재시도합니다."
                )
        return payload

    # Release the request read transaction before joining shared translation
    # work; a provider wait must not pin a Postgres connection.
    if db is not None:
        db.rollback()
    payload = build_position_news(
        session,
        stored["news_payload"],
        stored["analysis"],
        snapshot_id=str(stored.get("snapshot_id") or ""),
        _include_community_body=True,
    )
    # Project sentiment before filtering. An unfinished first article must not
    # shift the analysis attached to the second article when it becomes visible.
    # This also gives old/reused raw snapshots a translation retry path.
    payload = news_mod._localize_news_payload(payload)
    for item in payload["items"]:
        summary = str(item.get("summary") or "").strip()
        if not re.search(r"[가-힣]", summary) or news_mod._title_needs_korean_translation(summary):
            item["summary"] = item["title"]
    overview = str((payload.get("overview") or {}).get("text") or "")
    if not re.search(r"[가-힣]", overview) or news_mod._title_needs_korean_translation(overview):
        titles = [f"‘{item['title']}’" for item in payload["items"][:2]]
        text = (", ".join(titles) + " 소식이 확인됐어요." if titles else
                "뉴스를 한국어로 번역하고 있어요. 완료되는 대로 표시합니다."
                if payload.get("translation", {}).get("pending_count") else
                "관련 최신 뉴스를 확인하고 있어요.")
        payload["overview"] = {"text": text, "scope": "headlines_only"}
    collection = dict(stored.get("collection") or {})
    last_success_ms = int(collection.get("last_success_ms") or 0)
    last_observed_ms = max(last_success_ms, int(collection.get("last_attempt_ms") or 0)
                           if collection.get("status") == "empty" else 0)
    age_ms = max(0, int(time.time() * 1000) - last_observed_ms)
    collection["freshness"] = (
        "stale"
        if not last_observed_ms or age_ms > _STALE_SECONDS * 1000
        else "fresh"
    )
    collection["age_seconds"] = age_ms // 1000 if last_observed_ms else None
    collection["stale_after_seconds"] = _STALE_SECONDS
    payload["collection"] = collection
    if not payload["items"] and not payload.get("translation", {}).get("pending_count") and collection.get("status") == "empty":
        payload["analysis_status"] = "empty"
        payload["overview"] = {"text": "관련 기사를 최대 5년 범위까지 확인했지만 아직 찾지 못했어요. 자동으로 다시 확인합니다.",
                               "scope": "headlines_only"}
    return payload
