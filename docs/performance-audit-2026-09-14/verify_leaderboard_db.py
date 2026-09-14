"""Actual SQLModel queries against isolated in-memory SQLite, with no network access."""
import json
import os
from pathlib import Path
import socket
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'backend'))
for key in ('DATABASE_URL', 'GEMINI_API_KEY', 'COINDESK_API_KEY', 'PREFECT_API_URL'):
    os.environ[key] = ''
os.environ['SQLITE_PATH'] = ':memory:'

connect = socket.socket.connect
def no_network(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        raise AssertionError('Outbound network disabled in leaderboard DB probe')
    return connect(sock, address)
socket.socket.connect = no_network

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine
from app import leaderboard, paper
from app.db import LeaderboardEntry, LeaderboardVote, MacroUnlock, PaperSession

engine = create_engine('sqlite:///:memory:')
SQLModel.metadata.create_all(engine)
start = leaderboard.today_start_ms()
with Session(engine) as db:
    for i in range(1, 401):
        db.add(LeaderboardEntry(id=i, user_id='owner', owner_user_id=1, nickname='fixture',
            username='fixture', symbol='BTCUSDT', macro_json='{}', human_summary='fixture',
            paper_session_id=i, created_at=f'2026-09-14T00:{i % 60:02}:00Z',
            created_ms=start + i if i <= 250 else start - leaderboard.DAY_MS + i))
        db.add(PaperSession(id=i, macro_id=f'm-{i}', symbol='BTCUSDT', mode='live', status='stopped',
            started_at='2026-09-14T00:00:00Z', current_return=float(i), current_equity=1000))
        db.add(MacroUnlock(user_id=1, entry_id=i, price=100, created_at='2026-09-14T00:00:00Z'))
        for v in range(10):
            db.add(LeaderboardVote(entry_id=i, user_id=f'v-{v}', value=1))
    db.commit()

queries = []
@event.listens_for(engine, 'before_cursor_execute')
def capture(_conn, _cursor, statement, _params, _context, _many):
    queries.append(statement)

leaderboard._crown_cache.clear()
with Session(engine) as db:
    first = leaderboard.list_entries('viewer', 1, db)
cold = list(queries)
queries.clear()
with Session(engine) as db:
    second = leaderboard.list_entries('viewer', 1, db)
warm = list(queries)
assert len(cold) == 7, len(cold)
assert len(warm) == 4, len(warm)
assert len(first['items']) == 250
assert first['items'][0]['id'] == second['items'][0]['id'] == 250
assert all('LIMIT' not in q.upper() for q in cold)
print(json.dumps({'proof': 'actual_SQLModel_SQLite_query_count', 'fixture_today_entries': 250,
    'fixture_total_entries': 400, 'fixture_total_votes': 4000,
    'cold_crown_cache_queries': len(cold), 'warm_crown_cache_queries': len(warm),
    'both_requests_return_all_today_entries': len(second['items']),
    'queries_with_limit': sum('LIMIT' in q.upper() for q in cold),
    'note': 'Excludes authentication and challenge existence queries from GET handler.'}))
for label, statements in [('cold', cold), ('warm', warm)]:
    for i, sql in enumerate(statements, 1):
        compact = ' '.join(sql.split())
        print(f'{label} query {i}: ' + compact.split(' WHERE ')[0] + ' [predicate parameter count=' + str(sql.count('?')) + ']')
engine.dispose()
