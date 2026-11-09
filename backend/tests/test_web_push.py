"""앱(PWA) 알림 — 암호화(RFC 8291 예시) · VAPID · 구독 API · 중계 소식 → 대기열 → 보내기."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient
from sqlmodel import select

from app import agent_commentary as ac
from app import push_alerts, web_push
from app.db import PushOutbox, PushSubscription, get_session
from app.main import app
from tests.test_runner import _auth, _signup
from tests.test_runner_signals_api import _key, _stop

client = TestClient(app)
VAPID_KEY = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"  # RFC 8291 부록 A 의 서버 키(시험 전용)
FCM = "https://fcm.googleapis.com/fcm/send/"


@pytest.fixture
def vapid_on(monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", VAPID_KEY)
    monkeypatch.setenv("VAPID_SUBJECT", "https://gg-parrot.vercel.app")


@pytest.fixture(autouse=True)
def send_clock(monkeypatch):
    """보내는 데 걸린 시간 — 시험에서는 멈춰 두고, 필요한 시험만 send 안에서 앞으로 돌린다(다음 시도 = 응답 시각 + 대기)."""
    clock = {"s": 0.0}
    monkeypatch.setattr(push_alerts, "_monotonic", lambda: clock["s"])
    return clock


def device(name: str = "") -> dict:
    """브라우저 한 대 — PushManager 가 주는 것과 같은 모양의 구독과, 받은 본문을 풀 비밀키."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": FCM + (name or os.urandom(8).hex()), "keys": {"p256dh": web_push.b64u(public),
                                                                     "auth": web_push.b64u(os.urandom(16))},
            "_key": key}


def decrypt(body: bytes, sub: dict) -> bytes:
    """브라우저 쪽 풀기(RFC 8291) — 서버가 싼 본문을 그 기기만 풀 수 있는지 본다."""
    salt, idlen = body[:16], body[20]
    server_public = body[21:21 + idlen]
    ua_public = web_push.b64u_decode(sub["keys"]["p256dh"])
    auth = web_push.b64u_decode(sub["keys"]["auth"])
    shared = sub["_key"].exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public))

    def hkdf(s, ikm, info, n):
        return HKDF(algorithm=hashes.SHA256(), length=n, salt=s, info=info).derive(ikm)
    ikm = hkdf(auth, shared, b"WebPush: info\x00" + ua_public + server_public, 32)
    plain = AESGCM(hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(
        hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12), body[21 + idlen:], None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def public_sub(sub: dict) -> dict:
    return {key: value for key, value in sub.items() if not key.startswith("_")}


def stored(sub: dict) -> dict:
    """서버가 표에서 읽어 send 에 넘기는 모양."""
    return {"endpoint": sub["endpoint"], **sub["keys"]}


# --- 프로토콜 --------------------------------------------------------------------
def test_encryption_matches_the_rfc_8291_example():
    server = ec.derive_private_key(int.from_bytes(web_push.b64u_decode(VAPID_KEY), "big"), ec.SECP256R1())
    body = web_push.encrypt(b"When I grow up, I want to be a watermelon",
                            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                            "BTBZMqHH6r4Tts7J_aSIgg", salt=web_push.b64u_decode("DGv6ra1nlYgDCS1FRnbzlw"),
                            server_key=server)
    assert web_push.b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
        "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_a_random_message_round_trips_and_oversized_payloads_are_refused():
    sub = device()
    text = json.dumps({"title": "BTC · 관련 기사가 나왔어요", "body": "가" * 400}, ensure_ascii=False).encode()
    assert decrypt(web_push.encrypt(text, sub["keys"]["p256dh"], sub["keys"]["auth"]), sub) == text
    with pytest.raises(ValueError):
        web_push.encrypt(b"x" * (web_push.MAX_PLAINTEXT + 1), sub["keys"]["p256dh"], sub["keys"]["auth"])


def test_only_browser_push_services_are_reachable():
    for ok in ("https://fcm.googleapis.com/fcm/send/abc", "https://web.push.apple.com/QK1",
               "https://updates.push.services.mozilla.com/wpush/v2/x", "https://wns2-par02p.notify.windows.com/w/?token=1"):
        assert web_push.allowed_endpoint(ok), ok
    for bad in ("http://fcm.googleapis.com/fcm/send/abc", "https://169.254.169.254/latest", "https://localhost/x",
                "https://fcm.googleapis.com.evil.example/x", "https://user:pw@fcm.googleapis.com/x",
                "https://fcm.googleapis.com:8443/x", "https://evilnotify.windows.com/x", "file:///etc/passwd", "",
                "https://fcm.googleapis.com:bad/x", "https://fcm.googleapis.com:65536/x", "https://[::1/x"):
        assert not web_push.allowed_endpoint(bad), bad


def test_vapid_header_is_signed_for_the_push_service_origin(vapid_on):
    keys = web_push.vapid()
    header = web_push.vapid_authorization("https://web.push.apple.com/QK1abc", keys, now=1_800_000_000)
    token = header.split("t=")[1].split(",")[0]
    assert header.endswith(f"k={keys.public_key}")
    claims = jwt.decode(token, keys.private_key.public_key(), algorithms=["ES256"], audience="https://web.push.apple.com",
                        options={"verify_exp": False})
    assert claims == {"aud": "https://web.push.apple.com", "exp": 1_800_000_000 + 12 * 3600,
                      "sub": "https://gg-parrot.vercel.app"}


def test_send_reads_the_push_service_answer(vapid_on):
    sub = device()
    seen = []

    class Fake:
        def __init__(self, status):
            self.status = status

        def post(self, url, *, content, headers, timeout, follow_redirects):
            seen.append((url, headers, follow_redirects))
            return type("R", (), {"status_code": self.status})()
    assert web_push.send(stored(sub), b"{}", client=Fake(201)) == web_push.SENT
    url, headers, follow = seen[-1]
    assert url == sub["endpoint"] and headers["Content-Encoding"] == "aes128gcm" and headers["TTL"] == "3600"
    assert follow is False, "리다이렉트를 따라가 다른 곳으로 보내지 않는다"
    assert web_push.send(stored(sub), b"{}", client=Fake(410)) == web_push.GONE
    assert web_push.send(stored(sub), b"{}", client=Fake(404)) == web_push.GONE
    assert web_push.send(stored(sub), b"{}", client=Fake(429)) == web_push.RETRY
    assert web_push.send(stored(sub), b"{}", client=Fake(403)) == web_push.FAILED
    count = len(seen)
    assert web_push.send(stored(sub) | {"endpoint": "https://10.0.0.1/x"}, b"{}", client=Fake(201)) == web_push.FAILED
    assert len(seen) == count, "허용 목록 밖으로는 요청 자체를 보내지 않는다"


# --- 구독 API ----------------------------------------------------------------------
def test_push_is_off_without_a_key(monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    assert client.get("/api/push/config").json() == {"enabled": False, "public_key": ""}
    token = _signup()
    r = client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    assert r.status_code == 503


def test_subscribe_status_and_unsubscribe(vapid_on):
    config = client.get("/api/push/config").json()
    assert config["enabled"] and len(web_push.b64u_decode(config["public_key"])) == 65
    token = _signup()
    sub = device()
    assert client.put("/api/me/push/subscription", json=public_sub(sub)).status_code == 401
    r = client.put("/api/me/push/subscription", json=public_sub(sub) | {"platform": "android"}, headers=_auth(token))
    assert r.status_code == 200 and r.json() == {"subscribed": True}
    assert client.put("/api/me/push/subscription", json=public_sub(sub), headers=_auth(token)).status_code == 200
    with get_session() as db:
        rows = db.exec(select(PushSubscription).where(PushSubscription.endpoint == sub["endpoint"])).all()
        assert len(rows) == 1, "같은 기기는 한 줄"
    status = client.post("/api/me/push/status", json={"endpoint": sub["endpoint"]}, headers=_auth(token)).json()
    assert status == {"subscribed": True}
    other = _signup()
    assert client.post("/api/me/push/status", json={"endpoint": sub["endpoint"]}, headers=_auth(other)).json() == {
        "subscribed": False}
    # 같은 기기에서 다른 계정으로 켜면 그 계정으로 옮긴다 — 앞 계정의 소식이 이 기기에 오지 않는다.
    assert client.put("/api/me/push/subscription", json=public_sub(sub), headers=_auth(other)).status_code == 200
    assert client.post("/api/me/push/status", json={"endpoint": sub["endpoint"]}, headers=_auth(token)).json() == {
        "subscribed": False}
    assert client.post("/api/me/push/unsubscribe", json={"endpoint": sub["endpoint"]}, headers=_auth(other)).json() == {
        "subscribed": False}
    with get_session() as db:
        assert db.exec(select(PushSubscription).where(PushSubscription.endpoint == sub["endpoint"])).first() is None


def test_bad_subscriptions_are_refused(vapid_on):
    token = _signup()
    sub = public_sub(device())
    assert client.put("/api/me/push/subscription", json=sub | {"endpoint": "https://127.0.0.1/x"},
                      headers=_auth(token)).status_code == 400
    assert client.put("/api/me/push/subscription", json=sub | {"keys": {"p256dh": "AAAA", "auth": "BBBB"}},
                      headers=_auth(token)).status_code == 400
    assert client.put("/api/me/push/subscription", json=sub | {"endpoint": FCM + "x" * 3000},
                      headers=_auth(token)).status_code == 422
    # 포트가 망가진 주소는 서버 오류(500)가 아니라 거절(400) — 등록 · 확인 · 해제 모두
    for endpoint in ("https://fcm.googleapis.com:bad/x", "https://fcm.googleapis.com:65536/x"):
        assert client.put("/api/me/push/subscription", json=sub | {"endpoint": endpoint},
                          headers=_auth(token)).status_code == 400
        assert client.post("/api/me/push/status", json={"endpoint": endpoint}, headers=_auth(token)).status_code == 400
        assert client.post("/api/me/push/unsubscribe", json={"endpoint": endpoint},
                           headers=_auth(token)).status_code == 400


def test_an_account_keeps_at_most_ten_devices(vapid_on):
    token = _signup()
    endpoints = []
    for index in range(push_alerts.MAX_DEVICES + 2):
        sub = public_sub(device())
        endpoints.append(sub["endpoint"])
        push_alerts.subscribe(_user_id(token), sub["endpoint"], sub["keys"]["p256dh"], sub["keys"]["auth"],
                              now_ms=1_000 + index)
    with get_session() as db:
        kept = set(db.exec(select(PushSubscription.endpoint).where(PushSubscription.user_id == _user_id(token))).all())
    assert kept == set(endpoints[2:]), "가장 오래 안 쓴 두 대가 빠진다"


def _user_id(token: str) -> int:
    return client.get("/api/auth/me", headers=_auth(token)).json()["user"]["id"]


# --- 중계 소식 → 대기열 → 보내기 -----------------------------------------------------
MACRO = {"symbol": "LTCUSDT", "exchange": "binance", "rule_type": "A", "position_side": "long", "market": "spot",
         "leverage": 1, "candle_interval": "1h", "period": {"preset": "3m"},
         "params": {"take_profit_pct": 5, "initial_capital": 1000}, "risk": {}}


def _start(key):
    body = {"symbol": "LTCUSDT", "position_side": "long", "leverage": 1, "market": "spot", "testnet": True,
            "human_summary": "t", "macro": MACRO, "runner_version": "7"}
    r = client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _article(pid: int, title: str, published_ms: int) -> dict:
    return {"id": pid, "url": f"https://www.coindesk.com/markets/{pid}", "title": title, "source": "CoinDesk",
            "excerpt": "라이트코인 현물 ETF 심사가 다음 주로 미뤄졌다. 시장은 담담하다.",
            "published": datetime.fromtimestamp(published_ms / 1000, timezone.utc).isoformat()}


def _cycle(now_ms: int, items: list[dict], cursor: int):
    feed = {"items": items, "cursor": cursor, "reset": False}
    return ac.run_cycle(now_ms=now_ms, claim=False, candles_for=lambda f: [], wait_for=lambda args: None,
                        feed_for=lambda f, rev: feed if rev is not None else {"items": [], "cursor": 1, "reset": True})


def _rows(user_id: int) -> list[PushOutbox]:
    with get_session() as db:
        return list(db.exec(select(PushOutbox).where(PushOutbox.user_id == user_id).order_by(PushOutbox.id)).all())


def _posts(rows):
    return [row for row in rows if row.dedupe_key.startswith("post:")]


def test_agent_commentary_reaches_every_device_once(vapid_on):
    """중계 말풍선 전부(시작 · 매수 · 기사 · 종료 …)가 알림으로 — 기사는 계정마다 한 번, 그 밖은 세션마다."""
    token = _signup()
    user_id = _user_id(token)
    key = _key(token)
    phone, laptop = device(), device()
    for sub in (phone, laptop):
        assert client.put("/api/me/push/subscription", json=public_sub(sub), headers=_auth(token)).status_code == 200
    first, second = _start(key), _start(key)  # 같은 코인(LTC)을 두 세션이 돌린다
    try:
        r = client.post("/api/runner/heartbeat", json={"session_id": first, "last_price": 100.0, "in_position": True,
                                                       "entry_price": 100.0, "position_qty": 2.0, "events": [
            {"kind": "fill", "message": "실전 주문 결과", "event_name": "runner.order_result", "event_key": "fill-1",
             "details": {"action": "BUY", "executed_qty": 2.0, "fill_price": 100.0}}]}, headers={"X-Runner-Key": key})
        assert r.status_code == 200, r.text
        now = int(time.time() * 1000)
        _cycle(now, [], 1)  # 처음 본 피드는 위치만 기억
        keys = {row.dedupe_key for row in _rows(user_id)}
        assert {f"s{first}:start", f"s{second}:start"} <= keys
        fill = next(row for row in _rows(user_id) if row.dedupe_key.startswith(f"s{first}:fill"))
        fill_payload = json.loads(fill.payload_json)
        assert fill_payload.pop("at") > 0, "일어난 시각을 싣는다(늦게 도착해도 알림에 실제 시각)"
        assert fill_payload == {"title": "LTC · 샀어요!",
                                "body": "100.00 USDT · 2개 · 200.00 USDT\n시작하자마자 사는 매크로예요",
                                "url": f"/agents?session={first}", "tag": fill.dedupe_key}
        article = _article(7, "라이트코인 ETF 심사 연기", now - 60_000)
        _cycle(now + 2_000, [article], 2)
        posts = _posts(_rows(user_id))
        assert len(posts) == 1, "두 세션이 같은 기사를 말해도 알림은 한 번"
        payload = json.loads(posts[0].payload_json)
        assert payload["title"].split(" · ", 1) in (["LTC", "관련 기사가 나왔어요"], ["LTC", "이런 기사가 떴어요"])
        assert payload["body"].startswith("라이트코인 ETF 심사 연기\n라이트코인 현물 ETF 심사가 다음 주로 미뤄졌다.")
        assert payload["url"] in (f"/agents?session={first}", f"/agents?session={second}")
        queued = len(_rows(user_id))

        delivered = []

        def fake_send(subscription, data, **kwargs):
            delivered.append((subscription["endpoint"], data, kwargs))
            return web_push.GONE if subscription["endpoint"] == laptop["endpoint"] else web_push.SENT
        result = push_alerts.dispatch(now_ms=now + 3_000, send=fake_send)
        assert result["claimed"] == queued and result["jobs"] == queued * 2
        assert result["sent"] == 1 and result["gone"] == 1, "기기 수로 센다 — 전화는 받고 노트북 구독은 끝남"
        assert {endpoint for endpoint, _, _ in delivered} == {phone["endpoint"], laptop["endpoint"]}
        assert payload in [json.loads(data) for _, data, _ in delivered] and delivered[0][2]["urgency"] == "high"
        assert push_alerts.dispatch(now_ms=now + 4_000, send=fake_send) == {"claimed": 0}, "한 번 꺼낸 알림은 다시 안 간다"
        with get_session() as db:
            left = db.exec(select(PushSubscription.endpoint).where(PushSubscription.user_id == user_id)).all()
        assert left == [phone["endpoint"]], "끝난 구독(410)은 지운다"
    finally:
        _stop(key, first)
        _stop(key, second)
    # 끝나는 바퀴: '실행이 끝났어요'는 보내고, 그 바퀴에 나온 기사는 보내지 않는다 — '투자 중인' 코인 소식만.
    later = int(time.time() * 1000) + 10_000
    _cycle(later, [article, _article(8, "라이트코인 거래량 급증", later - 30_000)], 3)
    rows = _rows(user_id)
    assert {f"s{first}:end", f"s{second}:end"} <= {row.dedupe_key for row in rows}
    assert len(_posts(rows)) == 1


def test_old_happenings_stay_in_the_chat_only(vapid_on):
    facts = {"id": 5, "user_id": 1, "base": "BTC", "status": "running"}
    now = 10**13
    says = [{"key": "fill:1", "kind": "fill", "hl": "샀어요!", "at_ms": now - push_alerts.STALE_SAY_MS - 1},
            {"key": "loss:5", "kind": "loss", "hl": "손실이 -5%를 넘었어요", "at_ms": now, "nums": "-5.2%",
             "subs": [{"t": "손절선까지 1.8%p 남았어요"}, {"b": "포지션 보유 중", "t": " · 거래소에서 확인"}]}]
    with get_session() as db:
        assert push_alerts.enqueue_says(db, facts, says, now_ms=now) == 0, "기기가 없는 계정"
    assert push_alerts.say_payload(facts, says[1]) == {
        "title": "BTC · 손실이 -5%를 넘었어요", "body": "-5.2%\n손절선까지 1.8%p 남았어요\n포지션 보유 중 · 거래소에서 확인",
        "url": "/agents?session=5", "tag": "s5:loss:5", "at": now}


def test_accounts_without_a_device_queue_nothing(vapid_on):
    token = _signup()
    key = _key(token)
    sid = _start(key)
    try:
        now = int(time.time() * 1000)
        _cycle(now, [], 1)
        _cycle(now + 2_000, [_article(9, "라이트코인 해시레이트 최고치", now - 60_000)], 2)
        assert _rows(_user_id(token)) == []
    finally:
        _stop(key, sid)


def test_repeated_rejections_drop_the_device_and_test_push_goes_out(vapid_on):
    token = _signup()
    user_id = _user_id(token)
    sub = device()
    client.put("/api/me/push/subscription", json=public_sub(sub), headers=_auth(token))
    for attempt in range(push_alerts.FAIL_LIMIT):
        with get_session() as db:
            devices = push_alerts._subscriptions_for(db, {user_id})
        outcome = push_alerts._deliver([(user_id, {"title": "t"})], devices,
                                       now_ms=attempt, send=lambda *a, **k: web_push.FAILED)
        assert outcome["failed"] == 1
    with get_session() as db:
        assert db.exec(select(PushSubscription).where(PushSubscription.user_id == user_id)).first() is None
    assert client.post("/api/me/push/test", headers=_auth(token)).status_code == 404
    client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    sent = []
    result = push_alerts.send_test(user_id, now_ms=10**13, send=lambda s, d, **k: sent.append(d) or web_push.SENT)
    assert result == {"sent": 1, "devices": 1, "retry": 0} and json.loads(sent[0])["url"] == "/agents"
    with pytest.raises(Exception):
        push_alerts.send_test(user_id, now_ms=10**13 + 1_000, send=lambda *a, **k: web_push.SENT)


def test_test_push_tells_a_blocked_service_from_a_dead_device(vapid_on):
    """시험 알림이 0건이면 화면이 '보냈어요'라고 하지 않게 — 잠깐 막힘(retry)과 끝난 구독을 가려 준다."""
    token = _signup()
    user_id = _user_id(token)
    client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    now = 10**13
    assert push_alerts.send_test(user_id, now_ms=now, send=lambda *a, **k: web_push.RETRY) == {
        "sent": 0, "devices": 1, "retry": 1}
    assert push_alerts.send_test(user_id, now_ms=now + 10_000, send=lambda *a, **k: web_push.GONE) == {
        "sent": 0, "devices": 1, "retry": 0}
    with get_session() as db:
        assert db.exec(select(PushSubscription).where(PushSubscription.user_id == user_id)).first() is None, \
            "끝난 구독(410)은 지운다 — 화면은 상태를 다시 읽어 '꺼짐'으로"


# --- 보내다 막히거나 죽었을 때 -----------------------------------------------------------
FAR = 10**13  # 다른 시험이 남긴 대기열(지금 시각)과 섞이지 않게 먼 시각에서 돈다


def _queue(user_id: int, key: str, now_ms: int) -> None:
    facts = {"id": 77, "user_id": user_id, "base": "BTC", "status": "running"}
    say = {"key": key, "kind": "fill", "hl": "샀어요!", "at_ms": now_ms, "nums": "100.00 USDT"}
    with get_session() as db:
        assert push_alerts.enqueue_says(db, facts, [say], now_ms=now_ms) == 1
        db.commit()


def test_a_blocked_device_gets_the_alert_later_and_only_that_device(vapid_on):
    """429 · 5xx · 연결 실패는 그 기기에만 다시 보낸다 — 이미 받은 기기에 두 번 가지 않는다."""
    token = _signup()
    user_id = _user_id(token)
    phone, laptop = device(), device()
    for sub in (phone, laptop):
        client.put("/api/me/push/subscription", json=public_sub(sub), headers=_auth(token))
    _queue(user_id, "fill:blocked", FAR)
    answers = {phone["endpoint"]: [web_push.RETRY, web_push.SENT], laptop["endpoint"]: [web_push.SENT]}
    calls = []

    def fake_send(subscription, data, **kwargs):
        calls.append(subscription["endpoint"])
        return answers[subscription["endpoint"]].pop(0)
    first = push_alerts.dispatch(now_ms=FAR + 1_000, send=fake_send)
    assert (first["claimed"], first["sent"], first["retry"], first["later"]) == (1, 1, 1, 1)
    row = _rows(user_id)[0]
    assert row.sent_ms == 0 and row.attempts == 1, "막힌 기기가 남았으니 아직 끝나지 않았다"
    assert push_alerts.dispatch(now_ms=FAR + 2_000, send=fake_send) == {"claimed": 0}, "기다리는 동안은 꺼내지 않는다"
    second = push_alerts.dispatch(now_ms=FAR + 1_000 + push_alerts.RETRY_WAIT_MS[0], send=fake_send)
    assert (second["claimed"], second["jobs"], second["sent"]) == (1, 1, 1), "받은 노트북은 건너뛰고 전화에만"
    assert sorted(calls) == sorted([phone["endpoint"], phone["endpoint"], laptop["endpoint"]])
    assert _rows(user_id)[0].sent_ms == FAR + 1_000 + push_alerts.RETRY_WAIT_MS[0]
    assert push_alerts.dispatch(now_ms=FAR + 10 * 60_000, send=fake_send) == {"claimed": 0}


def test_a_crash_while_sending_does_not_lose_the_alert(vapid_on):
    """꺼낸 뒤 보내기 전에 프로세스가 죽어도 — 맡은 시간이 지나면 다시 꺼내 보낸다."""
    token = _signup()
    user_id = _user_id(token)
    client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    _queue(user_id, "fill:crash", FAR)

    class Crash(BaseException):
        pass

    def dying(*args, **kwargs):
        raise Crash()
    with pytest.raises(Crash):
        push_alerts.dispatch(now_ms=FAR + 1_000, send=dying)
    sent = []
    ok = lambda subscription, data, **kwargs: sent.append(data) or web_push.SENT  # noqa: E731
    assert push_alerts.dispatch(now_ms=FAR + 2_000, send=ok) == {"claimed": 0}, "맡은 동안 다른 바퀴가 겹쳐 보내지 않는다"
    result = push_alerts.dispatch(now_ms=FAR + 1_000 + push_alerts.LEASE_MS, send=ok)
    assert (result["claimed"], result["sent"]) == (1, 1) and len(sent) == 1
    assert _rows(user_id)[0].sent_ms > 0


def test_a_device_that_stays_blocked_is_given_up_after_a_few_tries(vapid_on):
    token = _signup()
    user_id = _user_id(token)
    client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    _queue(user_id, "fill:down", FAR)
    at = FAR
    for attempt in range(1, push_alerts.MAX_ATTEMPTS + 1):
        assert push_alerts.dispatch(now_ms=at, send=lambda *a, **k: web_push.RETRY)["claimed"] == 1, attempt
        at += push_alerts.RETRY_WAIT_MS[min(attempt, len(push_alerts.RETRY_WAIT_MS)) - 1]
    row = _rows(user_id)[0]
    assert row.sent_ms > 0 and row.attempts == push_alerts.MAX_ATTEMPTS, "시도를 다 쓰면 끝낸다"
    assert push_alerts.dispatch(now_ms=at + 60_000, send=lambda *a, **k: web_push.RETRY) == {"claimed": 0}
    with get_session() as db:
        assert db.exec(select(PushSubscription).where(PushSubscription.user_id == user_id)).first() is not None, \
            "잠깐 막힘은 기기를 지우지 않는다"


def test_a_late_worker_cannot_overwrite_the_pass_that_took_over(vapid_on):
    """보내다 맡은 시간이 지나 다른 바퀴가 다시 꺼내 끝냈으면, 늦게 끝난 옛 바퀴는 그 결과(받은 기기 · 다음 시각)를 덮지 않는다."""
    import threading
    token = _signup()
    user_id = _user_id(token)
    phone, laptop = device(), device()
    for sub in (phone, laptop):
        client.put("/api/me/push/subscription", json=public_sub(sub), headers=_auth(token))
    _queue(user_id, "fill:fence", FAR)
    took_over = FAR + 1_000 + push_alerts.LEASE_MS
    second = []
    lock = threading.Lock()

    def new_pass(subscription, data, **kwargs):
        return web_push.SENT if subscription["endpoint"] == phone["endpoint"] else web_push.RETRY

    def old_pass(subscription, data, **kwargs):
        with lock:  # 옛 바퀴가 보내는 동안 맡은 시간이 지나고, 새 바퀴가 다시 꺼내 전화에는 보낸다
            if not second:
                second.append(push_alerts.dispatch(now_ms=took_over, send=new_pass))
        return web_push.RETRY
    old = push_alerts.dispatch(now_ms=FAR + 1_000, send=old_pass)
    assert second[0]["claimed"] == 1 and second[0]["sent"] == 1
    assert old["lost"] == 1, "옛 바퀴의 결과는 쓰지 않는다"
    row = _rows(user_id)[0]
    assert row.attempts == 2 and json.loads(row.done_json) != [], "새 바퀴가 적은 '전화는 받음'이 남는다"
    assert row.lease_ms == took_over + push_alerts.RETRY_WAIT_MS[1], "다음 시각도 새 바퀴 것"
    calls = []
    retry = push_alerts.dispatch(now_ms=row.lease_ms, send=lambda s, d, **k: calls.append(s["endpoint"]) or web_push.SENT)
    assert retry["claimed"] == 1 and calls == [laptop["endpoint"]], "받은 전화에는 다시 보내지 않는다"


def test_retry_after_from_the_push_service_is_respected(vapid_on):
    class Fake:
        def __init__(self, status, headers):
            self.status, self.headers = status, headers

        def post(self, url, **kwargs):
            return type("R", (), {"status_code": self.status, "headers": self.headers})()
    sub = device()
    outcome = web_push.send(stored(sub), b"{}", client=Fake(429, {"Retry-After": "600"}))
    assert outcome == web_push.RETRY and outcome.retry_after_s == 600
    assert web_push.send(stored(sub), b"{}", client=Fake(503, {})).retry_after_s == 0
    assert web_push.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480 - 90) == 90
    assert web_push.retry_after_seconds("99999") == web_push.MAX_RETRY_AFTER_S
    assert web_push.retry_after_seconds("soon") == 0

    token = _signup()
    user_id = _user_id(token)
    client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    _queue(user_id, "fill:slow-down", FAR)
    busy = web_push._retry(600)
    assert push_alerts.dispatch(now_ms=FAR, send=lambda *a, **k: busy)["later"] == 1
    assert push_alerts.dispatch(now_ms=FAR + push_alerts.RETRY_WAIT_MS[0], send=lambda *a, **k: web_push.SENT) == {
        "claimed": 0}, "푸시 서비스가 10분 기다리라고 했으면 30초 뒤에 다시 보내 시도를 쓰지 않는다"
    assert push_alerts.dispatch(now_ms=FAR + 600_000, send=lambda *a, **k: web_push.SENT)["sent"] == 1


def test_waits_count_from_the_response_not_the_start_of_a_slow_batch(vapid_on, send_clock):
    """큰 묶음을 보내는 데 500초가 걸린 뒤 600초 기다리라는 답을 받으면 — 그 답을 받은 때부터 600초."""
    token = _signup()
    user_id = _user_id(token)
    client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    _queue(user_id, "fill:slow-batch", FAR)

    def slow(*args, **kwargs):
        send_clock["s"] += 500
        return web_push._retry(600)
    assert push_alerts.dispatch(now_ms=FAR, send=slow)["later"] == 1
    with get_session() as db:
        held = db.exec(select(PushSubscription.next_send_ms).where(PushSubscription.user_id == user_id)).one()
    assert held == FAR + 1_100_000 and _rows(user_id)[0].lease_ms == FAR + 1_100_000
    assert push_alerts.dispatch(now_ms=FAR + 700_000, send=lambda *a, **k: web_push.SENT) == {"claimed": 0}, \
        "답을 받고 100초 만에 다시 보내지 않는다"


def test_one_devices_retry_after_does_not_hold_back_the_others(vapid_on):
    """기기 하나가 한 시간 기다리라고 해도 다른 기기는 제 박자(30초 뒤)에 받는다."""
    token = _signup()
    user_id = _user_id(token)
    slow, quick = device(), device()
    for sub in (slow, quick):
        client.put("/api/me/push/subscription", json=public_sub(sub), headers=_auth(token))
    _queue(user_id, "fill:mixed-wait", FAR)
    answers = {slow["endpoint"]: web_push._retry(3700), quick["endpoint"]: web_push.RETRY}
    assert push_alerts.dispatch(now_ms=FAR, send=lambda s, d, **k: answers[s["endpoint"]])["later"] == 1
    calls = []
    retry = push_alerts.dispatch(now_ms=FAR + push_alerts.RETRY_WAIT_MS[0],
                                 send=lambda s, d, **k: calls.append(s["endpoint"]) or web_push.SENT)
    assert retry["claimed"] == 1 and calls == [quick["endpoint"]], "기다리라는 기기는 건너뛰고 다른 기기는 받는다"
    row = _rows(user_id)[0]
    assert row.sent_ms > 0 and row.result == "expired", "남은 기기는 한 시간 안에 받을 수 없어 끝낸다(까닭을 남긴다)"


def test_a_new_alert_also_waits_for_a_device_that_asked_to_wait(vapid_on):
    """기다리라는 기기에 새 알림이 쌓여도 그 시각 전에는 보내지 않는다(RFC 8030 §8.4) — 그 바퀴는 시도로 세지 않는다."""
    token = _signup()
    user_id = _user_id(token)
    client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    _queue(user_id, "fill:first", FAR)
    assert push_alerts.dispatch(now_ms=FAR, send=lambda *a, **k: web_push._retry(600))["later"] == 1
    _queue(user_id, "fill:second", FAR + 1_000)
    calls = []
    second = push_alerts.dispatch(now_ms=FAR + 1_000, send=lambda *a, **k: calls.append(1) or web_push.SENT)
    assert second["claimed"] == 1 and calls == [], "1초 뒤 새 알림도 그 기기에는 보내지 않는다"
    fresh = next(row for row in _rows(user_id) if row.dedupe_key.endswith("fill:second"))
    assert fresh.lease_ms == FAR + 600_000 and fresh.attempts == 0
    both = push_alerts.dispatch(now_ms=FAR + 600_000, send=lambda *a, **k: calls.append(1) or web_push.SENT)
    assert both["claimed"] == 2 and len(calls) == 2
    assert {row.result for row in _rows(user_id)} == {"delivered"}


def test_a_late_410_for_an_old_subscription_does_not_remove_a_new_one_with_the_same_id(vapid_on):
    """SQLite 는 지운 id 를 다시 쓴다 — 보내는 사이 껐다 켜 같은 id 를 받은 새 구독이 옛 구독의 410 으로 지워지지 않게."""
    token = _signup()
    user_id = _user_id(token)
    old = device()
    client.put("/api/me/push/subscription", json=public_sub(old), headers=_auth(token))
    with get_session() as db:
        snapshot = push_alerts._subscriptions_for(db, {user_id})
    new = device()

    def resubscribe_then_gone(subscription, data, **kwargs):
        # 옛 구독으로 보내는 사이 사용자가 껐다 켰다 — 새 구독이 같은 id 를 받는 경우를 그대로 만든다.
        with get_session() as db:
            row = db.get(PushSubscription, subscription["id"])
            row.endpoint, row.p256dh, row.auth = new["endpoint"], new["keys"]["p256dh"], new["keys"]["auth"]
            db.add(row)
            db.commit()
        return web_push.GONE
    push_alerts._deliver([(user_id, {"title": "t"})], snapshot, now_ms=FAR, send=resubscribe_then_gone)
    status = client.post("/api/me/push/status", json={"endpoint": new["endpoint"]}, headers=_auth(token)).json()
    assert status == {"subscribed": True}, "같은 id 라도 엔드포인트가 다르면 옛 결과를 적용하지 않는다"


def test_an_older_queue_table_gains_the_new_columns_on_boot(tmp_path, monkeypatch, vapid_on):
    """이전 버전(6칸) 대기열 표가 있는 DB — 기동 보정이 칸을 더해 쌓기 · 보내기가 그대로 돈다."""
    from sqlalchemy import create_engine, inspect
    from app import db as db_mod
    engine = create_engine(f"sqlite:///{tmp_path / 'older-push.db'}")
    monkeypatch.setattr(db_mod, "_engine", engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE pushoutbox (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            dedupe_key VARCHAR NOT NULL, payload_json VARCHAR NOT NULL DEFAULT '', created_ms BIGINT NOT NULL DEFAULT 0,
            sent_ms BIGINT NOT NULL DEFAULT 0)""")
        conn.exec_driver_sql("INSERT INTO pushoutbox VALUES (1, 41, 's1:start', '{\"title\":\"t\"}', 1000, 0)")
    try:
        db_mod.init_db()
        db_mod.init_db()
        assert {"lease_ms", "attempts", "done_json"} <= {col["name"] for col in inspect(engine).get_columns("pushoutbox")}
        result = push_alerts.dispatch(now_ms=2_000, send=lambda *a, **k: web_push.SENT)
        assert result["claimed"] == 1, "옛 줄도 꺼내 끝낸다"
    finally:
        engine.dispose()
    # 운영(Postgres) 기동 보정도 같은 세 칸을 더한다 — 마이그레이션 20261106130000 과 같은 정의.
    from tests.test_db_startup_migrations import current_schema
    state = current_schema()
    for column in ("lease_ms", "attempts", "done_json"):
        state["columns"].pop(("pushoutbox", column))
    statements = db_mod._pg_migration_statements(state)
    assert [s for s in statements if "pushoutbox" in s] == [
        "ALTER TABLE pushoutbox ADD COLUMN IF NOT EXISTS lease_ms BIGINT NOT NULL DEFAULT 0",
        "ALTER TABLE pushoutbox ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE pushoutbox ADD COLUMN IF NOT EXISTS done_json VARCHAR NOT NULL DEFAULT ''",
    ]


def test_leaving_the_service_removes_devices_and_history(vapid_on):
    token = _signup()
    user_id = _user_id(token)
    client.put("/api/me/push/subscription", json=public_sub(device()), headers=_auth(token))
    with get_session() as db:
        db.add(PushOutbox(user_id=user_id, dedupe_key="post:x", payload_json="{}", created_ms=1, sent_ms=1))
        db.commit()
    r = client.request("DELETE", "/api/me/account", headers=_auth(token),
                       json={"confirmation": "탈퇴", "password": "password123"})
    assert r.status_code == 200, r.text
    with get_session() as db:
        assert db.exec(select(PushSubscription).where(PushSubscription.user_id == user_id)).first() is None
        assert db.exec(select(PushOutbox).where(PushOutbox.user_id == user_id)).first() is None
