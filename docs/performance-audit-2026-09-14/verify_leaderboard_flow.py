"""Offline audit: execute unchanged function bodies with stand-in external/DB dependencies."""
import ast
import asyncio
import json
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]

def extract(relative, names, namespace):
    tree = ast.parse((ROOT / relative).read_text())
    chosen = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                arg.annotation = None
            node.returns = None
            if node.name == 'leaderboard_list':
                node.args.defaults = [ast.Constant(''), ast.Constant(None), ast.Constant(None)]
            chosen.append(node)
    assert len(chosen) == len(names)
    exec(compile(ast.fix_missing_locations(ast.Module(body=chosen, type_ignores=[])), str(ROOT / relative), 'exec'), namespace)

async def daily_path():
    events = []
    ready = False
    async def carry():
        events.append('carryover_write')
        await asyncio.sleep(.04)
    def existing(_date):
        return ready
    def claim(_date):
        events.append('challenge_claim_write')
        return {'status': 'claimed', 'claim_token': 'offline'}
    def market():
        events.append('market_fetch')
        time.sleep(.06)
        return 'BTCUSDT'
    def generate(_symbol, _count):
        events.append('ai_generation')
        time.sleep(.10)
        return [{'symbol': 'BTCUSDT'}] * 3
    async def start(*args):
        events.append('paper_start')
        await asyncio.sleep(.03)
        return {'session_id': 1}
    def complete(*args, **kwargs):
        nonlocal ready
        events.append('challenge_ready_write')
        ready = True
    class FakeMacro(NS):
        def model_dump_json(self):
            return '{}'
    lb = NS(ensure_today_carryover=carry, create_entry=lambda **kw: events.append('entry_write'),
            list_entries=lambda **kw: events.append('list_db_read') or {'items': []})
    challenge = {'asyncio': asyncio, '_today_kst': lambda: 'offline', '_existing': existing,
                 '_lock': asyncio.Lock(), '_claim_daily_challenge': claim, '_CLAIM_POLL_SECONDS': .01,
                 '_pick_symbol': market, 'ai_challenge': NS(generate_macros=generate),
                 'Macro': FakeMacro, 'paper_mod': NS(start_session=start), 'leaderboard_mod': lb,
                 'bot_name': lambda n: f'bot-{n}', 'human_summary': lambda m: 'offline',
                 '_complete_daily_challenge': complete,
                 '_fail_daily_challenge': lambda *a, **k: events.append('failed')}
    extract('backend/app/challenge.py', {'ensure_today'}, challenge)
    route = {'leaderboard_mod': lb, 'challenge_mod': NS(ensure_today=challenge['ensure_today'])}
    extract('backend/app/main.py', {'leaderboard_list'}, route)
    before = time.perf_counter()
    await route['leaderboard_list']('', None, None)
    elapsed = time.perf_counter() - before
    assert events[-1] == 'list_db_read'
    assert events.count('paper_start') == events.count('entry_write') == 3
    assert elapsed >= .28
    print(json.dumps({'proof': 'first_GET_awaits_generation_before_list', 'mock_delays_seconds': .29,
                      'request_seconds': round(elapsed, 3), 'events': events}))

class Col:
    def in_(self, values): return None
    def __ge__(self, other): return None
    def __eq__(self, other): return None

class Q:
    def where(self, *args): return self

class Result:
    def __init__(self, data): self.data = data
    def all(self): return self.data
    def __iter__(self): return iter(self.data)

class DB:
    def __init__(self, queued, delay=0):
        self.queued, self.delay, self.queries, self.loaded = list(queued), delay, 0, 0
    def exec(self, query):
        time.sleep(self.delay)
        self.queries += 1
        data = self.queued.pop(0)
        self.loaded += len(data)
        return Result(data)

def namespace():
    model = NS(id=Col(), created_ms=Col(), owner_user_id=Col(), entry_id=Col(), user_id=Col())
    ns = {'Optional': Optional, 'json': json, 'time': time, 'nullcontext': nullcontext,
          'select': lambda *a: Q(), 'LeaderboardEntry': model, 'LeaderboardVote': model,
          'MacroUnlock': model, 'today_start_ms': lambda: 0, 'seconds_to_reset': lambda: 1,
          'KEEP_TOP_N': 3, 'CROWN_CACHE_SECONDS': 180, '_crown_cache': {},
          '_CROWN_CACHE_MAX_ENTRIES': 64, 'CROWN_MIN_SALES': 3, 'CROWN_MIN_LIKES': 3,
          '_STATUS_NOT_PROVIDED': object(), '_kst_hhmm': lambda v: '00:00',
          '_kst_mmdd': lambda v: '01/01', 'points_mod': NS(UNLOCK_PRICE=100)}
    functions = {'list_entries', '_vote_tallies', '_unlocked_ids_for', '_crown_owner_ids',
                 '_compute_crown_owner_ids', '_entry_view', '_sort_board', '_live_return'}
    extract('backend/app/leaderboard.py', functions, ns)
    return ns

async def repeated_aggregation():
    ns = namespace()
    rows = [NS(id=i, owner_user_id=1, paper_session_id=i, macro_json='{}', username='u', nickname='u',
               symbol='BTCUSDT', streak_days=1, first_created_ms=None, created_ms=0,
               human_summary='', created_at=str(i), user_id='u', is_ai=False)
            for i in range(1, 251)]
    votes = [NS(entry_id=i, user_id=str(v), value=1) for i in range(1, 251) for v in range(10)]
    history = [(i, 1) for i in range(1, 401)]
    history_votes = [NS(entry_id=i, user_id=str(v), value=1) for i in range(1, 401) for v in range(10)]
    statuses = {i: {'current_return': float(i), 'current_equity': 1000, 'status': 'stopped', 'mode': 'live'}
                for i in range(1, 251)}
    def get_statuses(ids, db):
        db.exec(Q()).all()
        return statuses
    ns['paper_mod'] = NS(get_statuses=get_statuses)
    cold = DB([rows, votes, [], history, list(range(1, 401)), history_votes, rows])
    first = ns['list_entries']('u', 1, cold)
    warm = DB([rows, votes, [], rows])
    second = ns['list_entries']('u', 1, warm)
    assert cold.queries == 7 and warm.queries == 4
    assert first['items'][0]['id'] == second['items'][0]['id'] == 250
    assert len(first['items']) == 250
    print(json.dumps({'proof': 'repeated_full_aggregation_actual_functions', 'today_entries': 250,
                      'today_votes_reread_each_request': len(votes), 'cold_list_queries': cold.queries,
                      'warm_list_queries': warm.queries, 'cold_loaded_rows': cold.loaded,
                      'warm_loaded_rows': warm.loaded, 'pagination': False,
                      'note': 'Counts exclude endpoint challenge existence and authentication queries.'}))
    # Four slow synchronous DB reads in the actual list function run in the async route.
    slow = DB([rows, votes, [], rows], delay=.03)
    async def noop(): pass
    route = {'leaderboard_mod': NS(ensure_today_carryover=noop, list_entries=ns['list_entries']),
             'challenge_mod': NS(ensure_today=noop)}
    extract('backend/app/main.py', {'leaderboard_list'}, route)
    heartbeats = []
    async def heartbeat():
        for _ in range(3):
            heartbeats.append(time.perf_counter())
            await asyncio.sleep(.002)
    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    start = time.perf_counter()
    await route['leaderboard_list']('u', NS(id=1), slow)
    elapsed = time.perf_counter() - start
    await task
    gap = max(b-a for a,b in zip(heartbeats, heartbeats[1:]))
    assert gap >= .12
    print(json.dumps({'proof': 'async_route_blocks_event_loop', 'mock_db_read_ms': 30,
                      'db_queries': slow.queries, 'request_ms': round(elapsed*1000, 1),
                      'heartbeat_requested_ms': 2, 'heartbeat_max_gap_ms': round(gap*1000, 1)}))

async def carryover_failure():
    claim_persisted = False
    calls = 0
    def claim(_date):
        nonlocal claim_persisted
        if claim_persisted: return False
        claim_persisted = True
        return True
    def work():
        nonlocal calls
        calls += 1
        raise RuntimeError('simulated failure before carryover commit')
    ns = {'asyncio': asyncio, '_carryover_done_date': None, '_carryover_lock': asyncio.Lock(),
          '_today_kst': lambda: 'offline', '_claim_carryover': claim,
          '_carry_previous_day_top': work, '_record_carried': lambda *a: None}
    extract('backend/app/leaderboard.py', {'ensure_today_carryover'}, ns)
    try:
        await ns['ensure_today_carryover']()
    except RuntimeError:
        pass
    second = await ns['ensure_today_carryover']()
    assert calls == 1 and second == 0 and ns['_carryover_done_date'] == 'offline'
    print(json.dumps({'proof': 'failed_carryover_claim_prevents_same_day_retry', 'work_calls_after_two_attempts': calls,
                      'second_attempt_result': second, 'done_date_set': True}))

async def main():
    await daily_path()
    await repeated_aggregation()
    await carryover_failure()

asyncio.run(main())
