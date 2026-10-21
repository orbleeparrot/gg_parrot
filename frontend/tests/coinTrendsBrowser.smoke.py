"""코인동향(/news) 개편 화면 — 실제 빌드 + 로컬 fixture API 로 폭·테마별 점검.

FRONTEND_BUILD=/tmp/news-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  python frontend/tests/coinTrendsBrowser.smoke.py
COIN_TRENDS_OUTPUT overrides /tmp/ggp-coin-trends-check. External traffic is blocked.
mobileShellBrowser.smoke.py 가 Handler · Fixture · BUILD 를 가져다 쓴다.
"""
import json
import time
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
        elif url.path in ("/api/candles", "/api/candles/live"):
            # 코인 정보 칸 차트(직접 만들기와 같은 CandleChart) — 지금 시각에 맞춘 1,700원대 봉.
            step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[query.get("interval", ["1h"])[0]] * 1000
            now_ms = int(time.time() * 1000) // step * step
            count = 2 if url.path.endswith("/live") else 120
            data = {"candles": [{"t": now_ms - (count - 1 - i) * step, "o": 1750 + i % 9, "h": 1760 + i % 7, "l": 1740 + i % 5,
                                 "c": 1752 + i % 11, "v": 1000 + i, "closed": i < count - 1} for i in range(count)],
                    "server_time": now_ms, "fetched_at_ms": now_ms, "source": "fixture", "market": "spot", "fallback": False}
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


def tips_fit(page):
    """보드의 ⓘ 말풍선을 하나씩 열어 글이 상자 안에서 줄바꿈되고 왼쪽 맞춤인지 본다(2026-10-18 경주마 기준 넘침)."""
    seen = []
    triggers = page.locator(".ct-board .info-trigger")
    for index in range(triggers.count()):
        trigger = triggers.nth(index)
        if not trigger.is_visible():
            continue
        trigger.focus()
        box = trigger.evaluate("""(button) => { const e = document.getElementById(button.getAttribute('aria-controls')), s = getComputedStyle(e), r = e.getBoundingClientRect();
          return { sw: e.scrollWidth, cw: e.clientWidth, align: s.textAlign, left: Math.round(r.left), right: Math.round(r.right) }; }""")
        label = trigger.get_attribute("aria-label")
        assert box["sw"] <= box["cw"] and box["align"] in ("left", "start"), (label, box)
        assert 0 <= box["left"] and box["right"] <= page.viewport_size["width"], (label, box)
        trigger.blur()
        seen.append(label)
    return seen


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
                    expect(marquee.locator(".ggp-marquee-track button").first).to_contain_text("3,512원")
                    no_overflow(page)
                    two_columns = page.evaluate("getComputedStyle(document.querySelector('.ct-board')).gridTemplateColumns.split(' ').length > 1")
                    if two_columns:
                        # 위 · 아래 세로선이 한 줄, 페이지는 안쪽 스크롤로 늘어나지 않는다.
                        lines = page.evaluate("[...document.querySelectorAll('.ct-market, .ct-side')].map(e => Math.round(e.getBoundingClientRect().left))")
                        assert lines[0] == lines[1], lines
                    race = column_left(page, ".ct-lanes-head")
                    assert "경주마 기준" in tips_fit(page)
                    # 같은 역할은 같은 규칙(2026-10-17 점검): 순위 줄 높이 · 코인 이름 · 칸 맞춤 · 조작 글자 크기.
                    same = page.evaluate("""() => {
                      const st = (sel) => { const e = document.querySelector(sel); if (!e) return null; const s = getComputedStyle(e);
                        return { h: Math.round(e.getBoundingClientRect().height), size: s.fontSize, weight: s.fontWeight, top: Math.round(e.getBoundingClientRect().top) }; };
                      return { lane: st('.ct-lane'), laneName: st('.ct-lane-name b'), search: st('.ct-search-field'), tabs: st('.ct-side-tabs'),
                               select: st('.ct-select select'), seg: st('.ct-side-tabs .seg-item'), note: st('.ct-note'), briefNote: st('.ct-brief-note') }; }""")
                    assert same["lane"]["h"] == 56, same
                    if same["select"]:
                        assert same["select"]["size"] == same["seg"]["size"], same
                    if same["note"]:
                        assert (same["note"]["size"], same["note"]["weight"]) == (same["briefNote"]["size"], same["briefNote"]["weight"]), same
                    # 검색은 코인 순위 제목줄 오른쪽(2026-10-18 시안): 거래소 스위치와 같은 40 × 168, 같은 가운데 줄,
                    # 두 칸 머리(제목줄 · 필터)가 같은 높이, 순위 글자 끝(거래대금)이 필터 끝과 맞음(세로선 양옆 공백 같게).
                    head = page.evaluate("""() => { const b = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect();
                        return { top: Math.round(r.top), h: Math.round(r.height), w: Math.round(r.width), mid: Math.round(r.top + r.height / 2), right: Math.round(r.right) }; };
                      const text = (s) => { const e = document.querySelector(s); if (!e) return null; const rg = document.createRange(); rg.selectNodeContents(e); return Math.round(rg.getBoundingClientRect().right); };
                      const side = document.querySelector('.ct-side').getBoundingClientRect(), tabs = document.querySelector('.ct-side-tabs').getBoundingClientRect();
                      return { search: b('.ct-rank .ct-col-head .ct-search-field'), mini: b('.ct-rank .ct-mini'), title: b('.ct-rank .ct-sec-title'),
                               rankHead: b('.ct-rank .ct-col-head'), sideHead: b('.ct-side .ct-col-head'), sort: b('.ct-sort'), tabs: b('.ct-side-tabs'),
                               turn: text('.ct-lane-turn'), divider: Math.round(side.left), sideContent: Math.round(tabs.left),
                               rank: b('.ct-rank'), titleRow: b('.ct-rank-title'),
                               placeholder: document.querySelector('.ct-search input').placeholder }; }""")
                    assert head["search"] and head["placeholder"] == "업비트에서 검색", head
                    assert head["search"]["h"] == head["mini"]["h"] == 40 and head["mini"]["mid"] == head["title"]["mid"], head
                    assert head["titleRow"]["right"] <= head["rank"]["right"], head  # 제목 · ⓘ · 거래소가 칸 밖으로 안 나감
                    if head["rank"]["w"] >= 470:  # 한 줄일 때 — 좁으면 검색칸이 다음 줄에서 칸 너비를 다 쓴다
                        assert head["search"]["w"] == head["mini"]["w"] and head["search"]["mid"] == head["mini"]["mid"], head
                    if two_columns:
                        assert head["rankHead"]["h"] == head["sideHead"]["h"] == 44, head
                        assert (head["sort"]["top"], head["sort"]["h"]) == (head["tabs"]["top"], head["tabs"]["h"]) and head["sort"]["h"] == 44, head
                        assert head["turn"] == head["sort"]["right"], head
                        assert head["divider"] - head["turn"] == head["sideContent"] - head["divider"] - 1, head  # 1 = 세로선
                        # 소식 글 · 코인 칩 오른쪽 끝도 위 탭 끝과 같다
                        feed = page.evaluate("""() => [Math.round(document.querySelector('.ct-side-tabs').getBoundingClientRect().right),
                          Math.round(document.querySelector('.ct-post').getBoundingClientRect().right)]""")
                        assert feed[0] == feed[1], feed
                    page.locator(".ct-sort .seg-item").nth(1).click()
                    expect(page.locator(".ct-row")).to_have_count(20)
                    row = page.evaluate("""() => { const e = document.querySelector('.ct-row'), b = e.querySelector('.ct-row-name b');
                      return [Math.round(e.getBoundingClientRect().height), getComputedStyle(b).fontWeight, getComputedStyle(b).fontSize]; }""")
                    assert row[0] == same["lane"]["h"] and row[1] == same["laneName"]["weight"] and row[2] == same["laneName"]["size"], (row, same)
                    assert column_left(page, ".ct-thead") == race, (race, column_left(page, ".ct-thead"))
                    assert "언급 설명" in tips_fit(page) or not page.locator(".ct-h-ment").is_visible()
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
                    # 고른 코인은 검색칸(168px)에 로고 + 티커 — 전체 이름은 title · aria(잘리지 않게, 2026-10-18).
                    expect(page.locator(".ct-search input")).to_have_value("XRP")
                    expect(page.locator(".ct-search-field")).to_have_attribute("title", "엑스알피(리플) XRP")
                    expect(page.locator(".ct-search input")).to_have_attribute("aria-label", "코인 검색 — 지금: 엑스알피(리플) XRP")
                    assert page.evaluate("(() => { const i = document.querySelector('.ct-search input'); return i.scrollWidth <= i.clientWidth; })()")
                    # 커뮤니티 글이 없는 코인은 뉴스 탭이 먼저 열린다.
                    expect(page.get_by_role("tab", name="국내 뉴스", exact=False)).to_have_attribute("aria-selected", "true")
                    expect(page.get_by_text("해외 대비")).to_be_visible()
                    # 코인 정보 — 저가 · 고가 게이지 대신 직접 만들기 차트(embed, 2026-10-21 시안): 높이 320 · 휴대폰 260,
                    # 봉 간격 6칸 한 줄(누르면 그 간격으로 다시 받음), 휴대폰은 확대/축소 단추 없음.
                    chart = page.locator(".ct-coin .candle-chart.is-embed")
                    expect(chart).to_be_visible()
                    expect(page.locator(".ct-coin .ct-range")).to_have_count(0)
                    expect(chart.locator(".candle-chart-plot svg").first).to_be_attached()
                    picks = chart.locator(".candle-chart-pick .seg-item")
                    expect(picks).to_have_count(6)
                    expect(picks.nth(3)).to_have_attribute("aria-pressed", "true")
                    narrow = head["rank"]["w"] < 470
                    geo = page.evaluate("""() => { const box = document.querySelector('.ct-coin-chart').getBoundingClientRect();
                      const items = [...document.querySelectorAll('.ct-coin .candle-chart-pick .seg-item')];
                      return { h: Math.round(box.height), rows: new Set(items.map(e => Math.round(e.getBoundingClientRect().top))).size,
                               clipped: items.filter(e => e.scrollWidth > e.clientWidth || e.getBoundingClientRect().right > box.right + 1).length }; }""")
                    assert geo == {"h": 260 if narrow else 320, "rows": 1, "clipped": 0}, geo
                    zoom = chart.locator(".candle-chart-controls")
                    expect(zoom).to_be_hidden() if narrow else expect(zoom).to_be_visible()
                    picks.nth(4).click()
                    expect(picks.nth(4)).to_have_attribute("aria-pressed", "true")
                    page.wait_for_function("() => true")
                    assert any(call.startswith("/api/candles?") and "interval=4h" in call for call in fixture.calls), [c for c in fixture.calls if "candles" in c][-3:]
                    if two_columns:  # 고른 코인 정보도 필터(제목줄 검색칸) 끝에 맞음 — 목록 상자를 넓힌 만큼 안쪽을 채움
                        edges = page.evaluate("""() => { const r = (s) => document.querySelector(s).getBoundingClientRect();
                          return [Math.round(r('.ct-coin-top').left), Math.round(r('.ct-kv').right), Math.round(r('.ct-rank .ct-col-head').left), Math.round(r('.ct-rank .ct-col-head').right)]; }""")
                        assert edges[0] == edges[2] and edges[1] == edges[3], edges
                    no_overflow(page)
                    page.screenshot(path=str(OUTPUT / f"coin-{width}-{theme}.png"), animations="disabled")
                    page.locator(".ct-clear").click()
                    expect(page.locator(".ct-coin")).to_have_count(0)
                    expect(page.locator("#ct-side-title")).to_have_text("국내 소식")
                    page.locator(".ct-mode-item").nth(1).click()
                    expect(page.locator(".ct-ex-static")).to_contain_text("바이낸스")
                    expect(page.locator(".ct-search input")).to_have_attribute("placeholder", "바이낸스에서 검색")
                    expect(page.locator("#ct-side-title")).to_have_text("해외 소식")
                    # 하단 띠는 코인동향에서 고른 거래소와 상관없이 업비트 → 빗썸 → 바이낸스를 돌린다(세 거래소 모두 받는다).
                    expect(marquee.locator("img")).to_have_attribute("alt", "업비트")
                    hot = [call for call in fixture.calls if call.startswith("/api/hot-coins")]
                    assert any("exchange=upbit" in call for call in hot) and any("exchange=bithumb" in call for call in hot), hot
                    assert any("exchange=" not in call for call in hot), hot
                    # 왼쪽 거래소를 누르면 목록이 위로 열리고(띠가 화면 맨 아래), 고르면 그 거래소로 바로 넘어간다(2026-10-19 시안 B).
                    # 띠는 767px 이하(휴대폰)에선 원래 숨는다.
                    if marquee.is_visible():
                        marquee.get_by_role("combobox", name="오늘의 경주마 거래소").click()
                        options = marquee.get_by_role("option")
                        expect(options).to_have_count(3)
                        expect(options.nth(0)).to_have_attribute("aria-selected", "true")
                        menu = page.evaluate("""() => { const t = document.querySelector('.marquee-ex .select-menu-trigger').getBoundingClientRect(),
                            l = document.querySelector('.marquee-ex .select-menu-list').getBoundingClientRect();
                          return { bottom: Math.round(l.bottom), triggerTop: Math.round(t.top), left: Math.round(l.left), top: Math.round(l.top), right: Math.round(l.right) }; }""")
                        assert menu["bottom"] <= menu["triggerTop"] and menu["top"] >= 0 and menu["left"] >= 0 and menu["right"] <= width, menu
                        page.screenshot(path=str(OUTPUT / f"marquee-menu-{width}-{theme}.png"), animations="disabled")
                        options.nth(2).click()
                        expect(options).to_have_count(0)
                        expect(marquee.locator(".select-menu-trigger img")).to_have_attribute("alt", "바이낸스")
                        expect(marquee.locator(".ggp-marquee-track button").first).to_contain_text("TIA")
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
