"""바이낸스 연결 도우미(실행기 v19) — 공개 실행기가 v19 에 닿았을 때만 웹 빠른 실행이 v19 이상을 요구한다."""
from fastapi.testclient import TestClient

from app import runner as runner_mod
from app.db import RunnerLaunchTicket, get_session
from app.main import app
from tests.test_runner import _auth, _claimed_launch, _progress, _signup
from tests.test_runner_signals_api import A, _ticket

client = TestClient(app)
V19 = "https://github.com/orbleeparrot/gg_parrot/releases/download/runner-v19/ggparrot-runner.exe"


def test_gate_is_off_until_the_published_runner_has_the_helper(monkeypatch):
    monkeypatch.delenv("RUNNER_DOWNLOAD_URL", raising=False)
    assert runner_mod.binance_helper_active() is False  # 지금 공개본(v18)
    _launch, ticket = _ticket(_signup(), A)
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "18"})
    assert r.status_code == 200, r.text


def test_published_v19_requires_the_helper_for_binance_quick_run(monkeypatch):
    monkeypatch.setenv("RUNNER_DOWNLOAD_URL", V19)
    assert runner_mod.binance_helper_active() is True
    token = _signup()
    launch_id, ticket = _ticket(token, A)
    old = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "18"})
    assert old.status_code == 426
    assert "v19" in old.json()["detail"]
    with get_session() as db:
        assert db.get(RunnerLaunchTicket, launch_id).claimed_at == ""  # 소비하지 않는다
    status = client.get(f"/api/me/runner/launch-tickets/{launch_id}", headers=_auth(token)).json()
    assert status["status"] == "rejected"
    assert status["min_runner_version"] == "19"  # 웹이 '몇 버전이 필요한지' 를 같은 숫자로 말한다
    new = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "19"})
    assert new.status_code == 200, new.text


def test_download_info_announces_the_helper_only_when_published(monkeypatch):
    monkeypatch.delenv("RUNNER_DOWNLOAD_URL", raising=False)
    assert client.get("/api/runner/download/info").json()["binance_helper_min_runner_version"] == ""
    monkeypatch.setenv("RUNNER_DOWNLOAD_URL", V19)
    assert client.get("/api/runner/download/info").json()["binance_helper_min_runner_version"] == "19"


def test_progress_carries_the_binance_site_but_not_for_domestic():
    token = _signup()
    launch_id, runner_key = _claimed_launch(token)
    sent = client.post(f"/api/runner/launch-tickets/{launch_id}/progress", json=_progress(env="demo"),
                       headers={"X-Runner-Key": runner_key})
    assert sent.status_code == 200, sent.text
    status = client.get(f"/api/me/runner/launch-tickets/{launch_id}", headers=_auth(token)).json()
    assert status["progress"]["env"] == "demo"
    bad = client.post(f"/api/runner/launch-tickets/{launch_id}/progress", json=_progress(env="mainnet"),
                      headers={"X-Runner-Key": runner_key})
    assert bad.status_code == 422
    domestic = client.post(f"/api/runner/launch-tickets/{launch_id}/progress", json=_progress(exchange="upbit", env="live"),
                           headers={"X-Runner-Key": runner_key})
    assert domestic.status_code == 422
