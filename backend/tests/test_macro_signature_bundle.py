"""서명 버전 4 — legs · bundle_risk 를 넣고, 옛 버전은 바이트가 그대로여야 한다.

기대 바이트는 **머지 기준점의 코드가 실제로 낸 값**이다(`model_dump` 에서 되만드는 시험은
이 결함을 못 잡는다 — 되만들기는 오늘의 스키마를 쓰므로 언제나 통과한다).
"""
import hashlib
import hmac
import json

import pytest

from app.engine.schema import Macro
from app.macro_signing import (
    ACCEPTED_SIG_VERSIONS, SIG_VERSION, _key, canonical_bytes, sign, verify,
)

BASE = {
    "symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h",
    "period": {"preset": "3m"}, "params": {"k": 0.5, "initial_capital": 1000},
    "risk": {"invest_ratio": 1.0}, "created_at": "2026-01-01T00:00:00+00:00",
}


def test_sig_version_is_4():
    assert SIG_VERSION == 4
    assert ACCEPTED_SIG_VERSIONS == (1, 2, 3, 4)


def test_v3_bytes_have_no_bundle_keys():
    """v3 로 서명하던 매크로의 바이트에 새 칸이 끼면 기존 파일이 전부 '수정된 파일' 이 된다."""
    m = Macro(**BASE)
    v3 = json.loads(canonical_bytes(m, version=3).decode("utf-8"))
    assert "legs" not in v3 and "bundle_risk" not in v3
    assert "entry_filter" in v3            # v3 는 entry_filter 를 포함한다
    v2 = json.loads(canonical_bytes(m, version=2).decode("utf-8"))
    assert "entry_filter" not in v2 and "legs" not in v2
    v1 = json.loads(canonical_bytes(m, version=1).decode("utf-8"))
    assert "exchange" not in v1 and "quote_currency" not in v1 and "legs" not in v1


def test_v3_byte_length_frozen():
    """숫자를 손으로 못 박는다 — 어떤 칸이 들어오든 옛 바이트가 바뀌면 여기서 걸린다."""
    m = Macro(**BASE)
    assert len(canonical_bytes(m, version=3)) == 685
    assert len(canonical_bytes(m, version=2)) == 665
    assert len(canonical_bytes(m, version=1)) == 620


def test_v1_v2_v3_bytes_are_frozen():
    """길이만 보면 `sort_keys=True` 제거처럼 길이가 같고 바이트가 다른 변경을 놓친다.

    길이 685 는 그대로인데 바이트가 달라지면 수트는 초록이고 세상의 모든 서명이 깨진다.
    아래 지문은 분기점 83373ea 의 코드가 낸 값과 대조해 확인했다(v1 620B · v2 665B · v3 685B).
    """
    m = Macro(**BASE)
    got = {v: hashlib.sha256(canonical_bytes(m, version=v)).hexdigest()[:12] for v in (1, 2, 3)}
    assert got == {1: "c4b6409bc1da", 2: "c57db5416d59", 3: "992e3112c97c"}


def test_v4_bytes_include_bundle_keys():
    m = Macro(**BASE)
    v4 = json.loads(canonical_bytes(m, version=4).decode("utf-8"))
    assert v4["legs"] is None and v4["bundle_risk"] is None


def test_bundle_macro_signs_and_verifies():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 60},
                                  {"symbol": "ETHUSDT", "weight": 40}],
                 "bundle_risk": {"max_positions": 1}})
    block = sign(m)
    assert block["v"] == 4
    assert verify(m, block) is True


def test_v3_signature_on_bundle_macro_refused():
    """v3 를 통과시키면 legs 를 손으로 끼워 넣은 파일이 '원본' 으로 보인다."""
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 60},
                                  {"symbol": "ETHUSDT", "weight": 40}]})
    forged = {
        "v": 3, "alg": "HMAC-SHA256",
        "hmac": hmac.new(_key(), canonical_bytes(m, version=3), hashlib.sha256).hexdigest(),
    }
    assert verify(m, forged) is False


def test_v3_signature_on_plain_macro_still_accepted():
    """묶음이 아닌 옛 파일은 그대로 통과해야 한다 — 이것이 버전 범위의 존재 이유다."""
    m = Macro(**BASE)
    old = {
        "v": 3, "alg": "HMAC-SHA256",
        "hmac": hmac.new(_key(), canonical_bytes(m, version=3), hashlib.sha256).hexdigest(),
    }
    assert verify(m, old) is True
