"""Inspect real ASGI response headers with all account/data operations stubbed.

No application lifespan, real credentials, real DB operations or outgoing
connections. This proves missing policy, not a demonstrated HTTP cache leak.
"""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def forbidden(*args, **kwargs):
    raise AssertionError("Audit forbids external network/real account operations")


def main():
    with TemporaryDirectory(prefix="ggp-header-audit-") as directory, ExitStack() as stack:
        for name in ("DATABASE_URL", "GEMINI_API_KEY", "COINDESK_API_KEY", "PREFECT_API_URL"):
            os.environ[name] = ""
        os.environ["SQLITE_PATH"] = str(Path(directory) / "unused.db")
        for name in ("POSITION_NEWS_EMBEDDED_ENABLED", "PUBLIC_NEWS_EMBEDDED_ENABLED",
                     "LEADERBOARD_BACKGROUND_ENABLED", "WHALE_TRADE_EMBEDDED_ENABLED"):
            os.environ[name] = "false"
        os.environ["NEWS_IMAGES_DISABLED"] = "1"
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        stack.enter_context(patch.object(socket.socket, "connect", forbidden))
        stack.enter_context(patch.object(socket.socket, "connect_ex", forbidden))
        stack.enter_context(patch.object(socket, "create_connection", forbidden))
        from fastapi.testclient import TestClient
        from app import main as module

        user = SimpleNamespace(id=101)
        module.api_app.dependency_overrides[module.auth_mod.current_user] = lambda: user
        module.api_app.dependency_overrides[module.auth_mod.current_user_in_session] = lambda: user
        module.api_app.dependency_overrides[module.request_session] = lambda: None
        stubs = (
            (module.auth_mod, "user_view", {"id": 101, "points_balance": 0}),
            (module.account_mod, "dashboard", {"user": {"id": 101}}),
            (module.board_mod, "my_posts", []),
            (module.user_macros_mod, "list_macros", {"items": []}),
            (module.runner_mod, "get_or_create_key", {"key": "fake-audit-key"}),
            (module.runner_mod, "list_sessions", {"items": []}),
            (module.auth_mod, "make_runner_session_stream_token", {"token": "fake-audit-token"}),
        )
        for target, attribute, value in stubs:
            stack.enter_context(patch.object(target, attribute, return_value=value))
        # No `with TestClient`: deliberately omit application startup/workers.
        client = TestClient(module.app)
        records = []
        try:
            routes = [
                ("GET", "/api/auth/me"), ("GET", "/api/me/dashboard"),
                ("GET", "/api/me/macros"), ("GET", "/api/me/runner/key"),
                ("GET", "/api/me/runner/sessions"),
                ("POST", "/api/me/runner/sessions/stream-token"),
            ]
            for method, path in routes:
                response = client.request(method, path)
                assert response.status_code == 200, (path, response.status_code)
                records.append({
                    "path": path, "method": method, "status": response.status_code,
                    "cache_control": response.headers.get("cache-control"),
                    "etag": response.headers.get("etag"),
                })
            assert all(record["cache_control"] is None for record in records[:5])
            assert records[-1]["cache_control"] == "no-store"
        finally:
            client.close()
            module.api_app.dependency_overrides.clear()
        output = {
            "scope": "real ASGI headers; fake user and stubbed data methods; no lifespan or real DB",
            "interpretation": "Baseline policy gap, not evidence of actual HTTP cache disclosure. Assertions should change after a fix.",
            "records": records,
        }
        Path(__file__).with_name("private-headers.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
