"""Run the affected backend suites with temporary databases and no external I/O.

Run with the project's Python environment. tests/conftest.py isolates credentials,
databases and workers before importing the application.
"""
import json
import os
from pathlib import Path
import socket
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.chdir(ROOT / "backend")
sys.path.insert(0, str(ROOT / "backend"))
connect = socket.socket.connect


def offline(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError("External network prohibited in regression verification")
    return connect(self, address)


socket.socket.connect = offline
patterns = [
    "test_news*.py", "test_public_news*.py", "test_position_news*.py",
    "test_agent_position_news.py", "test_leaderboard*.py", "test_chat*.py",
    "test_challenge.py", "test_marketplace.py", "test_api.py",
    "test_request_scoped_db.py", "test_v6.py", "test_v7.py",
    "test_community_summaries.py", "test_http_runtime.py", "test_db*.py",
]
files = sorted({str(path) for pattern in patterns for path in Path("tests").glob(pattern)})


class Report:
    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        result = {
            "exit_code": int(exitstatus),
            "passed": len(terminalreporter.stats.get("passed", [])),
            "failed": len(terminalreporter.stats.get("failed", [])),
            "errors": len(terminalreporter.stats.get("error", [])),
            "skipped": len(terminalreporter.stats.get("skipped", [])),
            "test_files": files,
            "external_network": "blocked",
            "database": "temporary SQLite",
        }
        (HERE / "backend-regression-results.json").write_text(json.dumps(result, indent=2) + "\n")


raise SystemExit(pytest.main(["-q", "--disable-warnings", *files], plugins=[Report()]))
