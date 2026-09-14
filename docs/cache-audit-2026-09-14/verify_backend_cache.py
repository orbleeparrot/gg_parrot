#!/usr/bin/env python3
"""Reproduce the cache audit's current behavior without provider or application DB access.

Run from any directory with the backend's Python environment:
    python -B docs/cache-audit-2026-09-14/verify_backend_cache.py

This is an audit reproducer, not a regression suite asserting desirable behavior.
All market responses are fixtures. SQLite files live in TemporaryDirectory and
are deleted after the run. The 291-key case is synthetic stress, not production
traffic or a measurement of production memory use or latency.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Do not load app.main, dotenv, app.db, workers, or real credentials. Blanking
# these is additional protection if module imports change in a later version.
for name in ("DATABASE_URL", "GEMINI_API_KEY", "COINDESK_API_KEY", "PREFECT_API_URL"):
    os.environ[name] = ""


def deny_network(*_args, **_kwargs):
    raise AssertionError("Network access is forbidden in this offline audit")


def current_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def reproduce() -> dict:
    # These imports are intentionally limited to modules that do not initialize
    # application persistence or start workers.
    from app import chart, feargreed, hotcoins, kimchi
    from app.data import binance
    from app.http_runtime import SingleFlightGroup

    assert "app.main" not in sys.modules
    assert "app.db" not in sys.modules

    results = {}
    rows = [{"symbol": "BTCUSDT", "quoteVolume": "20000000",
             "priceChangePercent": "5", "lastPrice": "50000"}]
    hotcoins._cache.clear()
    with patch.object(hotcoins, "_fetch_tickers", return_value=rows) as fetch:
        for limit in (3, 10, 20, 3, 10):
            hotcoins.get_hot_coins(limit)
        assert fetch.call_count == 3
        results["hotcoins_limit_specific_cache"] = {
            "requested_limits": [3, 10, 20, 3, 10],
            "fixture_upstream_calls": fetch.call_count,
            "note": "Current page callers use limit=10; this demonstrates parameter-specific duplication.",
        }

    feargreed._cache = ({"ok": True, "value": 50}, 0)
    with patch.object(feargreed, "_fetch", return_value=None) as fetch:
        responses = [feargreed.get_fear_greed() for _ in range(5)]
        stale_count = sum(bool(response.get("stale")) for response in responses)
        assert fetch.call_count == stale_count == 5
        results["expired_cache_failure_retries"] = {
            "sequential_requests": 5, "fixture_upstream_calls": fetch.call_count,
            "stale_responses": stale_count,
        }

    entered, release = threading.Event(), threading.Event()
    group = SingleFlightGroup()

    def load():
        entered.set()
        if not release.wait(5):
            raise AssertionError("Audit did not release the synthetic loader")
        return "fresh"

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(group.run, "key", load, stale_value="old")
        try:
            assert entered.wait(2), "Synthetic loader did not start"
            waits = not first.done()
            follower = group.run("key", load, stale_value="old")
            assert waits and follower == ("old", "stale")
        finally:
            release.set()
        first_result = first.result(timeout=2)
        results["stale_singleflight"] = {
            "first_expired_request_waits_for_loader": waits,
            "concurrent_follower": list(follower), "first_result": list(first_result),
            "note": "Event ordering verifies blocking; this is not a latency benchmark.",
        }

    chart._cache.clear()
    chart._live_cache.clear()
    bar = {"t": 0, "o": 1.0, "h": 2.0, "l": 0.0, "c": 1.0,
           "v": 1.0, "closed": True}
    with patch.object(chart, "get_recent_klines", return_value=[bar]), \
            patch.object(chart.time, "time", return_value=1000):
        for limit in range(10, 301):
            chart.get_candles("BTCUSDT", limit=limit)
    with patch.object(chart, "get_recent_klines", return_value=[bar]), \
            patch.object(chart.time, "time", return_value=2000):
        chart.get_candles("ETHUSDT")
        expired = sum(expiry <= 2000 for _, expiry in chart._cache.values())
        assert expired == 291 and len(chart._cache) == 292
        results["chart_expired_key_retention"] = {
            "synthetic_limit_range": [10, 300], "expired_entries_remaining": expired,
            "total_entries_after_new_symbol": len(chart._cache),
            "note": "291 synthetic keys; neither actual production traffic nor measured memory consumption.",
        }

    kimchi._cache = {"fx:USDKRW": (1500, 0)}

    class FailedHTTP:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("offline fixture")

    with patch.object(kimchi, "FX_FALLBACK", 1380.0), \
            patch.object(kimchi, "get_http_client", return_value=FailedHTTP()):
        rate, fallback = kimchi._usdkrw()
        assert rate == 1380.0 and fallback
        results["expired_fx_on_failure"] = {
            "previous_valid_rate": 1500, "fixture_configured_fallback": 1380,
            "returned_rate": rate, "is_fallback": fallback,
        }

    with tempfile.TemporaryDirectory(prefix="ggp-cache-audit-") as tmp:
        temp_root = Path(tmp).resolve()
        original_connect = sqlite3.connect

        def isolated_connect(database, *args, **kwargs):
            path = Path(database).resolve()
            assert path.is_relative_to(temp_root), "Attempted non-temporary SQLite access"
            return original_connect(database, *args, **kwargs)

        with patch.object(binance, "_CACHE_DIR", tmp), \
                patch.object(sqlite3, "connect", side_effect=isolated_connect):
            # A candle opening at 60s closes at 119.999s. The requested periods
            # end at 90s and 91s, so the final candle is still in progress.
            raw = [[0, "10", "10", "10", "10", "1", 59999],
                   [60000, "10", "12", "9", "11", "1", 119999]]
            with patch.object(binance, "_DB_PATH", str(temp_root / "open-bar.db")), \
                    patch.object(binance, "_fetch_binance", return_value=raw) as fetch:
                first, _ = binance.get_klines("BTCUSDT", 0, 90000, interval="1m", allow_synthetic=False)
                fetch.return_value = [raw[0], [60000, "10", "15", "9", "14", "1", 119999]]
                second, source = binance.get_klines("BTCUSDT", 0, 91000, interval="1m", allow_synthetic=False)
                assert second["close"].iloc[-1] == 11 and fetch.call_count == 1 and source == "cache"
                results["historical_open_bar_cache"] = {
                    "first_close": float(first["close"].iloc[-1]),
                    "second_close": float(second["close"].iloc[-1]),
                    "new_fixture_upstream_close": 14,
                    "fixture_upstream_calls": fetch.call_count, "second_source": source,
                }

            raw = [[i * 60000, "10", "10", "10", "10", "1", (i + 1) * 60000 - 1]
                   for i in range(10)]
            with patch.object(binance, "_DB_PATH", str(temp_root / "extended-window.db")), \
                    patch.object(binance, "_fetch_binance", return_value=raw) as fetch:
                binance.get_klines("BTCUSDT", 0, 600000, interval="1m", allow_synthetic=False)
                fetch.return_value = raw + [
                    [600000, "10", "10", "10", "10", "1", 659999],
                    [660000, "10", "10", "10", "10", "1", 719999],
                ]
                binance.get_klines("BTCUSDT", 0, 720000, interval="1m", allow_synthetic=False)
                windows = [{"start_ms": call.args[2], "end_ms": call.args[3]}
                           for call in fetch.call_args_list]
                assert windows == [{"start_ms": 0, "end_ms": 600000},
                                   {"start_ms": 0, "end_ms": 720000}]
                results["extended_history_refetch"] = {"fixture_upstream_windows": windows}

    return {"audit_date": "2026-09-14", "source_commit": current_commit(),
            "scope": "Offline behavioral reproduction, not production telemetry",
            "network": "Socket connections and DNS resolution prohibited",
            "persistence": "Temporary SQLite only; app.main and app.db were not imported",
            "checks": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).with_name("backend-cache-results.json"))
    args = parser.parse_args()
    with patch.object(socket.socket, "connect", side_effect=deny_network), \
            patch.object(socket.socket, "connect_ex", side_effect=deny_network), \
            patch.object(socket, "create_connection", side_effect=deny_network), \
            patch.object(socket, "getaddrinfo", side_effect=deny_network):
        results = reproduce()
    serialized = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
