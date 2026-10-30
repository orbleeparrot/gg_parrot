"""구동 중 설정 변경의 **증빙** — 누가 · 언제 · 무엇을 · 포지션을 들고 있었는지 (2026-10-30).

사장님 요청: "매크로를 수정했다는 게 우리 쪽에 기록으로 남아야 한다. 수정해서 잘못
구동했을 때 우리 책임 소재가 아니라 수정 쪽 책임 소재 증빙이기 때문이다."

그래서 이 시험이 지키는 것은 둘이다.

1. **세션이 지워져도 기록은 남는다.** 끝난 세션은 보존 작업이 주기적으로 지우고
   사용자도 목록에서 지울 수 있다. 거기 딸려 사라지면 증빙이 아니다.
2. **쓴 뒤로는 바뀌지 않는다.** 나중에 바뀌는 칸은 실행기가 반영을 확인한 시각 하나뿐이다.
"""
from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import runner as runner_mod
from app.db import RunnerSettingChange, RunSession, get_session
from app.main import app

client = TestClient(app)
# 설정 변경을 지원하는 실행기 버전 — 관문보다 높으면 된다.
NEW_RUNNER = str(int(runner_mod.SETTINGS_MIN_VERSION))


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _signup() -> str:
    tag = secrets.token_hex(4)
    return client.post("/api/auth/signup", json={
        "email": f"set{tag}@ex.com", "username": f"set_{tag}", "password": "password123",
    }).json()["token"]


def _macro(symbol: str = "BTCUSDT") -> dict:
    return {
        "symbol": symbol, "rule_type": "F", "position_side": "long", "leverage": 1,
        "candle_interval": "1h",
        "params": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70,
                   "initial_capital": 1_000_000},
        "risk": {"invest_ratio": 1.0, "stop_loss_pct": 3.0},
        "period": {"preset": "3m"},
    }


_CREATED: list[int] = []
# 이 시험이 시작되기 전까지 쌓여 있던 증빙 줄의 마지막 번호. 세션을 지우면 SQLite 가
# 세션 번호를 재사용하므로, 번호만으로 고르면 앞 시험의 줄이 섞여 든다.
_WATERMARK = 0


@pytest.fixture(autouse=True)
def _clean_up_sessions():
    """이 파일이 띄운 세션을 치운다 — **증빙 줄은 남긴다.**

    시험 DB(SQLite) 는 파일 전체가 공유한다. 살아 있는 세션을 남기면 "되살릴 세션은
    이것 하나" 를 세는 다른 파일의 시험(test_runner_engine.py)이 깨진다.
    증빙 줄을 함께 지우면 "세션이 지워져도 남는다" 를 보는 시험이 거짓이 되므로 건드리지 않는다.
    """
    global _WATERMARK
    with get_session() as db:
        rows = list(db.exec(select(RunnerSettingChange.id)))
    _WATERMARK = max(rows) if rows else 0
    yield
    with get_session() as db:
        for session_id in _CREATED:
            row = db.get(RunSession, session_id)
            if row is not None:
                db.delete(row)
        db.commit()
    _CREATED.clear()


def _start(token: str, *, version: str = NEW_RUNNER) -> tuple[int, str]:
    """세션 하나를 띄운다. (session_id, runner_key)"""
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    started = client.post("/api/runner/start", json={
        "symbol": "BTCUSDT", "runner_version": version, "macro": _macro(),
    }, headers={"X-Runner-Key": key})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    _CREATED.append(session_id)
    return session_id, key


def _change(token: str, session_id: int, changes: dict, **over):
    body = {"changes": changes}
    body.update(over)
    return client.post(f"/api/me/runner/sessions/{session_id}/settings", json=body,
                       headers=_auth(token))


def _rows(session_id: int) -> list[RunnerSettingChange]:
    with get_session() as db:
        return list(db.exec(select(RunnerSettingChange)
                            .where(RunnerSettingChange.session_id == session_id,
                                   RunnerSettingChange.id > _WATERMARK)
                            .order_by(RunnerSettingChange.id)))


def _session(session_id: int) -> RunSession:
    with get_session() as db:
        return db.get(RunSession, session_id)


# --- 기록이 남는다 -----------------------------------------------------------

def test_a_change_writes_one_record_with_both_sides():
    token = _signup()
    session_id, _key = _start(token)
    res = _change(token, session_id, {"params": {"initial_capital": 2_000_000}})
    assert res.status_code == 200, res.text

    rows = _rows(session_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "web" and row.apply_mode == "immediate"
    assert row.changed_fields == "params.initial_capital"
    assert "1,000,000" in row.summary and "2,000,000" in row.summary
    before, after = json.loads(row.before_json), json.loads(row.after_json)
    assert before["params"]["initial_capital"] == 1_000_000
    assert after["params"]["initial_capital"] == 2_000_000
    assert row.before_digest and row.after_digest and row.before_digest != row.after_digest
    assert row.runner_version == NEW_RUNNER
    assert row.created_at and row.created_ms > 0


def test_the_record_says_whether_a_position_was_open():
    """책임 소재에서 가장 자주 묻게 될 값 — 들고 있는데 고쳤는가."""
    token = _signup()
    session_id, key = _start(token)
    client.post("/api/runner/heartbeat", json={
        "session_id": session_id, "last_price": 100.0, "in_position": True,
        "position_qty": 2.0, "entry_price": 95.0, "unrealized_pct": 5.0,
    }, headers={"X-Runner-Key": key})

    _change(token, session_id, {"risk": {"stop_loss_pct": 1.0}}, apply_mode="immediate")
    row = _rows(session_id)[-1]
    assert row.in_position is True
    assert row.position_qty == 2.0 and row.entry_price == 95.0 and row.unrealized_pct == 5.0


def test_the_source_tells_which_screen_did_it():
    token = _signup()
    session_id, _key = _start(token)
    _change(token, session_id, {"risk": {"stop_loss_pct": 2.0}}, source="runner")
    assert _rows(session_id)[-1].source == "runner"


def test_an_unknown_source_is_refused():
    token = _signup()
    session_id, _key = _start(token)
    assert _change(token, session_id, {"risk": {"stop_loss_pct": 2.0}}, source="누군가").status_code == 400
    assert _rows(session_id) == []


# --- 세션이 지워져도 남는다 --------------------------------------------------

def test_the_record_outlives_a_deleted_session():
    """이 표의 존재 이유. 세션과 함께 지워지면 증빙이 아니다."""
    token = _signup()
    session_id, key = _start(token)
    _change(token, session_id, {"params": {"initial_capital": 2_000_000}})
    assert len(_rows(session_id)) == 1

    client.post("/api/runner/stopped", json={"session_id": session_id, "status": "stopped"},
                headers={"X-Runner-Key": key})
    deleted = client.delete(f"/api/me/runner/sessions/{session_id}", headers=_auth(token))
    assert deleted.status_code == 200, deleted.text
    assert _session(session_id) is None, "세션은 지워졌다"
    assert len(_rows(session_id)) == 1, "그런데 기록은 남아 있어야 한다"


def test_the_deletion_helper_does_not_touch_the_record_table():
    """세션을 지우는 길은 ``_delete_session_rows`` 하나다 — 보존 작업도 사용자 삭제도 여기로 온다.

    위의 삭제 시험은 지금 동작을 지키고, 이 시험은 **그 한 곳**을 지킨다. 누군가 나중에
    "세션 지울 때 같이 지우자" 며 이 표를 거기 더하면 여기서 깨진다.
    """
    token = _signup()
    session_id, _key = _start(token)
    _change(token, session_id, {"params": {"initial_capital": 2_000_000}})
    with get_session() as db:
        runner_mod._delete_session_rows(db, [session_id])
        db.commit()
    assert _session(session_id) is None
    assert len(_rows(session_id)) == 1


# --- 세션에도 흔적이 남는다 --------------------------------------------------

def test_the_session_counts_the_changes_and_takes_the_new_digest():
    """``macro_origin`` 은 **시작 시점**의 출처라 "시작 뒤에 고쳤다" 를 말하지 못한다."""
    token = _signup()
    session_id, _key = _start(token)
    before = _session(session_id)
    assert before.settings_changed_count == 0

    _change(token, session_id, {"params": {"initial_capital": 2_000_000}})
    _change(token, session_id, {"risk": {"stop_loss_pct": 1.0}})
    after = _session(session_id)
    assert after.settings_changed_count == 2
    assert after.macro_digest == _rows(session_id)[-1].after_digest
    assert after.macro_origin == before.macro_origin, "시작 시점의 출처는 그대로 둔다"
    assert json.loads(after.macro_json)["params"]["initial_capital"] == 2_000_000


# --- 예약(포지션 정리 뒤) ----------------------------------------------------

def test_a_reserved_change_waits_for_the_position_to_close():
    token = _signup()
    session_id, key = _start(token)
    beat = {"session_id": session_id, "last_price": 100.0, "in_position": True,
            "position_qty": 1.0, "entry_price": 95.0, "unrealized_pct": 5.0}
    client.post("/api/runner/heartbeat", json=beat, headers={"X-Runner-Key": key})

    res = _change(token, session_id, {"params": {"entry_threshold": 20}}, apply_mode="next_entry")
    assert res.status_code == 200 and res.json()["applied"] is False
    held = _session(session_id)
    assert held.settings_changed_count == 0, "아직 적용 전이다"
    assert json.loads(held.macro_json)["params"]["entry_threshold"] == 30
    assert _rows(session_id)[-1].applied_ms == 0

    # 포지션이 비는 첫 heartbeat 에서 적용된다.
    client.post("/api/runner/heartbeat", json={**beat, "in_position": False, "position_qty": 0.0},
                headers={"X-Runner-Key": key})
    applied = _session(session_id)
    assert applied.settings_changed_count == 1
    assert json.loads(applied.macro_json)["params"]["entry_threshold"] == 20
    assert applied.pending_macro_json == "" and applied.pending_change_id == 0
    assert _rows(session_id)[-1].applied_ms > 0


def test_without_a_position_there_is_nothing_to_reserve():
    # 예약은 '들고 있는 포지션을 지키려고' 있다 — 없으면 바로 적용한다.
    token = _signup()
    session_id, _key = _start(token)
    res = _change(token, session_id, {"params": {"entry_threshold": 20}}, apply_mode="next_entry")
    assert res.json()["applied"] is True and res.json()["apply_mode"] == "immediate"
    assert _session(session_id).settings_changed_count == 1


# --- 관문 -------------------------------------------------------------------

def test_an_old_runner_is_refused_so_the_two_sides_cannot_drift():
    """옛 실행기는 settings 를 무시한다 — 막지 않으면 서버 전략만 바뀌고 안전망은 옛 값으로 남는다."""
    token = _signup()
    session_id, _key = _start(token, version=str(int(runner_mod.SETTINGS_MIN_VERSION) - 1))
    res = _change(token, session_id, {"risk": {"stop_loss_pct": 1.0}})
    assert res.status_code == 426
    assert _rows(session_id) == [], "거절한 요청은 기록도 남기지 않는다"


def test_a_stopped_session_cannot_be_changed():
    token = _signup()
    session_id, key = _start(token)
    client.post("/api/runner/stopped", json={"session_id": session_id, "status": "stopped"},
                headers={"X-Runner-Key": key})
    assert _change(token, session_id, {"risk": {"stop_loss_pct": 1.0}}).status_code == 409


def test_someone_elses_session_is_not_found():
    owner, stranger = _signup(), _signup()
    session_id, _key = _start(owner)
    assert _change(stranger, session_id, {"risk": {"stop_loss_pct": 1.0}}).status_code == 404
    assert _rows(session_id) == []


def test_an_identity_field_is_refused_and_leaves_no_record():
    token = _signup()
    session_id, _key = _start(token)
    res = _change(token, session_id, {"leverage": 3})
    assert res.status_code == 400 and "바꿀 수 없어요" in res.json()["detail"]
    assert _rows(session_id) == []


# --- 실행기가 받는 값 --------------------------------------------------------

def test_the_heartbeat_hands_the_runner_the_values_it_uses():
    token = _signup()
    session_id, key = _start(token)
    _change(token, session_id, {"params": {"initial_capital": 2_000_000},
                                "risk": {"stop_loss_pct": 1.5}})
    beat = client.post("/api/runner/heartbeat", json={
        "session_id": session_id, "last_price": 100.0, "in_position": False,
    }, headers={"X-Runner-Key": key}).json()
    settings = beat["settings"]
    assert settings["initial_capital"] == 2_000_000
    assert settings["stop_loss_pct"] == 1.5
    assert settings["changed_count"] == 1
    assert settings["digest"] == _session(session_id).macro_digest


# --- 화면이 받는 재료 --------------------------------------------------------

def test_the_session_view_carries_the_editable_fields():
    token = _signup()
    session_id, _key = _start(token)
    view = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    row = next(s for s in view["active"] if s["session_id"] == session_id)
    settings = row["settings"]
    assert settings["editable"] is True and settings["reason"] == ""
    paths = {f["path"] for f in settings["fields"]}
    assert "params.initial_capital" in paths and "risk.stop_loss_pct" in paths
    assert settings["changed_count"] == 0 and settings["pending"] is False


def test_an_old_runners_session_says_why_it_cannot_be_edited():
    token = _signup()
    session_id, _key = _start(token, version=str(int(runner_mod.SETTINGS_MIN_VERSION) - 1))
    view = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    row = next(s for s in view["active"] if s["session_id"] == session_id)
    assert row["settings"]["editable"] is False
    assert "업데이트" in row["settings"]["reason"], "못 바꾸면 이유를 말해야 한다"
    assert row["settings"]["fields"] == []


# --- 실행기가 반영했는가 ------------------------------------------------------

def test_the_runner_echo_marks_the_record_as_applied():
    """증빙은 "바꿨다" 뿐 아니라 "실행기가 받아 돌았다" 까지 말할 수 있어야 한다."""
    token = _signup()
    session_id, key = _start(token)
    _change(token, session_id, {"params": {"initial_capital": 2_000_000}})
    row = _rows(session_id)[-1]
    assert row.applied_ms == 0, "아직 실행기가 확인하기 전이다"

    client.post("/api/runner/heartbeat", json={
        "session_id": session_id, "last_price": 100.0, "in_position": False,
        "settings_digest": row.after_digest,
    }, headers={"X-Runner-Key": key})
    assert _rows(session_id)[-1].applied_ms > 0


def test_an_old_digest_never_marks_the_wrong_record():
    # 늦게 도착한 heartbeat 가 엉뚱한 줄에 시각을 찍으면 증빙이 거짓이 된다.
    token = _signup()
    session_id, key = _start(token)
    _change(token, session_id, {"params": {"initial_capital": 2_000_000}})
    stale = _rows(session_id)[-1].after_digest
    _change(token, session_id, {"params": {"initial_capital": 3_000_000}})

    client.post("/api/runner/heartbeat", json={
        "session_id": session_id, "last_price": 100.0, "settings_digest": stale,
    }, headers={"X-Runner-Key": key})
    assert all(row.applied_ms == 0 for row in _rows(session_id)), "옛 지문으로는 아무것도 확정하지 않는다"


# --- 실행기 화면에서 바꾸기 --------------------------------------------------

def test_the_runner_can_change_its_own_session_with_its_key():
    """실행기는 로그인 토큰이 없다 — 회원 키로 들어오되 하는 일은 웹과 같은 길이다."""
    token = _signup()
    session_id, key = _start(token)
    res = client.post(f"/api/runner/sessions/{session_id}/settings",
                      json={"changes": {"params": {"initial_capital": 2_000_000}}},
                      headers={"X-Runner-Key": key})
    assert res.status_code == 200, res.text
    row = _rows(session_id)[-1]
    assert row.source == "runner", "요청이 뭐라 하든 출처는 실행기다"


def test_the_runner_key_cannot_touch_another_members_session():
    owner, stranger = _signup(), _signup()
    session_id, _key = _start(owner)
    other_key = client.get("/api/me/runner/key", headers=_auth(stranger)).json()["key"]
    res = client.post(f"/api/runner/sessions/{session_id}/settings",
                      json={"changes": {"risk": {"stop_loss_pct": 1.0}}},
                      headers={"X-Runner-Key": other_key})
    assert res.status_code == 404
    assert _rows(session_id) == []


def test_the_runner_cannot_forge_the_source():
    token = _signup()
    session_id, key = _start(token)
    client.post(f"/api/runner/sessions/{session_id}/settings",
                json={"changes": {"risk": {"stop_loss_pct": 1.0}}, "source": "web"},
                headers={"X-Runner-Key": key})
    assert _rows(session_id)[-1].source == "runner"
