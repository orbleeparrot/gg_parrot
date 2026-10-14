"""Local, account/session-separated write-ahead event outbox. No credentials on disk.

Pending rows have no age/size eviction. Only server-confirmed identities may be
removed. This is application telemetry, not an order queue: it never executes,
replays or acknowledges trading commands.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import urlsplit, urlunsplit

MAX_BATCH = 100
# Lower than the server's 256 KiB cap, including JSON body framing. requests'
# json= encoder escapes non-ASCII text, so UTF-8 journal size is not wire size.
MAX_WIRE_BODY_BYTES = 200_000
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
    def __init__(self, path=None):
        self.path = str(default_path() if path is None else path)
        self._connection = None
        self._lock = threading.RLock()

    def _db(self):
        if self._connection is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            connection = sqlite3.connect(self.path, timeout=1.5, check_same_thread=False)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("""CREATE TABLE IF NOT EXISTS pending_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_scope TEXT NOT NULL, session_id INTEGER NOT NULL,
                    event_key TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(account_scope, session_id, event_key))""")
                connection.commit()
                if self.path != ":memory:" and os.name != "nt":
                    os.chmod(self.path, 0o600)
            except Exception:
                connection.close()
                raise
            self._connection = connection
        return self._connection

    def append(self, account_scope: str, session_id: int, event: dict) -> None:
        if not _EVENT_KEY.fullmatch(str(event.get("event_key") or "")):
            raise ValueError("invalid event identity")
        if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id < 0:
            raise ValueError("invalid session scope")
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if len(payload.encode()) > 16384:
            raise ValueError("event too large")
        with self._lock, self._db() as db:
            prior = db.execute("SELECT payload FROM pending_events WHERE account_scope=? AND session_id=? AND event_key=?",
                               (account_scope, session_id, event["event_key"])).fetchone()
            if prior and prior[0] != payload:
                raise ValueError("event identity is immutable")
            db.execute("INSERT OR IGNORE INTO pending_events(account_scope,session_id,event_key,payload) VALUES(?,?,?,?)",
                       (account_scope, session_id, event["event_key"], payload))

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
