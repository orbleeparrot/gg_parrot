"""Read-only cache-header audit; never calls production API endpoints.

Local API requests only read precomputed public data. Remote requests only read
public HTML/static assets. Responses, credentials, and cookies are not saved.
Run with the local backend on port 8000; failures are recorded, not concealed.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


OUTPUT = Path(__file__).with_name("http-headers.json")
HEADERS = (
    "cache-control", "cdn-cache-control", "vercel-cdn-cache-control", "etag",
    "last-modified", "age", "vary", "x-vercel-cache", "server-timing",
)


def inspect(url, method="HEAD", read_html=False):
    record = {"url": url, "method": method}
    body = ""
    try:
        with urlopen(Request(url, method=method), timeout=12) as response:
            record.update(status=response.status, headers={
                key: response.headers.get(key) for key in HEADERS
                if response.headers.get(key) is not None
            })
            if read_html:
                body = response.read(150_000).decode("utf-8", errors="replace")
    except HTTPError as exc:
        record.update(status=exc.code, error=exc.reason)
    except Exception as exc:
        record["error"] = str(exc)
    return record, body


def main():
    local = "http://127.0.0.1:8000"
    remote = "https://gg-parrot.vercel.app"
    jobs = [
        (local + path, "GET", False)
        for path in ("/api/news/market", "/api/news/coin/BTCUSDT", "/api/leaderboard")
    ] + [
        (remote + "/", "GET", True),
        (remote + "/brand/navigation/ggparrot-nav-agent.svg", "HEAD", False),
        (remote + "/favicon/manifest.json", "HEAD", False),
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda args: inspect(*args), jobs))
    records = [record for record, _ in results]
    html = results[3][1]
    script = re.search(r'<script[^>]+src=["\']([^"\']+\.js)["\']', html)
    if script:
        # Only inspect same-origin assets; never follow application API URLs.
        asset = urljoin(remote + "/", script.group(1))
        if asset.startswith(remote + "/assets/"):
            records.append(inspect(asset)[0])
    output = {
        "scope": "local precomputed public APIs and current production static files only",
        "records": records,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
