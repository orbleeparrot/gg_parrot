"""껄무새 중계 — 말할 순간(체결 · 손실 구간 · 변동성 · 글 · 연결 · 종료)과 한 번만 남기기."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import agent_commentary as ac
from app.db import AgentCommentaryState, AgentUtterance, get_session
from app.main import app
from sqlmodel import select
from tests.test_runner import _auth, _signup
from tests.test_runner_signals_api import _key, _stop

client = TestClient(app)
NOW = 1_792_100_000_000
MIN = 60_000


def facts(**over):
    base = {"id": 1, "user_id": 1, "symbol": "KRW-ICNT", "base": "ICNT", "side": "long", "leverage": 1, "market": "spot",
            "exchange": "bithumb", "mode_label": "실전", "rule": "A", "macro": {}, "tp_pct": 5.0, "sl_pct": None,
            "status": "running", "started_ms": NOW - 60 * MIN, "stopped_ms": 0, "last_hb_ms": 0,
            "last_price": 201.0, "in_position": False, "entry_price": 0.0, "position_qty": 0.0, "unrealized_pct": 0.0,
            "realized_pnl": 0.0, "note": "", "stopping": False}
    base.update(over)
    return base


def started_state():
    state = ac.new_state()
    state["started"] = True
    state["last_say_ms"] = NOW - MIN
    state["news_rev"] = 10
    return state


def fill(fid, action, qty, price, at, signal=None, reason="진입"):
    return {"id": fid, "at_ms": at, "action": action, "qty": qty, "price": price, "signal_price": signal, "reason": reason}


def bars(ranges, *, start_price=200.0, end_ms=NOW, closes=None):
    """5분 봉 — ranges[i] % 출렁임. 마지막 봉은 지금 막 닫힌 봉."""
    out = []
    count = len(ranges)
    for index, rng in enumerate(ranges):
        t = end_ms - (count - index) * 300_000
        close = closes[index] if closes else start_price
        out.append({"t": t, "o": close, "h": close * (1 + rng / 200), "l": close * (1 - rng / 200), "c": close,
                    "closed": True})
    return out


def test_start_names_the_mode_and_warns_when_there_is_no_stop_loss():
    says, state = ac.decide(ac.new_state(), facts(), now_ms=NOW)
    assert [s["key"] for s in says] == ["start"]
    start = says[0]
    assert start["hl"] == "ICNT 실전 시작했어요" and start["tone"] == "warn" and start["face"] == "focused"
    assert start["subs"][0] == {"b": "손절 기준 없음", "t": " · 떨어져도 매크로는 안 팔아요"}
    assert {"t": "익절은 +5%에서"} in start["subs"]
    again, _ = ac.decide(state, facts(), now_ms=NOW + MIN)
    assert again == [], "시작은 한 번만"
    with_sl, _ = ac.decide(ac.new_state(), facts(sl_pct=3.0, leverage=3, mode_label="모의"), now_ms=NOW)
    assert with_sl[0]["hl"] == "ICNT 모의 시작했어요" and with_sl[0]["tone"] == ""
    assert {"t": "손절은 -3%에서"} in with_sl[0]["subs"]
    assert any("레버리지 3배" in sub["t"] for sub in with_sl[0]["subs"])


def test_trades_read_like_the_session_276_case():
    """10/16 빗썸 KRW-ICNT 실전 — 201원 매수 → 209원 익절(신호 212.894) → 210원 바로 재매수."""
    state = started_state()
    says, state = ac.decide(state, facts(), fills=[fill(11, "BUY", 223.318, 201.0, NOW - 7 * MIN)], now_ms=NOW)
    buy = says[0]
    assert buy["hl"] == "샀어요!" and buy["face"] == "signal"
    assert buy["nums"] == "201원 · 223.318개 · 44,887원"
    assert buy["subs"] == [{"t": "시작하자마자 사는 매크로예요"}]
    assert buy["chips"][0] == {"kind": "macro", "label": "체결", "event_id": 11}
    sells, state = ac.decide(state, facts(), fills=[
        fill(12, "SELL", 223.318, 209.0, NOW - 60_000, signal=212.894, reason="청산"),
        fill(13, "BUY", 226.11, 210.0, NOW - 30_000)], now_ms=NOW)
    sell, rebuy = sells
    assert sell["face"] == "signal" and sell["hl"].startswith("익절")
    # 사고판 수수료(빗썸 0.04% × 2)를 뺀 값 — 가격 차이만으로는 +1,787원 · +3.98%
    assert sell["nums"] == "+1,750원 · +3.90%" and sell["nums_tone"] == "up"
    assert {"t": "209원 체결 · 수수료 0.04% × 2 뺀 값"} in sell["subs"]
    assert {"t": "신호 때 가격(212.89원)보다 1.8% 낮게 체결됐어요"} in sell["subs"]
    assert rebuy["hl"] == "쉬지도 않고 바로 다시 샀어요" and rebuy["face"] == "focused"
    assert {"t": "팔면 곧바로 다시 사는 매크로예요"} in rebuy["subs"]
    assert state["fill_cursor"] == 13 and state["pos"]["qty"] == 226.11


def test_a_breakeven_sell_is_not_called_a_win():
    """10/20 업비트 KRW-GTC 마틴게일 — 가격 차이 +0.00%(0원) 매도를 '익절했어요! 웃어요'(초록)라고 말했다."""
    gtc = dict(symbol="KRW-GTC", base="GTC", exchange="upbit", rule="H")
    state = started_state()
    _, state = ac.decide(state, facts(**gtc), fills=[fill(1, "BUY", 24.0, 425.0, NOW - 30 * MIN)], now_ms=NOW)
    says, state = ac.decide(state, facts(**gtc), fills=[fill(2, "SELL", 24.0, 425.0, NOW - MIN, reason="익절")], now_ms=NOW)
    sell = says[0]
    # 같은 값에 팔면 사고판 수수료만큼 손해 — 웃지 않고 손해(빨강)로
    assert sell["hl"] == "규칙대로 팔았어요" and sell["face"] == "focused" and sell["nums_tone"] == "down"
    assert sell["nums"] == "-10원 · -0.10%"
    assert {"t": "425원 체결 · 수수료 0.05% × 2 뺀 값"} in sell["subs"]
    # 가격이 수수료만큼만 올라 남은 게 없으면 본전 — 차분한 얼굴 · 색 없음 · '+0원' 이 아니라 '0원'
    _, state = ac.decide(state, facts(**gtc), fills=[fill(5, "BUY", 24.0, 425.0, NOW - 50_000)], now_ms=NOW)
    says, state = ac.decide(state, facts(**gtc), fills=[fill(6, "SELL", 24.0, 425.43, NOW - 40_000, reason="익절")],
                            now_ms=NOW)
    even = says[0]
    assert even["hl"] == "본전에 팔았어요" and even["face"] == "calm" and even["nums_tone"] == ""
    assert even["nums"] == "0원 · 0.00%"
    # 수수료를 넘는 이익은 그대로 익절
    _, state = ac.decide(state, facts(**gtc), fills=[fill(3, "BUY", 24.0, 425.0, NOW - 30_000)], now_ms=NOW)
    says, _ = ac.decide(state, facts(**gtc), fills=[fill(4, "SELL", 24.0, 430.0, NOW, reason="익절")], now_ms=NOW)
    assert says[0]["hl"].startswith("익절") and says[0]["nums"] == "+110원 · +1.08%" and says[0]["nums_tone"] == "up"
    # 가격은 조금 올랐어도 수수료를 빼면 손해면 웃지 않는다
    assert ac.rounds_to_zero(0.4, "KRW-GTC") and not ac.rounds_to_zero(0.6, "KRW-GTC")
    assert ac.rounds_to_zero(0.004, "BTCUSDT") and not ac.rounds_to_zero(0.01, "BTCUSDT")


def test_stop_loss_sell_and_short_positions_and_reasons():
    state = started_state()
    _, state = ac.decide(state, facts(rule="F"), fills=[fill(1, "BUY", 1, 100.0, NOW - MIN, reason="RSI 30.1 ≤ 32 · 진입")], now_ms=NOW)
    says, state = ac.decide(state, facts(rule="F"), fills=[fill(2, "SELL", 1, 97.0, NOW, reason="손절")], now_ms=NOW)
    assert says[0]["face"] == "calm" and says[0]["nums"] == "-3원 · -3.08%" and says[0]["nums_tone"] == "down"
    first, _ = ac.decide(started_state(), facts(rule="F"), fills=[fill(3, "BUY", 1, 100.0, NOW, reason="RSI 30.1 ≤ 32 · 진입")],
                         now_ms=NOW)
    assert {"t": "산 이유: RSI 30.1 ≤ 32"} in first[0]["subs"]
    short = facts(side="short", symbol="BTCUSDT", base="BTC", exchange="binance", rule="J")
    state = started_state()
    opened, state = ac.decide(state, short, fills=[fill(4, "SELL", 2, 100.0, NOW - MIN, reason="데드크로스 · 진입")], now_ms=NOW)
    assert opened[0]["hl"] == "팔아서 들어갔어요!" and opened[0]["nums"] == "100.00 USDT · 2개 · 200.00 USDT"
    closed, _ = ac.decide(state, short, fills=[fill(5, "BUY", 2, 95.0, NOW, reason="골든크로스 · 청산")], now_ms=NOW)
    assert closed[0]["nums"] == "+9.61 USDT · +4.80%" and {"t": "정리한 이유: 골든크로스"} in closed[0]["subs"]


def test_loss_and_gain_bands_speak_once_per_band_and_rearm_for_a_new_position():
    state = started_state()
    holding = dict(in_position=True, entry_price=210.0, position_qty=226.11)
    says, state = ac.decide(state, facts(**holding, last_price=201.0, unrealized_pct=-4.29), now_ms=NOW)
    loss = says[0]
    assert loss["hl"] == "어어? 손절 기준이 없는데, 이대로 괜찮으세요?" and loss["face"] == "warning" and loss["tone"] == "warn"
    assert loss["nums"] == "-4.3% · -2,035원" and loss["actions"] == [], "말풍선에 청산 버튼을 두지 않는다(실수로 누를 수 있다)"
    assert {"t": "다음엔 -5%에서 말할게요"} in loss["subs"]
    quiet, state = ac.decide(state, facts(**holding, last_price=202.0, unrealized_pct=-3.8), now_ms=NOW + MIN)
    assert [s["kind"] for s in quiet] == []
    deeper, state = ac.decide(state, facts(**holding, last_price=197.0, unrealized_pct=-6.19), now_ms=NOW + 2 * MIN)
    assert deeper[0]["hl"] == "-5%도 넘었어요. 차분히 볼게요" and deeper[0]["face"] == "critical" and deeper[0]["tone"] == "risk"
    _, state = ac.decide(state, facts(in_position=False), now_ms=NOW + 3 * MIN)
    again, state = ac.decide(state, facts(**holding, last_price=203.0, unrealized_pct=-3.3), now_ms=NOW + 4 * MIN)
    assert again and again[0]["kind"] == "loss", "새 포지션은 처음부터 다시 센다"
    gain, _ = ac.decide(started_state(), facts(in_position=True, entry_price=100.0, position_qty=1.0, last_price=105.2,
                                               unrealized_pct=5.2, tp_pct=None), now_ms=NOW)
    assert gain[0]["hl"] == "익절 안 하고 버티는 당신은 강심장!" and gain[0]["nums_tone"] == "up"


def test_stop_loss_distance_replaces_the_question_when_a_stop_exists():
    says, _ = ac.decide(started_state(), facts(in_position=True, entry_price=100.0, position_qty=1.0, last_price=96.8,
                                               unrealized_pct=-3.2, sl_pct=5.0), now_ms=NOW)
    assert says[0]["hl"] == "어어? -3% 넘었어요"
    assert {"t": "손절선(-5%)까지 1.8% 남았어요"} in says[0]["subs"]


def test_price_move_needs_three_percent_and_waits_fifteen_minutes():
    candles = bars([0.6] * 20, closes=[201.0] * 20)
    holding = dict(in_position=True, entry_price=201.0, position_qty=1.0, unrealized_pct=3.48)
    held = started_state() | {"pos_key": "f1", "pos_open_ms": NOW - 60 * MIN}
    says, state = ac.decide(held, facts(**holding, last_price=208.0), candles=candles, now_ms=NOW + 2 * MIN)
    move = [s for s in says if s["kind"] == "move"][0]
    assert move["face"] == "signal" and move["nums"] == "208원" and "+3.5%" in move["hl"]
    assert {"t": "익절선 211.05원까지 1.5%"} in move["subs"]
    again, state = ac.decide(state, facts(**holding, last_price=209.0), candles=candles, now_ms=NOW + 4 * MIN)
    assert not [s for s in again if s["kind"] == "move"], "같은 방향은 15분 쉰다"
    small, _ = ac.decide(held, facts(**holding, last_price=205.0), candles=candles, now_ms=NOW + 2 * MIN)
    assert not [s for s in small if s["kind"] == "move"]


def test_volatility_warns_escalates_and_calms_down():
    base = [0.65] * 276
    hot = bars(base + [2.2] * 12)
    says, state = ac.decide(started_state(), facts(in_position=True, entry_price=200.0, position_qty=1.0), candles=hot,
                            now_ms=NOW)
    vol = [s for s in says if s["kind"] == "volatility"]
    assert len(vol) == 1 and vol[0]["face"] == "warning" and vol[0]["tone"] == "warn"
    assert vol[0]["nums"] == "5분 봉 평균 2.2% · 평소 0.65%"
    assert {"t": "손절 기준이 없어 크게 빠져도 매크로는 들고 있어요"} in vol[0]["subs"]
    same, state = ac.decide(state, facts(), candles=hot, now_ms=NOW + 10 * MIN)
    assert not [s for s in same if s["kind"] == "volatility"], "같은 단계는 1시간에 한 번"
    hotter = bars(base[:-1] + [2.2] * 12 + [10.3])
    escalate, state = ac.decide(state, facts(), candles=hotter, now_ms=NOW + 15 * MIN)
    vol = [s for s in escalate if s["kind"] == "volatility"]
    assert len(vol) == 1 and vol[0]["hl"].startswith("출렁임이 더 커졌어요, 평소의")
    assert vol[0]["nums"].startswith("5분 봉 10.3% · ")
    calm = bars([0.65] * 288)
    first, state = ac.decide(state, facts(), candles=calm, now_ms=NOW + 20 * MIN)
    assert not [s for s in first if s["kind"] == "volatility"], "가라앉아도 30분은 지켜본다"
    later, state = ac.decide(state, facts(), candles=calm, now_ms=NOW + 51 * MIN)
    assert [s["hl"] for s in later if s["kind"] == "volatility"] == ["휴, 출렁임이 가라앉았어요"]
    assert ac.volatility(bars([0.6] * 50)) is None, "기준을 잴 봉이 모자라면 말하지 않는다"


def post(pid, title, author, published, summary="작성자는 ICNT가 0.1519달러에서 막히면 하락에 건다는 의견이다. 다만 조건부다."):
    return {"id": pid, "url": f"https://www.binance.com/en/square/post/{pid}", "title": title, "content_type": "community",
            "author": author, "source": "Binance Square", "community_summary": summary,
            "published": __import__("datetime").datetime.fromtimestamp(published / 1000, ac.timezone.utc).isoformat()}


def test_community_posts_come_one_by_one_without_repeats_or_floods():
    baseline, state = ac.decide(started_state() | {"news_rev": None}, facts(),
                                feed={"items": [post(1, "옛 글", "a", NOW - MIN)], "cursor": 40, "reset": True}, now_ms=NOW)
    assert not [s for s in baseline if s["kind"] == "post"] and state["news_rev"] == 40, "처음 본 피드는 위치만 기억"
    feed = {"cursor": 45, "reset": False, "items": [
        post(2, "$ICNT — 선물 매도 | 저항선 거부 설정", "DecodedCryptoHQ", NOW - 10 * MIN),
        post(3, "$ICNT — 선물 매도 | 저항선 거부 설정", "DecodedCryptoHQ", NOW - 9 * MIN),
        post(4, "$ICNT — 선물 매도 | 저항선 거부 설정", "Someone", NOW - 8 * MIN),
        post(5, "아주 오래된 글", "x", NOW - 4 * 3_600_000)]}
    says, state = ac.decide(state, facts(), feed=feed, now_ms=NOW)
    posts = [s for s in says if s["kind"] == "post"]
    assert len(posts) == 3, "글은 거르지 않는다 — 같은 사람이 다시 올린 글도 하나씩(3시간 넘은 옛 글만 새 글이 아니다)"
    assert posts[0]["post"]["title"] == "$ICNT — 선물 매도 | 저항선 거부 설정"
    assert posts[0]["post"]["summary"] == "ICNT가 0.1519달러에서 막히면 하락에 건다는 의견이다. 다만 조건부다.", "제목 + 내용 두 문장"
    assert posts[0]["post"]["author"] == "DecodedCryptoHQ" and posts[0]["face"] == "curious"
    assert posts[0]["chips"][0]["href"].startswith("https://www.binance.com/")
    assert posts[1]["hl"] == posts[2]["hl"] == "같은 내용 글이 또 올라왔어요"
    flood = {"cursor": 60, "reset": False, "items": [post(100 + i, f"글 {i}", f"u{i}", NOW - 5 * MIN + i) for i in range(10)]}
    says, state = ac.decide(state, facts(), feed=flood, now_ms=NOW + MIN)
    assert len([s for s in says if s["kind"] == "post"]) == 10, "개수로 자르지 않는다 — 걸러내는 건 같은 글 · 퍼 나른 글뿐"


def test_community_post_waits_for_its_summary_then_comes_with_title_and_body():
    """10/17 운영: 글을 보자마자 말해 83%가 제목만 나갔다(요약은 몇 분 뒤 준비됨)."""
    state = started_state() | {"news_rev": 40}
    pending = post(7, "$GTC 15분 만에 -5.15%!", "派大大", NOW - 2 * MIN, summary="") | {"community_summary_status": "pending"}
    ready_now = post(8, "GTC 롱 포지션이 근처에서 청산됐다", "S", NOW - MIN)
    says, state = ac.decide(state, facts(), feed={"cursor": 45, "reset": False, "items": [pending, ready_now]}, now_ms=NOW)
    assert [s["post"]["title"] for s in says if s["kind"] == "post"] == ["GTC 롱 포지션이 근처에서 청산됐다"]
    assert state["news_rev"] == 40, "요약을 기다리는 글이 있으면 읽은 위치를 넘기지 않는다"

    ready = pending | {"community_summary": "작성자는 GTC가 15분 동안 5.15% 급락했다고 전한다. 반등보다 하락이 멈추는지가 중요하다고 본다.",
                       "community_summary_status": "ready"}
    says, state = ac.decide(state, facts(), feed={"cursor": 46, "reset": False, "items": [ready, ready_now]}, now_ms=NOW + MIN)
    posts = [s for s in says if s["kind"] == "post"]
    assert [p["post"]["title"] for p in posts] == ["$GTC 15분 만에 -5.15%!"], "이미 말한 글은 다시 말하지 않는다"
    assert posts[0]["post"]["summary"] == "GTC가 15분 동안 5.15% 급락했다고 전한다. 반등보다 하락이 멈추는지가 중요하다고 본다."
    assert state["news_rev"] == 46

    stuck = post(9, "요약이 안 오는 글", "z", NOW, summary="") | {"community_summary_status": "pending"}
    _, state = ac.decide(state, facts(), feed={"cursor": 50, "reset": False, "items": [stuck]}, now_ms=NOW + 2 * MIN)
    says, state = ac.decide(state, facts(), feed={"cursor": 50, "reset": False, "items": [stuck]},
                            now_ms=NOW + 2 * MIN + ac.POST_SUMMARY_WAIT_MS)
    assert [s["post"]["title"] for s in says if s["kind"] == "post"] == ["요약이 안 오는 글"], "5분이 지나면 제목만이라도 말한다"
    assert state["news_rev"] == 50


def test_post_without_any_summary_status_yet_also_waits_but_only_briefly():
    """글이 처음 피드에 들어올 때는 요약 상태 칸이 없다 — 'pending' 만 보면 그런 글을 제목만 말했다(10/17 14:59 GTC)."""
    state = started_state() | {"news_rev": 40}
    fresh = post(21, "$GTC 박스권 저항선 돌파", "a", NOW - MIN, summary="")
    fresh.pop("community_summary")
    says, state = ac.decide(state, facts(), feed={"cursor": 41, "reset": False, "items": [fresh]}, now_ms=NOW)
    assert not [s for s in says if s["kind"] == "post"] and state["news_rev"] == 40
    assert ac.POST_SUMMARY_WAIT_MS == 30_000, "운영 실측 p80 23초 — 알림보다 한참 늦지 않게"
    says, state = ac.decide(state, facts(), feed={"cursor": 41, "reset": False, "items": [fresh]}, now_ms=NOW + 31_000)
    assert [s["post"]["title"] for s in says if s["kind"] == "post"] == ["$GTC 박스권 저항선 돌파"]
    gone = post(22, "요약을 못 만드는 글", "b", NOW, summary="") | {"community_summary_status": "failed"}
    says, _ = ac.decide(state, facts(), feed={"cursor": 42, "reset": False, "items": [gone]}, now_ms=NOW + 32_000)
    assert [s["post"]["title"] for s in says if s["kind"] == "post"] == ["요약을 못 만드는 글"], "못 만든다고 정해지면 기다리지 않는다"


def test_connection_problems_errors_and_the_end():
    state = started_state()
    says, state = ac.decide(state, facts(last_hb_ms=NOW - 90_000), now_ms=NOW)
    assert says[0]["key"] == f"offline:{NOW - 90_000}" and says[0]["tone"] == "warn"
    back, state = ac.decide(state, facts(last_hb_ms=NOW + 5 * MIN - 1000), now_ms=NOW + 5 * MIN)
    assert back[0]["hl"] == "휴, 다시 연결됐어요" and back[0]["subs"] == [{"t": "6분쯤 끊겼어요"}]
    errors = [{"id": 70, "at_ms": NOW, "message": "주문 거부: 잔고 부족", "event_name": "runner.order_failed"},
              {"id": 71, "at_ms": NOW + 1000, "message": "주문 거부: 잔고 부족", "event_name": "runner.order_failed"}]
    says, state = ac.decide(state, facts(), errors=errors, now_ms=NOW + 6 * MIN)
    problems = [s for s in says if s["kind"] == "problem"]
    assert len(problems) == 1 and problems[0]["hl"] == "주문이 안 나갔어요" and problems[0]["tone"] == "risk"
    end, state = ac.decide(state, facts(status="stopped", realized_pnl=1787.0, stopped_ms=NOW + 7 * MIN), now_ms=NOW + 7 * MIN)
    assert end[-1]["key"] == "end" and end[-1]["nums"] == "누적 실현 +1,787원" and state["ended"]
    assert ac.decide(state, facts(status="stopped"), now_ms=NOW + 8 * MIN)[0] == []


def test_old_errors_are_not_retold_when_a_running_session_is_first_seen():
    errors = [{"id": 5, "at_ms": NOW - 3 * 3_600_000, "message": "주문 거부", "event_name": "runner.order_failed"}]
    says, state = ac.decide(started_state(), facts(), errors=errors, now_ms=NOW)
    assert not [s for s in says if s["kind"] == "problem"] and state["err_cursor"] == 5


def test_decide_is_deterministic_and_does_not_mutate_its_input():
    state = started_state()
    snapshot = repr(state)
    inputs = dict(fills=[fill(1, "BUY", 1, 100.0, NOW)], feed={"cursor": 11, "reset": False, "items": [post(9, "글", "a", NOW)]})
    first, _ = ac.decide(state, facts(), now_ms=NOW, **inputs)
    second, _ = ac.decide(state, facts(), now_ms=NOW, **inputs)
    assert first == second and repr(state) == snapshot


# --- 저장 · API ----------------------------------------------------------------
MACRO_A = {"symbol": "LTCUSDT", "exchange": "binance", "rule_type": "A", "position_side": "long", "market": "spot",
           "leverage": 1, "candle_interval": "1h", "period": {"preset": "3m"},
           "params": {"take_profit_pct": 5, "initial_capital": 1000}, "risk": {}}


def _start_session(key):
    body = {"symbol": "LTCUSDT", "position_side": "long", "leverage": 1, "market": "spot", "testnet": True,
            "human_summary": "t", "macro": MACRO_A, "runner_version": "7"}
    r = client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _cycle(now):
    return ac.run_cycle(now_ms=now, claim=False, candles_for=lambda f: [], feed_for=lambda f, rev: None,
                        wait_for=lambda args: None)


def test_runtime_stores_each_saying_once_and_serves_it_to_the_owner_only():
    token = _signup()
    key = _key(token)
    sid = _start_session(key)
    try:
        r = client.post("/api/runner/heartbeat", json={"session_id": sid, "last_price": 100.0, "in_position": True,
                                                       "entry_price": 100.0, "position_qty": 2.0, "events": [
            {"kind": "fill", "message": "실전 주문 결과", "event_name": "runner.order_result", "event_key": "fill-1",
             "details": {"action": "BUY", "executed_qty": 2.0, "fill_price": 100.0}}]}, headers={"X-Runner-Key": key})
        assert r.status_code == 200, r.text
        import time
        now = int(time.time() * 1000)
        first = _cycle(now)
        assert first["added"] >= 2
        assert _cycle(now + 1000)["added"] == 0, "같은 일은 한 번만"
        body = client.get(f"/api/me/runner/sessions/{sid}/agent", headers=_auth(token)).json()
        kinds = [item["kind"] for item in body["items"]]
        assert kinds[:2] == ["start", "fill"]
        assert body["items"][1]["hl"] == "샀어요!" and body["items"][1]["nums"] == "100.00 USDT · 2개 · 200.00 USDT"
        assert body["cursor"] == body["items"][-1]["id"]
        tail = client.get(f"/api/me/runner/sessions/{sid}/agent?after={body['cursor']}", headers=_auth(token)).json()
        assert tail["items"] == [] and tail["cursor"] == body["cursor"]
        stranger = _signup()
        assert client.get(f"/api/me/runner/sessions/{sid}/agent", headers=_auth(stranger)).status_code == 404
    finally:
        _stop(key, sid)
    import time
    _cycle(int(time.time() * 1000) + 2000)
    body = client.get(f"/api/me/runner/sessions/{sid}/agent", headers=_auth(token)).json()
    assert body["items"][-1]["kind"] == "end" and body["status"] == "stopped"
    assert client.delete(f"/api/me/runner/sessions/{sid}", headers=_auth(token)).status_code == 200
    with get_session() as db:
        assert db.exec(select(AgentUtterance).where(AgentUtterance.session_id == sid)).first() is None
        assert db.get(AgentCommentaryState, sid) is None


def test_one_broken_session_does_not_stop_the_cycle(monkeypatch):
    token = _signup()
    key = _key(token)
    sid = _start_session(key)
    try:
        real = ac.evaluate_session
        calls = []

        def flaky(session_id, **kwargs):
            calls.append(session_id)
            if session_id == sid:
                raise RuntimeError("boom")
            return real(session_id, **kwargs)

        monkeypatch.setattr(ac, "evaluate_session", flaky)
        import time
        result = _cycle(int(time.time() * 1000))
        assert sid in calls and result["failed"] >= 1
    finally:
        _stop(key, sid)


def test_moves_before_the_entry_are_not_news_and_losses_are_not_cheered():
    candles = bars([0.6] * 20, closes=[195.0] * 19 + [201.0])
    fresh = started_state()
    _, fresh = ac.decide(fresh, facts(), fills=[fill(1, "BUY", 1, 201.0, NOW - 30_000)], now_ms=NOW - 20_000)
    says, _ = ac.decide(fresh, facts(in_position=True, entry_price=201.0, position_qty=1.0, last_price=201.5,
                                     unrealized_pct=0.2), candles=candles, now_ms=NOW + 4 * MIN)
    assert not [s for s in says if s["kind"] == "move"], "사기 전에 오른 것을 '방금 올랐다'고 하지 않는다"
    state = started_state()
    state["pos_key"], state["pos_open_ms"], state["loss_band"] = "f1", NOW - 60 * MIN, 5
    rebound, _ = ac.decide(state, facts(in_position=True, entry_price=210.0, position_qty=1.0, last_price=191.0,
                                        unrealized_pct=-9.0), candles=bars([0.6] * 20, closes=[178.0] * 20), now_ms=NOW + 2 * MIN)
    move = [s for s in rebound if s["kind"] == "move"][0]
    assert move["face"] == "focused" and not move["hl"].startswith("오오") and "실화" not in move["hl"]
    assert move["subs"][0] == {"t": "평단 210원 기준 -9.0%"}


def test_transient_errors_stay_in_the_log_and_uncertain_positions_speak_once():
    errors = [{"id": 9, "at_ms": NOW, "message": "일시 오류(시세): HTTPSConnectionPool(host='api.bithumb.com'): Read timed out. — 5초 후 재시도",
               "event_name": "runner.message"}]
    says, state = ac.decide(started_state(), facts(), errors=errors, now_ms=NOW + 1000)
    assert not [s for s in says if s["kind"] == "problem"]
    says, state = ac.decide(state, facts(position_uncertain=True), now_ms=NOW + 2000)
    assert [s["hl"] for s in says] == ["포지션 확인이 필요해요"]
    assert ac.decide(state, facts(position_uncertain=True), now_ms=NOW + 3000)[0] == []


def test_every_post_is_shown_and_reposts_are_only_labelled():
    state = started_state()
    feed = {"cursor": 20, "reset": False, "items": [
        post(11, "$ICNT 15분간 미결제약정 +6.07%, 가격 3.88% 상승.", "a", NOW - 9 * MIN),
        post(12, "ICNT 15분 보유량 6.07% 증가", "b", NOW - 8 * MIN, summary="미결제약정이 6.07% 늘었다고 전한다."),
        post(13, "❗️ ICNT 15분 보유량 6.07% 급증 👀", "c", NOW - 7 * MIN, summary="6.07% 늘었다."),
        post(14, "{알파}(84530xe0cd4cacddcbf4f36e845407ce53e87717b6601d)", "d", NOW - 6 * MIN),
        post(15, "🚀 $ICNT가 뜨거워지고 있다! 0.1519 돌파?", "e", NOW - 5 * MIN, summary="$0.1425~$0.1519 구간을 본다."),
        post(16, "0.1519 저항선 재시험", "f", NOW - 4 * MIN, summary="$0.1425~$0.1519 사이에서 움직인다.")]}
    says, state = ac.decide(state, facts(), feed=feed, now_ms=NOW)
    titles = [s["post"]["title"] for s in says if s["kind"] == "post"]
    assert titles == ["$ICNT 15분간 미결제약정 +6.07%, 가격 3.88% 상승.", "ICNT 15분 보유량 6.07% 증가",
                      "ICNT 15분 보유량 6.07% 급증", "{알파}(84530xe0cd4cacddcbf4f36e845407ce53e87717b6601d)",
                      "$ICNT가 뜨거워지고 있다! 0.1519 돌파?", "0.1519 저항선 재시험"], "글은 하나도 거르지 않는다"
    hls = [s["hl"] for s in says if s["kind"] == "post"]
    assert hls[1] == hls[2] == "같은 숫자 글이 또 올라왔어요" and hls[4] != "같은 숫자 글이 또 올라왔어요", hls
    assert ac.numbers_of("1시간 거래량 4.9배, 가격 10.82% 하락") == ["10.82%", "4.9"]


def test_closing_on_stop_and_the_end_comes_last_with_a_clean_note():
    state = started_state()
    _, state = ac.decide(state, facts(), fills=[fill(1, "BUY", 226.11, 210.0, NOW - 60 * MIN)], now_ms=NOW - 59 * MIN)
    final = [fill(2, "SELL", 226.11, 192.0, NOW + 5000)]
    says, state = ac.decide(state, facts(status="stopped", stopped_ms=NOW, realized_pnl=-2283.4,
                                         note="청산 완료 후 종료 · 최소 주문 단위 미만 잔여 수량 9.72281100076e-09 KRW-ICNT"),
                            fills=final, now_ms=NOW + 10_000)
    assert [s["kind"] for s in says] == ["fill", "end"]
    assert says[0]["hl"] == "종료하면서 정리했어요" and says[0]["nums"] == "-4,106원 · -8.65%"
    assert says[1]["at_ms"] >= says[0]["at_ms"] and says[1]["subs"] == [{"t": "청산 완료 후 종료"}]
    assert says[1]["nums"] == "누적 실현 -2,283원"


def test_no_offline_bubble_while_the_server_itself_just_started():
    """배포 직후엔 실행기 하트비트가 새 서버에 아직 안 닿았을 뿐 — '끊겼어요'라고 하지 않는다."""
    booting = facts(last_hb_ms=NOW - 90_000, boot_grace_until_ms=NOW + 60_000)
    says, state = ac.decide(started_state(), booting, now_ms=NOW)
    assert not [s for s in says if s["kind"] == "problem"] and not state["offline_since"]
    later, _ = ac.decide(state, facts(last_hb_ms=NOW - 90_000, boot_grace_until_ms=NOW - 1), now_ms=NOW)
    assert [s["key"] for s in later] == [f"offline:{NOW - 90_000}"], "서버가 자리 잡은 뒤의 끊김은 그대로 알린다"


def _wait(bar_close, *, touched=True, reached=False, last=None):
    conds = [{"key": "line", "label": "이번 봉 사는 선 26.118 넘기", "now": "고가 26.26", "ok": touched, "at_close": False},
             {"key": "ma", "label": "20봉 평균 26.523 위에서 끝나기", "now": "−1.2%", "ok": reached, "at_close": True,
              "wait": "20봉 평균 26.523 위에서 끝나야 사요"}]
    return {"rule": "I", "realtime": False, "sentence": "26.523 위에서 끝나면 사요", "headline": "26.523 위에서 끝나면 사요",
            "distance": "", "gap": "", "touched": touched, "reached": reached, "conditions": conds, "meter": None,
            "next_check_ms": bar_close, "last_bar": last}


def test_wait_says_why_it_held_and_then_that_the_bar_closed_without_buying():
    """FORT 10/17 — '마감되면 사요' 뒤에 말없이 다음 사는 선으로 넘어가 버그처럼 보였다."""
    state = started_state()
    bar = NOW + 5 * MIN
    says, state = ac.decide(state, facts(symbol="KRW-FORT", rule="I", last_price=26.2), wait=_wait(bar), now_ms=NOW)
    held = [s for s in says if s["kind"] == "wait"]
    assert [s["key"] for s in held] == [f"wait:held:{bar}"]
    assert held[0]["hl"] == "사는 선은 넘었는데, 아직 안 사요"
    assert held[0]["subs"][0] == {"b": "20봉 평균 26.523 위에서 끝나야 사요", "t": " · 지금 26.2원"}

    after = _wait(bar + 15 * MIN, touched=False, last={"at_ms": bar, "entered": False,
                                                       "why": "26.05로 끝나서 20봉 평균 26.523에 못 미쳤어요"})
    says, state = ac.decide(state, facts(symbol="KRW-FORT", rule="I", last_price=26.01), wait=after, now_ms=bar + 30_000)
    miss = [s for s in says if s["kind"] == "wait"]
    assert [s["key"] for s in miss] == [f"wait:miss:{bar}"]
    assert miss[0]["hl"] == "이번 봉은 안 샀어요"
    assert miss[0]["subs"][0] == {"b": "26.05로 끝나서 20봉 평균 26.523에 못 미쳤어요", "t": ""}
    assert miss[0]["subs"][1]["t"] == "새 봉 · 26.523 위에서 끝나면 사요"
    again, _ = ac.decide(state, facts(symbol="KRW-FORT", rule="I", last_price=26.01), wait=after, now_ms=bar + 40_000)
    assert not [s for s in again if s["kind"] == "wait"], "안 샀다는 말은 한 번만"


def test_wait_reached_bar_that_entered_says_nothing_more():
    state = started_state()
    bar = NOW + 5 * MIN
    says, state = ac.decide(state, facts(rule="I"), wait=_wait(bar, reached=True), now_ms=NOW)
    assert [s["hl"] for s in says if s["kind"] == "wait"] == ["사는 선을 넘었어요! 이대로 끝나면 사요"]
    done = _wait(bar + 15 * MIN, touched=False, last={"at_ms": bar, "entered": True, "why": ""})
    says, state = ac.decide(state, facts(rule="I"), wait=done, now_ms=bar + 30_000)
    assert not [s for s in says if s["kind"] == "wait"] and "wait_open" not in state
