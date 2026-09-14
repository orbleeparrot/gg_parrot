"""Read-only production builder check; fresh anonymous browser, no API fixtures.

CandleChart immediately requests history on mount. Its initial history timer
is also armed at 3 seconds before the response supplies a slower cadence, which
explains an extra early history query. That does not establish the LCP cause:
these marks distinguish first price insertion, live updates, loading and paint.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    output = Path(__file__).resolve().parent
    suffix = os.environ.get("PROBE_REPORT_SUFFIX", "-timing")
    responses, errors, blocked = [], [], []
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
        context.add_init_script("""(() => {
          window.lcp = [];
          window.probeTiming = {longTasks: [], priceDomChanges: [], priceNextFrames: [],
            firstPriceDomMs: null, fontEvents: []};
          const marks = window.probeTiming;
          new PerformanceObserver(list => {
            for (const e of list.getEntries()) window.lcp.push({ms: e.startTime,
              renderTime: e.renderTime, loadTime: e.loadTime, size: e.size,
              tag: e.element?.tagName, class: e.element?.className, url: e.url,
              text: e.element?.textContent?.slice(0,120)});
          }).observe({type: 'largest-contentful-paint', buffered: true});
          if (PerformanceObserver.supportedEntryTypes.includes('longtask')) {
            new PerformanceObserver(list => {
              for (const e of list.getEntries()) marks.longTasks.push({start: e.startTime,
                duration: e.duration, name: e.name,
                attribution: e.attribution?.map(a => ({name:a.name, containerType:a.containerType,
                  containerName:a.containerName, containerSrc:a.containerSrc}))});
            }).observe({type:'longtask', buffered:true});
          }
          let previousPrice = null;
          const capturePrice = () => {
            const element = document.querySelector('.candle-chart-current');
            const text = element?.textContent;
            if (!element || !text || text === previousPrice) return;
            previousPrice = text;
            const ms = performance.now();
            if (marks.firstPriceDomMs === null) marks.firstPriceDomMs = ms;
            const rect = element.getBoundingClientRect();
            marks.priceDomChanges.push({ms, text, width:rect.width, height:rect.height,
              fontStatus:document.fonts?.status, visibility:document.visibilityState});
            // A frame callback is a scheduling marker, not proof that pixels
            // have been presented. LCP/FCP below remain the paint measurements.
            requestAnimationFrame(() => marks.priceNextFrames.push({ms:performance.now(), text}));
          };
          new MutationObserver(capturePrice).observe(document, {childList:true, subtree:true, characterData:true});
          for (const event of ['loading', 'loadingdone', 'loadingerror']) {
            document.fonts?.addEventListener(event, () => marks.fontEvents.push({ms:performance.now(), event}));
          }
        })();""")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def response_received(response):
            if "/api/" in response.url:
                headers = response.headers
                responses.append({"url": response.url, "status": response.status,
                                  "type": headers.get("content-type"),
                                  "request_id": headers.get("x-request-id"),
                                  "server_timing": headers.get("server-timing")})

        page.on("response", response_received)
        page.goto("https://gg-parrot.vercel.app/builder", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(10000)
        timing = page.evaluate("""() => ({lcp: window.lcp, ...window.probeTiming,
          paint: performance.getEntriesByType('paint').map(e => e.toJSON()),
          navigation: performance.getEntriesByType('navigation')[0]?.toJSON(),
          resources: performance.getEntriesByType('resource')
            .filter(e => /\\.(js|css|woff2?)(?:$|[?#])|\\/api\\/candles/.test(e.name))
            .map(e => ({url:e.name, initiator:e.initiatorType, start:e.startTime,
              fetchStart:e.fetchStart, responseStart:e.responseStart, responseEnd:e.responseEnd,
              duration:e.duration, bytes:e.transferSize})),
          slowResources: performance.getEntriesByType('resource').filter(e => e.duration > 500)
            .map(e => ({url:e.name, ms:e.duration, bytes:e.transferSize}))})""")
        search = page.get_by_role("combobox", name="종목 검색")
        search.fill("DOT")
        page.wait_for_timeout(1000)
        search_text = page.get_by_role("listbox", name="종목 검색 결과").inner_text()
        page.screenshot(path=str(output / f"production-builder{suffix}.png"))
        browser.close()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Production; anonymous fresh Chromium, no throttling, GET-only, no API mocks; one lab sample, not user RUM",
              "responses": responses, "page_errors": errors, "blocked_writes": blocked,
              "dot_search": search_text, "performance": timing}
    (output / f"production-builder{suffix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"lcp": timing["lcp"], "api_failures": [r for r in responses if r["status"] >= 400],
                      "dot_search": search_text, "page_errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
