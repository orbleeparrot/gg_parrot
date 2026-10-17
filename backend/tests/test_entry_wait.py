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
    assert m["distance"] == "지금 101.00 · +1.0% 더 오르면" and m["gap"] == "+1.0%"
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
    assert m["gap"] == "RSI " + m["meter"]["now"]


def test_bollinger_and_ma_cross_and_band_mirrors():
    closes = [100 + (i % 5) for i in range(40)]
    short = entry_wait.compute({"rule_type": "G", "candle_interval": "1h", "position_side": "short", "params": {"bb_period": 20, "bb_std": 2}},
                               candles=_bars(closes), last_price=closes[-1])
    assert "상단 밴드" in short["sentence"] and short["sentence"].endswith("위면 팔아서 들어가요.")
    down = [200 - i for i in range(80)]
    j = entry_wait.compute({"rule_type": "J", "candle_interval": "1h", "params": {"fast_period": 5, "slow_period": 20}},
                           candles=_bars(down), last_price=down[-1])
    assert j["sentence"] == "5선이 20선 위로 올라가면 사요." and j["distance"].startswith("두 선 간격 ")
    assert j["gap"].startswith("간격 ") and j["gap"].endswith("%")
    b = entry_wait.compute({"rule_type": "B", "symbol": "KRW-WAL", "params": {"buy_price": 60, "sell_price": 70}},
                           candles=[], last_price=62, symbol="KRW-WAL")
    assert b["sentence"] == "가격이 60 이하로 내려오면 사요." and b["distance"] == "지금 62 · −3.2% 더 내려오면"
    assert b["realtime"] and b["next_check_ms"] is None and b["gap"] == "−3.2%"
    hit = entry_wait.compute({"rule_type": "B", "params": {"buy_price": 60, "sell_price": 70}}, candles=[], last_price=59.5, symbol="KRW-WAL")
    assert hit["reached"] and hit["gap"] == "곧 사요"


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


def test_breakout_with_ma_filter_is_not_reached_until_close_is_above_average():
    """2026-10-17 KRW-FORT — 고가가 사는 선을 넘었지만 마감가가 20봉 평균 아래라 엔진은 안 샀다.
    화면이 '마감되면 사요'라고 하면 안 산 봉이 버그로 보인다."""
    closes = [110.0] * 15 + [100.0] * 6 + [101.0]
    candles = _bars(closes, rng=4.0, forming_open=100.0, forming_high=103.0)
    macro = {"rule_type": "I", "candle_interval": "1h", "symbol": "QNTUSDT", "params": {"k": 0.5, "ma_filter_period": 20}}
    m = entry_wait.compute(macro, candles=candles, last_price=101.0, now_ms=T0 + 21 * H + H // 2)
    assert m["touched"] and not m["reached"]
    # 평균선(107)이 사는 선(102)보다 위 — '평균선 위에서 끝나기' 하나로 두 조건이 다 맞으니 큰 글씨는 그 숫자 하나.
    assert m["sentence"] == m["headline"] == "107.00 위에서 끝나면 사요"
    assert m["distance"] == "지금 101.00 · +5.9% 더 · 사는 선은 넘었고 20봉 평균선이 남았어요"
    assert m["gap"] == "평균선 −5.6%"
    assert [(c["key"], c["label"], c["now"], c["ok"]) for c in m["conditions"]] == [
        ("line", "사는 선 102.00 넘기", "넘음 · 고가 103.00", True),
        ("ma", "20봉 평균 107.00 위에서 끝나기", "−5.6%", False)]
    ladder = m["ladder"]
    assert [(mk["label"], mk["value"], mk["main"]) for mk in ladder["marks"]] == [("사는 선", "102.00", False), ("20봉 평균", "107.00", True)]
    line, ma = (mk["pos"] for mk in ladder["marks"])
    assert 0 < ladder["now"]["pos"] < line < ma == ladder["zone"] < 1, "지금 점은 두 선 아래, 사는 구역은 평균선부터"
    assert m["last_bar"]["entered"] is False and m["last_bar"]["at_ms"] == T0 + 21 * H

    above = entry_wait.compute(macro, candles=candles[:-1] + [{**candles[-1], "h": 109.0}], last_price=108.0,
                               now_ms=T0 + 21 * H + H // 2)
    assert above["reached"] and above["headline"] == "이대로 끝나면 사요" and above["gap"] == "곧 사요"
    assert above["distance"] == "사는 선과 평균선을 모두 넘었어요"

    plain = entry_wait.compute({**macro, "params": {"k": 0.5}}, candles=candles, last_price=101.0, now_ms=T0 + 21 * H + H // 2)
    assert plain["ladder"] is None and len(plain["conditions"]) == 1, "필터 없는 매크로는 막대 하나 · 체크 목록 없음"
    assert plain["reached"] and plain["headline"] == "이대로 끝나면 사요", "필터가 없으면 고가가 넘은 순간 마감 때 산다"


def test_closed_bar_reason_names_the_average_when_only_the_filter_failed():
    closes = [110.0] * 15 + [100.0] * 6
    bars = _bars(closes + [100.0], rng=4.0)
    bars[-2] = {**bars[-2], "h": 104.0}  # 마지막 마감봉: 고가는 사는 선을 넘었고 마감가는 평균 아래
    macro = {"rule_type": "I", "candle_interval": "1h", "params": {"k": 0.5, "ma_filter_period": 20}}
    m = entry_wait.compute(macro, candles=bars, last_price=100.0, now_ms=T0 + 21 * H + 60_000)
    assert m["last_bar"]["entered"] is False
    assert m["last_bar"]["why"].startswith("100.00으로 끝나서 20봉 평균 ") and m["last_bar"]["why"].endswith("에 못 미쳤어요")


def test_stale_forming_bar_does_not_reuse_the_previous_buy_line():
    """거래소 봉이 늦게 바뀌면 지난 봉의 사는 선과 지금 가격을 비교해 '넘었어요'라고 했다(11:00:42 FORT)."""
    candles = _bars([100.0] * 10 + [101.0], rng=4.0, forming_open=100.0, forming_high=101.0)
    macro = {"rule_type": "I", "candle_interval": "1h", "params": {"k": 0.5}}
    m = entry_wait.compute(macro, candles=candles, last_price=103.0, now_ms=T0 + 11 * H + 30_000)
    assert not m["touched"] and not m["reached"] and m["buy_line"] is None
    assert m["sentence"] == "새 봉이 열리면 사는 가격이 정해져요."


def test_rsi_confirm_and_entry_filter_hold_back_reached():
    closes = [100 - i * 0.8 for i in range(40)]
    macro = {"rule_type": "F", "candle_interval": "1h",
             "params": {"rsi_period": 7, "entry_threshold": 30, "exit_threshold": 70, "confirm_candles": 50}}
    m = entry_wait.compute(macro, candles=_bars(closes), last_price=closes[-1])
    assert m["touched"] and not m["reached"]
    assert [c["key"] for c in m["conditions"]] == ["rsi", "streak"] and m["conditions"][1]["ok"] is False
    assert m["distance"].endswith("/50봉이에요") and m["gap"].endswith("/50봉")

    filtered = {"rule_type": "F", "candle_interval": "1h", "params": {"rsi_period": 7, "entry_threshold": 30, "exit_threshold": 70},
                "entry_filter": {"kind": "ma", "params": {"ma_type": "SMA", "period": 5, "side": "above"}}}
    f = entry_wait.compute(filtered, candles=_bars(closes), last_price=closes[-1])
    assert f["touched"] and not f["reached"]
    assert f["conditions"][-1]["key"] == "filter" and f["conditions"][-1]["ok"] is False
    assert "진입 필터(5봉 SMA 이동평균 위)가 막고 있어요" in f["distance"] and f["gap"] == "필터 대기"
    assert f["last_bar"]["why"] == "진입 필터(5봉 SMA 이동평균 위)가 막았어요"
