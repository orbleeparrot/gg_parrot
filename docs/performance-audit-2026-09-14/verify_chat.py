"""Offline SQL-count audit against generated chat fixtures in temporary SQLite."""
from pathlib import Path
import os, sys, tempfile, json, socket, time
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
for key in ('DATABASE_URL', 'GEMINI_API_KEY', 'COINDESK_API_KEY', 'PREFECT_API_URL'):
    os.environ[key] = ''
fd, dbpath = tempfile.mkstemp(prefix='ggp-chat-audit-', suffix='.db')
os.close(fd)
os.environ['SQLITE_PATH'] = dbpath
for key in ('POSITION_NEWS_EMBEDDED_ENABLED','WHALE_TRADE_EMBEDDED_ENABLED','POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED','POSITION_NEWS_EXTRA_RSS_ENABLED','BINANCE_SQUARE_ENABLED'):
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
def measure(name, method='GET', path='/api/chat?seen_id=0', payload=None, use_auth=True):
    queries.clear()
    for k in counts: counts[k]=0
    from urllib.parse import urlsplit, parse_qs
    account = auth.optional_user(headers.get('Authorization') if use_auth else None)
    if method == 'GET':
        opts = {k:int(v[0]) for k,v in parse_qs(urlsplit(path).query).items()}
        d = chat.list_messages(account, **opts)
    elif method == 'PUT':
        d = chat.mark_read(account,payload['last_seen_id'])
    else:
        d = {'message':chat.add_message(account,payload['text'])}
    print(json.dumps({'case':name,'sql_statements':len(queries),'selects':sum(q.startswith('SELECT') for q in queries),**counts,'response_bytes':len(json.dumps(d,ensure_ascii=False,separators=(',',':')).encode()),'messages':len(d.get('items',[])), 'unseen_count':d.get('unseen_count')},ensure_ascii=False))
    return d, queries[:]
first, plain=measure('member plain')
second,_=measure('member unchanged repeat')
assert first == second
measure('anonymous initialized cursor',use_auth=False)
measure('history page',path='/api/chat?seen_id=0&before_id=51')
measure('read cursor write',method='PUT',path='/api/chat/read',payload={'last_seen_id':250})
measure('redundant read cursor write',method='PUT',path='/api/chat/read',payload={'last_seen_id':250})
with db_mod.get_session() as db:
    row=db.get(ChatMessage,250); row.text=f'[reply:1] [macro:{mid}] local fixture'; db.add(row); db.commit()
_, enrich=measure('member with macro and reply',path='/api/chat?seen_id=250')
measure('post plain',method='POST',path='/api/chat',payload={'text':'local fixture'})
print('PLAIN_SQL',json.dumps(plain,ensure_ascii=False))
print('ENRICH_EXTRA',json.dumps([q for q in enrich if q not in plain],ensure_ascii=False))
print('PRODUCTION_NETWORK_CALLS',0)
db_mod._engine.dispose(); os.unlink(dbpath)
