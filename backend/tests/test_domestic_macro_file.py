"""국내 거래소 매크로 파일 내려받기 — 실행기에 넣어 돌릴 수 있어야 한다."""
import json

import pytest
from fastapi.testclient import TestClient

from app import macro_signing, realtrade, runner
from app.engine import Macro
from app.main import app

UPBIT = {"exchange": "upbit", "symbol": "KRW-BTC", "rule_type": "A", "position_side": "long",
         "leverage": 1, "candle_interval": "1d",
         "params": {"initial_capital": 1000000, "take_profit_pct": 3}}
BITHUMB = {**UPBIT, "exchange": "bithumb"}
UPBIT_PORTFOLIO = {**UPBIT, "symbols": ["KRW-BTC", "KRW-ETH"]}


@pytest.mark.parametrize("payload,exchange", [(UPBIT, "upbit"), (BITHUMB, "bithumb")])
def test_domestic_macro_file_downloads_and_is_signed(payload, exchange):
    """업비트·빗썸도 바이낸스처럼 .ggm.json 을 내려받는다 — 서명이 맞아야 실행기가 '원본' 으로 받는다."""
    with TestClient(app) as client:
        res = client.post("/api/realtrade/macro-file", json={"macro": payload})
        assert res.status_code == 200, res.text
        assert ".ggm.json" in res.headers["Content-Disposition"]
        body = json.loads(res.content)
        assert body["exchange"] == exchange
        assert body["symbol"] == payload["symbol"]
        # 사람이 읽을 요약과 서명이 함께 온다 — 둘 다 바이낸스 파일과 같은 모양이다.
        assert isinstance(body.get("human_summary"), str) and body["human_summary"]
        signature = body.pop("_sig")
        body.pop("human_summary")
        assert macro_signing.verify(Macro.model_validate(body), signature) is True


def test_domestic_portfolio_macro_file_is_still_refused():
    """실행기는 여러 종목을 못 돌린다 — 국내가 열렸어도 묶음 거절은 그대로다."""
    with TestClient(app) as client:
        res = client.post("/api/realtrade/macro-file", json={"macro": UPBIT_PORTFOLIO})
        assert res.status_code == 422, res.text
        assert res.json()["detail"] == runner.PORTFOLIO_UNSUPPORTED_DETAIL


def test_domestic_bundle_is_refused_without_calling_the_macro_file_forbidden():
    """독립 봇 압축파일은 국내에서 계속 막는다 — 다만 '매크로 파일' 을 금지된 것으로 말하지 않는다."""
    with TestClient(app) as client:
        res = client.post("/api/realtrade/bundle", json={"macro": UPBIT})
        assert res.status_code == 422, res.text
        detail = res.json()["detail"]
        assert detail == runner.DOMESTIC_BUNDLE_DETAIL
        assert "독립 봇 압축파일" in detail
        # 문구 회귀 방지: 매크로 파일은 이제 국내도 내려받을 수 있으므로 금지로 말하면 거짓이다.
        assert "매크로 파일은 바이낸스 전용" not in detail
        assert "매크로 파일(.ggm.json)" in detail  # 갈 길을 알려 준다


def test_bundle_builder_copy_matches_the_endpoint_copy():
    """build_bundle 안의 사본 문구가 상수와 갈라지지 않게 못 박는다(순환 import 를 피해 글자로 뒀다)."""
    with pytest.raises(ValueError) as caught:
        realtrade.build_bundle(Macro.model_validate(UPBIT))
    assert str(caught.value) == runner.DOMESTIC_BUNDLE_DETAIL


def test_runner_version_gate_for_domestic_is_untouched():
    """내려받기는 열렸지만 실행기 버전 관문은 그대로다 — v9 에 넣으면 세션 시작에서 426 이 난다."""
    assert runner.DOMESTIC_MIN_VERSION == "10"
    assert runner.supports_domestic("9") is False
    assert runner.supports_domestic("10") is True
