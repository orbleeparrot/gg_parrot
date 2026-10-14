"""Local, account/session-separated write-ahead event outbox. No credentials on disk.

Unconfirmed telemetry is retained for at most seven days, 10,000 rows and
32 MiB of UTF-8 JSON across all accounts. Retention loss is counted separately
from server acknowledgements. This is never an order queue.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit, urlunsplit

MAX_BATCH = 100
# Lower than the server's 256 KiB cap, including JSON body framing. requests'
# json= encoder escapes non-ASCII text, so UTF-8 journal size is not wire size.
MAX_WIRE_BODY_BYTES = 200_000
PENDING_TTL_SECONDS = 7 * 86400
MAX_PENDING_ROWS = 10_000
MAX_PENDING_BYTES = 32 * 1024 * 1024
MAX_RETAINED_JOURNAL_BYTES = 4 * 1024 * 1024
MEMORY_MAX_ROWS = 1_000
MEMORY_MAX_BYTES = 2 * 1024 * 1024
MAINTENANCE_SECONDS = 60
LOSS_REASONS = ("disk_expired", "disk_capacity", "memory_expired", "memory_capacity")
_MAX_COUNTER = 2**63 - 1
_EVENT_KEY = re.compile(r"^runner:[a-f0-9]{32}$")
_SECRET = re.compile(r"(?i)((?:api[_-]?key|secret|password|authorization|access[_-]?token|token|x-runner-key)"
                     r"[\"']?\s*[:=]\s*)[\"']?(?:bearer\s+)?[^\s,;\"']+")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]+")
_PROVIDER_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|ggp_[A-Za-z0-9_-]{20,})\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_DSN = re.compile(r"\b(?:postgres(?:ql)?|mysql|redis)(?:\+[A-Za-z0-9_-]+)?://[^\s\"']+", re.I)
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)


def default_path() -> Path:
    # Stable across runner upgrades; never place a journal in the exe directory.
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "GGParrot" / "activity" / "outbox.sqlite3"


def namespace(base: str, member_key: str) -> str:
    # Authentication stays in memory. A different account/server cannot flush
    # these rows, even if both happen to have a session numbered 1.
    material = "ggp-event-outbox-v1\0" + base.rstrip("/") + "\0" + member_key
    return hashlib.sha256(material.encode()).hexdigest()


def redact(value: str, member_key: str = "") -> str:
    text = str(value)
    if member_key:
        text = text.replace(member_key, "[키 숨김]")
    text = _SECRET.sub(r"\1[redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _PROVIDER_KEY.sub("[redacted]", text)
    text = _JWT.sub("[redacted]", text)
    text = _DSN.sub("[connection]", text)
    def public_url(match):
        try:
            parts = urlsplit(match.group())
            if parts.username or parts.password:
                return "[authenticated-url]"
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        except ValueError:
            return "[url]"
    return _URL.sub(public_url, text)


def wire_batch(events: list[dict], *, body: dict | None = None) -> list[dict]:
    """Oldest-first prefix fitting both count and actual ASCII JSON byte limits.

    With default separators, replacing [] costs each encoded event plus ', '
    between events. Count the surrounding heartbeat/stopped body too; never
    delete or skip an event just because it belongs in the next request.
    """
    framing = {**(body or {}), "events": []}
    size = len(json.dumps(framing, ensure_ascii=True, allow_nan=False).encode("utf-8"))
    result = []
    for event in events[:MAX_BATCH]:
        contribution = len(json.dumps(event, ensure_ascii=True, allow_nan=False).encode("utf-8")) + (2 if result else 0)
        if size + contribution > MAX_WIRE_BODY_BYTES:
            break
        result.append(event)
        size += contribution
    return result


class EventOutbox:
    def __init__(self, path=None, *, clock=None, max_rows=MAX_PENDING_ROWS,
                 max_bytes=MAX_PENDING_BYTES, ttl_seconds=PENDING_TTL_SECONDS):
        self.path = str(default_path() if path is None else path)
        self._clock = clock or time.time
        # Test/embedding policies may be stricter, never unbounded or larger.
        self.max_rows = max(1, min(MAX_PENDING_ROWS, int(max_rows)))
        self.max_bytes = max(1, min(MAX_PENDING_BYTES, int(max_bytes)))
        self.ttl_seconds = max(1, min(PENDING_TTL_SECONDS, int(ttl_seconds)))
        self._connection = None
        self._lock = threading.RLock()

    def now_ms(self):
        return max(1, int(self._clock() * 1000))

    def _db(self):
        if self._connection is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            connection = sqlite3.connect(self.path, timeout=1.5, check_same_thread=False)
            try:
                # Effective for a new database. Legacy databases reuse freed
                # pages; enabling auto-vacuum there requires a blocking VACUUM,
                # which we intentionally do not run in the trading heartbeat.
                connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
                connection.execute("PRAGMA journal_mode=WAL")
                # Shrink retained WAL on reuse/checkpoint. This is not a hard
                # bound while a reader prevents checkpointing an active WAL.
                connection.execute(f"PRAGMA journal_size_limit={MAX_RETAINED_JOURNAL_BYTES}")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE IF NOT EXISTS pending_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_scope TEXT NOT NULL, session_id INTEGER NOT NULL,
                    event_key TEXT NOT NULL, payload TEXT NOT NULL,
                    created_ms INTEGER NOT NULL DEFAULT 0,
                    payload_bytes INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(account_scope, session_id, event_key))""")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(pending_events)")}
                for column in ("created_ms", "payload_bytes"):
                    if column not in columns:
                        connection.execute(f"ALTER TABLE pending_events ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
                connection.execute("CREATE INDEX IF NOT EXISTS pending_events_created ON pending_events(created_ms,seq)")
                connection.execute("""CREATE TABLE IF NOT EXISTS retention_meta (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    disk_expired INTEGER NOT NULL DEFAULT 0, disk_capacity INTEGER NOT NULL DEFAULT 0,
                    memory_expired INTEGER NOT NULL DEFAULT 0, memory_capacity INTEGER NOT NULL DEFAULT 0,
                    noted_disk_expired INTEGER NOT NULL DEFAULT 0, noted_disk_capacity INTEGER NOT NULL DEFAULT 0,
                    noted_memory_expired INTEGER NOT NULL DEFAULT 0, noted_memory_capacity INTEGER NOT NULL DEFAULT 0,
                    last_loss_ms INTEGER NOT NULL DEFAULT 0, last_notice_ms INTEGER NOT NULL DEFAULT 0,
                    last_maintenance_ms INTEGER NOT NULL DEFAULT 0)""")
                connection.execute("INSERT OR IGNORE INTO retention_meta(id) VALUES(1)")
                # One-time legacy migration, chunked rather than loading an
                # arbitrarily large pre-policy journal into Python memory.
                now = self.now_ms()
                while True:
                    rows = connection.execute("SELECT seq,payload FROM pending_events WHERE created_ms=0 LIMIT 250").fetchall()
                    if not rows:
                        break
                    updates = []
                    for seq, payload in rows:
                        created = now
                        try:
                            stamp = json.loads(payload).get("ts")
                            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                            if parsed.tzinfo is not None:
                                created = max(1, min(now, int(parsed.timestamp() * 1000)))
                        except (ValueError, TypeError, AttributeError, OverflowError, OSError):
                            pass  # Unknown legacy age starts at migration, never guessed into a session.
                        updates.append((created, len(payload.encode("utf-8")), seq))
                    connection.executemany("UPDATE pending_events SET created_ms=?,payload_bytes=? WHERE seq=?", updates)
                connection.commit()
                if self.path != ":memory:" and os.name != "nt":
                    os.chmod(self.path, 0o600)
            except Exception:
                connection.close()
                raise
            self._connection = connection
        return self._connection

    def _count_loss(self, db, reason, count, now):
        if reason not in LOSS_REASONS or count <= 0:
            return
        db.execute(f"UPDATE retention_meta SET {reason}=MIN(?,{reason}+?),last_loss_ms=? WHERE id=1",
                   (_MAX_COUNTER, int(count), now))

    def _prune(self, db, now):
        # No account/session predicate: old member keys and session=0 orphans
        # count toward exactly the same bounded local retention budget.
        expired = db.execute("DELETE FROM pending_events WHERE created_ms<=?",
                             (now - self.ttl_seconds * 1000,)).rowcount
        self._count_loss(db, "disk_expired", expired, now)
        count, size = db.execute("SELECT COUNT(*),COALESCE(SUM(payload_bytes),0) FROM pending_events").fetchone()
        removed = 0
        while count > self.max_rows or size > self.max_bytes:
            rows = db.execute("SELECT seq,payload_bytes FROM pending_events ORDER BY seq LIMIT 250").fetchall()
            delete = []
            for seq, payload_bytes in rows:
                if count <= self.max_rows and size <= self.max_bytes:
                    break
                delete.append((seq,))
                count -= 1
                size -= payload_bytes
            db.executemany("DELETE FROM pending_events WHERE seq=?", delete)
            removed += len(delete)
        self._count_loss(db, "disk_capacity", removed, now)

    def _compact(self, db):
        # Bounded, non-blocking work; the logical cap is not a promise that the
        # SQLite main/WAL files are precisely 32 MiB (indexes/page overhead and
        # legacy free pages exist). PASSIVE never waits for other readers.
        try:
            db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            db.execute("PRAGMA incremental_vacuum(128)").fetchall()
        except sqlite3.DatabaseError:
            pass

    def maintain(self, *, force=False):
        with self._lock:
            db, now = self._db(), self.now_ms()
            last = db.execute("SELECT last_maintenance_ms FROM retention_meta WHERE id=1").fetchone()[0]
            if not force and 0 <= now - last < MAINTENANCE_SECONDS * 1000:
                return
            with db:
                db.execute("BEGIN IMMEDIATE")
                self._prune(db, now)
                db.execute("UPDATE retention_meta SET last_maintenance_ms=? WHERE id=1", (now,))
            self._compact(db)

    def loss_stats(self):
        with self._lock:
            db = self._db()
            values = db.execute("SELECT " + ",".join(LOSS_REASONS) + " FROM retention_meta WHERE id=1").fetchone()
            count, size = db.execute("SELECT COUNT(*),COALESCE(SUM(payload_bytes),0) FROM pending_events").fetchone()
            return dict(zip(LOSS_REASONS, values), pending_rows=count, pending_bytes=size)

    def record_memory_loss(self, counts):
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for reason in ("memory_expired", "memory_capacity"):
                self._count_loss(db, reason, max(0, int(counts.get(reason, 0))), self.now_ms())

    def take_loss_notice(self):
        """Claim a small delta at most once/minute across clients on this file."""
        with self._lock:
            db, now = self._db(), self.now_ms()
            with db:
                db.execute("BEGIN IMMEDIATE")
                values = db.execute("SELECT " + ",".join(LOSS_REASONS) + "," +
                    ",".join("noted_" + reason for reason in LOSS_REASONS) +
                    ",last_notice_ms FROM retention_meta WHERE id=1").fetchone()
                delta = {reason: values[index] - values[index + 4] for index, reason in enumerate(LOSS_REASONS)}
                if not any(delta.values()) or 0 <= now - values[-1] < MAINTENANCE_SECONDS * 1000:
                    return {}
                db.execute("UPDATE retention_meta SET " + ",".join("noted_" + reason + "=" + reason for reason in LOSS_REASONS) +
                           ",last_notice_ms=? WHERE id=1", (now,))
                return delta

    def existing_unassigned_keys(self, account_scope, keys):
        with self._lock:
            db, existing = self._db(), set()
            keys = list(keys)
            for offset in range(0, len(keys), MAX_BATCH):
                chunk = keys[offset:offset + MAX_BATCH]
                placeholders = ",".join("?" for _key in chunk)
                existing.update(row[0] for row in db.execute(
                    f"SELECT event_key FROM pending_events WHERE account_scope=? AND session_id=0 AND event_key IN ({placeholders})",
                    (account_scope, *chunk)))
            return existing

    def append(self, account_scope: str, session_id: int, event: dict, *, created_ms=None) -> None:
        if not _EVENT_KEY.fullmatch(str(event.get("event_key") or "")):
            raise ValueError("invalid event identity")
        if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id < 0:
            raise ValueError("invalid session scope")
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True)
        payload_bytes = len(payload.encode("utf-8"))
        if payload_bytes > 16384:
            raise ValueError("event too large")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT payload FROM pending_events WHERE account_scope=? AND session_id=? AND event_key=?",
                               (account_scope, session_id, event["event_key"])).fetchone()
            if prior and prior[0] != payload:
                raise ValueError("event identity is immutable")
            now = self.now_ms()
            created = now if created_ms is None else max(1, min(now, int(created_ms)))
            db.execute("INSERT OR IGNORE INTO pending_events(account_scope,session_id,event_key,payload,created_ms,payload_bytes) VALUES(?,?,?,?,?,?)",
                       (account_scope, session_id, event["event_key"], payload, created, payload_bytes))
            self._prune(db, now)  # Enforce capacity before this write commits, even during a burst.
        self.maintain()

    def bind_unassigned(self, account_scope: str, session_id: int, event_keys) -> None:
        # Only identities emitted by THIS still-alive client can acquire its
        # newly known session. An old orphan or another starting macro must
        # never be guessed into this execution.
        keys = [key for key in event_keys if isinstance(key, str) and _EVENT_KEY.fullmatch(key)]
        if session_id <= 0 or not keys:
            return
        with self._lock, self._db() as db:
            db.executemany("UPDATE pending_events SET session_id=? WHERE account_scope=? AND session_id=0 AND event_key=?",
                           [(session_id, account_scope, key) for key in keys])

    def peek(self, account_scope: str, session_id: int, limit=MAX_BATCH, *, event_keys=None) -> list[dict]:
        bound = max(1, min(MAX_BATCH, int(limit)))
        self.maintain()
        with self._lock:
            db = self._db()
            if event_keys is None:
                rows = db.execute("SELECT seq,payload FROM pending_events WHERE account_scope=? AND session_id=? ORDER BY seq LIMIT ?",
                                  (account_scope, session_id, bound)).fetchall()
            else:
                rows, keys = [], list(event_keys)
                for offset in range(0, len(keys), MAX_BATCH):
                    chunk = keys[offset:offset + MAX_BATCH]
                    placeholders = ",".join("?" for _key in chunk)
                    rows.extend(db.execute(f"SELECT seq,payload FROM pending_events WHERE account_scope=? AND session_id=? "
                                           f"AND event_key IN ({placeholders}) ORDER BY seq LIMIT ?",
                                           (account_scope, session_id, *chunk, bound)).fetchall())
                rows = sorted(rows)[:bound]
        return [json.loads(row[1]) for row in rows]

    def sessions(self, account_scope: str) -> list[int]:
        self.maintain()
        with self._lock:
            return [row[0] for row in self._db().execute(
                "SELECT session_id FROM pending_events WHERE account_scope=? AND session_id>0 GROUP BY session_id ORDER BY MIN(seq)",
                (account_scope,)).fetchall()]

    def acknowledge(self, account_scope: str, session_id: int, keys: list[str]) -> None:
        keys = [key for key in keys[:MAX_BATCH] if isinstance(key, str) and _EVENT_KEY.fullmatch(key)]
        if not keys:
            return
        with self._lock, self._db() as db:
            db.executemany("DELETE FROM pending_events WHERE account_scope=? AND session_id=? AND event_key=?",
                           [(account_scope, session_id, key) for key in keys])

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
