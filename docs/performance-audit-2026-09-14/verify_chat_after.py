"""Reproduce chat SQL counts and payload sizes without contacting external services.

Run with an interpreter that has backend/requirements.txt installed:
    python docs/performance-audit-2026-09-14/verify_chat_after.py

A fresh temporary SQLite file is used. Socket connections are blocked before
application imports, and TestClient does not run application lifespan workers.
"""
import os, sys, tempfile, json, socket, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / 'backend'))
for key in ('DATABASE_URL', 'GEMINI_API_KEY', 'COINDESK_API_KEY', 'PREFECT_API_URL'):
    os.environ[key] = ''
fd, dbpath = tempfile.mkstemp(prefix='ggp-chat-audit-', suffix='.db')
os.close(fd)
os.environ['SQLITE_PATH'] = dbpath
for key in ('POSITION_NEWS_EMBEDDED_ENABLED','WHALE_TRADE_EMBEDDED_ENABLED','POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED','POSITION_NEWS_EXTRA_RSS_ENABLED','BINANCE_SQUARE_ENABLED','PUBLIC_NEWS_EMBEDDED_ENABLED','LEADERBOARD_BACKGROUND_ENABLED'):
    os.environ[key] = 'false'
os.environ['NEWS_IMAGES_DISABLED'] = '1'
def deny_network(*args, **kwargs):
    raise RuntimeError('Network disabled for local chat audit')
socket.socket.connect = deny_network
from sqlalchemy import event
from app import db as db_mod, auth
from app.db import User, ChatMessage, ChatReadState, LeaderboardEntry
from app import chat
from datetime import datetime, timezone
db_mod.init_db()
now = int(time.time() * 1000)
with db_mod.get_session() as db:
    a = User(email='reader@example.invalid', username='reader', password_hash='unused', created_at='2026-01-01T00:00:00Z')
    b = User(email='author@example.invalid', username='author', password_hash='unused', created_at='2026-01-01T00:00:00Z')
    db.add(a); db.add(b); db.commit(); db.refresh(a); db.refresh(b)
    aid, bid = a.id, b.id
    db.add(ChatReadState(user_id=aid, last_seen_id=0))
    for i in range(250):
        db.add(ChatMessage(user_id=bid, username='author', text='example message ' + str(i), created_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), created_ms=now))
    macro = LeaderboardEntry(user_id='local-audit', nickname='fixture', username='author', owner_user_id=bid, symbol='BTCUSDT', macro_json='{}', human_summary='fixture macro', created_at='2026-01-01T00:00:00Z', created_ms=now)
    db.add(macro); db.commit(); db.refresh(macro); mid=macro.id
headers={'Authorization': 'Bearer ' + auth.make_token(aid)}
queries=[]
counts={'checkouts':0, 'rollbacks':0, 'commits':0}
def capture(conn, cursor, statement, parameters, context, executemany):
    queries.append(' '.join(statement.split()))
def checkout(*args): counts['checkouts'] += 1
def rollback(*args): counts['rollbacks'] += 1
def commit(*args): counts['commits'] += 1
event.listen(db_mod._engine,'before_cursor_execute',capture)
event.listen(db_mod._engine,'checkout',checkout)
event.listen(db_mod._engine,'rollback',rollback)
event.listen(db_mod._engine,'commit',commit)
from app.main import app
from fastapi.testclient import TestClient
api = TestClient(app)
def measure(name, path, method="GET", payload=None):
    queries.clear()
    for key in counts: counts[key] = 0
    res = api.request(method, path, headers=headers, json=payload)
    assert res.status_code == 200, (res.status_code, res.text)
    data = res.json()
    print(json.dumps({"case": name, "sql_statements": len(queries), "selects": sum(q.startswith("SELECT") for q in queries), **counts, "response_bytes": len(res.content), "messages": len(data.get("items", []))}))
measure("full snapshot with unread", "/api/chat?seen_id=0")
measure("closed metadata unread", "/api/chat?metadata_only=true&seen_id=0")
measure("closed metadata caught up", "/api/chat?metadata_only=true&seen_id=250")
measure("open unchanged delta", "/api/chat?after_id=250&seen_id=250")
measure("read acknowledgment", "/api/chat/read", "PUT", {"last_seen_id": 250})
measure("plain post", "/api/chat", "POST", {"text": "local fixture"})
api.close()
db_mod._engine.dispose()
os.unlink(dbpath)
