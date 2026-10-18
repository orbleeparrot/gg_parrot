"""코인동향(/news) 개편 화면 — 실제 빌드 + 로컬 fixture API 로 폭·테마별 점검.

FRONTEND_BUILD=/tmp/news-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  python frontend/tests/coinTrendsBrowser.smoke.py
COIN_TRENDS_OUTPUT overrides /tmp/ggp-coin-trends-check. External traffic is blocked.
mobileShellBrowser.smoke.py 가 Handler · Fixture · BUILD 를 가져다 쓴다.
"""
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

FRONTEND = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("FRONTEND_BUILD", FRONTEND / "dist")).resolve()
OUTPUT = Path(os.environ.get("COIN_TRENDS_OUTPUT", "/tmp/ggp-coin-trends-check"))
NOW = "2026-10-17T03:51:00Z"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (BUILD / urlsplit(self.path).path.lstrip("/")).is_file():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


def coin(base, korean, price, change, turnover, exchange="upbit", **extra):
    symbol = f"{base}USDT" if exchange == "binance" else f"KRW-{base}"
    return {"symbol": symbol, "base": base, "exchange": exchange, "quote_currency": "USDT" if exchange == "binance" else "KRW",
            "korean_name": korean, "english_name": base.title(), "last_price": price, "change_pct": change,
            "quote_volume": turnover, "volume": 1, "high": price * 1.02, "low": price * 0.97, "warning": False, **extra}


DOMESTIC = [coin("XRP", "엑스알피(리플)", 1766, -0.39, 85e9), coin("BTC", "비트코인", 109_922_000, -0.32, 63.8e9),
            coin("ETH", "이더리움", 3_274_000, -0.09, 43.5e9), coin("TIA", "셀레스티아", 983, 5.7, 31.8e9),
            coin("DKA", "디카르고", 31.6, 10.53, 5.8e9), coin("GTC", "깃코인", 280, 6.06, 4.5e9),
            coin("RLUSD", "리플유에스디", 1354, 0.15, 0.03e9), coin("MANTRA", "만트라", 5.87, -5.93, 16.9e9, warning=True)]
DOMESTIC += [coin(f"C{index:02d}", f"코인{index:02d}", 100 + index, index % 7 - 3, (40 - index) * 1e8) for index in range(40)]
BITHUMB = [{**row, "exchange": "bithumb"} for row in DOMESTIC[:6]] + [coin("EGG", "네스트리", 0.4531, 72.44, 3.4e9, "bithumb")]
BINANCE = [coin("XRP", "엑스알피(리플)", 1.3087, -2.38, 9.6e7, "binance"), coin("BTC", "비트코인", 81608.45, -0.67, 1.1e9, "binance"),
           coin("TIA", "셀레스티아", 0.7296, 20.46, 4.4e7, "binance")]
RANKINGS = {
    "upbit": {"coins": DOMESTIC, "racers": ["DKA", "GTC", "TIA"], "racer_rule": {"pool": 50, "min_turnover": 0, "change_basis": "previous_close"}},
    "bithumb": {"coins": BITHUMB, "racers": ["EGG", "TIA"], "racer_rule": {"pool": 50, "min_turnover": 0, "change_basis": "previous_close"}},
    "binance": {"coins": BINANCE, "racers": ["TIA"], "racer_rule": {"pool": 100, "min_turnover": 1e7, "change_basis": "24h"}},
}
OPINION = {"title": "작성자의 매수 주장 게시글", "source": "Binance Square", "content_type": "community", "community_post_id": "123",
           "url": "https://fixture.invalid/opinion", "published": NOW}
MARKET = {"as_of": "2026-10-17", "overview": "시장 전반의 거래량이 늘었어요.", "translation": {"status": "ready"},
          "items": [{"title": "정책 기관의 공식 발표 기사", "source": "블루밍비트", "url": "https://fixture.invalid/a", "published": NOW,
                     "image": ""}, OPINION]}


def post(index, kind, region):
    base = ["XRP", "BTC", "ETH"][index % 3]
    return {"id": f"{kind}-{region}-{index}", "title": f"{'국내' if region == 'kr' else '해외'} {'글' if kind == 'community' else '기사'} {index:02d} {base} 이야기",
            "url": f"https://fixture.invalid/{kind}/{index}", "source": "디시인사이드 비트코인 갤러리" if kind == "community" else "블록미디어",
            "published": NOW, "asset_symbols": [base], "content_type": "community" if kind == "community" else "article"}


class Fixture:
    def __init__(self):
        self.calls = []

    def trends(self, query):
        region = query.get("region", ["kr"])[0]
        # 다음 쪽은 서버처럼 next_cursor(여기서는 '0.<시작 위치>')로 받는다.
        cursor = query.get("cursor", [""])[0]
        offset = int(cursor.split(".", 1)[1]) if cursor else 0
        limit = int(query.get("limit", ["20"])[0])
        symbol = query.get("symbol", [""])[0]
        sections = query.get("sections", ["rankings,news,community"])[0].split(",")
        body = {"region": region, "offset": offset, "rankings": None, "news": None, "community": None,
                "sources": [{"id": "blockmedia_rss", "name": "블록미디어", "content_type": "article"},
                            {"id": "bloomingbit_rss", "name": "블루밍비트", "content_type": "article"},
                            {"id": "dcinside_bitcoin", "name": "디시인사이드 비트코인 갤러리", "content_type": "community"},
                            {"id": "coinpan_free", "name": "코인판 자유게시판", "content_type": "community"}]}
        for kind in ("news", "community"):
            if kind not in sections:
                continue
            items = [post(index, kind, region) for index in range(45)]
            if symbol:
                base = symbol.replace("KRW-", "").replace("USDT", "")
                items = [item for item in items if base in item["asset_symbols"]]
                if kind == "community" and base == "XRP":
                    items = []
            more = offset + limit < len(items)
            section = {"items": items[offset:offset + limit], "total": len(items) if not cursor else None,
                       "next_cursor": f"0.{offset + limit}" if more else None,
                       "collection": {"status": "ready", "sources": [1, 2, 3, 4]}}
            if kind == "news":
                section.update(overview="정부가 19일 거래소 대표들과 가상자산 과세 간담회를 열어요. 비트코인은 약세를 보였어요.",
                               summary={"status": "ready", "updated_at": NOW})
            else:
                section["mentions"] = {"BTC": 15, "XRP": 15, "ETH": 15}
            body[kind] = section
        return body

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.hostname not in {"127.0.0.1", "localhost"}:
            route.abort()
            return
        if not url.path.startswith("/api/"):
            route.continue_()
            return
        query = parse_qs(url.query)
        self.calls.append(url.path + "?" + url.query)
        data = {"items": []}
        if url.path == "/api/market/trends":
            exchange = query.get("exchange", ["upbit"])[0]
            data = {**RANKINGS[exchange], "exchange": exchange, "updated_at": NOW, "status": "ready", "stale": False}
        elif url.path == "/api/trends":
            data = self.trends(query)
        elif url.path == "/api/usdkrw":
            data = {"usdkrw": 1334.89}
        elif url.path == "/api/news/market":
            data = MARKET
        elif url.path == "/api/hot-coins":
            # 하단 '오늘의 경주마' 띠 — 코인동향에서 고른 거래소를 따라간다.
            exchange = query.get("exchange", ["binance"])[0]
            data = ({"exchange": "binance", "coins": [{"symbol": "TIAUSDT", "base": "TIA", "last_price": 0.73, "change_pct": 20.4, "quote_volume": 4.4e7}]}
                    if exchange == "binance" else
                    {"exchange": exchange, "coins": [{"symbol": "KRW-XRP", "base": "XRP", "last_price": 3512, "change_pct": 4.2, "quote_volume": 8.5e10}]})
        elif url.path == "/api/kimchi-premium": data = {"ok": True, "premium_pct": .9}
        elif url.path == "/api/fear-greed": data = {"ok": True, "value": 55}
        elif url.path == "/api/hangang-temp": data = {"ok": True, "temperature": 22}
        route.fulfill(json=data)


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def column_left(page, selector):
    return page.evaluate(f"""() => {{ const row = document.querySelector('{selector}');
      return row ? [...row.children].slice(0, 2).map(cell => Math.round(cell.getBoundingClientRect().left)) : null; }}""")


def main():
    if not (BUILD / "index.html").is_file():
        raise SystemExit(f"Build missing: {BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks = [], []
    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if os.environ.get("BROWSER_EXECUTABLE_PATH"): launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
            browser = playwright.chromium.launch(**launch)
            origin = f"http://127.0.0.1:{server.server_port}"
            for width in (320, 390, 768, 1099, 1280, 1440):
                for theme in ("dark", "light"):
                    fixture = Fixture()
                    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme, reduced_motion="reduce")
                    context.add_init_script(f"localStorage.setItem('ggp_theme', '{theme}')")
                    context.route("**/*", fixture.route)
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(origin + "/news", wait_until="networkidle")
                    notice = page.get_by_role("button", name="확인했어요", exact=True)
                    if notice.count():
                        notice.click()
                    expect(page.get_by_role("heading", name="오늘의 코인동향", level=1)).to_be_visible()
                    expect(page.get_by_role("heading", name="오늘의 요약")).to_be_visible()
                    expect(page.get_by_role("heading", name="시장·규제 한눈에")).to_be_visible()
                    expect(page.get_by_text("커뮤니티 의견 · 사실 확인 안 됨", exact=False).first).to_be_visible()
                    expect(page.locator(".ct-lane")).to_have_count(3)
                    # 구역 제목 아래 설명 줄은 두 구역이 같은 크기 · 굵기 · 색 · 제목과의 간격(2026-10-17 지적).
                    subs = page.evaluate("""() => [...document.querySelectorAll('.ct-sec-sub')].map(e => {
                      const s = getComputedStyle(e), title = e.closest('section').querySelector('.ct-sec-title');
                      return [s.fontSize, s.fontWeight, s.color, Math.round(e.getBoundingClientRect().top - title.getBoundingClientRect().bottom)]; })""")
                    assert len(subs) == 2 and subs[0] == subs[1], subs
                    marquee = page.locator(".site-marquee")
                    expect(marquee.locator("img")).to_have_attribute("alt", "업비트")
                    expect(marquee.locator("button").first).to_contain_text("3,512원")
                    no_overflow(page)
                    two_columns = page.evaluate("getComputedStyle(document.querySelector('.ct-board')).gridTemplateColumns.split(' ').length > 1")
                    if two_columns:
                        # 위 · 아래 세로선이 한 줄, 페이지는 안쪽 스크롤로 늘어나지 않는다.
                        lines = page.evaluate("[...document.querySelectorAll('.ct-market, .ct-side')].map(e => Math.round(e.getBoundingClientRect().left))")
                        assert lines[0] == lines[1], lines
                    race = column_left(page, ".ct-lanes-head")
                    # 같은 역할은 같은 규칙(2026-10-17 점검): 순위 줄 높이 · 코인 이름 · 칸 맞춤 · 조작 글자 크기.
                    same = page.evaluate("""() => {
                      const st = (sel) => { const e = document.querySelector(sel); if (!e) return null; const s = getComputedStyle(e);
                        return { h: Math.round(e.getBoundingClientRect().height), size: s.fontSize, weight: s.fontWeight, top: Math.round(e.getBoundingClientRect().top) }; };
                      return { lane: st('.ct-lane'), laneName: st('.ct-lane-name b'), search: st('.ct-search-field'), tabs: st('.ct-side-tabs'),
                               select: st('.ct-select select'), seg: st('.ct-side-tabs .seg-item'), note: st('.ct-note'), briefNote: st('.ct-brief-note') }; }""")
                    assert same["lane"]["h"] == 56, same
                    assert same["search"]["h"] == 48, same  # DESIGN.md text-field 규격
                    if same["select"]:
                        assert same["select"]["size"] == same["seg"]["size"], same
                    if same["note"]:
                        assert (same["note"]["size"], same["note"]["weight"]) == (same["briefNote"]["size"], same["briefNote"]["weight"]), same
                    page.locator(".ct-sort .seg-item").nth(1).click()
                    expect(page.locator(".ct-row")).to_have_count(20)
                    row = page.evaluate("""() => { const e = document.querySelector('.ct-row'), b = e.querySelector('.ct-row-name b');
                      return [Math.round(e.getBoundingClientRect().height), getComputedStyle(b).fontWeight, getComputedStyle(b).fontSize]; }""")
                    assert row[0] == same["lane"]["h"] and row[1] == same["laneName"]["weight"] and row[2] == same["laneName"]["size"], (row, same)
                    assert column_left(page, ".ct-thead") == race, (race, column_left(page, ".ct-thead"))
                    height = page.evaluate("document.documentElement.scrollHeight")
                    page.evaluate("document.querySelector('.ct-rank-scroll').scrollTop = 1e6")
                    expect(page.locator(".ct-row")).to_have_count(40)
                    page.evaluate("document.querySelector('.ct-side-list').scrollTop = 1e6")
                    page.wait_for_function("() => document.querySelectorAll('.ct-post').length > 20")
                    assert any("sections=community" in call and "cursor=0.20" in call for call in fixture.calls), fixture.calls[-5:]
                    if two_columns:
                        assert page.evaluate("document.documentElement.scrollHeight") == height
                    page.locator(".ct-search input").fill("리플")
                    expect(page.locator(".ct-pop-item").first).to_contain_text("엑스알피(리플)")
                    page.keyboard.press("Enter")
                    expect(page.locator(".ct-coin h3")).to_contain_text("엑스알피(리플)")
                    expect(page.locator("#ct-side-title")).to_contain_text("엑스알피(리플) 소식")
                    expect(page.locator(".ct-search input")).to_have_value("엑스알피(리플) XRP")
                    # 커뮤니티 글이 없는 코인은 뉴스 탭이 먼저 열린다.
                    expect(page.get_by_role("tab", name="국내 뉴스", exact=False)).to_have_attribute("aria-selected", "true")
                    expect(page.get_by_text("해외 대비")).to_be_visible()
                    no_overflow(page)
                    page.screenshot(path=str(OUTPUT / f"coin-{width}-{theme}.png"), animations="disabled")
                    page.locator(".ct-clear").click()
                    expect(page.locator(".ct-coin")).to_have_count(0)
                    expect(page.locator("#ct-side-title")).to_have_text("국내 소식")
                    page.locator(".ct-mode-item").nth(1).click()
                    expect(page.locator(".ct-ex-static")).to_contain_text("바이낸스")
                    expect(page.locator("#ct-side-title")).to_have_text("해외 소식")
                    # 하단 띠는 코인동향에서 고른 거래소와 상관없이 업비트 → 빗썸 → 바이낸스를 돌린다(세 거래소 모두 받는다).
                    expect(marquee.locator("img")).to_have_attribute("alt", "업비트")
                    hot = [call for call in fixture.calls if call.startswith("/api/hot-coins")]
                    assert any("exchange=upbit" in call for call in hot) and any("exchange=bithumb" in call for call in hot), hot
                    assert any("exchange=" not in call for call in hot), hot
                    no_overflow(page)
                    page.screenshot(path=str(OUTPUT / f"global-{width}-{theme}.png"), full_page=True, animations="disabled")
                    checks.append(f"{width}-{theme}")
                    context.close()
            browser.close()
    finally:
        server.shutdown()
    if errors:
        raise SystemExit("page errors: " + json.dumps(errors[:5], ensure_ascii=False))
    print(json.dumps({"checks": checks, "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
