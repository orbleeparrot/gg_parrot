"""묶음 실거래 — 실행기가 레그마다 시작을 보내고 서버가 나눈다 (2026-10-30).

설계: docs/superpowers/specs/2026-10-30-portfolio-live-run-design.md

가장 조심할 것은 **서명**이다. 실행기는 원본 묶음 매크로를 그대로 보내고 몇 번째 레그인지만
덧붙인다 — 실행기가 쪼개 보내면 모든 묶음이 "고쳐서 돌린 파일" 로 기록된다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import runner as runner_mod
from app import runner_engine as eng
from app.db import RunSession, get_session
from app.main import app
from tests.test_runner_signals_api import A, _key, _signup

client = TestClient(app)

BUNDLE = {**A, "symbols": ["BTCUSDT", "ETHUSDT"], "candle_interval": "1h",
          "market": "spot", "leverage": 1}

# 이 파일이 만든 세션 번호 — 끝나고 치운다(아래 _clean_up_sessions).
_CREATED: list[int] = []


@pytest.fixture(autouse=True)
def no_driver(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)


@pytest.fixture(autouse=True)
def _clean_up_sessions():
    """이 파일이 띄운 세션을 치운다.

    시험 DB(SQLite)는 파일 전체가 공유한다. 살아 있는 세션을 남기면 "되살릴 세션은 이것
    하나" 를 세는 다른 파일의 시험(test_runner_engine.py)이 깨진다.
    """
    _CREATED.clear()
    yield
    with get_session() as db:
        for session_id in _CREATED:
            row = db.get(RunSession, session_id)
            if row is not None:
                db.delete(row)
        db.commit()
    _CREATED.clear()


def _start(key, macro, *, leg=None, bundle_key="", version="23", symbol=None):
    body = {"symbol": symbol or macro["symbol"], "exchange": macro.get("exchange", "binance"),
            "position_side": macro.get("position_side", "long"), "leverage": 1,
            "market": macro.get("market", "spot"), "testnet": True, "human_summary": "t",
            "macro": macro, "runner_version": version}
    if leg is not None:
        body["leg_index"] = leg
    if bundle_key:
        body["bundle_key"] = bundle_key
    reply = client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})
    if reply.status_code == 200:
        _CREATED.append(reply.json()["session_id"])
    return reply


def _session(sid):
    with get_session() as db:
        row = db.get(RunSession, sid)
        return {"symbol": row.symbol, "macro_json": row.macro_json,
                "bundle_key": row.bundle_key, "bundle_index": row.bundle_index,
                "bundle_size": row.bundle_size, "bundle_digest": row.bundle_digest,
                "macro_digest": row.macro_digest, "macro_origin": row.macro_origin}


# --- 나뉘어 시작된다 ---------------------------------------------------------

def test_each_leg_becomes_its_own_single_symbol_session():
    key = _key(_signup())
    first = _start(key, BUNDLE, leg=0, bundle_key="b1", symbol="BTCUSDT")
    second = _start(key, BUNDLE, leg=1, bundle_key="b1", symbol="ETHUSDT")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    a = _session(first.json()["session_id"])
    b = _session(second.json()["session_id"])
    assert (a["symbol"], b["symbol"]) == ("BTCUSDT", "ETHUSDT")
    # 세션이 들고 있는 매크로는 **레그**다 — 실행기가 실제로 돌리는 것과 같아야 한다.
    assert '"symbols":null' in a["macro_json"].replace(" ", "")
    assert '"BTCUSDT"' in a["macro_json"] and '"ETHUSDT"' not in a["macro_json"]


def test_the_capital_is_split_by_weight():
    key = _key(_signup())
    sid = _start(key, BUNDLE, leg=1, bundle_key="b1", symbol="ETHUSDT").json()["session_id"]
    import json as _json
    macro = _json.loads(_session(sid)["macro_json"])
    assert macro["params"]["initial_capital"] == A["params"]["initial_capital"] / 2


def test_the_legs_are_grouped_so_the_screen_can_show_them_as_one():
    key = _key(_signup())
    first = _start(key, BUNDLE, leg=0, bundle_key="grp", symbol="BTCUSDT").json()["session_id"]
    second = _start(key, BUNDLE, leg=1, bundle_key="grp", symbol="ETHUSDT").json()["session_id"]
    a, b = _session(first), _session(second)
    assert a["bundle_key"] == b["bundle_key"] == "grp"
    assert (a["bundle_index"], b["bundle_index"]) == (0, 1)
    assert a["bundle_size"] == b["bundle_size"] == 2
    # 원본 지문은 레그마다 같다 — "무엇을 돌렸나" 를 원본으로 되짚는 값이다.
    assert a["bundle_digest"] and a["bundle_digest"] == b["bundle_digest"]
    assert a["macro_digest"] != b["macro_digest"], "레그 지문은 레그마다 달라야 한다"
    assert a["macro_digest"] != a["bundle_digest"]


# --- 서명 ---------------------------------------------------------------------

def test_the_signature_is_checked_against_the_whole_bundle():
    """실행기가 쪼개 보내면 모든 묶음이 '고친 파일' 이 된다 — 그래서 원본을 그대로 보낸다."""
    key = _key(_signup())
    sid = _start(key, BUNDLE, leg=0, bundle_key="b", symbol="BTCUSDT").json()["session_id"]
    assert _session(sid)["macro_origin"] != "file_modified"


# --- 거절 ---------------------------------------------------------------------

def test_a_bundle_without_a_leg_number_is_refused():
    """레그를 말하지 않는 실행기에 묶음을 넘기면 첫 종목에 전액이 들어간다."""
    key = _key(_signup())
    r = _start(key, BUNDLE)
    assert r.status_code == 422
    assert r.json()["detail"] == runner_mod.PORTFOLIO_UNSUPPORTED_DETAIL


def test_a_leg_number_outside_the_bundle_is_refused():
    key = _key(_signup())
    assert _start(key, BUNDLE, leg=5, bundle_key="b", symbol="BTCUSDT").status_code == 422


def test_a_bundle_with_a_limit_is_refused_even_with_a_leg_number():
    key = _key(_signup())
    capped = {"symbol": "BTCUSDT", "rule_type": "E", "position_side": "long",
              "market": "spot", "leverage": 1, "candle_interval": "1h",
              "period": {"preset": "3m"},
              "params": {"trail_percent": 2.0, "activation_profit": 3.0, "initial_capital": 1000},
              "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0},
              "symbols": ["BTCUSDT", "ETHUSDT"], "bundle_risk": {"max_positions": 1}}
    r = _start(key, capped, leg=0, bundle_key="b", symbol="BTCUSDT")
    assert r.status_code == 422 and "묶음 한도" in r.json()["detail"]


# --- 단일 종목은 그대로다 -----------------------------------------------------

def test_a_single_symbol_run_is_untouched():
    """레그 하나는 지금까지 돌려 온 것과 똑같아야 한다 — 이 설계의 전부다."""
    key = _key(_signup())
    sid = _start(key, A).json()["session_id"]
    row = _session(sid)
    assert row["bundle_key"] == "" and row["bundle_size"] == 0 and row["bundle_digest"] == ""


def test_the_session_view_groups_legs_and_leaves_single_runs_alone():
    token = _signup()
    key = _key(token)
    _start(key, BUNDLE, leg=0, bundle_key="view", symbol="BTCUSDT")
    _start(key, A)
    body = client.get("/api/me/runner/sessions",
                      headers={"Authorization": f"Bearer {token}"}).json()
    bundles = [s for s in body["active"] if s.get("bundle")]
    singles = [s for s in body["active"] if not s.get("bundle")]
    assert len(bundles) == 1 and bundles[0]["bundle"]["size"] == 2
    assert bundles[0]["bundle"]["key"] == "view"
    assert singles, "단일 종목 실행에는 묶음 칸이 없다"
