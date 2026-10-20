"""엔진 세대 게이트 — 실행기가 스스로 판단해도 백테스트와 같은 답이 나오는가 (2026-10-20).

진입 판단이 실행기로 내려가면 엔진이 사용자 PC 마다 다른 세대로 돈다. 웹에서 본 백테스트는
서버의 최신 엔진이 낸 결과인데 손에 든 실행기가 옛 엔진이면 같은 매크로가 **다른 자리에서**
사고판다. 늦게 사는 것은 사용자 눈에 보이지만 다르게 사는 것은 보이지 않는다.

지금은 판단을 서버가 하므로 게이트에 이빨이 없다 — 배포된 실행기는 세대를 밝히지 않고,
밝히지 않으면 통과한다. 이 시험은 **그 통과가 의도된 것**임을 못 박고, 세대를 밝히는
실행기가 나왔을 때 비교가 실제로 작동함을 함께 확인한다.
"""
import pytest
from fastapi.testclient import TestClient

from app import runner as runner_mod
from app import runner_engine as eng
from app.db import RunSession, get_session
from app.main import app
from engine import Macro
from engine import version as engine_version_mod
from engine.version import (
    ENGINE_VERSION,
    meets_engine_version,
    parse_engine_version,
    required_engine_version,
)
from tests.test_runner import _auth, _signup
from tests.test_runner_signals_api import A, _key, _stop, _ticket

client = TestClient(app)


def _start(key, macro, runner_version="20", engine_version=None):
    body = {"symbol": macro["symbol"], "position_side": "long", "leverage": 1, "market": "spot",
            "testnet": True, "human_summary": "t", "macro": macro, "runner_version": runner_version}
    if engine_version is not None:
        body["engine_version"] = engine_version
    return client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})


# --- 숫자 읽기 --------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("1", 1), ("12", 12), (3, 3), ("", 0), (None, 0), ("v3", 0), ("3.0", 0),
    ("-1", 0), (" 4 ", 4), ("1234567", 0), ("３", 0),  # 전각 숫자는 isascii 에서 걸린다
])
def test_세대를_읽는다(raw, expected):
    assert parse_engine_version(raw) == expected


def test_현재_세대는_양수다():
    assert isinstance(ENGINE_VERSION, int) and ENGINE_VERSION >= 1


# --- 매크로가 요구하는 세대 -------------------------------------------------
def test_지금은_어느_매크로도_1_이면_된다():
    """판정이 갈린 적이 없다. 갈리는 변경을 넣으면 _MIN_ENGINE_BY_RULE 에 적어야 한다."""
    assert required_engine_version(Macro.model_validate(A)) == 1
    assert required_engine_version(None) == 1


def test_표에_적으면_요구_세대가_올라간다(monkeypatch):
    """표가 실제로 읽히는지 — 비어 있는 표만 보고 '통과한다' 고 믿으면 안 된다."""
    macro = Macro.model_validate(A)
    monkeypatch.setitem(engine_version_mod._MIN_ENGINE_BY_RULE, "A", 5)
    assert required_engine_version(macro) == 5
    assert meets_engine_version("4", macro) is False
    assert meets_engine_version("5", macro) is True
    assert meets_engine_version("6", macro) is True


def test_세대를_안_밝히면_막지_않는다(monkeypatch):
    """판단을 서버에 맡기는 실행기 — 그 엔진이 몇이든 결과에 영향이 없다."""
    macro = Macro.model_validate(A)
    monkeypatch.setitem(engine_version_mod._MIN_ENGINE_BY_RULE, "A", 99)
    assert meets_engine_version("", macro) is True
    assert meets_engine_version(None, macro) is True
    assert meets_engine_version("아무말", macro) is True


# --- 세션 시작 --------------------------------------------------------------
def test_옛_실행기는_세대를_안_보내도_시작된다(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    key = _key(_signup())
    r = _start(key, A)
    assert r.status_code == 200
    _stop(key, r.json()["session_id"])


def test_낮은_세대는_세션을_못_연다(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    monkeypatch.setitem(engine_version_mod._MIN_ENGINE_BY_RULE, "A", 7)
    key = _key(_signup())
    r = _start(key, A, engine_version="6")
    assert r.status_code == 426
    detail = r.json()["detail"]
    assert runner_mod.ENGINE_REQUIRED_DETAIL in detail
    assert "필요 7" in detail and "지금 6" in detail, "무엇이 모자란지 말해야 사용자가 움직인다"


def test_충분한_세대는_통과하고_기록된다(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    monkeypatch.setitem(engine_version_mod._MIN_ENGINE_BY_RULE, "A", 7)
    key = _key(_signup())
    r = _start(key, A, engine_version="7")
    assert r.status_code == 200
    sid = r.json()["session_id"]
    with get_session() as db:
        assert db.get(RunSession, sid).engine_version == "7"
    _stop(key, sid)


def test_세대가_세션_조회에_보인다(monkeypatch):
    """문의가 들어오면 '그 사람 실행기가 몇 세대였나' 를 로그만 보고 답해야 한다."""
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    token = _signup()
    key = _key(token)
    sid = _start(key, A, engine_version="3").json()["session_id"]
    body = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    mine = next(s for s in body["active"] if s["session_id"] == sid)
    assert mine["engine_version"] == "3"
    _stop(key, sid)


# --- 티켓 청구 --------------------------------------------------------------
def test_낮은_세대는_티켓을_못_가져가고_티켓은_살아_있다(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    monkeypatch.setitem(engine_version_mod._MIN_ENGINE_BY_RULE, "A", 7)
    token = _signup()
    launch_id, ticket = _ticket(token, A)
    r = client.post("/api/runner/launch-tickets/claim",
                    json={"ticket": ticket, "runner_version": "20", "engine_version": "6"})
    assert r.status_code == 426 and runner_mod.ENGINE_REQUIRED_DETAIL in r.json()["detail"]
    status = client.get(f"/api/me/runner/launch-tickets/{launch_id}", headers=_auth(token)).json()
    assert status["status"] == "rejected"
    # 업데이트하면 같은 티켓으로 이어서 받아갈 수 있다 — 소비되지 않았다.
    ok = client.post("/api/runner/launch-tickets/claim",
                     json={"ticket": ticket, "runner_version": "20", "engine_version": "7"})
    assert ok.status_code == 200


def test_세대를_안_보내는_실행기는_티켓을_가져간다(monkeypatch):
    """배포된 실행기 전부가 이 경로를 쓴다 — 여기서 막히면 모두가 멈춘다."""
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    monkeypatch.setitem(engine_version_mod._MIN_ENGINE_BY_RULE, "A", 99)
    token = _signup()
    _, ticket = _ticket(token, A)
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "20"})
    assert r.status_code == 200


# --- 실행기가 실제로 그 값을 보낸다 -----------------------------------------
def test_실행기가_자기_세대를_싣는다():
    """서버 쪽만 만들어 두고 실행기가 안 보내면 게이트는 영원히 잠들어 있다."""
    import sys
    from pathlib import Path

    runner_dir = Path(__file__).resolve().parents[2] / "runner"
    sys.path.insert(0, str(runner_dir))
    try:
        import macro_runner
    finally:
        sys.path.remove(str(runner_dir))
    assert macro_runner.ENGINE_VERSION == ENGINE_VERSION, "실행기가 다른 엔진을 들고 있다"
    # 글자 찾기가 아니라 실제로 만들어지는 payload 를 본다 — 다른 자리의 같은 글자에 속지 않게.
    payload = macro_runner._session_payload(A, testnet=True, mode="mock")
    assert payload["engine_version"] == str(ENGINE_VERSION), "시작 요청에 세대가 없다"
    # 티켓 청구에도 실어야 한다 — 웹 빠른 실행은 이 경로로만 들어온다.
    # 글자 찾기 대신 그 호출의 json= 본문을 구문 트리에서 꺼낸다.
    import ast

    tree = ast.parse((runner_dir / "macro_runner.py").read_text(encoding="utf-8"))
    claim_fn = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_claim_protocol_ticket")
    bodies = [kw.value for call in ast.walk(claim_fn) if isinstance(call, ast.Call)
              for kw in call.keywords if kw.arg == "json" and isinstance(kw.value, ast.Dict)]
    assert bodies, "_claim_protocol_ticket 에서 json= 본문을 못 찾았다"
    keys = {k.value for body in bodies for k in body.keys if isinstance(k, ast.Constant)}
    assert "engine_version" in keys, f"티켓 청구에 세대가 없다 (보낸 칸: {sorted(keys)})"
