"""Importing the HTTP app does not migrate a live database."""
import os
import subprocess
import sys


def test_app_import_never_initializes_schema_and_lifespan_initializes_once(tmp_path):
    script = '''
import asyncio
from unittest.mock import AsyncMock, Mock
from app import db
db.init_db = Mock(side_effect=AssertionError("schema initialization during import"))
from app import main
db.init_db.assert_not_called()
main.init_db = Mock()
main.position_news_runtime.start = Mock()
main.position_news_runtime.stop = AsyncMock()
main.paper_mod.shutdown_running_sessions = AsyncMock()
main.optimize_runtime_mod.shutdown = Mock()
main.ai_runtime_mod.close_ai_runtime = Mock()
main.http_runtime_mod.close_http_runtime = Mock()
async def start():
    async with main.lifespan(main.app):
        main.init_db.assert_called_once_with()
        main.position_news_runtime.start.assert_called_once_with()
asyncio.run(start())
main.init_db.assert_called_once_with()
'''
    env = {**os.environ, "DATABASE_URL": "", "SQLITE_PATH": str(tmp_path / "untouched.db"),
           "ANTHROPIC_API_KEY": "", "COINDESK_API_KEY": "", "PREFECT_API_URL": ""}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "untouched.db").exists()
