"""Saved-macro position-news boundary without HTTP server or external I/O."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("sqlmodel")

from fastapi import HTTPException, Response

from app.agent_features.position_news import router


_SAVED = {
    "id": 7,
    "position_side": "long",
    "macro": {
        "symbol": "BTCUSDT",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "rule_type": "A",
        "position_side": "long",
        "candle_interval": "1d",
        "params": {
            "take_profit_pct": 3.0,
            "initial_capital": 1_000_000,
        },
        "risk": {
            "invest_ratio": 0.5,
            "stop_loss_pct": 2.0,
        },
        "period": {"preset": "3m"},
    },
}


def test_saved_macro_projects_selected_portfolio_ticker(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        router.user_macros_mod,
        "get_macro",
        lambda user_id, macro_id, *, db=None: (
            captured.update({"owner": user_id, "macro_id": macro_id})
            or _SAVED
        ),
    )
    monkeypatch.setattr(
        router.service,
        "get_position_news",
        lambda context, *, db=None: captured.update({"context": context}) or context,
    )
    response = Response()

    result = router.macro_position_news(
        7,
        response,
        symbol="eth",
        user=SimpleNamespace(id=3),
    )

    assert captured["owner"] == 3
    assert captured["macro_id"] == 7
    assert result["user_macro_id"] == 7
    assert result["symbol"] == "ETHUSDT"
    assert result["position_side"] == "long"
    assert response.headers["cache-control"] == "private, no-store"


def test_saved_macro_rejects_ticker_outside_portfolio(monkeypatch):
    monkeypatch.setattr(
        router.user_macros_mod,
        "get_macro",
        lambda _user_id, _macro_id, *, db=None: _SAVED,
    )

    with pytest.raises(HTTPException) as exc:
        router.macro_position_news(
            7,
            Response(),
            symbol="SOLUSDT",
            user=SimpleNamespace(id=3),
        )

    assert exc.value.status_code == 422


def _saved_with_symbols(*symbols):
    return {
        **_SAVED,
        "macro": {
            **_SAVED["macro"],
            "symbol": symbols[0],
            "symbols": list(symbols),
        },
    }


@pytest.mark.parametrize(
    ("members", "requested", "expected"),
    [
        (["PYUSDUSDT"], "pyusd", "PYUSDUSDT"),
        (["PYUSDUSDT", "PYUSD"], "PYUSD", "PYUSD"),
        (
            ["A" * 15 + "USDT"],
            "A" * 15 + "USDT",
            "A" * 15 + "USDT",
        ),
    ],
)
def test_saved_macro_symbol_resolution_preserves_suffix_assets(
    monkeypatch,
    members,
    requested,
    expected,
):
    saved = _saved_with_symbols(*members)
    monkeypatch.setattr(
        router.user_macros_mod,
        "get_macro",
        lambda _user_id, _macro_id, *, db=None: saved,
    )
    monkeypatch.setattr(
        router.service,
        "get_position_news",
        lambda context, *, db=None: context,
    )

    result = router.macro_position_news(
        7,
        Response(),
        symbol=requested,
        user=SimpleNamespace(id=3),
    )

    assert result["symbol"] == expected
