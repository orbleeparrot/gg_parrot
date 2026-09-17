"""Prepared public news reads and bounded background collection.

HTTP readers only read durable article projections. They can wake an already
running worker through memory, but never fetch, translate or claim DB work.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import threading
import time
import uuid

from sqlalchemy import BigInteger, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Field, SQLModel, select

from . import news, news_images
from .collector_runs import RunRecorder, bump_source
from .db import get_session
from .agent_features.position_news import articles, collector
from .public_news_cache import responses as _responses

logger = logging.getLogger(__name__)
_runtime = None
_INTERNAL = frozenset((*news._COMMUNITY_BODY_FIELDS, "assessment", "analysis"))
# 종목 수집기가 리스를 못 잡았거나(skipped) 다른 실행에 밀려 결과를 버린(superseded) 회차. 관리자 표에는 '건너뜀'으로
# 남기고 소스 호출·성공으로는 세지 않는다.
_NOT_COLLECTED = frozenset({"skipped", "superseded"})
# 공개 워밍 스레드 표시. run_parallel 이 copy_context 로 소스 로더 스레드에도 넘기므로 워밍 안의 모든 호출에 보인다.
_warming = contextvars.ContextVar("public_news_warming", default=False)


def _skip_source_records_while_warming(record):
    """news._record_collector_sources 를 감싼다: 공개 워밍(_collect_ticker) 중 온 호출은 버린다.

    news.py 의 소스 누적 훅은 호출 주체를 모르고 position_news 엔진으로만 쓴다. 그대로 두면 같은 fetch 가
    position_news 소스(google·coindesk…)와 public_news/ticker 로 두 번 세어지고, 웹 프로세스가 워밍마다 소스 수만큼
    upsert 를 더 한다 — 그 회차는 _record_refresh 가 이미 남긴다. 엔진 표시를 collector_runs 에 두는 게 제자리지만
    이 모듈만 고치려 훅을 감싼다. 워밍 밖의 호출은 그대로 통과한다.
    """
    def guarded(sources):
        if _warming.get():
            return
        record(sources)
    guarded.__wrapped__ = record
    return guarded


if not hasattr(news._record_collector_sources, "__wrapped__"):
    news._record_collector_sources = _skip_source_records_while_warming(news._record_collector_sources)


class PublicNewsLease(SQLModel, table=True):
    scope: str = Field(primary_key=True)
    token: str = ""
    lease_until_ms: int = Field(default=0, sa_type=BigInteger)
    next_run_ms: int = Field(default=0, sa_type=BigInteger)


def claim_work(scope: str, *, now_ms=None) -> str | None:
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    token = uuid.uuid4().hex
    with get_session() as db:
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        db.exec(insert(PublicNewsLease).values(scope=scope).on_conflict_do_nothing(
            index_elements=[PublicNewsLease.scope]))
        result = db.exec(update(PublicNewsLease).where(
            PublicNewsLease.scope == scope, PublicNewsLease.next_run_ms <= millis,
            or_(PublicNewsLease.token == "", PublicNewsLease.lease_until_ms <= millis),
        ).values(token=token, lease_until_ms=millis + 120_000))
        db.commit()
        return token if result.rowcount == 1 else None


def renew_work(scope: str, token: str) -> None:
    with get_session() as db:
        result = db.exec(update(PublicNewsLease).where(PublicNewsLease.scope == scope,
                                                      PublicNewsLease.token == token)
                .values(lease_until_ms=int(time.time() * 1000) + 120_000))
        db.commit()
        if result.rowcount != 1:
            raise RuntimeError("public news preparation lease lost")


def finish_work(scope: str, token: str, *, retry_seconds=300) -> None:
    with get_session() as db:
        db.exec(update(PublicNewsLease).where(PublicNewsLease.scope == scope,
                                             PublicNewsLease.token == token)
                .values(token="", lease_until_ms=0,
                        next_run_ms=int((time.time() + retry_seconds) * 1000)))
        db.commit()


def request_refresh(scope: str) -> None:
    if _runtime is not None:
        _runtime.request(scope)


def _public_payload(scope: str, feed: dict | None) -> dict:
    market = scope == "MARKET"
    name = news._COIN_KO.get(scope, scope)
    result = news._envelope([], overview=None,
                            label="코인 시장·규제" if market else f"{name} 뉴스", query="")
    if not market:
        result.update(symbol=scope, coin_name=name)
    if feed is None:
        result.update(translation={"status": "partial", "pending_count": 0, "retry_after_seconds": 3},
                      collection={"status": "pending"}, refresh_seconds=3, data_source="prepared_db",
                      cursor=0, image_status="ready")
        return result
    result.update({key: value for key, value in feed.items()
                   if key not in {"analysis", "items", "has_more", "reset"}})
    within_window = news._within_live_news_window if market else news._within_coin_news_window
    items = [{key: value for key, value in item.items() if key not in _INTERNAL}
             for item in feed.get("items", []) if within_window(item)]
    # Sorting a bounded prepared article list does not fetch or analyze news.
    result["items"] = news._sort_news_items_newest_first(items)
    result["data_source"] = "prepared_db"
    pending = (result.get("translation") or {}).get("status") == "partial"
    summary_pending = sum(item.get("content_type") == "community"
                          and item.get("community_summary_status") == "pending" for item in items)
    result["community_summaries"] = {"status": "partial" if summary_pending else "ready",
                                     "pending_count": summary_pending, "retry_after_seconds": 3}
    result["collection"] = {"status": result.pop("collection_status", "ready")}
    result["refresh_seconds"] = 3 if pending or summary_pending or result["collection"]["status"] == "pending" else 30
    result.setdefault("image_status", "ready")
    if market and news_images.enabled():
        result["image_status"] = "pending" if any(
            item.get("url") and not (item.get("image") or item.get("image_resolved")) for item in items
        ) else "ready"
    if not market:
        result = news._with_news_history(result)
    return result


def get_market_news() -> dict:
    request_refresh("MARKET")
    return _cached_news("MARKET")


def get_coin_news(symbol: str) -> dict:
    scope = news.asset_from_market_symbol(symbol)
    if not scope:
        raise ValueError("코인 심볼이 올바르지 않아요.")
    request_refresh(scope)
    return _cached_news(scope)


def _cached_news(scope: str) -> dict:
    result, state = _responses.get_or_load(scope, lambda: _read_news(scope), ttl=1, stale_ttl=9)
    if state == "stale":
        result["stale"] = True
    return result


def _read_news(scope: str) -> dict:
    with get_session() as db:
        feed = articles.read_article_feed(scope, limit=100, include_analysis=False, db=db)
        if feed is None and scope != "MARKET":
            # Rolling upgrades may start with only the old durable snapshot.
            # Expose its already translated items; never translate during GET.
            from .agent_features.position_news.repository import get_latest_snapshot
            stored = get_latest_snapshot(scope, db=db)
            if stored:
                raw = stored.get("news_payload") or {}
                ready = [item for item in raw.get("items", []) if articles._ready(item)]
                feed = {**raw, "items": ready,
                        "translation": {"status": "partial" if len(ready) < len(raw.get("items", [])) else "ready",
                                        "pending_count": len(raw.get("items", [])) - len(ready), "retry_after_seconds": 3}}
    return _public_payload(scope, feed)


def collect_market(*, claim_token=None) -> dict:
    """Publish raw/ready articles before summary and optional image work."""
    as_of = news._kst_date()
    overview = news._load_durable_market_summary(as_of)
    def publish(payload):
        with get_session() as db:
            if claim_token:
                lease = db.exec(select(PublicNewsLease).where(PublicNewsLease.scope == "MARKET")
                                .with_for_update()).one_or_none()
                if lease is None or lease.token != claim_token or lease.lease_until_ms <= int(time.time() * 1000):
                    raise RuntimeError("stale market news publication")
            articles.upsert_articles("MARKET", payload.get("items", []), payload=payload, db=db)

    def source_ready(payload):
        publish({**payload, "as_of": as_of, "overview": overview,
                 "ai": bool(overview), "collection_status": "pending"})

    raw = news._fetch_public_news_payload(on_progress=source_ready)
    raw.update(as_of=as_of, overview=overview, ai=bool(overview), collection_status="pending")
    publish(raw)
    result = news._localize_news_payload(raw, on_progress=publish)
    publish(result)
    if overview is None:
        overview = news._summarize(result.get("items", []), label="코인 시장·규제")
        if overview:
            news._store_durable_market_summary(raw["as_of"], overview)
    result.update(overview=overview, ai=bool(overview), collection_status="ready")
    result["image_status"] = news_images.attach(result.get("items", []))
    publish(result)
    if result["image_status"] == "pending":
        # Image resolution stays outside HTTP and independently publishes each
        # result; article text and the overview are already visible.
        news_images.ensure_resolving(result["items"], on_ready=lambda item:
            articles.update_article_image("MARKET", articles.article_id(item), item))
    return result


def _collect_ticker(scope: str) -> dict:
    """공개 워밍은 브라우저·AI 없이 종목 수집기를 부른다. 저장한 기사 수는 결과에 없어 fetch 응답에서 센다."""
    counted = {"items": 0}

    def fetch(symbol, **kwargs):
        payload = collector._call_with_progress(news.fetch_coin_news_for_collector, symbol, **kwargs)
        counted["items"] = len(payload.get("items") or [])
        return payload

    # The collector's ticker lease is shared with the agent worker.
    # Public prewarming performs no browser crawl or direction AI.
    flag = _warming.set(True)
    try:
        result = collector.collect_ticker(scope, allow_ai=False, fetcher=fetch, enricher=lambda _symbol, payload: payload)
    finally:
        _warming.reset(flag)
    return {**result, "item_count": counted["items"]}


def _item_count(result) -> int:
    if not isinstance(result, dict):
        return 0
    items = result.get("items")
    return len(items) if isinstance(items, list) else int(result.get("item_count") or 0)


def _record_refresh(run: RunRecorder, scope: str) -> None:
    """범위 한 번의 결과를 관리자 표에 남긴다(실행 기록 + market/ticker 소스 누적). 절대 raise 하지 않는다."""
    try:
        run.finish()
        if run.status == "skipped":
            return  # 소스를 부르지 않은 회차 — 호출·성공으로 세지 않는다
        failed = run.status == "error"
        bump_source("public_news", "market" if scope == "MARKET" else "ticker", calls=1, targets=1, items=run.items,
                    failures=1 if failed else 0, error=run.error, success_ms=0 if failed else run.finished_ms)
    except Exception:
        logger.warning("Public news run record skipped: scope=%s", scope)


class PublicNewsRuntime:
    def __init__(self):
        self.pending = {"MARKET": 0.0}
        self.last_requested = {"MARKET": time.monotonic()}
        self.lock = threading.Lock()
        self.active = {}
        self.loop = None
        self.changed = None
        self.task = None
        self.discovery = None
        self.stopping = False

    def request(self, scope):
        if not scope or len(scope) > 32 or not scope.isalnum():
            return
        with self.lock:
            if scope in self.pending or len(self.pending) < 200:
                self.last_requested[scope] = time.monotonic()
                self.pending.setdefault(scope, 0.0)
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.changed.set)

    def start(self):
        self.loop = asyncio.get_running_loop()
        self.changed = asyncio.Event()
        self.task = self.loop.create_task(self.run(), name="public-news-preparation")
        self.discovery = self.loop.create_task(self.discover(), name="public-news-hot-coins")

    async def discover(self):
        from . import hotcoins
        while not self.stopping:
            try:
                payload = await asyncio.to_thread(hotcoins.get_hot_coins, 10)
                for coin in payload.get("coins", []):
                    self.request(news.asset_from_market_symbol(coin["symbol"]))
            except Exception:
                logger.warning("Public news prewarm discovery deferred")
            await asyncio.sleep(300)

    async def refresh(self, scope):
        token = await asyncio.to_thread(claim_work, scope)
        if token is None:
            return 30
        retry_seconds = 300
        worker = None
        run = RunRecorder("public_news")  # 관리자 표용 실행 기록. 리스를 못 잡은 회차는 실행이 아니라 세지 않는다.
        try:
            if scope == "MARKET":
                worker = asyncio.create_task(asyncio.to_thread(collect_market, claim_token=token))
            else:
                worker = asyncio.create_task(asyncio.to_thread(_collect_ticker, scope))
            while not worker.done():
                done, _ = await asyncio.wait({worker}, timeout=20)
                if not done:
                    await asyncio.to_thread(renew_work, scope, token)
            result = await worker
            status = str(result.get("status") or "ready") if isinstance(result, dict) else "ready"
            collected = status not in _NOT_COLLECTED
            run.report({"scope": scope, "status": status}, status=None if collected else "skipped",
                       targets=1 if collected else 0, items=_item_count(result) if collected else 0)
        except Exception as exc:
            retry_seconds = 30
            run.fail(exc)
            logger.warning("Public news preparation deferred: scope=%s reason=%s", scope, type(exc).__name__)
        finally:
            try:
                if worker is not None and not worker.done():
                    await asyncio.shield(worker)
            finally:
                await asyncio.to_thread(finish_work, scope, token, retry_seconds=retry_seconds)
                await asyncio.to_thread(_record_refresh, run, scope)
        return retry_seconds

    async def run(self):
        while not self.stopping:
            self.changed.clear()
            for scope, task in list(self.active.items()):
                if task.done():
                    self.active.pop(scope)
                    delay = 30
                    try:
                        delay = task.result() or 300
                    except Exception:
                        logger.exception("Public news task failed")
                    with self.lock:
                        self.pending[scope] = time.monotonic() + delay
            with self.lock:
                expired = [scope for scope in self.last_requested if scope != "MARKET"
                           and scope not in self.active
                           and time.monotonic() - self.last_requested[scope] > 3600]
                for scope in expired:
                    self.pending.pop(scope, None)
                    self.last_requested.pop(scope, None)
                due = [scope for scope, at in self.pending.items()
                       if at <= time.monotonic() and scope not in self.active]
            for scope in due[:max(0, 3 - len(self.active))]:
                self.active[scope] = asyncio.create_task(self.refresh(scope))
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        self.stopping = True
        self.changed.set()
        self.discovery.cancel()
        await asyncio.gather(self.discovery, return_exceptions=True)
        await self.task
        await asyncio.gather(*self.active.values(), return_exceptions=True)


def start():
    global _runtime
    if os.environ.get("PUBLIC_NEWS_EMBEDDED_ENABLED", "true").lower() in {"0", "false", "no"}:
        return
    if _runtime is None:
        _runtime = PublicNewsRuntime()
        _runtime.start()


async def stop():
    global _runtime
    current, _runtime = _runtime, None
    if current is not None:
        await current.stop()
