"""진입 대기 — 기다리는 매크로가 포지션 없이 돌 때 '무엇을 · 얼마나 · 언제' 를 엔진과 같은 규칙으로 계산해
하트비트 응답 · 세션 응답 · 시작 전 미리보기로 보낸다.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import entry_wait
from app.main import app
from tests.test_runner import _auth, _signup
from tests.test_runner_signals_api import _key, _stop

client = TestClient(app)
H = 3_600_000
T0 = 1_792_000_000_000 - (1_792_000_000_000 % H)


def _bars(closes, *, rng=1.0, forming_open=None, forming_high=None):
    out = []
    for i, c in enumerate(closes):
        last = i == len(closes) - 1
        o = forming_open if last and forming_open is not None else (closes[i - 1] if i else c)
        h = max(o, c) + rng / 2
        if last and forming_high is not None:
            h = forming_high
        out.append({"t": T0 + i * H, "o": o, "h": h, "l": min(o, c) - rng / 2, "c": c, "closed": not last})
    return out


def test_waiting_rules_and_e_only_in_dip_mode():
    assert entry_wait.is_waiting({"rule_type": "I", "params": {}})
    assert not entry_wait.is_waiting({"rule_type": "A", "params": {}})
    assert not entry_wait.is_waiting({"rule_type": "E", "params": {"entry_mode": "immediate"}})
    assert entry_wait.is_waiting({"rule_type": "E", "params": {"entry_mode": "dip"}})


def test_breakout_target_is_open_plus_k_times_previous_range():
    candles = _bars([100.0] * 10 + [101.0], rng=4.0, forming_open=100.0, forming_high=101.0)
    macro = {"rule_type": "I", "candle_interval": "1h", "symbol": "QNTUSDT", "params": {"k": 0.5}}
    m = entry_wait.compute(macro, candles=candles, last_price=101.0, started_ms=T0 + 7 * H, now_ms=T0 + 10 * H + H // 2)
    assert m["sentence"] == "가격이 102.00 위로 올라가면 사요."
    assert m["distance"] == "지금 101.00 · +1.0% 더 오르면"
    assert m["buy_line"]["price"] == 102.0
    assert m["next_check_ms"] == T0 + 11 * H
    assert m["checks"] == 3 and m["last_check_ms"] == T0 + 10 * H
    assert m["log"][0]["at_ms"] == T0 + 10 * H and "사는 선 102.00" in m["log"][0]["text"]


def test_rsi_uses_engine_state_and_threshold_distance():
    closes = [100 + (0.5 if i % 2 else -0.5) + i * 0.05 for i in range(40)]
    macro = {"rule_type": "F", "candle_interval": "1h", "params": {"rsi_period": 7, "entry_threshold": 33, "exit_threshold": 75}}
    m = entry_wait.compute(macro, candles=_bars(closes), last_price=closes[-1])
    assert m["sentence"] == "RSI(7)가 33 아래로 내려가면 사요."
    assert m["distance"].startswith("지금 RSI ") and m["distance"].endswith("더 내려가면")
    assert m["meter"]["from"]["value"] == "75" and m["meter"]["to"]["value"] == "33"


def test_bollinger_and_ma_cross_and_band_mirrors():
    closes = [100 + (i % 5) for i in range(40)]
    short = entry_wait.compute({"rule_type": "G", "candle_interval": "1h", "position_side": "short", "params": {"bb_period": 20, "bb_std": 2}},
                               candles=_bars(closes), last_price=closes[-1])
    assert "상단 밴드" in short["sentence"] and short["sentence"].endswith("위면 팔아서 들어가요.")
    down = [200 - i for i in range(80)]
    j = entry_wait.compute({"rule_type": "J", "candle_interval": "1h", "params": {"fast_period": 5, "slow_period": 20}},
                           candles=_bars(down), last_price=down[-1])
    assert j["sentence"] == "5선이 20선 위로 올라가면 사요." and j["distance"].startswith("두 선 간격 ")
    b = entry_wait.compute({"rule_type": "B", "symbol": "KRW-WAL", "params": {"buy_price": 60, "sell_price": 70}},
                           candles=[], last_price=62, symbol="KRW-WAL")
    assert b["sentence"] == "가격이 60 이하로 내려오면 사요." and b["distance"] == "지금 62 · −3.2% 더 내려오면"
    assert b["realtime"] and b["next_check_ms"] is None


def test_dip_entry_reads_only_server_target():
    macro = {"rule_type": "E", "params": {"entry_mode": "dip", "entry_dip": 3}}
    m = entry_wait.compute(macro, candles=[], last_price=100, levels={"entry_target": 97})
    assert m["sentence"] == "가격이 97.00까지 내려오면 사요." and m["distance"] == "지금 100.00 · −3.0% 더 내려오면"
    pending = entry_wait.compute(macro, candles=[], last_price=100, levels={"pending": True})
    assert "첫 봉이 마감되면" in pending["distance"]


def test_typical_wait_counts_new_hits_and_longest_gap():
    rows = [{"hit": h} for h in [False, True, True, False, False, True, False, False, False, False, True, False]]
    stat = entry_wait.typical_wait(rows, H)
    assert stat == {"episodes": 3, "span_ms": 12 * H, "avg_ms": 4 * H, "longest_ms": 4 * H}
    assert entry_wait.typical_wait(rows[:5], H) is None


def test_price_format_matches_web():
    assert entry_wait.fmt_price(209.515, "QNTUSDT") == "209.515"
    assert entry_wait.fmt_price(102.0, "QNTUSDT") == "102.00"
    assert entry_wait.fmt_price(1804.0, "KRW-XRP") == "1,804"
    assert entry_wait.fmt_price(64.2, "KRW-WAL") == "64.2"


def _start_band(key, version="20"):
    macro = {"symbol": "KRW-WAL", "exchange": "upbit", "rule_type": "B", "position_side": "long", "market": "spot", "leverage": 1,
             "candle_interval": "1h", "period": {"preset": "3m"}, "params": {"buy_price": 60, "sell_price": 70, "initial_capital": 100000},
             "risk": {"stop_loss_pct": 5}}
    body = {"symbol": "KRW-WAL", "exchange": "upbit", "position_side": "long", "leverage": 1, "market": "spot", "testnet": True, "mode": "mock",
            "human_summary": "t", "macro": macro, "runner_version": version}
    r = client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_heartbeat_and_session_view_carry_entry_wait(monkeypatch):
    monkeypatch.setattr(entry_wait, "_candles_for", lambda macro, symbol, market: _bars([65.0 + (i % 3) for i in range(30)]))
    entry_wait._memo.clear()
    token = _signup()
    key = _key(token)
    sid = _start_band(key)
    try:
        hb = client.post("/api/runner/heartbeat", json={"session_id": sid, "last_price": 62.0, "in_position": False},
                         headers={"X-Runner-Key": key}).json()
        assert hb["action"] == "continue"
        assert hb["entry_wait"]["sentence"] == "가격이 60 이하로 내려오면 사요."
        assert hb["entry_wait"]["distance"] == "지금 62 · −3.2% 더 내려오면"
        sessions = client.get("/api/me/runner/sessions", headers=_auth(token)).json()["active"]
        view = next(s for s in sessions if s["session_id"] == sid)
        assert view["entry_wait"]["rule"] == "B"
        # 사면 보내지 않는다
        held = client.post("/api/runner/heartbeat", json={"session_id": sid, "last_price": 59.0, "in_position": True,
                                                          "entry_price": 59.0, "position_qty": 10},
                           headers={"X-Runner-Key": key}).json()
        assert "entry_wait" not in held
    finally:
        _stop(key, sid)


def test_entry_preview_is_runner_key_only(monkeypatch):
    monkeypatch.setattr(entry_wait, "_candles_for", lambda macro, symbol, market: _bars([100 + (i % 7) for i in range(60)]))
    token = _signup()
    key = _key(token)
    macro = {"symbol": "BTCUSDT", "rule_type": "F", "candle_interval": "1h", "params": {"rsi_period": 7, "entry_threshold": 33, "exit_threshold": 75}}
    assert client.post("/api/runner/entry-preview", json={"macro": macro}).status_code in (401, 403, 422)
    r = client.post("/api/runner/entry-preview", json={"macro": macro}, headers={"X-Runner-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["waiting"] is True and body["sentence"] == "RSI(7)가 33 아래로 내려가면 사요."
    assert body["typical"]["span_ms"] > 0 and body["interval_label"] == "1시간"
    plain = client.post("/api/runner/entry-preview", json={"macro": {"symbol": "BTCUSDT", "rule_type": "A", "params": {}}},
                        headers={"X-Runner-Key": key}).json()
    assert plain == {"waiting": False}
