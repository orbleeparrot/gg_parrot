"""Runner -> embedded collection -> owned feed -> confirmed stop, no external I/O."""
import secrets
import time

from fastapi.testclient import TestClient

from app.main import app
from app import news, whales
from app.agent_features.position_news import repository


def test_web_lifespan_collects_new_ticker_and_preserves_close_result(monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_EMBEDDED_ENABLED", "true")
    monkeypatch.setenv("POSITION_NEWS_SCAN_SECONDS", "1")
    fetched = []
    def fetch(asset):
        fetched.append(asset)
        return {"symbol": asset, "coin_name": asset, "updated_at": "2026-09-07T01:00:00Z",
                "items": [{"title": f"{asset} 네트워크 업데이트", "source": "테스트 뉴스",
                           "url": "https://example.invalid/article"}]}
    monkeypatch.setattr(news, "fetch_coin_news_for_collector", fetch)
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *_: [])
    ticker = "Q" + secrets.token_hex(3).upper()
    with TestClient(app) as client:
        account = client.post("/api/auth/signup", json={
            "email": f"{ticker}@example.com", "username": ticker, "password": "password123",
        }).json()
        auth = {"Authorization": f"Bearer {account['token']}"}
        key = client.get("/api/me/runner/key", headers=auth).json()["key"]
        runner_auth = {"X-Runner-Key": key}
        session_id = client.post("/api/runner/start", headers=runner_auth,
                                 json={"symbol": ticker + "USDT"}).json()["session_id"]
        path = f"/api/me/agents/sessions/{session_id}/position-news"
        deadline = time.monotonic() + 5
        payload = {}
        while time.monotonic() < deadline:
            response = client.get(path, headers=auth)
            assert response.status_code == 200
            payload = response.json()
            if payload["items"]:
                break
            time.sleep(.02)
        assert payload["items"][0]["title"] == f"{ticker} 네트워크 업데이트"
        assert fetched.count(ticker) == 1
        assert client.get(path).status_code == 401
        assert client.get(f"/api/me/agents/sessions/{session_id}/whale-activity", headers=auth).json()["status"] == "empty"
        client.post("/api/runner/heartbeat", headers=runner_auth, json={
            "session_id": session_id, "in_position": True, "position_qty": 2,
            "entry_price": 100, "last_price": 105, "unrealized_pct": 5,
        })
        client.post(f"/api/me/runner/sessions/{session_id}/request-stop", headers=auth,
                    json={"mode": "close_and_stop"})
        heartbeat = client.post("/api/runner/heartbeat", headers=runner_auth,
                                json={"session_id": session_id, "in_position": True, "position_qty": 2})
        assert heartbeat.json()["action"] == "close_and_stop"
        response = client.post("/api/runner/stopped", headers=runner_auth, json={
            "session_id": session_id, "status": "stopped", "note": "청산 완료 후 종료",
            "snapshot": {"in_position": False, "position_qty": 0, "entry_price": 0, "realized_pnl": 10},
        })
        assert response.status_code == 200
        recent = client.get("/api/me/runner/sessions", headers=auth).json()["recent"]
        ended = next(item for item in recent if item["session_id"] == session_id)
        assert ended["status"] == "stopped" and ended["in_position"] is False
        assert ended["realized_pnl"] == 10
        assert ticker not in repository.discover_tracked_symbols()
