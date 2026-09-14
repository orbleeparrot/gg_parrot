"""Backend regression runner: temporary DBs and blocked external connections.

Run with backend Python dependencies. Optional arguments are forwarded to pytest;
without arguments the full backend suite is run. Writes a separate after report.
"""
import json
import os
from pathlib import Path
import socket
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPORT = Path(os.environ.get("CACHE_VERIFICATION_REPORT", str(HERE / "backend-after-results.json"))).resolve()
os.chdir(ROOT / "backend")
sys.path.insert(0, str(ROOT / "backend"))
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex


def offline(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError("External network forbidden during regression verification")
    return original_connect(self, address)


def offline_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError("External network forbidden during regression verification")
    return original_connect_ex(self, address)


socket.socket.connect = offline
socket.socket.connect_ex = offline_ex
arguments = sys.argv[1:] or ["tests"]


class Report:
    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        result = {
            "exit_code": int(exitstatus), "arguments": arguments,
            "passed": len(terminalreporter.stats.get("passed", [])),
            "failed": len(terminalreporter.stats.get("failed", [])),
            "errors": len(terminalreporter.stats.get("error", [])),
            "skipped": len(terminalreporter.stats.get("skipped", [])),
            "external_network": "blocked", "database": "temporary SQLite",
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(result, indent=2) + "\n")


raise SystemExit(pytest.main(["-q", "--disable-warnings", *arguments], plugins=[Report()]))
