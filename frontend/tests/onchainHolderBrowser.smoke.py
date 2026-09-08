"""On-chain balance alert rendering and duplicate suppression using API fixtures only."""
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("agent_fixture", Path(__file__).with_name("agentNotificationBrowser.smoke.py"))
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)


class HolderFixtures(fixture_module.Fixtures):
    def __init__(self, coin):
        super().__init__("empty")
        self.coin = coin
        self.onchain_status = "baseline"
        self.holder_changes = []
        self.trade_status = "empty"

    def session(self):
        session = super().session()
        symbol = "ETHUSDT" if self.coin == "WETH" else self.coin + "USDT"
        session.update(symbol=symbol, macro={**session["macro"], "symbol": symbol})
        return session

    def route(self, route):
        if not urlparse(route.request.url).path.endswith("/whale-activity"):
            return super().route(route)
        self.calls["whales"] += 1
        payload = {
            "status": self.trade_status, "symbol": self.session()["symbol"], "market": "spot",
            "quote_asset": "USDT", "items": [], "observed_at": self.now,
            "onchain": {"status": self.onchain_status, "coin": self.coin, "items": self.holder_changes},
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def change(self):
        is_xrp = self.coin == "XRP"
        return {
            "id": f"onchain:{self.coin}:2", "occurred_at": self.now,
            "previous_observed_at": self.now - (86_400_000 if is_xrp else 600_000),
            "increased_count": 3, "decreased_count": 2, "compared_count": 47, "tracked_count": 49,
            "source": "xrpscan" if is_xrp else "blockscout",
            "source_label": "XRPScan 상위 잔고" if is_xrp else "Blockscout WETH 보유 지갑",
            "source_url": "https://xrpscan.com/balances" if is_xrp else "https://eth.blockscout.com/token/0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "scope": "XRP 상위 보유 계정" if is_xrp else "WETH 토큰 상위 보유 지갑",
            "daily_source": is_xrp,
        }


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True,
        executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH", "/opt/google/chrome/chrome"),
        args=["--no-sandbox"])
    reports = []
    for coin in ("WETH", "XRP"):
        fixture = HolderFixtures(coin)
        page, errors = fixture_module.open_fixture(browser, fixture)
        expect(page.get_by_role("button", name="고래 동향", exact=False)).to_be_visible()
        assert fixture_module.messages(page) == []
        fixture_module.advance(page, fixture, 35)
        assert fixture_module.messages(page) == []

        fixture.holder_changes = [fixture.change()]
        fixture.onchain_status = "ready"
        fixture_module.advance(page, fixture, 35)
        observed = fixture_module.messages(page)
        assert len(observed) == 1 and observed[0]["title"] == f"{coin} 지갑 잔고 변화", observed
        assert "증가 3개" in observed[0]["summary"] and "감소 2개" in observed[0]["summary"]
        assert not any(word in observed[0]["summary"] for word in ("매수", "매도", "체결"))
        assert observed[0]["source"] == fixture.holder_changes[0]["source_url"]
        expect(page.get_by_text("비교 기간", exact=False)).to_have_count(1)
        assert ("일 단위" if coin == "XRP" else "WETH 토큰") in observed[0]["summary"]
        fixture_module.assert_quiet_refresh(page, fixture, 70)

        # A retry temporarily hides the cached observation but cannot announce it twice.
        fixture.onchain_status = "pending"
        fixture_module.advance(page, fixture, 35)
        fixture.onchain_status = "ready"
        fixture_module.advance(page, fixture, 35)
        assert fixture_module.messages(page) == observed
        fixture.trade_status = "unavailable"
        fixture_module.advance(page, fixture, 35)
        assert len(fixture_module.messages(page)) == 2
        fixture_module.assert_quiet_refresh(page, fixture, 35)
        assert sum(message["title"] == f"{coin} 지갑 잔고 변화" for message in fixture_module.messages(page)) == 1
        assert fixture.calls["stop"] == 0 and not errors, errors
        page.screenshot(path=f"/tmp/gg-parrot-onchain-{coin.lower()}.png", full_page=True)
        reports.append({"coin": coin, "passed": True, "requests": fixture.calls,
                        "messages": fixture_module.messages(page), "page_errors": errors,
                        "external_source_requests": 0})
        page.close()
    print(json.dumps({"passed": True, "scenarios": reports}, ensure_ascii=False))
    browser.close()
