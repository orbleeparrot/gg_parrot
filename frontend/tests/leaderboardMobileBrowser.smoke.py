"""Filled mobile leaderboard layout and existing actions, using local fixtures.

FRONTEND_BUILD=/tmp/leaderboard-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  python frontend/tests/leaderboardMobileBrowser.smoke.py
LEADERBOARD_MOBILE_OUTPUT overrides /tmp/ggp-leaderboard-mobile-check.
No backend starts and no external request is allowed through to the network.
"""
import copy
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
OUTPUT = Path(os.environ.get("LEADERBOARD_MOBILE_OUTPUT", "/tmp/ggp-leaderboard-mobile-check")).resolve()
USER = {"id": 101, "username": "매크로연구하는껄무새", "email": "fixture@example.com", "points_balance": 1200, "avatar_url": None}
SUMMARY = "7일마다 일정 금액을 분할 매수하고 수익률이 8%에 도달하면 일부를 매도해요. 시장 상황에 따라 조건을 조절하는 전략입니다."


def macro(symbol, leverage=1):
    return {
        "symbol": symbol, "position_side": "long", "rule_type": "E", "candle_interval": "1d",
        "leverage": leverage, "margin_mode": "isolated", "market": "auto", "period": {"preset": "1y"},
        "params": {"entry_mode": "immediate", "activation_profit": 5, "trail_percent": 3, "initial_capital": 1000},
        "risk": {"invest_ratio": 1}, "fees": {"commission_pct": .1, "slippage_pct": .05},
    }


def entries():
    rows = []
    for index, (symbol, name, result) in enumerate([
        ("BTCUSDT", USER["username"], 1234.56),
        ("1000PEPEUSDT", "매일꾸준하게기록하는초보투자자", 12.34),
        ("ETHUSDT", "AI전략연구소", -99.99),
        ("SOLUSDT", "파란고래", None),
    ]):
        rows.append({
            "id": index + 1, "symbol": symbol, "username": name, "nickname": name,
            "return_pct": result, "likes": 12345 if index == 0 else 32, "dislikes": 102 if index == 0 else 3,
            "my_vote": 0, "created_kst": "14:35", "first_created_kst": "09.01", "defending": index == 0,
            "streak_days": 9 if index == 0 else 1, "is_owner": index == 0, "is_mine": index == 0,
            "is_ai": index == 2, "crown": index == 0, "for_sale": index < 3, "locked": index == 1,
            "unlock_price": 100 if index == 1 else 0,
            "macro": None if index == 1 else macro(symbol, 20 if index == 0 else 1),
            "human_summary": "" if index == 1 else SUMMARY,
        })
    return rows


def short_entries():
    """Ordinary rows must stay compact, not just fit unusually long fixtures."""
    rows = entries()[:3]
    for row, symbol, author, result, locked, summary in zip(
        rows,
        ("DOTUSDT", "CHIPUSDT", "XRPUSDT"),
        ("포기하지않는새", "분할매수", "느린고래"),
        (2.91, -.42, .08),
        (True, False, True),
        ("", "0.0011달러에 매수하고 0.0013달러에 매도해요.", ""),
    ):
        row.update(
            symbol=symbol, username=author, nickname=author, return_pct=result,
            likes=0, dislikes=0, is_owner=False, is_mine=False, is_ai=False, crown=False,
            defending=False, streak_days=1, for_sale=True, locked=locked,
            unlock_price=100 if locked else 0, macro=None if locked else macro(symbol),
            human_summary=summary,
        )
    return rows


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (BUILD / urlsplit(self.path).path.lstrip("/")).is_file():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


class Fixture:
    def __init__(self):
        self.rows = entries()
        self.user = copy.deepcopy(USER)
        self.votes, self.unlocks, self.saved, self.deleted = [], [], [], []

    def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        if url.hostname not in {"127.0.0.1", "localhost"}:
            route.abort()
            return
        path = url.path
        if not path.startswith("/api/"):
            route.continue_()
            return
        data = {"items": [], "symbols": [], "coins": [], "active": [], "recent": []}
        if path == "/api/auth/me":
            data = {"user": self.user}
        elif path == "/api/auth/google/config":
            data = {"enabled": False}
        elif path == "/api/leaderboard":
            data = {"items": self.rows, "seconds_to_reset": 12600}
        elif path.startswith("/api/leaderboard/") and path.endswith("/vote"):
            row = next(row for row in self.rows if row["id"] == int(path.split("/")[3]))
            value = request.post_data_json["value"]
            self.votes.append((row["id"], value))
            if row["my_vote"] == 1: row["likes"] -= 1
            if row["my_vote"] == -1: row["dislikes"] -= 1
            row["my_vote"] = 0 if row["my_vote"] == value else value
            if row["my_vote"] == 1: row["likes"] += 1
            if row["my_vote"] == -1: row["dislikes"] += 1
            data = {"entry_id": row["id"], "likes": row["likes"], "dislikes": row["dislikes"], "my_vote": row["my_vote"]}
        elif path.startswith("/api/leaderboard/") and path.endswith("/unlock"):
            assert request.headers.get("authorization") == "Bearer fixture-a"
            row = next(row for row in self.rows if row["id"] == int(path.split("/")[3]))
            self.unlocks.append(row["id"])
            assert row["locked"], "An already-unlocked fixture must not be purchased again"
            self.user["points_balance"] -= row["unlock_price"]
            row.update(locked=False, unlocked=True, unlock_price=0, macro=macro(row["symbol"]), human_summary=SUMMARY)
            data = {"points_balance": self.user["points_balance"], "user_macro": {"id": 202}}
        elif path.startswith("/api/leaderboard/") and request.method == "DELETE":
            entry_id = int(path.rsplit("/", 1)[-1])
            self.deleted.append(entry_id)
            self.rows = [row for row in self.rows if row["id"] != entry_id]
            data = {"ok": True}
        elif path == "/api/me/macros" and request.method == "POST":
            self.saved.append(request.post_data_json)
            data = {"item": {"id": 901}}
        elif path == "/api/kimchi-premium":
            data = {"ok": True, "premium_pct": .82, "label": "김프"}
        elif path == "/api/fear-greed":
            data = {"ok": True, "value": 64, "classification_ko": "탐욕"}
        elif path == "/api/hangang-temp":
            data = {"ok": True, "temperature": 24.5}
        route.fulfill(json=data)


def open_page(browser, origin, fixture, errors, *, width=390, height=844, theme="dark", member=True, quick=False):
    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=width < 1100, color_scheme=theme, reduced_motion="reduce")
    context.route("**/*", fixture.route)
    context.add_init_script("localStorage.setItem('ggp_theme', " + json.dumps(theme) + ");")
    if member:
        context.add_init_script("localStorage.setItem('ggp_token', 'fixture-a'); localStorage.setItem('ggp_user', " + json.dumps(json.dumps(USER)) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(origin + "/leaderboard" + ("?from=quick-run" if quick else ""), wait_until="networkidle")
    expect(page.locator(".lb-row:not(.lb-row-head)")).to_have_count(len(fixture.rows))
    page.evaluate("document.fonts.ready")
    return context, page


def mobile_geometry(rows):
    return rows.evaluate_all("""rows => rows.map(row => {
      const rect = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,w:r.width,h:r.height}; };
      const get = s => rect(row.querySelector(s));
      const buttons = [...row.querySelectorAll('button')].map(button => {
        const r = rect(button), style = getComputedStyle(button), after = getComputedStyle(button, '::after');
        const left = parseFloat(after.left), right = parseFloat(after.right), top = parseFloat(after.top), bottom = parseFloat(after.bottom);
        return {...r,background:style.backgroundColor,border:parseFloat(style.borderTopWidth),shadow:style.boxShadow,font:parseFloat(style.fontSize),
          icons:[...button.querySelectorAll('svg')].map(rect),
          hit:{x:r.x+left,y:r.y+top,right:r.right-right,bottom:r.bottom-bottom,w:r.w-left-right,h:r.h-top-bottom}};
      });
      return {row:rect(row),rank:get('.lb-rank'),coin:get('.lb-coin'),name:get('.lb-name'),returns:get('.lb-return'),summary:get('.lb-summary'),meta:get('.lb-mobile-meta'),actions:get('.lb-actions'),buttons,
        symbolFits:row.querySelector('.lb-mobile-symbol strong').scrollWidth <= row.querySelector('.lb-mobile-symbol strong').clientWidth + 1,
        returnFits:row.querySelector('.lb-return').scrollWidth <= row.querySelector('.lb-return').clientWidth + 1};
    })""")


def assert_compact_geometry(geometry):
    for data in geometry:
        assert data["rank"]["right"] <= data["coin"]["x"], data
        assert data["coin"]["right"] <= data["name"]["x"], data
        assert data["name"]["right"] <= data["returns"]["x"], data
        assert data["coin"]["w"] <= 28 and data["coin"]["h"] <= 28, data
        assert data["summary"]["y"] >= max(data[key]["bottom"] for key in ("rank", "coin", "name", "returns")), data
        assert 3.9 <= data["meta"]["y"] - data["summary"]["bottom"] <= 4.1, data
        assert data["actions"]["y"] >= data["meta"]["bottom"] + 6, data
        assert data["symbolFits"] and data["returnFits"], data
        assert data["row"]["h"] < 350, data
        for index, button in enumerate(data["buttons"]):
            assert button["w"] >= 36 and 31.9 <= button["h"] <= 32.1, data
            assert button["background"] == "rgba(0, 0, 0, 0)" and button["border"] == 0 and button["shadow"] == "none", data
            assert button["font"] == 12, data
            assert all(icon["w"] == 18 and icon["h"] == 18 for icon in button["icons"]), data
            assert button["x"] >= data["row"]["x"] and button["right"] <= data["row"]["right"] + 1, data
            hit = button["hit"]
            assert hit["w"] >= 44 and hit["h"] >= 44, data
            for other in data["buttons"][index + 1:]:
                other_hit = other["hit"]
                # Expanded touch rectangles may meet, but must never overlap.
                assert max(other_hit["x"] - hit["right"], hit["x"] - other_hit["right"], other_hit["y"] - hit["bottom"], hit["y"] - other_hit["bottom"]) >= -.1, data


def assert_expanded_hit_targets(row):
    row.locator(".lb-actions").evaluate("el => el.scrollIntoView({block:'center', inline:'nearest'})")
    result = row.locator(".lb-actions button").evaluate_all("""buttons => buttons.map(button => {
      const r = button.getBoundingClientRect(), p = getComputedStyle(button, '::after');
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      const probes = [[x,y],[r.x+parseFloat(p.left)+1,y],[r.right-parseFloat(p.right)-1,y],[x,r.y+parseFloat(p.top)+1],[x,r.bottom-parseFloat(p.bottom)-1]];
      return {name:button.getAttribute('aria-label') || button.textContent.trim(),probes:probes.map(([x,y]) => {
        const hit = document.elementFromPoint(x,y);
        return {x,y,ok:hit === button || button.contains(hit),actual:hit?.closest('button')?.getAttribute('aria-label') || hit?.tagName};
      })};
    })""")
    assert all(probe["ok"] for button in result for probe in button["probes"]), result
    return result


def main():
    if not (BUILD / "index.html").is_file():
        raise SystemExit(f"Build missing: {BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    errors, checks = [], {}
    report = {"passed": False, "build": str(BUILD), "checks": checks, "page_errors": errors}
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if os.environ.get("BROWSER_EXECUTABLE_PATH"):
                launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
            browser = playwright.chromium.launch(**launch)
            origin = f"http://127.0.0.1:{server.server_port}"
            for width, height in [(320, 844), (390, 844), (844, 390), (1440, 1000)]:
                for theme in ("dark", "light"):
                    fixture = Fixture()
                    context, page = open_page(browser, origin, fixture, errors, width=width, height=height, theme=theme)
                    rows = page.locator(".lb-row:not(.lb-row-head)")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, theme)
                    if width < 1100:
                        expect(page.locator(".lb-row-head")).to_be_hidden()
                        expect(rows.nth(1).locator(".lb-mobile-symbol strong")).to_have_text("1000PEPE")
                        expect(rows.nth(1).locator(".lb-mobile-author-name")).to_have_text(fixture.rows[1]["username"])
                        geometry = mobile_geometry(rows)
                        assert_compact_geometry(geometry)
                        expect(rows.first.locator(".lb-fact-strategy dt")).to_have_text("전략")
                        assert rows.first.locator(".lb-summary").get_attribute("aria-label") is None
                        assert rows.first.locator(".lb-mobile-meta").get_attribute("aria-label") is None
                        expect(rows.first.locator(".lb-return")).to_have_attribute("aria-label", "수익률 +1234.56%")
                        expect(rows.first.locator(".lb-mobile-field-label")).to_have_count(0)
                        expect(rows.nth(1).locator(".lb-fact-locked dd")).to_have_text("잠긴 전략")
                        hits = [assert_expanded_hit_targets(rows.nth(index)) for index in range(rows.count())]
                        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                        last_action = rows.last.get_by_role("button", name="빌더로 복사", exact=True)
                        expect(last_action).to_be_in_viewport()
                        assert last_action.evaluate("""el => {
                          const r = el.getBoundingClientRect();
                          const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                          return hit === el || el.contains(hit);
                        }"""), "The floating chat control must not cover the last entry's action"
                        page.evaluate("window.scrollTo(0, 0)")
                        checks[f"filled-layout-{width}-{theme}"] = {"geometry": geometry, "expanded_hit_targets": hits}
                    else:
                        expect(page.locator(".lb-row-head")).to_be_visible()
                        expect(rows.first.locator(".lb-title")).to_be_visible()
                        expect(rows.first.locator(".lb-title")).to_have_text(fixture.rows[0]["username"])
                        assert rows.first.locator(".lb-title").evaluate("el => getComputedStyle(el).fontSize") == "17px"
                        expect(page.locator(".lb-col-name")).to_have_text("매크로")
                        expect(page.locator(".lb-col-summary")).to_have_text("전략")
                        expect(rows.first.locator(".lb-strategy-ticker strong")).to_have_text("BTC")
                        expect(rows.first.locator(".lb-mobile-meta")).to_be_hidden()
                        expect(rows.first.locator(".lb-mobile-symbol")).to_be_hidden()
                        assert len(rows.first.evaluate("el => getComputedStyle(el).gridTemplateColumns").split()) == 6
                        checks[f"desktop-table-{theme}"] = "passed"
                    page.screenshot(path=str(OUTPUT / f"leaderboard-{width}-{theme}.png"), full_page=True)
                    context.close()

            for width in (320, 390):
                for theme in ("dark", "light"):
                    fixture = Fixture()
                    fixture.rows = short_entries()
                    context, page = open_page(browser, origin, fixture, errors, width=width, theme=theme)
                    rows = page.locator(".lb-row:not(.lb-row-head)")
                    geometry = mobile_geometry(rows)
                    assert_compact_geometry(geometry)
                    heights = [entry["row"]["h"] for entry in geometry]
                    assert all(height <= 190 for height in heights), heights
                    assert sum(heights) <= 570, heights
                    expect(rows.first.locator(".lb-fact-locked dd")).to_have_text("잠긴 전략")
                    expect(rows.nth(1).locator(".lb-summary-text")).to_have_text(fixture.rows[1]["human_summary"])
                    page.screenshot(path=str(OUTPUT / f"leaderboard-short-{width}-{theme}.png"), full_page=True)
                    checks[f"short-rows-{width}-{theme}"] = {"heights": heights, "total": sum(heights)}
                    context.close()

            for width in (390, 1440):
                fixture = Fixture()
                short = fixture.rows[1]
                short.update(locked=False, human_summary="1000PEPE · 숏 · 0.009 이상 숏 진입 / 0.007 이하 청산 · -3% 손절 · 자금 37.5% 투입",
                             macro={**macro(short["symbol"]), "rule_type": "B", "position_side": "short", "risk": {"invest_ratio": .375}})
                dca = fixture.rows[2]
                dca.update(human_summary="ETH · 롱 · 7일마다 250.5 분할매수(DCA)",
                           macro={**macro(dca["symbol"]), "rule_type": "C", "params": {"amount_per_buy": 250.5, "interval_days": 7}})
                context, page = open_page(browser, origin, fixture, errors, width=width)
                row = page.locator("#leaderboard-entry-2")
                expect(row.locator(".lb-position")).to_have_text("숏")
                expect(row.locator(".lb-fact-capital .num")).to_have_text("37.5%")
                expect(row.locator(".lb-summary-text")).to_have_text("0.009 이상 숏 진입 / 0.007 이하 청산 · -3% 손절")
                expect(page.locator("#leaderboard-entry-3 .lb-fact-capital dt")).to_have_text("회당 자금")
                expect(page.locator("#leaderboard-entry-3 .lb-fact-capital .num")).to_have_text("250.5")
                expect(page.locator("#leaderboard-entry-3 .lb-fact-capital small")).to_have_text("USDT")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert row.locator(".lb-summary-text").evaluate("el => el.scrollHeight <= el.clientHeight + 1")
                page.screenshot(path=str(OUTPUT / f"leaderboard-structured-{width}.png"), full_page=True)
                checks[f"structured-summary-{width}"] = "passed"
                context.close()

            fixture = Fixture()
            context, page = open_page(browser, origin, fixture, errors)
            locked = page.locator("#leaderboard-entry-2")
            locked.get_by_role("button", name="좋아요 32", exact=True).click()
            expect(locked.get_by_role("button", name="좋아요 33", exact=True)).to_have_attribute("aria-pressed", "true")
            assert fixture.votes == [(2, 1)] and not fixture.unlocks
            assert fixture.user["points_balance"] == 1200
            locked.get_by_role("button", name="언락 100P", exact=True).click()
            expect(locked.get_by_role("button", name="빌더로 복사", exact=True)).to_be_visible()
            expect(locked.locator(".lb-summary-text")).to_have_text(SUMMARY)
            assert fixture.unlocks == [2] and fixture.user["points_balance"] == 1100
            assert page.evaluate("JSON.parse(localStorage.getItem('ggp_user')).points_balance") == 1100
            locked.get_by_role("button", name="빌더로 복사", exact=True).click()
            page.wait_for_url("**/builder")
            assert page.evaluate("history.state.usr.macro.symbol") == "1000PEPEUSDT"
            checks["vote-purchase-points-copy"] = "passed"
            context.close()

            fixture = Fixture()
            context, page = open_page(browser, origin, fixture, errors, width=320)
            owner = page.locator("#leaderboard-entry-1")
            owner.get_by_role("button", name="수정", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert not fixture.deleted
            page.once("dialog", lambda dialog: dialog.dismiss())
            owner.get_by_role("button", name="삭제", exact=True).click()
            expect(owner).to_be_visible()
            assert not fixture.deleted
            page.once("dialog", lambda dialog: dialog.accept())
            owner.get_by_role("button", name="삭제", exact=True).click()
            expect(owner).to_have_count(0)
            assert fixture.deleted == [1]
            checks["owner-edit-and-confirmed-delete"] = "passed"
            context.close()

            fixture = Fixture()
            context, page = open_page(browser, origin, fixture, errors, member=False)
            page.locator("#leaderboard-entry-2").get_by_role("button", name="언락 100P", exact=True).click()
            page.wait_for_url("**/login?**")
            assert parse_qs(urlsplit(page.url).query)["next"] == ["/leaderboard"]
            assert not fixture.unlocks
            checks["guest-purchase-login"] = "passed"
            context.close()

            for entry_id, state_key, expected in [(1, "selectedSourceRef", 1), (4, "selectedMacroId", 901), (2, "selectedMacroId", 202)]:
                fixture = Fixture()
                context, page = open_page(browser, origin, fixture, errors, quick=True, width=320)
                row = page.locator(f"#leaderboard-entry-{entry_id}")
                assert_compact_geometry(mobile_geometry(page.locator(".lb-row:not(.lb-row-head)")))
                assert_expanded_hit_targets(row)
                row.get_by_role("button", name="언락 100P 후 사용" if entry_id == 2 else "이 매크로 사용", exact=True).click()
                page.wait_for_url("**/?run=1&step=1")
                assert page.evaluate(f"history.state.usr.{state_key}") == expected
                if entry_id == 4: assert fixture.saved[0]["macro"]["symbol"] == "SOLUSDT"
                if entry_id == 2: assert fixture.unlocks == [2]
                checks[f"quick-run-entry-{entry_id}"] = "passed"
                context.close()
            browser.close()
        assert not errors, errors
        report["passed"] = True
        print(json.dumps({"passed": True, "checks": len(checks), "report": str(OUTPUT / "report.json")}))
    finally:
        server.shutdown()
        server.server_close()
        (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
