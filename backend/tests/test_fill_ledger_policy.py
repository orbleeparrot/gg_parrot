"""체결 기준 장부 — 새 세션의 실행 방식(exec_policy)은 시작 때 한 번 정하고 바꾸지 않는다(2026-10-21 설계 §9 결정 4 · 5).

1단계는 마틴게일(H) · 롱 · 현물 · 레버리지 1 을 실행기 연동 레벨 21 이상으로 새로 시작한 세션에만 켠다.
그 밖(다른 규칙 · 레벨 20 이하 · 진행 중인 세션)은 서버가 재기동해도 지금 경로 그대로 돈다.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import runner as runner_mod
from app import runner_engine as eng
from app.db import RunSession, get_session
from app.engine import Macro
from app.main import app
from tests.test_runner import _auth, _signup

client = TestClient(app)

H = {"symbol": "BTCUSDT", "rule_type": "H", "position_side": "long", "market": "spot", "leverage": 1,
     "candle_interval": "15m", "period": {"preset": "1w"},
     "params": {"base_order_size": 100, "safety_order_size": 100, "price_deviation": 1.0, "take_profit": 2.0,
                "max_safety_orders": 3, "initial_capital": 1000},
     "risk": {"invest_ratio": 1.0, "stop_loss_pct": 5.0}, "fees": {"commission_pct": 0.1, "slippage_pct": 0.0}}
RSI = {"symbol": "ONEUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1,
       "candle_interval": "5m", "period": {"preset": "1w"},
       "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 32},
       "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0.1, "slippage_pct": 0.05}}


@pytest.fixture(autouse=True)
def _reset_engine_memory():
    eng._drivers.clear()
    yield
    eng._drivers.clear()


def _policy(body: dict, version: str) -> str:
    return runner_mod.fill_ledger_policy(Macro.model_validate(body), version)


@pytest.mark.parametrize("over,version,want", [
    ({}, "21", "fill_ledger_v1"),
    ({}, "22", "fill_ledger_v1"),
    ({}, "20", ""),  # 레벨 20 실행기 — 체결을 보고하지 못한다
    ({}, "", ""),
    ({}, "21a", ""),
    ({"position_side": "short"}, "21", ""),
    ({"market": "futures", "leverage": 2}, "21", ""),
    ({"market": "futures", "leverage": 1}, "21", ""),
    ({"exchange": "upbit", "symbol": "KRW-GTC"}, "21", "fill_ledger_v1"),
])
def test_policy_is_only_for_long_spot_martingale_on_level_21(over, version, want):
    assert _policy({**H, **over}, version) == want


def test_other_rules_keep_the_current_path_even_on_level_21():
    assert _policy(RSI, "21") == ""
    assert runner_mod.fill_ledger_policy(None, "21") == ""


def test_off_switch_stops_new_sessions_from_getting_the_policy(monkeypatch):
    monkeypatch.setattr(runner_mod, "FILL_LEDGER_MIN_VERSION", "999")
    assert _policy(H, "21") == ""


def _start(key: str, macro: dict, version: str):
    body = {"symbol": macro["symbol"], "position_side": "long", "leverage": 1, "market": "spot", "testnet": True,
            "human_summary": "t", "macro": macro, "runner_version": version}
    return client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})


def _stop(key: str, sid: int) -> None:
    # running 인 v8+ 세션을 남기지 않는다 — 재기동 복구 시험이 DB 의 running 세션을 센다.
    client.post("/api/runner/stopped", json={"session_id": sid, "status": "stopped"}, headers={"X-Runner-Key": key})


def test_start_stores_the_policy_and_heartbeat_never_changes_it():
    key = client.get("/api/me/runner/key", headers=_auth(_signup())).json()["key"]
    new = _start(key, H, "21").json()["session_id"]
    old = _start(key, H, "20").json()["session_id"]
    try:
        r = client.post("/api/runner/heartbeat",
                        json={"session_id": old, "in_position": False, "runner_version": "21",
                              "exec_policy": "fill_ledger_v1"},
                        headers={"X-Runner-Key": key})
        assert r.status_code == 200
        with get_session() as db:
            assert db.get(RunSession, new).exec_policy == "fill_ledger_v1"
            row = db.get(RunSession, old)
            assert row.exec_policy == "" and row.runner_version == "20"
    finally:
        _stop(key, new)
        _stop(key, old)


class _FlatFeed:
    def __init__(self):
        self.subs = []

    async def history(self, symbol, interval, market, n):
        return [(i * 900_000, 100, 100, 100, 100) for i in range(20)]

    def subscribe(self, symbol, interval, market, cb, **kwargs):
        self.subs.append(cb)
        return ("sub", symbol)

    def unsubscribe(self, sub):
        self.subs.clear()


async def _no_loop(live):
    """틱 루프를 끈다 — 시험이 드라이버 상태만 본다."""


def _running(policy: str, version: str) -> int:
    now = eng._now_iso()
    with get_session() as db:
        row = RunSession(user_id=1, symbol="BTCUSDT", position_side="long", leverage=1, market="spot",
                         status="running", started_at=now, last_heartbeat_at=now, runner_version=version,
                         macro_json=Macro.model_validate(H).model_dump_json(), exec_policy=policy)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _drop(*ids: int) -> None:
    with get_session() as db:
        for sid in ids:
            row = db.get(RunSession, sid)
            if row is not None:
                db.delete(row)
        db.commit()


def test_driver_follows_the_stored_policy_across_a_server_restart(monkeypatch):
    new, old = _running("fill_ledger_v1", "21"), _running("", "20")
    monkeypatch.setattr(eng, "feed", _FlatFeed())
    monkeypatch.setattr(eng, "_run", _no_loop)

    async def scenario():
        assert await eng.start_driver(new) and await eng.start_driver(old)
        assert eng._drivers[new].driver.fill_ledger is True
        assert eng._drivers[old].driver.fill_ledger is False
        await eng.shutdown_drivers()  # 배포 · 재기동 — 새 프로세스가 DB 의 값으로 되살린다
        monkeypatch.setattr(eng, "_running_v8_ids", lambda: [new, old])
        assert await eng.resume_running_runner_sessions() == 2
        assert eng._drivers[new].driver.fill_ledger is True
        assert eng._drivers[old].driver.fill_ledger is False
        await eng.shutdown_drivers()

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _drop(new, old)
