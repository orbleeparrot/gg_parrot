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


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(coingecko, "ranked", lambda: list(RANKED))
    monkeypatch.setattr(coingecko, "category_ids", lambda slug: set(CATEGORIES.get(slug, ())))
    monkeypatch.setattr(coingecko, "derivative_ids", lambda: set(DERIVATIVES))
    monkeypatch.setattr(P.symbols_mod, "list_symbols",
                        lambda *, exchange="binance": _listing(ALL_BASES))


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
    assert _bases(_by_key()["sector_top4"]) == ["BTC", "LINK", "UNI", "DOGE"]


def test_defi_top3_stays_inside_its_category():
    assert _bases(_by_key()["defi_top3"]) == ["LINK", "UNI", "AAVE"]


def test_a_category_is_matched_by_id_not_by_ticker(monkeypatch):
    """범주 판정을 티커로 하면 같은 티커를 쓰는 다른 코인이 끌려 들어온다."""
    monkeypatch.setattr(coingecko, "category_ids",
                        lambda slug: {"some-wrapped-btc"} if slug == coingecko.CATEGORY_DEFI else set())
    assert "defi_top3" not in _by_key(), "id 가 안 맞으면 한 종목도 안 잡혀야 한다"


# --- 이름이 약속한 것을 못 지키면 안 낸다 ------------------------------------

def test_a_portfolio_that_cannot_be_filled_is_dropped_whole(monkeypatch):
    """"TOP5" 라고 해 놓고 셋만 주면 그 이름이 거짓말이 된다."""
    monkeypatch.setattr(P.symbols_mod, "list_symbols",
                        lambda *, exchange="binance": _listing(["BTC", "ETH", "LINK"]))
    keys = set(_by_key())
    assert "cap_top5" not in keys and "sector_top4" not in keys
    assert "btc_eth_duo" in keys, "둘로 충분한 묶음은 그대로 나온다"


def test_a_rule_bigger_than_the_engine_cap_is_left_out(monkeypatch):
    """엔진이 받아 주지 않는 크기를 화면에 내면 사용자가 고장으로 읽는다."""
    monkeypatch.setattr(P, "MAX_LEGS", 3)
    assert "cap_top5" not in _by_key()
    assert "defi_top3" in _by_key()


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
