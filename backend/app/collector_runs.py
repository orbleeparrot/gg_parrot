"""수집 엔진 실행 기록 — 관리자 '뉴스 수집 현황' 탭의 크롤링 엔진·시간대별·소스별 표의 데이터.

두 표에 쓴다. ``CollectorRun`` 은 실행 한 번(Prefect flow 한 회차, 공개 뉴스 범위 한 번, 보강 배치 한 회차)의
요약이고, ``CollectorSourceDaily`` 는 소스(Google News RSS · CoinDesk API · Playwright …)별 하루 누적이다.
flow 는 지금도 요약 dict 를 stdout 에 찍지만 Prefect 로그는 화면에서 못 읽으니 같은 값을 표에 남긴다.

기록은 모두 best-effort 다: 표가 없거나 DB 가 죽어도 수집 흐름을 절대 막지 않는다(예외는 삼키고 1분에 한 번만
경고). news.py 는 db 를 모듈 수준에서 import 하지 않으므로 세션은 함수 안에서 늦게 가져온다.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import case, delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))
WARN_INTERVAL_SECONDS = 60.0
RUN_RETENTION_DAYS = 14
PRUNE_EVERY_MS = 3_600_000
SUMMARY_MAX_BYTES = 8 * 1024
ERROR_MAX_CHARS = 300
_SUMMARY_LIST_MAX = 20

ENGINE_POSITION_NEWS = "position_news"
ENGINE_WHALE = "whale_activity"
ENGINE_ONCHAIN = "onchain_holders"
ENGINE_PUBLIC_NEWS = "public_news"
ENGINE_ENRICHMENT = "article_enrichment"
ENGINE_COINDESK_PROBE = "coindesk_probe"

# 화면 순서 그대로. 기대 주기(초)는 '지연' 판정의 기준이고 0 이면 수동 실행이라 지연을 보지 않는다.
# 고래 수집 주기는 저장소 상수(30초)를 읽는다 — 값이 바뀌면 여기 판정도 같이 따라간다.
ENGINES: tuple[tuple[str, str, str, Optional[int]], ...] = (
    (ENGINE_POSITION_NEWS, "종목 뉴스 수집", "Prefect · gg-parrot-position-news", 60),
    (ENGINE_WHALE, "고래 거래 수집", "Prefect · gg-parrot-whale-activity", None),
    (ENGINE_ONCHAIN, "온체인 보유 수집", "Prefect · gg-parrot-onchain-holders", 60),
    # 공개 뉴스의 '실행' 은 스케줄러 회차가 아니라 범위(MARKET · 종목) 갱신 한 번이다 — 문구에 적어 둔다.
    (ENGINE_PUBLIC_NEWS, "공개 뉴스 (코인동향)", "웹 · MARKET + 종목별 · 범위마다 1회", 300),
    # 보강은 5초마다 스캔하지만 실행 행은 일감이 있던 회차만 남긴다 — 빈 회차까지 쌓지 않는다.
    (ENGINE_ENRICHMENT, "기사 보강 (AI 요약)", "웹 · 5초 스캔(일감 있을 때만 기록)", 120),
    (ENGINE_COINDESK_PROBE, "CoinDesk 소스 점검", "Prefect · 수동", 0),
)
STATUS_LABELS = {
    "ok": "정상", "delayed": "지연", "error": "오류", "idle": "대기", "stalled": "정체 쉼",
    # 실행 행에 남는 원래 상태 — flow 가 준 run_status 를 그대로 저장한다(A9). 화면 상태로는 '오류(소스 실패)'.
    "degraded": "소스 실패", "source_unavailable": "소스 실패", "skipped": "건너뜀", "skipped_late": "건너뜀",
    "cached": "캐시 응답",
}
# 소스를 부르지 않은 회차 — runs_today(일한 회차)에서 빼고 skipped_today 로 따로 센다.
_SKIPPED_STATUSES = frozenset({"skipped", "skipped_late"})
# 캐시·스냅샷으로 응답해 HTTP 요청이 없던 공개 뉴스 회차(public_news 만). 소스 행을 만들지 않고 따로 센다.
STATUS_CACHED = "cached"
# 최근 실행이 이 상태면 엔진은 '오류(소스 실패)' 다 — 예외 없이 끝났어도 소스가 죽어 있던 회차.
_SOURCE_FAILED_STATUSES = frozenset({"degraded", "source_unavailable"})
# 보강 정체가 이 배수 × 정체 쉼 시간보다 오래 이어지면 '정체 지속' 오류다(기본 60초 × 10 = 10분).
ENRICHMENT_STALL_ERROR_MULTIPLIER = 10

# 소스 키(CollectorSourceDaily.source) → 화면 라벨. 순서가 표 순서이고 모르는 키는 그대로 보여준다.
SOURCE_LABELS: dict[str, str] = {
    "google": "Google News RSS",
    "coindesk": "CoinDesk RSS",
    "coindesk_api": "CoinDesk API (유료)",
    "binance_square": "Binance Square",
    "decrypt_rss": "Decrypt RSS",
    "cryptoslate_rss": "CryptoSlate RSS",
    "openeden": "OpenEden",
    "browser": "브라우저 보강 (Playwright)",
    # 온체인·고래 엔진의 소스 — 소스별 표에 모든 엔진이 들어간다(A7). 키는 flow 결과의 source/market 값 그대로.
    "blockscout": "Blockscout",
    "xrpscan": "XRPScan",
    "spot": "Binance 현물",
    "futures": "Binance 선물",
}
# news.py 의 envelope 소스 이름 → 소스 키. 이름은 저장 스냅샷·Prefect 로그와 공유되므로 바꾸지 않고 여기서 접는다.
_SOURCE_KEYS = {
    "google_news_rss": "google",
    "coindesk_rss": "coindesk",
    "coindesk_news_api": "coindesk_api",
    "binance_square": "binance_square",
    "openeden_official_rss": "openeden",
}
# 시도하지 않은 소스(설정으로 꺼짐·이번 회차 건너뜀)는 호출로 세지 않는다.
_NOT_ATTEMPTED = {"disabled", "skipped"}
_FAILED = {"error", "rate_limited", "budget_exhausted", "call_budget_exhausted"}
_SUCCEEDED = {"ready", "empty", "partial"}
_SECRET_RE = re.compile(r"(?i)(authorization|api[_-]?key|token|password|secret)(\s*[:=]\s*)\S+")

_warn_lock = threading.Lock()
_last_warn_monotonic: Optional[float] = None
_prune_lock = threading.Lock()
_last_prune_ms = 0


# --- 시각 ------------------------------------------------------------------------
def _now_ms(now_ms: Optional[int] = None) -> int:
    return int(now_ms if now_ms is not None else time.time() * 1000)


def day_kst(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(_KST).strftime("%Y-%m-%d")


def _hour_kst(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(_KST).hour


def _warn_throttled(message: str, error: BaseException) -> None:
    global _last_warn_monotonic
    now = time.monotonic()
    with _warn_lock:
        if _last_warn_monotonic is not None and now - _last_warn_monotonic < WARN_INTERVAL_SECONDS:
            return
        _last_warn_monotonic = now
    logger.warning("[collector_runs] %s: %s: %s", message, type(error).__name__, error)


def _insert_for(db):
    return pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


def _session():
    from .db import get_session

    return get_session()


# --- 값 다듬기 ----------------------------------------------------------------------
def clean_error(value, *, limit: int = ERROR_MAX_CHARS) -> str:
    """오류 문자열 한 줄로: 여러 줄 스택은 첫 줄만, 키·토큰처럼 보이는 값은 가리고, 길이를 자른다."""
    if isinstance(value, BaseException):
        text = str(value).strip() or type(value).__name__
        value = f"{type(value).__name__}: {text}" if str(value).strip() else text
    lines = str(value or "").strip().splitlines()
    text = lines[0].strip() if lines else ""
    return _SECRET_RE.sub(r"\1\2[redacted]", text)[:limit]


def _scalar(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def compact_summary(summary) -> str:
    """요약 dict → summary_json. 항목별 결과 같은 큰 리스트는 개수만 남기고, 전체는 8 KB 를 넘지 않는다."""
    if not isinstance(summary, dict):
        return "{}"
    compact: dict = {}
    for key, value in summary.items():
        if isinstance(value, (list, tuple)):
            if len(value) <= _SUMMARY_LIST_MAX and all(_scalar(item) for item in value):
                compact[str(key)] = list(value)
            else:
                compact[f"{key}_count"] = len(value)
        else:
            compact[str(key)] = value
    text = json.dumps(compact, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) <= SUMMARY_MAX_BYTES:
        return text
    # 중첩 설정(configuration 등)이 커진 경우 — 스칼라만 남긴다.
    compact = {key: value for key, value in compact.items() if _scalar(value)}
    text = json.dumps(compact, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) <= SUMMARY_MAX_BYTES:
        return text
    return json.dumps({"truncated": True, "keys": sorted(compact)[:50]}, ensure_ascii=False)


def source_key(name: str) -> str:
    return _SOURCE_KEYS.get(str(name or ""), str(name or "unknown"))


def source_label(key: str) -> str:
    return SOURCE_LABELS.get(str(key), str(key))


# --- 기록 --------------------------------------------------------------------------
def prune_runs(db, *, now_ms: Optional[int] = None, retention_days: int = RUN_RETENTION_DAYS) -> int:
    from .db import CollectorRun

    cutoff = _now_ms(now_ms) - int(retention_days) * 86_400_000
    result = db.exec(delete(CollectorRun).where(CollectorRun.started_ms < cutoff))
    rowcount = getattr(result, "rowcount", None)
    return int(rowcount) if rowcount is not None and rowcount >= 0 else 0


def _maybe_prune(db, millis: int) -> None:
    """기록할 때마다가 아니라 프로세스마다 한 시간에 한 번만 오래된 행을 지운다(별도 스케줄러 없이)."""
    global _last_prune_ms
    with _prune_lock:
        if millis - _last_prune_ms < PRUNE_EVERY_MS:
            return
        _last_prune_ms = millis
    try:
        prune_runs(db, now_ms=millis)
        db.commit()
    except Exception as error:
        db.rollback()
        _warn_throttled("실행 기록 정리 실패", error)


def record_run(
    engine: str,
    *,
    started_ms: int,
    finished_ms: int,
    status: str = "ok",
    targets: int = 0,
    items: int = 0,
    failures: int = 0,
    error: str = "",
    summary=None,
    db=None,
):
    """실행 한 건을 남긴다. 돌려주는 값은 저장된 CollectorRun 행, 실패하면 None. 절대 raise 하지 않는다."""
    try:
        from .db import CollectorRun

        started = int(started_ms or 0)
        finished = int(finished_ms or 0) or _now_ms()
        row = CollectorRun(
            engine=str(engine or "")[:40],
            day_kst=day_kst(started or finished),
            started_ms=started,
            finished_ms=finished,
            status=str(status or "ok")[:20],
            targets=max(0, int(targets or 0)),
            items=max(0, int(items or 0)),
            failures=max(0, int(failures or 0)),
            error=clean_error(error),
            summary_json=compact_summary(summary),
        )
        # flush 로 id 를 받고(INSERT … RETURNING / lastrowid) 커밋 전에 세션에서 떼어 낸다 — 커밋이 행을 만료시키지
        # 않으니 refresh(행 전체 SELECT, summary_json 포함) 없이도 돌려준 값을 세션이 닫힌 뒤에 읽을 수 있다.
        if db is None:
            with _session() as owned:
                owned.add(row)
                owned.flush()
                owned.expunge(row)
                owned.commit()
                _maybe_prune(owned, finished)
        else:
            try:
                db.add(row)
                db.flush()
                db.expunge(row)
                db.commit()
            except Exception:
                # 빌린 세션을 실패한 트랜잭션 채로 돌려주면 호출자의 다음 쿼리까지 깨진다.
                db.rollback()
                raise
            _maybe_prune(db, finished)
        return row
    except Exception as error:
        _warn_throttled(f"{engine} 실행 기록 실패", error)
        return None


def _source_values(engine: str, source: str, *, millis: int, calls: int = 0, items: int = 0, failures: int = 0,
                   targets: int = 0, error: str = "", success_ms: int = 0, day: Optional[str] = None) -> dict:
    # 날짜는 호출자가 실행 시작(started_ms) 기준으로 넘길 수 있다 — 자정을 넘긴 회차가 다음 날 행에 쌓이지 않게(A12).
    return {
        "day_kst": str(day or day_kst(millis)),
        "engine": str(engine or "")[:40],
        "source": str(source or "unknown")[:60],
        "calls": max(0, int(calls or 0)),
        "items": max(0, int(items or 0)),
        "failures": max(0, int(failures or 0)),
        "targets": max(0, int(targets or 0)),
        "last_error": clean_error(error),
        "last_success_ms": max(0, int(success_ms or 0)),
        "updated_ms": millis,
    }


def _upsert_sources(db, rows: list[dict]) -> None:
    """소스 하루 행 여러 개를 INSERT … ON CONFLICT 한 문장으로 더한다. rows 의 (day_kst, engine, source) 는 서로 달라야 한다."""
    from .db import CollectorSourceDaily

    if not rows:
        return
    statement = _insert_for(db)(CollectorSourceDaily).values(rows)
    excluded = statement.excluded
    # excluded["items"] — 속성 접근은 ColumnCollection.items() 메서드와 이름이 겹친다.
    set_ = {name: getattr(CollectorSourceDaily, name) + excluded[name]
            for name in ("calls", "items", "failures", "targets")}
    # 빈 오류로 마지막 오류를 지우지 않고, 마지막 성공 시각은 뒤로만 간다(늦게 도착한 기록이 앞서지 않게).
    set_["last_error"] = case((excluded.last_error != "", excluded.last_error), else_=CollectorSourceDaily.last_error)
    set_["last_success_ms"] = case((excluded.last_success_ms > CollectorSourceDaily.last_success_ms, excluded.last_success_ms),
                                   else_=CollectorSourceDaily.last_success_ms)
    set_["updated_ms"] = excluded.updated_ms
    # rowcount 는 보지 않는다 — 운영 Postgres 에서 INSERT … ON CONFLICT 의 rowcount 는 -1 이다.
    db.exec(statement.on_conflict_do_update(index_elements=["day_kst", "engine", "source"], set_=set_))


def _write_sources(rows: list[dict], *, db=None) -> None:
    """세션 하나·트랜잭션 하나로 소스 행들을 쓴다. 빌린 세션은 실패 시 되돌려서 호출자의 다음 쿼리를 지킨다."""
    if db is None:
        with _session() as owned:
            _upsert_sources(owned, rows)
            owned.commit()
    else:
        try:
            _upsert_sources(db, rows)
            db.commit()
        except Exception:
            db.rollback()
            raise


def bump_source(
    engine: str,
    source: str,
    *,
    calls: int = 0,
    items: int = 0,
    failures: int = 0,
    targets: int = 0,
    error: str = "",
    success_ms: int = 0,
    now_ms: Optional[int] = None,
    day_kst: Optional[str] = None,
    db=None,
) -> bool:
    """소스 하루 행에 더한다(기본은 오늘 KST, ``day_kst`` 로 실행 시작일을 넘길 수 있다). 실패하면 False. 절대 raise 하지 않는다."""
    try:
        millis = _now_ms(now_ms)
        _write_sources([_source_values(engine, source, millis=millis, calls=calls, items=items, failures=failures,
                                       targets=targets, error=error, success_ms=success_ms, day=day_kst)], db=db)
        return True
    except Exception as error:
        _warn_throttled(f"{engine}/{source} 소스 누적 실패", error)
        return False


def bump_sources(engine: str, buckets: dict, *, now_ms: Optional[int] = None, day_kst: Optional[str] = None,
                 db=None) -> bool:
    """소스 키 → 누적값(``bump_source`` 의 키워드) 여러 개를 한 세션·한 트랜잭션·한 문장으로 더한다.

    한 종목의 뉴스 소스 5~8개를 소스마다 따로 커밋하면 수집 리스를 쥔 채 왕복이 그만큼 늘어난다. 실패하면 False.
    절대 raise 하지 않는다.
    """
    try:
        millis = _now_ms(now_ms)
        rows = [_source_values(engine, source, millis=millis, day=day_kst, **bucket)
                for source, bucket in (buckets or {}).items()]
        _write_sources(rows, db=db)
        return True
    except Exception as error:
        _warn_throttled(f"{engine} 소스 누적 실패", error)
        return False


def bump_source_results(engine: str, rows, *, source_key_name: str, items_key: str, subject_key: str,
                        now_ms: Optional[int] = None, day_kst: Optional[str] = None) -> None:
    """flow 의 항목별 결과 목록(페어·코인)을 소스별로 접어 한 소스에 한 번만 쓴다.

    ``skipped`` 는 요청을 보내지 않은 것이라 호출로 세지 않고, ``error`` 만 실패다(``superseded`` 는 응답은
    받았지만 다른 실행이 먼저 저장한 경우). 절대 raise 하지 않는다.
    """
    try:
        millis = _now_ms(now_ms)
        folded: dict[str, dict] = {}
        for row in rows or []:
            if not isinstance(row, dict) or str(row.get("status") or "") in _NOT_ATTEMPTED:
                continue
            key = str(row.get(source_key_name) or "unknown")
            bucket = folded.setdefault(key, {"calls": 0, "items": 0, "failures": 0, "error": "", "success_ms": 0})
            bucket["calls"] += 1
            if str(row.get("status") or "") == "error":
                bucket["failures"] += 1
                parts = [str(row.get("error_code") or "").strip(), str(row.get("http_status") or "").strip()]
                subject = str(row.get(subject_key) or "").strip()
                bucket["error"] = " ".join(part for part in parts if part) + (f" ({subject})" if subject else "")
            else:
                bucket["items"] += max(0, int(row.get(items_key) or 0))
                bucket["success_ms"] = millis
        bump_sources(engine, folded, now_ms=millis, day_kst=day_kst)
    except Exception as error:
        _warn_throttled(f"{engine} 소스 결과 접기 실패", error)


def bump_news_sources(sources, *, targets: int = 1, engine: str = ENGINE_POSITION_NEWS,
                      now_ms: Optional[int] = None, day_kst: Optional[str] = None) -> None:
    """종목 뉴스 envelope 의 ``sources`` 목록(소스마다 한 항목)을 소스 키별로 누적한다. 절대 raise 하지 않는다.

    공유 캐시 히트(``cached``)는 호출이 아니므로 세지 않는다. 브라우저 페이지(``*_playwright``)는 한 종목에 여러
    페이지가 있어 'browser' 한 소스로 접는다 — 호출 = 페이지 수, 대상 = 종목 1.
    """
    try:
        millis = _now_ms(now_ms)
        folded: dict[str, dict] = {}
        for source in sources or []:
            if not isinstance(source, dict):
                continue
            status = str(source.get("status") or "")
            if status in _NOT_ATTEMPTED or source.get("cached"):
                continue
            is_browser = str(source.get("source_type") or "").endswith("_playwright")
            key = "browser" if is_browser else source_key(source.get("name"))
            bucket = folded.setdefault(key, {"calls": 0, "items": 0, "failures": 0, "error": "", "success_ms": 0,
                                             "targets": max(0, int(targets or 0))})
            bucket["calls"] += 1
            error = str(source.get("error") or "").strip()
            if status in _FAILED or error in _FAILED:
                bucket["failures"] += 1
                detail = " ".join(part for part in (error or status, str(source.get("message") or "").strip(),
                                                    str(source.get("http_status") or "").strip()) if part)
                bucket["error"] = detail
            else:
                bucket["items"] += max(0, int(source.get("item_count") or 0))
                if status in _SUCCEEDED:
                    bucket["success_ms"] = millis
        bump_sources(engine, folded, now_ms=millis, day_kst=day_kst)
    except Exception as error:
        _warn_throttled("뉴스 소스 누적 실패", error)


class RunRecorder:
    """``with RunRecorder(engine) as run:`` 한 줄로 실행 한 건을 남긴다.

    블록 안에서 ``run.report(...)`` 로 요약·개수를 넘기고, 예외는 그대로 통과시키되 status=error 로 남긴다.
    async 흐름처럼 with 를 못 쓰는 곳은 ``fail(exc)`` 뒤에 ``finish()`` 를 스레드에서 부른다. 기록 자체가 실패해도
    호출자에게는 아무 영향이 없다.
    """

    def __init__(self, engine: str, *, enabled: bool = True, now_ms: Optional[int] = None):
        self.engine = engine
        self.enabled = enabled
        self.started_ms = _now_ms(now_ms)
        self.finished_ms = 0
        self.status: Optional[str] = None
        self.targets = 0
        self.items = 0
        self.failures = 0
        self.error = ""
        self.summary = None
        self.row = None

    def report(self, summary=None, *, status: Optional[str] = None, targets: Optional[int] = None,
               items: Optional[int] = None, failures: Optional[int] = None, error: Optional[str] = None) -> None:
        if summary is not None:
            self.summary = summary
        if status is not None:
            self.status = status
        if targets is not None:
            self.targets = int(targets)
        if items is not None:
            self.items = int(items)
        if failures is not None:
            self.failures = int(failures)
        if error is not None:
            self.error = error

    def fail(self, error) -> None:
        self.status = "error"
        self.error = self.error or clean_error(error)

    @property
    def day_kst(self) -> str:
        """이 실행이 속한 날(KST, 시작 시각 기준) — 소스 누적 훅이 같은 날 행에 쓰도록 넘긴다."""
        return day_kst(self.started_ms)

    def finish(self, now_ms: Optional[int] = None):
        if not self.enabled:
            return None
        try:
            self.finished_ms = _now_ms(now_ms)
            self.row = record_run(self.engine, started_ms=self.started_ms, finished_ms=self.finished_ms,
                                  status=self.status or "ok", targets=self.targets, items=self.items,
                                  failures=self.failures, error=self.error, summary=self.summary)
        except Exception as error:
            _warn_throttled(f"{self.engine} 실행 기록 마감 실패", error)
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        if exc is not None:
            self.fail(exc)
        self.finish()
        return False


# --- 보고 --------------------------------------------------------------------------
def _expected_period_seconds(engine: str, configured: Optional[int]) -> int:
    if configured is not None:
        return int(configured)
    try:
        from .agent_features.whale_activity.repository import collection_interval_seconds

        return max(1, int(collection_interval_seconds()))
    except Exception as error:
        _warn_throttled(f"{engine} 기대 주기 조회 실패", error)
        return 60


def _delayed_after_ms(engine: str, period_seconds: int) -> int:
    """'지연' 기준 = 기대 주기의 3배. 종목 뉴스는 한 회차가 주기보다 길 수 있어(최대 사이클 + 30초) 그 길이도 넘어야 지연이다."""
    threshold = 3 * period_seconds
    if engine == ENGINE_POSITION_NEWS:
        try:
            cycle = max(90, int(os.environ.get("POSITION_NEWS_MAX_CYCLE_SECONDS", "240")) + 30)
        except ValueError:
            cycle = 270
        threshold = max(threshold, cycle + period_seconds)
    return threshold * 1000


def _stall_remaining_seconds(db, millis: int) -> int:
    try:
        from .agent_features.position_news.articles import ENRICHMENT_STALL_LEASE, NewsMaintenanceLease

        lease = db.get(NewsMaintenanceLease, ENRICHMENT_STALL_LEASE)
        until = int(lease.next_run_ms) if lease else 0
        return max(0, math.ceil((until - millis) / 1000)) if until > millis else 0
    except Exception as error:
        _warn_throttled("보강 정체 리스 조회 실패", error)
        return 0


def _enrichment_has_due_work(db, millis: int) -> bool:
    try:
        from .agent_features.position_news.articles import has_pending_articles

        return bool(has_pending_articles(now_ms=millis, db=db))
    except Exception as error:
        _warn_throttled("보강 대기 조회 실패", error)
        return True


def _enrichment_stall_persisted(db, millis: int) -> bool:
    """보강 정체가 '정체 쉼 × 10'(기본 10분)보다 오래 이어졌는가 — 공급자가 몇 시간 죽어 있어도 '정체 쉼' 만 보이던 문제.

    5초 스캔에 DB 읽기를 더하지 않으려고 보고 시점에 CollectorRun 으로 판단한다. 기준은 **정체가 시작된 시각**이다:
    마지막으로 진전한 실행(items > 0) 뒤에 온 첫 무진전 회차의 시작 시각. 마지막 진전 시각을 기준으로 재면 몇 시간
    조용하다가(대기 기사 없음) 방금 막힌 첫 회차가 곧바로 '정체 지속' 으로 찍힌다 — 조용했던 시간은 정체가 아니다.
    진전한 실행이 없으면 첫 실행이 곧 첫 무진전 회차이고, 실행 행이 하나도 없으면 리스가 걸린 시각(next_run - 쉼)이다.

    리스가 만료됐는데 보강할 기사가 없으면 정체가 아니라 '대기' 다 — 마지막 회차가 무진전이었다는 흔적(리스 값)만
    남은 채 큐가 빈 상태를 몇 시간째 오류로 보이지 않게.
    """
    try:
        from .agent_features.position_news.articles import (ENRICHMENT_STALL_LEASE, NewsMaintenanceLease,
                                                             enrichment_stall_seconds)
        from .db import CollectorRun

        lease = db.get(NewsMaintenanceLease, ENRICHMENT_STALL_LEASE)
        lease_until = int(lease.next_run_ms or 0) if lease else 0
        if lease_until <= 0:
            return False  # 정체 상태가 아니다(마지막 회차에 진전이 있었다)
        if lease_until <= millis and not _enrichment_has_due_work(db, millis):
            return False  # 쉼이 끝났고 일감도 없다 — 정체가 아니라 놀고 있는 것
        stall_ms = enrichment_stall_seconds() * 1000
        limit_ms = stall_ms * ENRICHMENT_STALL_ERROR_MULTIPLIER
        last_progress_started = int(db.exec(
            select(func.max(CollectorRun.started_ms)).where(CollectorRun.engine == ENGINE_ENRICHMENT, CollectorRun.items > 0)
        ).one() or 0)
        stall_started = db.exec(
            select(func.min(CollectorRun.started_ms))
            .where(CollectorRun.engine == ENGINE_ENRICHMENT, CollectorRun.started_ms > last_progress_started)
        ).one()
        reference = int(stall_started or 0) or (lease_until - stall_ms)
        return millis - reference > limit_ms
    except Exception as error:
        _warn_throttled("보강 정체 지속 판정 실패", error)
        return False


def _engine_status(engine: str, latest, *, millis: int, period_seconds: int, db) -> tuple[str, str]:
    """상태와 라벨. 순서: 기록 없음 → 대기, 마지막이 오류·소스 실패 → 오류, (보강) 정체 지속 → 오류, 정체 쉼,
    주기 3배 넘게 안 돎 → 지연, 그 외 정상."""
    if latest is None:
        return "idle", STATUS_LABELS["idle"]
    started_ms, finished_ms, status, _error = latest
    if str(status) == "error":
        return "error", STATUS_LABELS["error"]
    if str(status) in _SOURCE_FAILED_STATUSES:
        return "error", STATUS_LABELS[str(status)]
    if engine == ENGINE_ENRICHMENT:
        if _enrichment_stall_persisted(db, millis):
            return "error", "정체 지속"
        remaining = _stall_remaining_seconds(db, millis)
        if remaining > 0:
            return "stalled", f"정체 쉼 · {remaining}초 뒤 탐침"
    if period_seconds > 0:
        last_ms = int(finished_ms or started_ms or 0)
        if millis - last_ms > _delayed_after_ms(engine, period_seconds):
            # 보강은 일한 회차만 기록하므로, 대기 기사가 없어 안 돈 것은 지연이 아니다.
            if engine != ENGINE_ENRICHMENT or _enrichment_has_due_work(db, millis):
                return "delayed", STATUS_LABELS["delayed"]
    return "ok", STATUS_LABELS["ok"]


def engines_report(db, *, now_ms: Optional[int] = None) -> dict:
    """``GET /api/admin/news`` 의 engines · hourly · sources. 요약 열만 읽고 summary_json 은 읽지 않는다(egress)."""
    from .db import CollectorRun, CollectorSourceDaily

    millis = _now_ms(now_ms)
    today = day_kst(millis)

    # 오늘 실행을 엔진·상태별로 한 번에 읽는다. '실행' 은 일한 회차(ok·error·소스 실패)만이고, 소스를 부르지 않은
    # 회차(skipped)와 캐시로 응답한 회차(cached)는 따로 센다 — 뜻이 엔진마다 다르던 runs_today 를 통일(A10·A13).
    totals: dict[str, dict] = {}
    for engine, status, runs, targets, items, failures in db.exec(
        select(CollectorRun.engine, CollectorRun.status, func.count(CollectorRun.id),
               func.coalesce(func.sum(CollectorRun.targets), 0), func.coalesce(func.sum(CollectorRun.items), 0),
               func.coalesce(func.sum(CollectorRun.failures), 0))
        .where(CollectorRun.day_kst == today).group_by(CollectorRun.engine, CollectorRun.status)
    ).all():
        total = totals.setdefault(engine, {"runs": 0, "skipped": 0, "cached": 0, "targets": 0, "items": 0, "failures": 0})
        status = str(status or "")
        if status in _SKIPPED_STATUSES:
            total["skipped"] += int(runs or 0)
        elif status == STATUS_CACHED:
            total["cached"] += int(runs or 0)
        else:
            total["runs"] += int(runs or 0)
        total["targets"] += int(targets or 0)
        total["items"] += int(items or 0)
        total["failures"] += int(failures or 0)

    engines = []
    for engine, label, mode, configured in ENGINES:
        latest = db.exec(
            select(CollectorRun.started_ms, CollectorRun.finished_ms, CollectorRun.status, CollectorRun.error)
            .where(CollectorRun.engine == engine).order_by(CollectorRun.started_ms.desc()).limit(1)
        ).first()
        # 마지막 오류는 오늘 것만 — 며칠 전 오류가 '정상' 엔진 옆에 계속 보이지 않게.
        last_error = db.exec(
            select(CollectorRun.error).where(CollectorRun.engine == engine, CollectorRun.error != "",
                                             CollectorRun.day_kst == today)
            .order_by(CollectorRun.started_ms.desc()).limit(1)
        ).first()
        period = _expected_period_seconds(engine, configured)
        status, status_label = _engine_status(engine, latest, millis=millis, period_seconds=period, db=db)
        total = totals.get(engine, {"runs": 0, "skipped": 0, "cached": 0, "targets": 0, "items": 0, "failures": 0})
        entry = {
            "engine": engine, "label": label, "mode": mode,
            "status": status, "status_label": status_label,
            "last_run_ms": int((latest[1] or latest[0]) if latest else 0),
            "runs_today": total["runs"], "skipped_today": total["skipped"],
            "targets_today": total["targets"], "items_today": total["items"], "failures_today": total["failures"],
            "last_error": str(last_error or ""),
        }
        if engine == ENGINE_PUBLIC_NEWS:
            entry["served_from_cache_today"] = total["cached"]  # 화면 캡션 "캐시 응답 N회 제외"
        engines.append(entry)

    # 시간대별 수집은 기사를 실제로 저장하는 두 엔진의 합이다 — 운영에서는 종목 뉴스 워커 대신 공개 뉴스 워밍이
    # 대부분을 수집해, 종목 뉴스만 읽던 차트가 24칸 전부 0 이었다(2026-09-18 점검).
    hourly = [{"hour": hour, "items": 0, "failures": 0} for hour in range(24)]
    for started_ms, items, failures in db.exec(
        select(CollectorRun.started_ms, CollectorRun.items, CollectorRun.failures)
        .where(CollectorRun.engine.in_((ENGINE_POSITION_NEWS, ENGINE_PUBLIC_NEWS)), CollectorRun.day_kst == today)
    ).all():
        bucket = hourly[_hour_kst(int(started_ms or 0))]
        bucket["items"] += int(items or 0)
        bucket["failures"] += int(failures or 0)

    # 같은 크롤러(Google News RSS …)를 종목 뉴스 워커와 공개 뉴스 워밍이 번갈아 부른다 — 화면에는 소스가 하나로
    # 보여야 하므로 엔진별 행을 소스 키로 합친다. 합치지 않으면 그날 어느 프로세스가 리스를 쥐었는지에 따라 표가 빈다.
    # 온체인·고래 엔진의 소스도 같은 표에 들어간다(빠지면 blockscout 실패가 화면에 없다). 어느 엔진의 소스인지는
    # engine · engine_label 로 알린다(여러 엔진이 부른 소스는 '+' 로 잇는다).
    order = {key: index for index, key in enumerate(SOURCE_LABELS)}
    engine_order = {engine: index for index, (engine, *_rest) in enumerate(ENGINES)}
    engine_labels = {engine: label for engine, label, *_rest in ENGINES}
    folded: dict[str, dict] = {}
    for row in db.exec(select(CollectorSourceDaily).where(CollectorSourceDaily.day_kst == today)).all():
        if row.source in {"market", "ticker"}:
            continue  # 공개 뉴스의 범위 단위 카운터 — 소스가 아니라 엔진 표에 속한다
        bucket = folded.setdefault(row.source, {"source": row.source, "label": source_label(row.source),
                                                "targets": 0, "calls": 0, "items": 0, "failures": 0,
                                                "last_success_ms": 0, "last_error": "", "_updated_ms": 0,
                                                "_engines": set()})
        bucket["_engines"].add(str(row.engine or ""))
        bucket["targets"] += int(row.targets or 0)
        bucket["calls"] += int(row.calls or 0)
        bucket["items"] += int(row.items or 0)
        bucket["failures"] += int(row.failures or 0)
        bucket["last_success_ms"] = max(bucket["last_success_ms"], int(row.last_success_ms or 0))
        if row.last_error and int(row.updated_ms or 0) >= bucket["_updated_ms"]:
            bucket["last_error"] = str(row.last_error)
            bucket["_updated_ms"] = int(row.updated_ms or 0)
    sources = []
    for bucket in folded.values():
        bucket.pop("_updated_ms", None)
        engines_of = sorted(bucket.pop("_engines"), key=lambda engine: (engine_order.get(engine, len(engine_order)), engine))
        bucket["engine"] = "+".join(engines_of)
        bucket["engine_label"] = " + ".join(engine_labels.get(engine, engine) for engine in engines_of)
        calls = bucket["calls"]
        # 호출이 없으면 실패율은 없다(0.0% 가 아니라 None — 가짜 숫자 금지).
        bucket["failure_pct"] = round(bucket["failures"] * 100.0 / calls, 1) if calls else None
        sources.append(bucket)
    sources.sort(key=lambda row: (order.get(row["source"], len(order)), row["source"]))
    return {"engines": engines, "hourly": hourly, "sources": sources}
