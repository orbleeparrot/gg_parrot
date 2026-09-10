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
    expect(page.locator(".lb-row:not(.lb-row-head)")).to_have_count(4)
    page.evaluate("document.fonts.ready")
    return context, page


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
                        geometry = rows.evaluate_all("""rows => rows.map(row => {
                          const r = el => { const b = el.getBoundingClientRect(); return {x:b.x,y:b.y,right:b.right,bottom:b.bottom,w:b.width,h:b.height}; };
                          const get = s => r(row.querySelector(s));
                          const buttons = [...row.querySelectorAll('button')].map(r);
                          return {row:r(row),rank:get('.lb-rank'),coin:get('.lb-coin'),name:get('.lb-name'),returns:get('.lb-return'),summary:get('.lb-summary'),meta:get('.lb-mobile-meta'),actions:get('.lb-actions'),buttons,
                            symbolFits:row.querySelector('.lb-mobile-symbol strong').scrollWidth <= row.querySelector('.lb-mobile-symbol strong').clientWidth + 1,
                            returnFits:row.querySelector('.lb-return').scrollWidth <= row.querySelector('.lb-return').clientWidth + 1};
                        })""")
                        for data in geometry:
                            assert data["rank"]["right"] <= data["coin"]["x"], data
                            assert data["coin"]["right"] <= data["name"]["x"], data
                            assert data["name"]["right"] <= data["returns"]["x"], data
                            assert data["summary"]["y"] >= max(data[key]["bottom"] for key in ("rank", "coin", "name", "returns")), data
                            assert data["meta"]["y"] >= data["summary"]["bottom"] + 8, data
                            assert data["actions"]["y"] >= data["meta"]["bottom"] + 8, data
                            assert data["symbolFits"] and data["returnFits"], data
                            for index, button in enumerate(data["buttons"]):
                                assert button["w"] >= 48 and button["h"] >= 48, data
                                assert button["x"] >= data["row"]["x"] and button["right"] <= data["row"]["right"] + 1, data
                                for other in data["buttons"][index + 1:]:
                                    assert max(other["x"] - button["right"], button["x"] - other["right"], other["y"] - button["bottom"], button["y"] - other["bottom"]) >= 7.9, data
                        for field in ("전략", "수익률", "작성자"):
                            expect(rows.first.locator(".lb-mobile-field-label").filter(has_text=field)).to_be_visible()
                        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                        last_action = rows.last.get_by_role("button", name="빌더로 복사", exact=True)
                        expect(last_action).to_be_in_viewport()
                        assert last_action.evaluate("""el => {
                          const r = el.getBoundingClientRect();
                          const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                          return hit === el || el.contains(hit);
                        }"""), "The floating chat control must not cover the last entry's action"
                        page.evaluate("window.scrollTo(0, 0)")
                        checks[f"filled-layout-{width}-{theme}"] = geometry
                    else:
                        expect(page.locator(".lb-row-head")).to_be_visible()
                        expect(rows.first.locator(".lb-title")).to_be_visible()
                        expect(rows.first.locator(".lb-mobile-meta")).to_be_hidden()
                        expect(rows.first.locator(".lb-mobile-symbol")).to_be_hidden()
                        assert len(rows.first.evaluate("el => getComputedStyle(el).gridTemplateColumns").split()) == 6
                        checks[f"desktop-table-{theme}"] = "passed"
                    page.screenshot(path=str(OUTPUT / f"leaderboard-{width}-{theme}.png"), full_page=True)
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
            context, page = open_page(browser, origin, fixture, errors, member=False)
            page.locator("#leaderboard-entry-2").get_by_role("button", name="언락 100P", exact=True).click()
            page.wait_for_url("**/login?**")
            assert parse_qs(urlsplit(page.url).query)["next"] == ["/leaderboard"]
            assert not fixture.unlocks
            checks["guest-purchase-login"] = "passed"
            context.close()

            for entry_id, state_key, expected in [(1, "selectedSourceRef", 1), (4, "selectedMacroId", 901), (2, "selectedMacroId", 202)]:
                fixture = Fixture()
                context, page = open_page(browser, origin, fixture, errors, quick=True)
                row = page.locator(f"#leaderboard-entry-{entry_id}")
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
