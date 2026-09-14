"""Compare two public Binance metadata responses; never import app.main or DB.

Run from the repository with backend Python dependencies installed. This makes
exactly two unauthenticated public GET requests and saves summary metrics only.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))
from app.data.symbols import _spot_rows

assert "app.main" not in sys.modules and "app.db" not in sys.modules
BASE = "https://data-api.binance.vision/api/v3/exchangeInfo"
PARAMS = {"permissions": "SPOT", "showPermissionSets": "false", "symbolStatus": "TRADING"}
results = {}
catalogs = {}
for name, query in (("original", ""), ("filtered", "?" + urlencode(PARAMS))):
    started = time.monotonic()
    request = Request(BASE + query, headers={"Accept-Encoding": "identity", "User-Agent": "gg-parrot-public-catalog-verification/1.0"})
    with urlopen(request, timeout=20) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise RuntimeError("Public metadata exceeded the verification size limit")
        document = json.loads(raw)
        rows = _spot_rows(document)
        results[name] = {
            "status": response.status, "response_bytes": len(raw),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "all_symbol_rows": len(document.get("symbols", [])),
            "tradable_usdt_spot_rows": len(rows), "includes_dot": "DOTUSDT" in rows,
        }
        catalogs[name] = set(rows)
        del document, raw
report = {
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "source": BASE, "filtered_parameters": PARAMS,
    "scope": "Two public metadata GETs from this verification host; not Render memory or startup measurements",
    "requests": results,
    "same_tradable_usdt_spot_catalog": catalogs["original"] == catalogs["filtered"],
    "missing_after_filter": sorted(catalogs["original"] - catalogs["filtered"]),
    "added_after_filter": sorted(catalogs["filtered"] - catalogs["original"]),
    "response_reduction_pct": round(100 * (1 - results["filtered"]["response_bytes"] / results["original"]["response_bytes"]), 2),
}
(HERE / "spot-payload-comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
