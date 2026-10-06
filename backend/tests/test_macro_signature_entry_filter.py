"""매크로 파일 서명 — Macro 에 entry_filter 가 생긴 뒤에도 옛 서명이 그대로 통과하는지.

배경: Macro 에 필드가 하나 늘면 model_dump 에 그 키가 들어가 서명 대상 바이트가 바뀐다.
그러면 예전에 서명된 파일이 전부 "수정된 파일" 로 판정된다. test_exchange_contracts 의
옛 서명 검사는 오늘의 model_dump 에서 키를 빼서 "옛 바이트" 를 만들기 때문에, 새 필드가
양쪽에 똑같이 생겨 이 문제를 못 잡았다. 여기서는 옛 코드가 실제로 만든 바이트를 그대로
박아 둔(frozen) 리터럴로 검사한다.
"""
import hashlib
import hmac

import pytest

from app import macro_signing
from app.engine import Macro

# entry_filter 가 생기기 전(커밋 603da89 의 부모)의 Macro 로 canonical_bytes 를 돌려 얻은 바이트.
# Macro(rule_type="A", params={"take_profit_pct": 3, "initial_capital": 1000})
FROZEN_V2 = (
    b'{"candle_interval":"1d","created_at":null,"exchange":"binance",'
    b'"fees":{"commission_pct":0.1,"funding_pct":0.0,"slippage_pct":0.05},'
    b'"leverage":1,"macro_id":null,"margin_mode":"isolated","market":"auto",'
    b'"params":{"initial_capital":1000,"take_profit_pct":3},'
    b'"period":{"end":null,"preset":"1y","start":null},"position_side":"long",'
    b'"quote_currency":"USDT",'
    b'"risk":{"cooldown_minutes":0.0,"daily_max_loss_pct":null,"invest_ratio":1.0,'
    b'"max_holding_hours":null,"stop_loss_pct":null},'
    b'"rule_type":"A","share_slug":null,"symbol":"BTCUSDT","symbols":null}'
)
# 같은 매크로의 v1 바이트 — exchange 와 quote_currency 가 없다.
FROZEN_V1 = (
    b'{"candle_interval":"1d","created_at":null,'
    b'"fees":{"commission_pct":0.1,"funding_pct":0.0,"slippage_pct":0.05},'
    b'"leverage":1,"macro_id":null,"margin_mode":"isolated","market":"auto",'
    b'"params":{"initial_capital":1000,"take_profit_pct":3},'
    b'"period":{"end":null,"preset":"1y","start":null},"position_side":"long",'
    b'"risk":{"cooldown_minutes":0.0,"daily_max_loss_pct":null,"invest_ratio":1.0,'
    b'"max_holding_hours":null,"stop_loss_pct":null},'
    b'"rule_type":"A","share_slug":null,"symbol":"BTCUSDT","symbols":null}'
)

BREAKOUT = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
            "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}
MA20 = {"kind": "ma", "params": {"period": 20, "side": "above"}}


def plain_macro() -> Macro:
    return Macro(rule_type="A", params={"take_profit_pct": 3, "initial_capital": 1000})


def old_sig(version: int, payload: bytes) -> dict:
    """옛 서명 — 반드시 박아 둔 바이트에서 만든다(canonical_bytes 를 다시 부르면 재구성이 된다)."""
    return {"v": version, "hmac": hmac.new(macro_signing._key(), payload, hashlib.sha256).hexdigest()}


def test_v2_canonical_payload_is_frozen():
    # 이 테스트가 깨졌다면 Macro 필드를 추가하거나 이름을 바꾼 것이다.
    # 리터럴을 새 출력으로 고쳐 넣지 말 것. SIG_VERSION 을 올리고, 옛 버전의 canonical_bytes 에서
    # 새 필드를 빼야 이미 내려받은 파일의 서명이 계속 "원본" 으로 확인된다.
    assert macro_signing.canonical_bytes(plain_macro(), version=2) == FROZEN_V2


def test_v1_canonical_payload_is_frozen():
    assert macro_signing.canonical_bytes(plain_macro(), version=1) == FROZEN_V1


def test_v2_signature_from_before_entry_filter_still_verifies():
    assert macro_signing.verify(plain_macro(), old_sig(2, FROZEN_V2)) is True


def test_v1_signature_still_verifies():
    assert macro_signing.verify(plain_macro(), old_sig(1, FROZEN_V1)) is True


@pytest.mark.parametrize("version", [1, 2])
def test_old_signature_is_refused_when_the_macro_carries_a_filter(version):
    """옛 파일에 필터를 손으로 끼워 넣어도 "원본" 으로 보이면 안 된다."""
    macro = Macro(**{**BREAKOUT, "entry_filter": MA20})
    payload = macro_signing.canonical_bytes(macro, version=version)
    assert b"entry_filter" not in payload  # 옛 서명은 필터를 덮지 않는다 — 그래서 해시가 맞아 버린다
    assert macro_signing.verify(macro, old_sig(version, payload)) is False


@pytest.mark.parametrize("entry_filter", [None, MA20])
def test_v3_signature_round_trips_with_and_without_a_filter(entry_filter):
    macro = Macro(**{**BREAKOUT, **({"entry_filter": entry_filter} if entry_filter else {})})
    signed = macro_signing.sign(macro)
    assert signed["v"] == 3
    assert macro_signing.verify(macro, signed) is True


def test_v3_signature_binds_the_filter():
    """v3 는 필터를 덮는다 — 서명 뒤에 필터를 고치면 불일치."""
    macro = Macro(**{**BREAKOUT, "entry_filter": MA20})
    signed = macro_signing.sign(macro)
    edited = Macro(**{**BREAKOUT, "entry_filter": {"kind": "ma", "params": {"period": 50, "side": "above"}}})
    stripped = Macro(**BREAKOUT)
    assert macro_signing.verify(edited, signed) is False
    assert macro_signing.verify(stripped, signed) is False


def test_every_version_up_to_sig_version_is_accepted():
    """SIG_VERSION 이하 **모든** 버전의 서명이 받아들여진다 — 다음 bump 가 직전 버전을 떨어뜨리지 않게.

    손으로 적은 목록은 올릴 때마다 한 칸씩 빠뜨린다(v1→v2 때 `(1, SIG_VERSION)` 이라 v2 가 떨어질 참이었다).
    이 시험은 `range(1, SIG_VERSION + 1)` 로 돌므로 SIG_VERSION 이 4·5 가 되어도 새 줄을 더할 필요가 없다 —
    그때 v3·v4 를 떨어뜨리면 여기서 빨개진다.
    """
    macro = plain_macro()  # 바이낸스·USDT·필터 없음 — v1 의 거래소 제약과 v3 미만의 필터 제약을 둘 다 비껴간다
    versions = list(range(1, macro_signing.SIG_VERSION + 1))
    assert len(versions) >= 3  # 지금 v3 — 범위가 조용히 비면 이 시험이 아무것도 증명하지 않는다
    for version in versions:
        payload = macro_signing.canonical_bytes(macro, version=version)
        assert macro_signing.verify(macro, old_sig(version, payload)) is True, version
    # 미래 버전은 그대로 거절한다(아직 그 바이트 모양을 모른다).
    future = macro_signing.SIG_VERSION + 1
    assert macro_signing.verify(macro, old_sig(future, macro_signing.canonical_bytes(macro))) is False
