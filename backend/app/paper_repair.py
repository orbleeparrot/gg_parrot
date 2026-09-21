"""2026-09-21 일회성 데이터 복구 — 재기동 복구 첫 배포가 캔들형 세션 자산을 초기 자본으로 되돌렸다.

CandleSim 에 restore() 가 없어 RSI·볼린저·EMA·트레일링 봇 8개가 0% 로 리셋됐고, 되살아난 러너가
체크포인트로 DB 값을 덮어써 원래 자산이 사라졌다. 리셋 직전 리더보드가 보여 주던 수익률을 그대로
되돌린다(포지션은 이미 잃었으므로 현금으로 복구). 만료 시각이 지나면 아무것도 하지 않는다 —
만료 뒤 첫 정리 때 이 파일과 main.py 의 호출을 지운다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .db import PaperSession, get_session

log = logging.getLogger(__name__)

REPAIR_EXPIRES = datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)
# session_id → 리셋 직전 수익률(%) — 2026-09-21 06:20Z 리더보드 스냅샷 값.
REPAIR_RETURNS: dict[int, float] = {
    136: 21.4567, 147: 33.9869, 148: 15.1686, 153: 1.7509,
    156: 0.6986, 152: -19.8141, 151: -23.5282, 150: -25.575,
}


def apply_one_off_repair(now: datetime | None = None) -> int:
    """만료 전이면 대상 세션의 자산·수익률을 되돌린다. 되돌린 수를 돌려준다."""
    now = now or datetime.now(timezone.utc)
    if now >= REPAIR_EXPIRES:
        return 0
    fixed = 0
    with get_session() as db:
        for sid, ret in REPAIR_RETURNS.items():
            row = db.get(PaperSession, sid)
            if row is None or row.status != "running":
                continue
            base = float(row.virtual_balance or 0.0) or 1_000_000.0
            row.current_return = ret
            row.current_equity = round(base * (1 + ret / 100.0), 4)
            row.state_json = ""  # 포지션은 이미 잃었다 — 현금으로 복구
            db.add(row)
            fixed += 1
        if fixed:
            db.commit()
    if fixed:
        log.warning("paper one-off repair: restored returns on %d session(s)", fixed)
    return fixed
