"""진입 대기 — 기다리는 매크로가 포지션 없이 돌 때 '무엇을 · 얼마나 · 언제' 를 엔진과 같은 규칙으로 계산해
하트비트 응답 · 세션 응답 · 시작 전 미리보기로 보낸다.
"""
from __future__ import annotations

import math

from fastapi.testclient import TestClient

from app import entry_wait
from app.engine.candles import MAState
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
    assert j["sentence"] == "5선이 20선 위로 올라가면 사요." and j["headline"].endswith(" 위에서 끝나면 사요")
    assert j["distance"].startswith("지금 121.00 · +") and "더 오르면" in j["distance"]
    assert j["gap"].startswith("+") and j["gap"].endswith("%")
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


def test_down_to_buy_rules_draw_a_value_scale_with_the_buy_zone_below():
    """RSI 40 아래로 내려가면 사는데 막대는 '청산 기준 70 → 사는 선 40' 이 오른쪽으로 차서 거꾸로 읽혔다(10/17)."""
    closes = [100 + (i % 7) - 3 + i * 0.02 for i in range(60)]
    m = entry_wait.compute({"rule_type": "F", "candle_interval": "15m",
                            "params": {"rsi_period": 14, "entry_threshold": 40, "exit_threshold": 70}},
                           candles=_bars(closes), last_price=closes[-1])
    scale = m["scale"]
    assert scale["zone_side"] == "below" and m["ladder"] is None, "옛 실행기가 거꾸로 칠하지 않게 ladder 는 비운다"
    (buy, out) = scale["marks"]
    assert (buy["label"], buy["value"], buy["main"]) == ("사는 선", "40", True) and (out["label"], out["value"]) == ("청산 기준", "70")
    assert buy["pos"] == scale["zone"] < scale["now"]["pos"] < out["pos"], "왼쪽이 작은 값 — 사는 구역은 40 아래(왼쪽)"

    band = entry_wait.compute({"rule_type": "B", "params": {"buy_price": 60, "sell_price": 70}}, candles=[], last_price=62,
                              symbol="KRW-WAL")["scale"]
    assert band["zone_side"] == "below" and [mk["label"] for mk in band["marks"]] == ["사는 가격", "매도가"]
    assert band["zone"] < band["now"]["pos"] < band["marks"][1]["pos"]
    dip = entry_wait.compute({"rule_type": "E", "params": {"entry_mode": "dip", "entry_dip": 3}}, candles=[], last_price=100,
                             levels={"entry_target": 97})["scale"]
    assert dip["zone_side"] == "below" and dip["zone"] < dip["now"]["pos"]


def _crossed(ma_type, fast_n, slow_n, closes):
    """엔진 MAState 로 closes 를 먹인 뒤 마지막 봉에서 단기선 − 장기선."""
    fast, slow = MAState(ma_type, fast_n), MAState(ma_type, slow_n)
    for c in closes:
        f, s = fast.update(c), slow.update(c)
    return f - s


def test_ma_cross_buy_line_is_the_close_where_the_engine_lines_cross():
    """2026-10-17 B3 — 이동평균 크로스만 막대도, 넘어야 할 숫자도 없었다. 사는 선은 이번 봉이 그 값보다 위에서 끝나면
    단기선이 장기선을 넘는 마감가이고, 엔진(MAState)의 판정과 정확히 같아야 한다."""
    closes = [100 + 6 * math.sin(i / 4) - i * 0.15 for i in range(90)]
    for ma_type in ("SMA", "EMA"):
        for fast_n, slow_n in ((5, 20), (20, 60)):
            line = entry_wait._cross_price(ma_type, fast_n, slow_n, closes)
            assert line and line > 0
            assert _crossed(ma_type, fast_n, slow_n, closes + [line * (1 + 1e-9)]) > 0
            assert _crossed(ma_type, fast_n, slow_n, closes + [line * (1 - 1e-9)]) < 0
    assert entry_wait._cross_price("SMA", 5, 20, closes[:18]) is None, "장기선을 못 구하면 숫자를 지어내지 않는다"

    down = [200 - i for i in range(80)]
    m = entry_wait.compute({"rule_type": "J", "candle_interval": "1h", "params": {"fast_period": 5, "slow_period": 20}},
                           candles=_bars(down), last_price=down[-1])
    line = entry_wait._cross_price("SMA", 5, 20, down[:-1])
    assert m["headline"] == f"{entry_wait.fmt_price(line)} 위에서 끝나면 사요" and not m["touched"]
    scale = m["scale"]
    assert scale["zone_side"] == "above" and m["ladder"] is None
    assert [(mk["label"], mk["main"]) for mk in scale["marks"]] == [("사는 선", True), ("20선", False)]
    assert scale["now"]["pos"] < scale["marks"][1]["pos"] < scale["marks"][0]["pos"] == scale["zone"], "지금 · 20선 · 사는 선 순"

    short = entry_wait.compute({"rule_type": "J", "candle_interval": "1h", "position_side": "short",
                                "params": {"fast_period": 5, "slow_period": 20}},
                               candles=_bars([100 + i for i in range(80)]), last_price=179)
    assert short["headline"].endswith(" 아래에서 끝나면 팔아서 들어가요") and "더 내려가면" in short["distance"]
    assert short["scale"]["zone_side"] == "below" and short["scale"]["marks"][0]["label"] == "들어가는 선"
    crossed = entry_wait.compute({"rule_type": "J", "candle_interval": "1h", "position_side": "short",
                                  "params": {"fast_period": 5, "slow_period": 20}}, candles=_bars(down), last_price=down[-1])
    assert crossed["reached"] and crossed["headline"] == "이대로 끝나면 팔아서 들어가요", "숏이 '사요'라고 하면 안 된다"
    limit = entry_wait.compute({"rule_type": "B", "position_side": "short", "params": {"buy_price": 60, "sell_price": 70}},
                               candles=[], last_price=71, symbol="KRW-WAL")
    assert limit["reached"] and limit["headline"] == "곧 들어가요"


def test_ma_cross_waiting_for_confirm_bars_names_the_line_to_hold():
    turn = [200 - i for i in range(60)] + [141 + 6 * i for i in range(1, 6)]  # 지난 봉에 막 엇갈림
    m = entry_wait.compute({"rule_type": "J", "candle_interval": "1h",
                            "params": {"fast_period": 5, "slow_period": 20, "confirm_candles": 10}},
                           candles=_bars(turn), last_price=turn[-1])
    assert m["touched"] and not m["reached"]
    # 사는 선은 이번 봉에만 맞는 값이라 'N봉 더'와 붙이지 않고 이번 봉이 몇 번째인지만 말한다
    assert m["headline"] == "116.00 위에서 끝나면 2/10봉이에요"
    assert m["distance"] == "5선이 위에 있어요 · 9봉 더 이어지면 사요"
    assert m["scale"]["now"]["pos"] > m["scale"]["zone"], "단기선이 이미 위 — 지금 점은 사는 구역 안"
    filtered = entry_wait.compute({"rule_type": "J", "candle_interval": "1h", "params": {"fast_period": 5, "slow_period": 20},
                                   "entry_filter": {"kind": "ma", "params": {"ma_type": "SMA", "period": 50, "side": "below"}}},
                                  candles=_bars(turn), last_price=turn[-1])
    assert filtered["touched"] and not filtered["reached"] and filtered["headline"] == "116.00 위에서 끝나면 사요", "1/1봉이라고 하지 않는다"
    assert "진입 필터(" in filtered["distance"]


def test_breakout_without_filter_draws_a_value_scale_and_marks_the_high_once_touched():
    """필터 없는 돌파만 진행 막대(시가 → 사는 선)라 다른 규칙과 모양이 달랐고, 고가가 넘은 뒤 가격이 내려오면
    '이대로 끝나면 사요'인데 막대는 0% 였다."""
    macro = {"rule_type": "I", "candle_interval": "1h", "symbol": "QNTUSDT", "params": {"k": 0.5}}
    waiting = entry_wait.compute(macro, candles=_bars([100.0] * 10 + [101.0], rng=4.0, forming_open=100.0, forming_high=101.0),
                                 last_price=101.0, now_ms=T0 + 10 * H + H // 2)
    assert waiting["headline"] == "102.00 넘으면 사요" and waiting["ladder"] is None
    assert [(mk["label"], mk["value"], mk["main"]) for mk in waiting["scale"]["marks"]] == [("사는 선", "102.00", True), ("이 봉 시가", "100.00", False)]
    assert waiting["scale"]["zone_side"] == "above" and waiting["scale"]["now"]["pos"] < waiting["scale"]["zone"]

    touched = entry_wait.compute(macro, candles=_bars([100.0] * 10 + [101.0], rng=4.0, forming_open=100.0, forming_high=103.0),
                                 last_price=101.0, now_ms=T0 + 10 * H + H // 2)
    assert touched["reached"] and touched["headline"] == "이대로 끝나면 사요"
    assert touched["distance"] == "이 봉 고가 103.00 · 사는 선을 넘었어요"
    high = touched["scale"]["marks"][1]
    assert (high["label"], high["value"]) == ("이 봉 고가", "103.00") and high["pos"] > touched["scale"]["zone"]


def test_bollinger_squeeze_filter_is_a_condition_like_the_engine():
    """빌더 '변동성이 줄어든 구간만 사용'을 진입 대기가 안 봐서, 밴드를 넘으면 엔진은 안 사는데 '이대로 끝나면 사요'라고 했다."""
    closes = [100 + math.sin(i) * (0.5 + i * 0.08) for i in range(70)] + [90.0]  # 출렁임이 커지는 중 — 밴드 폭이 넓다
    params = {"bb_period": 20, "bb_std": 2, "squeeze_filter": True, "squeeze_lookback": 50}
    m = entry_wait.compute({"rule_type": "G", "candle_interval": "1h", "params": params}, candles=_bars(closes), last_price=90.0)
    assert m["touched"] and not m["reached"], "밴드는 넘었지만 폭이 넓어 엔진은 안 산다"
    assert [c["key"] for c in m["conditions"]] == ["band", "squeeze"] and m["conditions"][1]["ok"] is False
    assert m["conditions"][1]["label"].startswith("변동성이 줄어든 구간 · 밴드 폭 ") and m["conditions"][1]["now"].startswith("지금 ")
    assert "밴드를 넘었어요 · 밴드 폭이 " in m["distance"] and m["distance"].endswith("% 이하로 좁아져야 사요")
    assert m["gap"] == "밴드 폭 대기" and m["headline"].endswith(" 아래에서 끝나면 사요")

    plain = entry_wait.compute({"rule_type": "G", "candle_interval": "1h", "params": {**params, "squeeze_filter": False}},
                               candles=_bars(closes), last_price=90.0)
    assert plain["reached"] and [c["key"] for c in plain["conditions"]] == ["band"]

    bars = _bars(closes[:-1] + [90.0, 90.0])  # 밴드 밖에서 마감했지만 폭이 넓었던 봉
    last = entry_wait.compute({"rule_type": "G", "candle_interval": "1h", "params": params}, candles=bars, last_price=90.0)
    assert last["last_bar"]["entered"] is False and last["last_bar"]["why"] == "밴드 폭이 아직 좁아지지 않았어요"


def test_headline_is_one_number_to_reach_for_every_waiting_rule():
    closes = [100 + (0.5 if i % 2 else -0.5) + i * 0.05 for i in range(40)]
    rsi = entry_wait.compute({"rule_type": "F", "candle_interval": "1h", "params": {"rsi_period": 7, "entry_threshold": 33, "exit_threshold": 75}},
                             candles=_bars(closes), last_price=closes[-1])
    assert rsi["headline"] == "RSI 33 아래에서 끝나면 사요"
    rsi3 = entry_wait.compute({"rule_type": "F", "candle_interval": "1h", "position_side": "short",
                               "params": {"rsi_period": 7, "entry_threshold": 33, "exit_threshold": 75, "confirm_candles": 3}},
                              candles=_bars(closes), last_price=closes[-1])
    assert rsi3["headline"] == "RSI 75 위에서 3봉 연속 끝나면 팔아서 들어가요"
    band = entry_wait.compute({"rule_type": "G", "candle_interval": "1h", "params": {"bb_period": 20, "bb_std": 2, "strategy": "breakout"}},
                              candles=_bars([100 + (i % 5) for i in range(40)]), last_price=101)
    assert band["headline"] == f"{band['scale']['marks'][0]['value']} 위에서 끝나면 사요"
    limit = entry_wait.compute({"rule_type": "B", "params": {"buy_price": 60, "sell_price": 70}}, candles=[], last_price=62, symbol="KRW-WAL")
    assert limit["headline"] == "60 이하로 내려오면 사요" and limit["sentence"] == "가격이 60 이하로 내려오면 사요."
    dip = entry_wait.compute({"rule_type": "E", "params": {"entry_mode": "dip", "entry_dip": 3}}, candles=[], last_price=100,
                             levels={"entry_target": 97})
    assert dip["headline"] == "97.00까지 내려오면 사요"
