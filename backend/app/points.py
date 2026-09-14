"""Virtual points wallet + ledger (no real money yet).

The wallet (``User.points_balance``) is the source of truth; every mutation is
mirrored into :class:`PointLedger` for an auditable history. All changes go
through :func:`apply` or :func:`credit_current` so balance and ledger never drift, and debits can never
push a balance below zero.

The unlock economy: revealing/copying someone's leaderboard macro costs points;
``UNLOCK_CREATOR_SHARE_PCT`` (70%) of that goes to the macro's creator and the
rest is a platform sink. When real-money top-up/withdrawal lands (Stage 2) it
plugs in here as new ``reason`` codes — the transfer logic stays the same.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update

from .db import MacroUnlock, PointLedger, User

# Tunables (env-overridable later if needed).
SIGNUP_GRANT = 1000  # starter points on registration
UNLOCK_PRICE = 100  # cost to reveal+copy one leaderboard macro
UNLOCK_CREATOR_SHARE_PCT = 70  # % of the unlock price returned to the creator


class InsufficientPoints(Exception):
    """Raised when a debit would push a balance below zero."""


def _now():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def apply(db, user: User, delta: int, reason: str, ref: str = "") -> None:
    """Mutate ``user`` balance by ``delta`` and append a ledger row.

    Does NOT commit — the caller controls the transaction so multi-step transfers
    (spend + credit) are atomic. Raises :class:`InsufficientPoints` on overdraft.
    """
    new_balance = user.points_balance + delta
    if new_balance < 0:
        raise InsufficientPoints(
            f"포인트가 부족해요 (보유 {user.points_balance}, 필요 {-delta})."
        )
    user.points_balance = new_balance
    db.add(user)
    created_at, created_ms = _now()
    db.add(
        PointLedger(
            user_id=user.id,
            delta=delta,
            balance_after=new_balance,
            reason=reason,
            ref=ref,
            created_at=created_at,
            created_ms=created_ms,
        )
    )


def credit_current(db, user_id: int, delta: int, reason: str, ref: str = "") -> int | None:
    """Credit a live account using the database balance, in the caller's transaction.

    Long-running requests can hold an older User instance. SQL arithmetic keeps
    rewards from overwriting a purchase or another reward committed meanwhile.
    The update also serializes against account withdrawal; a deleted account
    receives neither a reward nor a ledger row. Returns the resulting balance.
    """
    if delta <= 0:
        raise ValueError("A credit must be positive.")
    balance = db.exec(
        update(User)
        .where(User.id == user_id, User.is_deleted.is_(False))
        .values(points_balance=User.points_balance + delta)
        .returning(User.points_balance)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if balance is None:
        return None
    created_at, created_ms = _now()
    db.add(PointLedger(user_id=user_id, delta=delta, balance_after=balance,
                       reason=reason, ref=ref, created_at=created_at, created_ms=created_ms))
    return balance


def creator_share(price: int) -> int:
    """Points returned to the creator for one unlock (floor of the 70%)."""
    return price * UNLOCK_CREATOR_SHARE_PCT // 100


def unlock_transfer(db, *, viewer: User, creator: User, entry_id: int, price: int = UNLOCK_PRICE) -> int:
    """Charge ``viewer`` ``price`` points, pay the creator their share, record it.

    Atomic within the caller's transaction. Returns the creator's share. The
    caller must commit. Owners unlocking their own entry never reach here.
    """
    apply(db, viewer, -price, "unlock_spend", f"entry:{entry_id}")
    share = creator_share(price)
    if creator.id != viewer.id and share > 0:
        apply(db, creator, share, "unlock_earn", f"entry:{entry_id}")
    created_at, _ = _now()
    db.add(MacroUnlock(user_id=viewer.id, entry_id=entry_id, price=price, created_at=created_at))
    return share
