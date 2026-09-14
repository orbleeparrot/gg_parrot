"""One read-only public builder visit with a bounded Chrome CPU profile.

Raw profiler data stays in /tmp. Only compact function/time summaries are saved
beside this script. This is a lab sample, not a production RUM distribution.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent


def main():
    blocked = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ["BROWSER_EXECUTABLE_PATH"], args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block")

        def read_only(route):
            if route.request.method not in {"GET", "HEAD", "OPTIONS"}:
                blocked.append({"method": route.request.method, "url": route.request.url})
                route.abort()
            else:
                route.continue_()

        context.route("**/*", read_only)
        context.add_init_script("""window.cpuProbe = {longTasks: [], lcp: []};
          new PerformanceObserver(list => {
            for(const e of list.getEntries()) window.cpuProbe.longTasks.push({start:e.startTime, duration:e.duration});
          }).observe({type:'longtask', buffered:true});
          new PerformanceObserver(list => {
            for(const e of list.getEntries()) window.cpuProbe.lcp.push({ms:e.startTime,tag:e.element?.tagName,class:e.element?.className});
          }).observe({type:'largest-contentful-paint', buffered:true});""")
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("Performance.enable")
        cdp.send("Profiler.enable")
        cdp.send("Profiler.setSamplingInterval", {"interval": 1000})
        cdp.send("Profiler.start")
        page.goto("https://gg-parrot.vercel.app/builder", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        profile = cdp.send("Profiler.stop")["profile"]
        metrics = {row["name"]: row["value"] for row in cdp.send("Performance.getMetrics")["metrics"]}
        timing = page.evaluate("""() => ({...window.cpuProbe,
          paint:performance.getEntriesByType('paint').map(e=>e.toJSON()),
          resources:performance.getEntriesByType('resource').filter(e=>/\\/api\\/candles/.test(e.name))
            .map(e=>({url:e.name,start:e.startTime,end:e.responseEnd}))})""")
        browser.close()

    raw_fd, raw_name = tempfile.mkstemp(prefix="ggp-builder-", suffix=".cpuprofile")
    raw = Path(raw_name)
    with os.fdopen(raw_fd, "w") as handle:
        json.dump(profile, handle)
    nodes = {node["id"]: node for node in profile["nodes"]}
    parents = {child: node["id"] for node in profile["nodes"] for child in node.get("children", [])}
    self_us, inclusive_us = Counter(), Counter()
    stacks = Counter()
    navigation_us = metrics.get("NavigationStart", 0) * 1_000_000
    cursor = profile["startTime"]

    def identity(node_id):
        frame = nodes[node_id]["callFrame"]
        return (frame.get("functionName") or "(anonymous)", frame.get("url", ""), frame.get("lineNumber", -1) + 1, frame.get("columnNumber", -1) + 1)

    long_tasks = [(entry["start"], entry["start"] + entry["duration"]) for entry in timing["longTasks"]]
    long_self, long_inclusive = Counter(), Counter()
    for sample, delta in zip(profile.get("samples", []), profile.get("timeDeltas", [])):
        cursor += delta
        key = identity(sample)
        self_us[key] += delta
        path = []
        node_id = sample
        while node_id in nodes:
            path.append(identity(node_id))
            node_id = parents.get(node_id)
        for frame in set(path):
            inclusive_us[frame] += delta
        stacks[tuple(reversed(path))] += delta
        since_navigation_ms = (cursor - navigation_us) / 1000
        if any(start <= since_navigation_ms <= end for start, end in long_tasks):
            long_self[key] += delta
            for frame in set(path):
                long_inclusive[frame] += delta

    def leaders(counts):
        return [{"function": key[0], "url": key[1], "line": key[2], "column": key[3], "sampled_ms": round(value / 1000, 2)}
                for key, value in counts.most_common(25)]

    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "One anonymous public GET-only browser visit, CPU sampling until 5s after DOMContentLoaded; profiler overhead applies",
        "blocked_writes": blocked, "raw_profile": str(raw), "timing": timing,
        "navigation_clock_available": bool(navigation_us),
        "self": leaders(self_us), "inclusive": leaders(inclusive_us),
        "long_task_self": leaders(long_self), "long_task_inclusive": leaders(long_inclusive),
        "top_stacks": [{"sampled_ms": round(value / 1000, 2),
                        "frames": [{"function": row[0], "url": row[1], "line": row[2], "column": row[3]} for row in path]}
                       for path, value in stacks.most_common(8)],
    }
    (HERE / "production-builder-cpu.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"raw_profile": str(raw), "lcp": timing["lcp"], "long_tasks": timing["longTasks"],
                      "long_task_self": summary["long_task_self"][:8], "self": summary["self"][:8]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
