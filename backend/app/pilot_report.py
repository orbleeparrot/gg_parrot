"""관리자 대시보드 — 파일럿 테스트 실전 매크로 성과(실시간).

파일럿 테스터 계정들의 실전(live) 실행기 세션을 모아 계정별 합계와 세션별 손익을 낸다.
실행기가 보고하는 ``realized_pnl`` 은 수수료 전이라, 실현손익은 체결 기록(``runner.order_result``
fill 이벤트의 수량 × 체결가)으로 다시 계산하고 거래소 수수료를 뺀다. 아직 팔지 않은 수량은
세션의 마지막 가격(heartbeat)으로 평가한다.

대상 계정과 기간은 환경 변수로 바꿀 수 있다(파일럿이 끝나면 탭째 지워도 된다).
- ``PILOT_USER_IDS``: 쉼표로 구분한 user id
- ``PILOT_START`` / ``PILOT_END``: UTC ISO 시각(그 사이에 시작한 세션, 그 사이에 난 체결만 센다)
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from .db import RunSession, RunSessionEvent, User
from .members import mask_email

# 2026-10-14 08:00 ~ 10-16 17:00 KST, 테스터 10명(노희재 · Been · 하면그만이야 · 껄무섀 · 먀먀 · 이도연 · gggggggggg · AlexKim · 껄껄껄 · 이도연3호기).
DEFAULT_USER_IDS = (1, 3, 442, 443, 444, 440, 439, 445, 430, 441)
DEFAULT_START = "2026-10-13T23:00:00Z"
DEFAULT_END = "2026-10-16T08:00:00Z"

# 매수 · 매도 각각에 붙는 거래소 수수료율(원화 마켓 기본 · 바이낸스 현물 기본).
FEE_RATE = {"upbit": 0.0005, "bithumb": 0.0004, "binance": 0.001}
CACHE_SECONDS = 15
_cache: dict = {}
_cache_lock = threading.Lock()


def _config() -> tuple[tuple[int, ...], str, str]:
    raw = os.environ.get("PILOT_USER_IDS", "").strip()
    ids = tuple(int(x) for x in raw.split(",") if x.strip().isdigit()) if raw else DEFAULT_USER_IDS
    start = os.environ.get("PILOT_START", "").strip() or DEFAULT_START
    end = os.environ.get("PILOT_END", "").strip() or DEFAULT_END
    return ids, start, end


def _ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _json(value: str) -> dict:
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _base(symbol: str) -> str:
    if symbol.startswith("KRW-"):
        return symbol[4:]
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


def session_pnl(session: RunSession, exchange: str, fills: list[dict]) -> dict:
    """체결 기록으로 한 세션의 실현손익 · 보유 수량 · 수수료를 다시 계산한다(평균단가 방식)."""
    fee_rate = FEE_RATE.get(exchange, FEE_RATE["binance"])
    qty = cost = realized = fees = 0.0
    buys = sells = 0
    for d in fills:
        q = _num(d.get("executed_qty"))
        p = _num(d.get("fill_price"))
        if q <= 0 or p <= 0:
            continue
        notional = q * p
        fee = notional * fee_rate
        fees += fee
        action = str(d.get("action") or "").upper()
        if action == "BUY":
            buys += 1
            cost += notional + fee
            # 원화 거래소는 실제로 받은 수량(acquired_qty)이 기준, 바이낸스는 체결 수량.
            qty += (_num(d.get("acquired_qty")) or q) if exchange != "binance" else q
        elif action == "SELL":
            sells += 1
            if qty > 0:
                sold = min(q, qty)
                avg = cost / qty
                realized += sold * p - fee - avg * sold
                cost -= avg * sold
                qty -= sold
    last = _num(session.last_price)
    unreal = qty * last - cost if qty > 1e-12 and last > 0 else 0.0
    # 체결 기록이 없는데(실행기 v18 이전) 실행기가 손익을 보고했다면 그 값(수수료 전)을 쓴다.
    reported = _num(session.realized_pnl)
    fallback = buys == 0 and abs(reported) > 0.5
    if fallback:
        realized = reported
    return {
        "realized": round(realized, 2),
        "unrealized": round(unreal, 2),
        "net": round(realized + unreal, 2),
        "fees": round(fees, 2),
        "buys": buys,
        "sells": sells,
        "open_qty": round(qty, 8) if qty > 1e-12 else 0.0,
        "last_price": last,
        "reported": round(reported, 2),
        "fallback": fallback,
    }


def _issue_lines(events: list[RunSessionEvent]) -> list[str]:
    """운영자가 확인해야 할 것만 짧은 문장으로 — 일시적인 연결 재시도 · 시세 지연은 뺀다."""
    funds = scope = unknown = mismatch = 0
    other: dict[str, int] = defaultdict(int)
    for e in events:
        msg = e.message or ""
        if e.event_name == "command.failed":
            if "insufficient_funds" in msg:
                funds += 1
            elif "out_of_scope" in msg:
                scope += 1
            elif "로컬 리스크 보류" in msg:
                continue  # 신호가와 현재가 차이로 스스로 건너뛴 것 — 정상 안전장치
            else:
                other[msg.replace("⚠ ", "")[:80]] += 1
        elif e.event_name == "runner.order_result" and "status=unknown" in msg:
            unknown += 1
        elif e.event_name == "engine.position_mismatch":
            mismatch += 1
    lines = []
    if funds:
        lines.append(f"잔고 부족으로 진입 주문이 {funds}번 나가지 않았어요.")
    if scope:
        lines.append(f"API 키에 주문 권한이 없어 진입이 {scope}번 실패했어요.")
    if unknown:
        lines.append(f"실전 주문 {unknown}건이 '결과 확인 불가'로 끝났어요. 거래소에서는 체결됐을 수 있어 본인 거래소 계정의 주문 · 보유를 확인해야 해요.")
    if mismatch:
        lines.append(f"서버는 보유 중인데 실행기는 비어 있다는 경고가 {mismatch}번 있었어요. 거래소 보유 수량 확인이 필요해요.")
    for msg, n in other.items():
        lines.append(f"{msg} ({n}번)")
    return lines


def build(db: Session, now: Optional[datetime] = None) -> dict:
    ids, start, end = _config()
    now = now or datetime.now(timezone.utc)
    start_dt, end_dt = _ts(start), _ts(end)
    users = {u.id: u for u in db.exec(select(User).where(User.id.in_(ids))).all()}
    sessions = db.exec(
        select(RunSession)
        .where(RunSession.user_id.in_(ids))
        .where(RunSession.started_at >= start)
        .where(RunSession.started_at < end)
        .order_by(RunSession.id)
    ).all()
    session_ids = [s.id for s in sessions]
    events_by: dict[int, list[RunSessionEvent]] = defaultdict(list)
    if session_ids:
        rows = db.exec(
            select(RunSessionEvent)
            .where(RunSessionEvent.session_id.in_(session_ids))
            .where(RunSessionEvent.kind.in_(("fill", "warn", "error")))
            .order_by(RunSessionEvent.id)
        ).all()
        for e in rows:
            events_by[e.session_id].append(e)

    per_user: dict[int, dict] = {
        uid: {"live": [], "mock": 0, "events": []} for uid in ids
    }
    for s in sessions:
        bucket = per_user.setdefault(s.user_id, {"live": [], "mock": 0, "events": []})
        if s.mode != "live":
            bucket["mock"] += 1
            continue
        macro = _json(s.macro_json)
        exchange = str(macro.get("exchange") or "binance")
        evs = events_by.get(s.id, [])
        fills = []
        for e in evs:
            if e.kind != "fill":
                continue
            ts = _ts(e.ts)
            if end_dt and ts and ts > end_dt:
                continue  # 기간이 끝난 뒤의 체결은 넣지 않는다
            fills.append(_json(e.details_json))
        pnl = session_pnl(s, exchange, fills)
        running = s.status == "running"
        invested = _num(s.invested_usdt)
        bucket["events"].extend(evs)
        bucket["live"].append({
            "id": s.id,
            "symbol": s.symbol,
            "base": _base(s.symbol),
            "exchange": exchange,
            "rule": str(macro.get("rule_type") or ""),
            "status": s.status,
            "running": running,
            "started_at": s.started_at,
            "ended_at": None if running else (s.stopped_at or s.last_heartbeat_at or s.started_at),
            "invested": round(invested, 2),
            "return_pct": round(pnl["net"] / invested * 100, 2) if invested > 0 else None,
            **pnl,
        })

    accounts = []
    for uid in ids:
        u = users.get(uid)
        bucket = per_user.get(uid, {"live": [], "mock": 0, "events": []})
        live = bucket["live"]
        traded = [x for x in live if x["buys"] or x["fallback"]]
        shown = [x for x in live if x["buys"] or x["fallback"] or x["running"]]
        accounts.append({
            "user_id": uid,
            "name": (u.username if u else "") or f"#{uid}",
            "email": mask_email(u.email) if u else "",
            "realized": round(sum(x["realized"] for x in live), 2),
            "unrealized": round(sum(x["unrealized"] for x in live), 2),
            "net": round(sum(x["net"] for x in live), 2),
            "fees": round(sum(x["fees"] for x in live), 2),
            "live_sessions": len(live),
            "traded_sessions": len(traded),
            "running_sessions": len([x for x in live if x["running"]]),
            "idle_sessions": len([x for x in live if not (x["buys"] or x["fallback"] or x["running"])]),
            "mock_sessions": bucket["mock"],
            "sessions": sorted(shown, key=lambda x: x["started_at"]),
            "issues": _issue_lines(bucket["events"]),
        })
    # 체결한 계정을 합계순으로 먼저, 체결 없는 계정은 뒤에(0원을 순위에 끼우면 '본전'처럼 읽힌다).
    accounts.sort(key=lambda a: (a["traded_sessions"] == 0, -a["net"]))
    active = [a for a in accounts if a["traded_sessions"]]
    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "period": {"start": start, "end": end, "ended": bool(end_dt and now >= end_dt)},
        "fee_rates": FEE_RATE,
        "totals": {
            "net": round(sum(a["net"] for a in accounts), 2),
            "realized": round(sum(a["realized"] for a in accounts), 2),
            "unrealized": round(sum(a["unrealized"] for a in accounts), 2),
            "fees": round(sum(a["fees"] for a in accounts), 2),
            "live_sessions": sum(a["live_sessions"] for a in accounts),
            "traded_sessions": sum(a["traded_sessions"] for a in accounts),
            "running_sessions": sum(a["running_sessions"] for a in accounts),
            "testers": len(accounts),
            "winners": len([a for a in active if a["net"] > 0.5]),
            "losers": len([a for a in active if a["net"] < -0.5]),
            "no_trades": len(accounts) - len(active),
        },
        "accounts": accounts,
    }


def report(db: Session) -> dict:
    """15초 캐시 — 대시보드를 여러 사람이 열어도 DB 를 한 번만 읽는다."""
    key = _config()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = build(db)
    with _cache_lock:
        _cache.clear()
        _cache[key] = (time.monotonic(), data)
    return data
