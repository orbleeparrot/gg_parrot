"""물어볼까 v2 — 종목 후보 풀과 AI 선별 검증."""
from __future__ import annotations

from app import ask_candidates as ac


def _t(symbol, qvol, high, low, change=1.0):
    return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str(low),
            "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low)}


def _tickers():
    # 변동폭: CALM 1% · MID 5% · WILD 20%, 거래대금은 CALM > MID > WILD
    return [
        _t("CALMUSDT", 900_000_000, 101, 100),
        _t("MIDUSDT", 800_000_000, 105, 100),
        _t("WILDUSDT", 700_000_000, 120, 100),
        _t("USDCUSDT", 950_000_000, 101, 100),      # 스테이블 → 제외
        _t("BTCUPUSDT", 850_000_000, 130, 100),     # 레버리지 토큰 → 제외
        _t("TINYUSDT", 5_000, 200, 100),            # 거래대금 미달 → 제외
    ]


def test_pool_drops_stable_leverage_and_illiquid():
    pool = ac.build_pool(_tickers(), profile="balanced", size=10)
    symbols = [c["symbol"] for c in pool]
    assert "USDCUSDT" not in symbols
    assert "BTCUPUSDT" not in symbols
    assert "TINYUSDT" not in symbols


def test_pool_carries_volume_rank_by_quote_volume():
    pool = ac.build_pool(_tickers(), profile="balanced", size=10)
    ranks = {c["symbol"]: c["volume_rank"] for c in pool}
    assert ranks["CALMUSDT"] == 1
    assert ranks["MIDUSDT"] == 2
    assert ranks["WILDUSDT"] == 3


def test_stable_profile_prefers_calm_coins():
    pool = ac.build_pool(_tickers(), profile="stable", size=3)
    assert pool[0]["symbol"] == "CALMUSDT"


def test_aggressive_profile_prefers_wild_coins():
    pool = ac.build_pool(_tickers(), profile="aggressive", size=3)
    assert pool[0]["symbol"] == "WILDUSDT"


def test_balanced_profile_prefers_middle_volatility():
    pool = ac.build_pool(_tickers(), profile="balanced", size=3)
    assert pool[0]["symbol"] == "MIDUSDT"


def test_scalper_profile_prefers_wild_among_most_traded():
    pool = ac.build_pool(_tickers(), profile="scalper", size=3)
    assert pool[0]["symbol"] == "WILDUSDT"


def test_pool_size_is_capped():
    assert len(ac.build_pool(_tickers(), profile="balanced", size=2)) == 2
