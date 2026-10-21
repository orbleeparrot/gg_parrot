"""체결 기록표(runnerfill) 읽기 · 쓰기 — 체결 기준 장부(fill_ledger_v1) 세션의 원장(2026-10-21 설계 §5).

하트비트가 실행기 보고(ack 의 ``orders`` · 본문의 ``local_fills``)를 여기 넣고, 서버 전략 루프가 틱마다 아직 접지 않은
행을 읽어 장부에 접는다(runner_engine). 같은 주문 키는 한 행 — 응답 유실로 같은 보고가 다시 와도 두 번 넣지 않는다.
보고는 실행기가 한 말이지 거래소에서 확인한 사실이 아니다(verification 은 runner_reported_not_exchange_verified).
"""
from __future__ import annotations

import math
from typing import List, Optional

from sqlmodel import select

from .db import RunnerCommand, RunnerFill, RunSession

MAX_ORDERS_PER_ACK = 5  # 명령 하나는 주문 0~1개 — 넉넉히 두되 이상한 보고가 표를 채우지 못하게
MAX_LOCAL_FILLS = 20


def _positive(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _fact(raw) -> Optional[dict]:
    """주문 사실 하나를 칸으로. 키가 없거나 체결 수량 · 가격이 양수가 아니면 None(장부에 영향 없는 보고)."""
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("client_order_id") or "").strip()[:64]
    executed, price = _positive(raw.get("executed_qty")), _positive(raw.get("fill_price"))
    if not key or executed is None or price is None:
        return None
    acquired = _positive(raw.get("acquired_qty"))
    try:
        ts_ms = max(0, int(raw.get("ts_ms") or 0))
    except (TypeError, ValueError):
        ts_ms = 0
    return {"fill_key": key, "exchange_order_id": str(raw.get("exchange_order_id") or "")[:64],
            "side": str(raw.get("side") or "").lower()[:8], "status": str(raw.get("status") or "")[:24],
            "executed_qty": executed, "acquired_qty": min(acquired, executed) if acquired else executed,
            "price": price, "fee_quote": _positive(raw.get("fee_quote")) or 0.0,
            "fee_known": raw.get("fee_known") is True, "reason": str(raw.get("reason") or "")[:40],
            "runner_ts_ms": ts_ms}


def _insert(db, row: RunSession, fact: dict, *, command_id: Optional[int], source: str, now_ms: int) -> bool:
    """한 행 넣기 — 같은 (세션, 키)가 있으면 넣지 않는다(동시에 같은 보고가 와도 유일 색인이 하나만 남긴다)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(RunnerFill).values(
        session_id=row.id, command_id=command_id, source=source, created_ms=int(now_ms), applied_ms=0, **fact,
    ).on_conflict_do_nothing(index_elements=[RunnerFill.session_id, RunnerFill.fill_key]).returning(RunnerFill.id)
    return db.exec(statement).scalar_one_or_none() is not None


def record_command_orders(db, row: RunSession, cmd: RunnerCommand, ack: dict, now_ms: int) -> dict:
    """명령 하나의 ack 에 실린 주문 사실을 넣고 명령의 누적 체결(수량 합 · 가중평균가)을 다시 쓴다.

    레벨 21 실행기는 ``orders`` 를 싣는다. 빠졌는데 ack 에 체결 수량 · 가격이 있으면 그 값으로 한 행을 만든다 —
    키는 체결 값에서 지어 같은 ack 가 다시 와도 한 행이다(수수료는 모름).
    반환: ``{"added": 새 행 수, "ignored": 버린 보고 수}`` — 체결 0 인 주문 보고(거절 등)는 버린 것으로 세지 않는다.
    """
    raw = ack.get("orders")
    facts, ignored = [], 0
    for item in (raw[:MAX_ORDERS_PER_ACK] if isinstance(raw, list) else []):
        fact = _fact(item)
        if fact is not None:
            facts.append(fact)
        elif not _zero_fill(item):
            ignored += 1
    if not isinstance(raw, list):
        executed, price = _positive(ack.get("executed_qty")), _positive(ack.get("fill_price"))
        if executed is not None and price is not None:
            facts = [{"fill_key": f"cmd:{cmd.id}:{executed!r}:{price!r}"[:64], "exchange_order_id": "",
                      "side": "", "status": "", "executed_qty": executed, "acquired_qty": executed, "price": price,
                      "fee_quote": 0.0, "fee_known": False, "reason": "", "runner_ts_ms": 0}]
    added = 0
    side = "sell" if cmd.action in ("sell", "cover") else "buy"
    for fact in facts:
        fact["side"] = side  # 방향은 명령이 정한다 — 보고의 방향 칸은 믿지 않는다
        added += _insert(db, row, fact, command_id=cmd.id, source="command", now_ms=now_ms)
    refresh_command_totals(db, cmd)
    return {"added": added, "ignored": ignored}


def _zero_fill(item) -> bool:
    """키가 있고 체결 수량이 정확히 0 인 주문 보고 — 거절 · 0 체결. 장부에 넣을 것이 없을 뿐 이상한 보고가 아니다."""
    if not isinstance(item, dict) or not str(item.get("client_order_id") or "").strip():
        return False
    try:
        return float(item.get("executed_qty") or 0.0) == 0.0
    except (TypeError, ValueError):
        return False


def refresh_command_totals(db, cmd: RunnerCommand) -> None:
    """명령의 체결 수량 · 가격을 그 명령 체결 행 전부의 합 · 가중평균으로. 행이 없으면 0."""
    rows = db.exec(select(RunnerFill.executed_qty, RunnerFill.price)
                   .where(RunnerFill.session_id == cmd.session_id, RunnerFill.command_id == cmd.id)).all()
    qty = sum(float(q) for q, _ in rows)
    cmd.executed_qty = qty
    cmd.fill_price = (sum(float(q) * float(p) for q, p in rows) / qty) if qty > 0 else 0.0


def record_local_fills(db, row: RunSession, local_fills, now_ms: int) -> int:
    """실행기가 서버 명령 없이 낸 주문(로컬 손절 · 안전 규칙 정리)의 체결을 넣는다. 새로 넣은 행 수를 돌려준다."""
    if not isinstance(local_fills, list):
        return 0
    added = 0
    for item in local_fills[:MAX_LOCAL_FILLS]:
        fact = _fact(item)
        if fact is None:
            continue
        fact["side"] = "sell"  # 로컬 주문은 정리뿐이다(손절 · 안전 규칙)
        added += _insert(db, row, fact, command_id=None, source="local", now_ms=now_ms)
    return added


def has_unapplied(db, session_id: int) -> bool:
    """아직 장부에 접지 않은 체결 행이 있는가."""
    return db.exec(select(RunnerFill.id).where(RunnerFill.session_id == session_id,
                                               RunnerFill.applied_ms == 0)).first() is not None


FOLD_BATCH = 50  # 한 틱에 접는 체결 행 — 보통 한두 개다


def unapplied_fills(db, session_id: int, limit: int = FOLD_BATCH) -> List[dict]:
    """아직 장부에 접지 않은 체결 행(들어온 순서). 각 행에 그 명령의 주문 번호(ref)를 붙인다 — 로컬 체결은 빈 값."""
    rows = list(db.exec(select(RunnerFill).where(RunnerFill.session_id == session_id, RunnerFill.applied_ms == 0)
                        .order_by(RunnerFill.id.asc()).limit(limit)).all())
    ids = {row.command_id for row in rows if row.command_id}
    refs = dict(db.exec(select(RunnerCommand.id, RunnerCommand.order_ref).where(RunnerCommand.id.in_(ids))).all()) if ids else {}
    return [{"id": row.id, "key": row.fill_key, "ref": refs.get(row.command_id, "") if row.command_id else "",
             "command_id": row.command_id, "source": row.source, "side": row.side, "executed_qty": row.executed_qty,
             "acquired_qty": row.acquired_qty, "price": row.price, "fee_quote": row.fee_quote,
             "fee_known": row.fee_known, "reason": row.reason, "ts_ms": row.runner_ts_ms} for row in rows]


TERMINAL = ("acked", "failed", "expired", "cancelled")


def commands_for_refs(db, session_id: int, refs) -> List[RunnerCommand]:
    """주문 번호로 명령 행을 찾는다(끝난 것 · 진행 중인 것 모두)."""
    refs = [str(ref) for ref in refs if ref]
    if not refs:
        return []
    return list(db.exec(select(RunnerCommand).where(RunnerCommand.session_id == session_id,
                                                    RunnerCommand.order_ref.in_(refs))
                        .order_by(RunnerCommand.seq.asc())).all())


def mark_applied(db, ids, now_ms: int) -> None:
    ids = [int(i) for i in ids]
    if not ids:
        return
    for row in db.exec(select(RunnerFill).where(RunnerFill.id.in_(ids), RunnerFill.applied_ms == 0)).all():
        row.applied_ms = int(now_ms)
        db.add(row)

