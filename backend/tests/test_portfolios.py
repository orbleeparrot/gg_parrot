"""추천 묶음 — 기준이 거래소마다 제대로 풀리는가 (2026-10-30).

설계: docs/superpowers/specs/2026-10-30-portfolio-presets-design.md

여기서는 네트워크를 타지 않는다. 코인게코 응답과 거래소 종목 목록을 손으로 쥐여 주고,
**골라내는 규칙**만 본다.
"""
from __future__ import annotations

import pytest

from app import portfolios as P
from app.data import coingecko
from engine import Macro

# 시가총액 내림차순. 실제 응답과 같은 모양이다 — 스테이블이 위쪽에 섞여 있고,
# 티커가 겹치는 포장 토큰(id 가 다른 "BTC")도 들어 있다.
RANKED = [
    {"symbol": "BTC", "id": "bitcoin", "market_cap": 1_500e9},
    {"symbol": "ETH", "id": "ethereum", "market_cap": 270e9},
    {"symbol": "USDT", "id": "tether", "market_cap": 180e9},
    {"symbol": "BNB", "id": "binancecoin", "market_cap": 94e9},
    {"symbol": "XRP", "id": "ripple", "market_cap": 79e9},
    {"symbol": "USDC", "id": "usd-coin", "market_cap": 71e9},
    {"symbol": "SOL", "id": "solana", "market_cap": 57e9},
    {"symbol": "TRX", "id": "tron", "market_cap": 32e9},
    {"symbol": "WBTC", "id": "wrapped-bitcoin", "market_cap": 20e9},
    {"symbol": "BTC", "id": "some-wrapped-btc", "market_cap": 9e9},   # 티커 도둑
    {"symbol": "DOGE", "id": "dogecoin", "market_cap": 11e9},
    {"symbol": "LINK", "id": "chainlink", "market_cap": 9e9},
    {"symbol": "UNI", "id": "uniswap", "market_cap": 5e9},
    {"symbol": "AAVE", "id": "aave", "market_cap": 4e9},
    {"symbol": "BTCUP", "id": "btc-up", "market_cap": 3e9},           # 레버리지 토큰
]

CATEGORIES = {
    coingecko.CATEGORY_LAYER1: {"bitcoin", "ethereum", "binancecoin", "ripple", "solana", "tron"},
    coingecko.CATEGORY_DEFI: {"chainlink", "uniswap", "aave"},
    coingecko.CATEGORY_MEME: {"dogecoin"},
    # 레이어2 · 게임 · AI 는 일부러 비워 둔다 — 못 채우는 분야를 **안 내는지** 보는 자리다.
}
# 포장 자산 · 스테이킹 파생. **티커가 "BTC" 인 포장 토큰이 여기 들어 있다** — 실제
# 코인게코 응답이 그렇다.
DERIVATIVES = {"wrapped-bitcoin", "some-wrapped-btc"}

ALL_BASES = ["BTC", "ETH", "USDT", "BNB", "XRP", "USDC", "SOL", "TRX",
             "WBTC", "DOGE", "LINK", "UNI", "AAVE", "BTCUP"]


def _listing(bases, *, warned=(), futures_only_missing=()):
    return {"items": [{"symbol": base, "base": base, "spot": True,
                       "futures": base not in futures_only_missing,
                       "warning": base in warned}
                      for base in bases]}


# 24시간 거래대금. 시가총액 순위와 **일부러 다르게** 둔다 — 두 기준이 같은 답을 내면
# 거래대금 묶음이 정말 거래대금을 보는지 알 수 없다.
VOLUMES = {"DOGE": 9e9, "XRP": 8e9, "BTC": 7e9, "SOL": 6e9, "ETH": 5e9,
           "BNB": 4e9, "TRX": 3e9, "LINK": 2e9, "UNI": 1e9, "AAVE": 5e8,
           "USDT": 99e9, "WBTC": 50e9}   # 걸러져야 할 것들도 큰 값으로 둔다


# 하루 변동폭(%). 거래대금과 **다른 순서**로 둔다 — 두 숫자가 섞이면 알아채야 한다.
RANGES = {"BTC": 2.0, "ETH": 4.0, "BNB": 6.0, "XRP": 8.0, "SOL": 10.0, "TRX": 3.0,
          "LINK": 5.0, "UNI": 7.0, "AAVE": 9.0, "DOGE": 12.0, "USDT": 0.1, "WBTC": 2.0}


def _tickers(exchange="binance"):
    domestic = exchange in ("upbit", "bithumb")
    out = []
    for base, volume in VOLUMES.items():
        low = 100.0
        row = {"symbol": (f"KRW-{base}" if domestic else f"{base}USDT"), "quoteVolume": volume}
        if base in RANGES:
            row["lowPrice"], row["highPrice"] = low, low * (1 + RANGES[base] / 100)
        out.append(row)
    return out


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(coingecko, "ranked", lambda: list(RANKED))
    monkeypatch.setattr(coingecko, "category_ids", lambda slug: set(CATEGORIES.get(slug, ())))
    monkeypatch.setattr(coingecko, "derivative_ids", lambda: set(DERIVATIVES))
    monkeypatch.setattr(P.symbols_mod, "list_symbols",
                        lambda *, exchange="binance": _listing(ALL_BASES))
    monkeypatch.setattr(P.hotcoins, "get_cached_tickers", lambda: _tickers())
    monkeypatch.setattr(P.krw, "get_all_tickers", lambda exchange: _tickers(exchange))


def _by_key(exchange="binance", market="spot"):
    return {item.key: item for item in P.build(exchange, market)}


def _bases(item):
    return [leg.symbol.replace("KRW-", "").replace("USDT", "") for leg in item.legs]


# --- 티커가 겹치는 문제 ------------------------------------------------------

def test_a_wrapped_token_that_squats_the_btc_ticker_does_not_erase_bitcoin():
    """실제로 코인게코의 포장 자산 범주에 티커가 그냥 "BTC" 인 토큰이 있다.

    티커로 거르면 **진짜 비트코인이 시가총액 1위에서 사라진다.** 처음 짤 때 그렇게 됐다.
    """
    assert "BTC" in _bases(_by_key()["cap_top5"])


def test_the_real_wrapped_bitcoin_is_still_excluded():
    """WBTC 는 BTC 를 한 번 더 담는 것이라 분산이 아니다 — 이건 빠져야 한다."""
    assert "WBTC" not in _bases(_by_key()["cap_top5"])


# --- 기준이 거래소마다 다르게 풀린다 -----------------------------------------

def test_market_cap_top5_follows_the_ranking():
    assert _bases(_by_key()["cap_top5"]) == ["BTC", "ETH", "BNB", "XRP", "SOL"]


def test_a_coin_the_exchange_does_not_list_is_filled_by_the_next_in_line(monkeypatch):
    """BNB 는 업비트에 없다. 구성을 박아 뒀다면 여기서 깨진다 — 기준은 알아서 메운다."""
    monkeypatch.setattr(P.symbols_mod, "list_symbols",
                        lambda *, exchange="binance": _listing([b for b in ALL_BASES if b != "BNB"]))
    assert _bases(_by_key("upbit")["cap_top5"]) == ["BTC", "ETH", "XRP", "SOL", "TRX"]


def test_domestic_and_binance_spell_the_symbol_their_own_way():
    assert _by_key("binance")["btc_eth_duo"].legs[0].symbol == "BTCUSDT"
    assert _by_key("upbit")["btc_eth_duo"].legs[0].symbol == "KRW-BTC"


# --- 거르기 ------------------------------------------------------------------

def test_stablecoins_never_enter_a_portfolio():
    """USDT · USDC 는 시가총액 3위 · 6위다. 안 거르면 묶음의 절반이 스테이블이 된다."""
    assert not ({"USDT", "USDC"} & set(_bases(_by_key()["cap_top5"])))


def test_leverage_tokens_never_enter_a_portfolio():
    assert "BTCUP" not in _bases(_by_key()["cap_top5"])


def test_a_coin_the_exchange_flagged_is_left_out(monkeypatch):
    """거래소가 거래유의로 찍은 종목을 우리가 권할 이유가 없다."""
    monkeypatch.setattr(P.symbols_mod, "list_symbols",
                        lambda *, exchange="binance": _listing(ALL_BASES, warned=("XRP",)))
    assert "XRP" not in _bases(_by_key("upbit")["cap_top5"])


def test_futures_only_asks_for_symbols_that_trade_on_futures(monkeypatch):
    monkeypatch.setattr(P.symbols_mod, "list_symbols",
                        lambda *, exchange="binance": _listing(ALL_BASES, futures_only_missing=("BNB",)))
    assert "BNB" not in _bases(_by_key("binance", "futures")["cap_top5"])


# --- 분야별 ------------------------------------------------------------------

def test_sector_mix_takes_one_layer1_two_defi_and_one_meme():
    assert _bases(_by_key()["mixed_top4"]) == ["BTC", "LINK", "UNI", "DOGE"]


def test_the_defi_sector_stays_inside_its_category():
    assert _bases(_by_key()["sector_defi"]) == ["LINK", "UNI", "AAVE"]


def test_a_category_is_matched_by_id_not_by_ticker(monkeypatch):
    """범주 판정을 티커로 하면 같은 티커를 쓰는 다른 코인이 끌려 들어온다."""
    monkeypatch.setattr(coingecko, "category_ids",
                        lambda slug: {"some-wrapped-btc"} if slug == coingecko.CATEGORY_DEFI else set())
    assert "sector_defi" not in _by_key(), "id 가 안 맞으면 한 종목도 안 잡혀야 한다"


# --- 이름이 약속한 것을 못 지키면 안 낸다 ------------------------------------

def test_a_portfolio_that_cannot_be_filled_is_dropped_whole(monkeypatch):
    """"TOP5" 라고 해 놓고 셋만 주면 그 이름이 거짓말이 된다."""
    monkeypatch.setattr(P.symbols_mod, "list_symbols",
                        lambda *, exchange="binance": _listing(["BTC", "ETH", "LINK"]))
    keys = set(_by_key())
    assert "cap_top5" not in keys and "mixed_top4" not in keys
    assert "btc_eth_duo" in keys, "둘로 충분한 묶음은 그대로 나온다"


def test_a_rule_bigger_than_the_engine_cap_is_left_out(monkeypatch):
    """엔진이 받아 주지 않는 크기를 화면에 내면 사용자가 고장으로 읽는다."""
    monkeypatch.setattr(P, "MAX_LEGS", 3)
    assert "cap_top5" not in _by_key()
    assert "sector_defi" in _by_key()


def test_nothing_is_shown_when_the_ranking_cannot_be_fetched(monkeypatch):
    """틀린 구성을 보여 주느니 없는 게 낫다."""
    monkeypatch.setattr(coingecko, "ranked", lambda: None)
    assert P.build() == []
    assert P.view()["note"]


# --- 비중 --------------------------------------------------------------------

def test_weights_are_equal_and_add_up_to_exactly_a_hundred():
    for item in P.build():
        total = sum(leg.weight for leg in item.legs)
        assert abs(total - 100.0) < 0.01, f"{item.key} 의 비중 합이 {total}"


def test_every_portfolio_really_builds_a_macro():
    """화면에 내는 묶음이 엔진에서 거절당하면 안 된다 — 두 벌이 아니라는 뜻."""
    for item in P.build("binance", "spot"):
        macro = Macro(
            exchange="binance", symbol=item.legs[0].symbol, rule_type="F",
            candle_interval="1h", market="spot", leverage=1,
            params={"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70,
                    "initial_capital": 1_000_000},
            risk={"invest_ratio": 1.0, "stop_loss_pct": 3.0}, period={"preset": "3m"},
            legs=[{"symbol": leg.symbol, "weight": leg.weight} for leg in item.legs],
        )
        assert macro.is_portfolio()
        assert len(macro.legs) == len(item.legs)


def test_three_way_split_still_sums_to_a_hundred():
    """100/3 은 나누어떨어지지 않는다 — 스키마가 0.01 까지만 봐준다."""
    assert sum(P._equal_weights(3)) == pytest.approx(100.0, abs=1e-9)
    assert sum(P._equal_weights(7)) == pytest.approx(100.0, abs=1e-9)


# --- 거래대금 TOP5 -----------------------------------------------------------

def test_volume_top5_follows_turnover_not_market_cap():
    """시가총액 1위는 BTC 지만 거래대금 1위는 DOGE 다 — 다른 기준이어야 의미가 있다."""
    assert _bases(_by_key()["volume_top5"]) == ["DOGE", "XRP", "BTC", "SOL", "ETH"]


def test_volume_top5_still_drops_stablecoins_and_wrapped():
    """거래대금만 보면 USDT 가 1등이다. 거르기는 두 기준 모두에 걸려야 한다."""
    bases = set(_bases(_by_key()["volume_top5"]))
    assert not (bases & {"USDT", "USDC", "WBTC", "BTCUP"})


def test_volume_top5_is_dropped_when_turnover_is_unavailable(monkeypatch):
    monkeypatch.setattr(P.hotcoins, "get_cached_tickers", lambda: None)
    assert "volume_top5" not in _by_key()
    assert "cap_top5" in _by_key(), "시가총액 쪽은 거래대금과 상관없이 나온다"


# --- 시가총액 TOP10 ----------------------------------------------------------

def test_cap_top10_now_fits_the_engine():
    """상한을 5 → 10 으로 올려서 생긴 묶음이다."""
    assert len(_by_key()["cap_top10"].legs) == 10


def test_cap_top10_disappears_if_the_cap_goes_back_down(monkeypatch):
    monkeypatch.setattr(P, "MAX_LEGS", 5)
    assert "cap_top10" not in _by_key()


# --- 끝점 --------------------------------------------------------------------

def _client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_the_endpoint_hands_the_screen_what_it_needs():
    body = _client().get("/api/portfolios", params={"exchange": "binance"}).json()
    assert body["max_symbols"] == P.MAX_LEGS
    first = body["items"][0]
    assert set(first) == {"key", "name", "lead", "tags", "count", "legs", "kind", "stats"}
    assert first["count"] == len(first["legs"])
    assert set(first["legs"][0]) == {"symbol", "weight"}


def test_the_endpoint_says_why_instead_of_failing(monkeypatch):
    """묶음을 못 만드는 건 서버 오류가 아니다 — 화면이 자리를 비우고 안내만 띄우면 된다."""
    monkeypatch.setattr(coingecko, "ranked", lambda: None)
    response = _client().get("/api/portfolios")
    assert response.status_code == 200
    assert response.json()["items"] == [] and response.json()["note"]


def test_the_endpoint_passes_the_market_through():
    body = _client().get("/api/portfolios", params={"market": "futures"}).json()
    assert body["market"] == "futures"
    # 아무 글자나 선물로 읽지 않는다.
    assert _client().get("/api/portfolios", params={"market": "뭐지"}).json()["market"] == "spot"


# --- 분야 묶음 (2026-11-02) ---------------------------------------------------
#
# 설계: docs/superpowers/specs/2026-11-02-sector-bundles-design.md

def test_a_sector_bundle_holds_only_coins_from_that_category():
    assert _bases(_by_key()["sector_layer1"]) == ["BTC", "ETH", "BNB", "XRP", "SOL"]


def test_a_sector_takes_the_biggest_five_and_stops():
    """레이어1 에는 여섯이 있다(TRX 까지). 다섯에서 끊어야 한다."""
    assert "TRX" not in _bases(_by_key()["sector_layer1"])


def test_a_sector_that_cannot_find_three_is_not_shown():
    """밈은 하나뿐이다. 하나짜리는 분야가 아니라 그냥 한 종목이다."""
    keys = set(_by_key())
    assert "sector_meme" not in keys
    assert "sector_layer2" not in keys and "sector_gaming" not in keys and "sector_ai" not in keys


def test_a_sector_of_exactly_three_is_shown():
    """이름이 종목 수를 약속하지 않으므로 셋이어도 참말이다."""
    assert len(_by_key()["sector_defi"].legs) == 3


def test_the_screen_can_tell_a_sector_from_a_size_rule():
    """빌더는 두 줄로 나눠 그린다 — 어느 줄에 놓을지 서버가 말해 줘야 한다."""
    by_key = _by_key()
    assert by_key["sector_defi"].kind == "sector"
    assert by_key["cap_top5"].kind == "size"
    assert by_key["mixed_top4"].kind == "size", "여러 분야를 섞은 것은 분야 묶음이 아니다"


def test_the_old_defi_top3_is_gone():
    """분야 묶음의 디파이 칸과 같은 것이었다 — 두 줄에 같은 묶음이 앉으면 안 된다."""
    assert "defi_top3" not in _by_key()


def test_a_coin_may_sit_in_two_sectors(monkeypatch):
    """IMX 는 레이어2이면서 게임이다. 한쪽을 임의로 지우면 그 분야가 거짓이 된다."""
    both = dict(CATEGORIES)
    both[coingecko.CATEGORY_LAYER2] = {"chainlink", "uniswap", "aave"}
    monkeypatch.setattr(coingecko, "category_ids", lambda slug: set(both.get(slug, ())))
    by_key = _by_key()
    assert _bases(by_key["sector_layer2"]) == ["LINK", "UNI", "AAVE"]
    assert _bases(by_key["sector_defi"]) == ["LINK", "UNI", "AAVE"]


# --- 묶음에 붙는 숫자 두 칸 ---------------------------------------------------

def test_the_stats_come_from_tickers_we_already_hold():
    """거래대금 순위의 폭과 하루 변동폭의 평균. 둘 다 받아 둔 티커로만 구한다."""
    stats = _by_key()["sector_layer1"].stats
    # BTC 3위 · ETH 5위 · BNB 6위 · XRP 2위 · SOL 4위
    assert (stats.rank_min, stats.rank_max) == (2, 6)
    # 2 · 4 · 6 · 8 · 10 의 평균
    assert stats.range_avg_pct == 6.0


def test_the_stats_follow_the_legs_not_the_whole_pool():
    stats = _by_key()["sector_defi"].stats
    assert (stats.rank_min, stats.rank_max) == (8, 10)
    assert stats.range_avg_pct == 7.0


def test_no_stats_rather_than_made_up_ones(monkeypatch):
    """한 레그라도 숫자를 모르면 칸을 안 그린다 — 0 을 적거나 나머지로 평균 내지 않는다."""
    thin = [row for row in _tickers() if not row["symbol"].startswith("AAVE")]
    monkeypatch.setattr(P.hotcoins, "get_cached_tickers", lambda: thin)
    assert _by_key()["sector_defi"].stats is None


def test_the_stats_reach_the_screen():
    item = next(i for i in P.view()["items"] if i["key"] == "sector_layer1")
    assert item["stats"] == {"rank_min": 2, "rank_max": 6, "range_avg_pct": 6.0}
    assert item["kind"] == "sector"
