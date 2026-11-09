"""앱(PWA) 알림 — 암호화(RFC 8291 예시) · VAPID · 구독 세대 · 중계 소식 → 알림 이벤트 → 기기별 전송."""
from __future__ import annotations

import itertools
import json
import os
import random
import threading
import time
from datetime import datetime, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlmodel import select

from app import agent_commentary as ac
from app import push_alerts, web_push
from app.db import PushDelivery, PushOutbox, PushSubscription, get_session
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


def _user_id(token: str) -> int:
    return client.get("/api/auth/me", headers=_auth(token)).json()["user"]["id"]


def _on(token: str, sub: dict | None = None, **extra) -> dict:
    """이 기기를 켠다(앱의 켜기와 같은 요청) — 받은 세대를 sub['version'] 에 둔다."""
    sub = sub or device()
    r = client.put("/api/me/push/subscription", json=public_sub(sub) | extra, headers=_auth(token))
    assert r.status_code == 200, r.text
    sub["version"] = r.json()["version"]
    return sub


def _status(token: str, sub: dict) -> dict:
    return client.post("/api/me/push/status", json={"endpoint": sub["endpoint"]}, headers=_auth(token)).json()


def test_subscribe_status_and_unsubscribe(vapid_on):
    config = client.get("/api/push/config").json()
    assert config["enabled"] and len(web_push.b64u_decode(config["public_key"])) == 65
    token = _signup()
    sub = device()
    assert client.put("/api/me/push/subscription", json=public_sub(sub)).status_code == 401
    _on(token, sub, platform="android")
    first = sub["version"]
    assert first > 0
    assert _on(token, sub)["version"] == first, "같은 계정이 다시 켜면 세대는 그대로"
    with get_session() as db:
        rows = db.exec(select(PushSubscription).where(PushSubscription.endpoint == sub["endpoint"])).all()
        assert len(rows) == 1, "같은 기기는 한 줄"
    assert _status(token, sub) == {"subscribed": True, "version": first}
    other = _signup()
    assert _status(other, sub) == {"subscribed": False, "version": 0}
    # 같은 기기에서 다른 계정으로 켜면 그 계정으로 옮기고 세대를 올린다 — 앞 계정의 소식이 이 기기에 오지 않는다.
    assert _on(other, sub)["version"] > first
    assert _status(token, sub)["subscribed"] is False
    assert client.post("/api/me/push/unsubscribe", json={"endpoint": sub["endpoint"]}, headers=_auth(other)).json() == {
        "subscribed": False}
    with get_session() as db:
        assert db.exec(select(PushSubscription).where(PushSubscription.endpoint == sub["endpoint"])).first() is None


def test_old_requests_cannot_change_a_newer_generation(vapid_on):
    """규칙 2 — 앱이 기대한 세대가 아니면 서버가 거절한다: 다른 계정이 가져간 기기를 옛 재등록이 덮지 않고, 서버가 지운
    기기를 재등록이 되살리지 않고, 늦게 온 옛 해제가 새로 켠 것을 지우지 않는다."""
    a, b = _signup(), _signup()
    sub = _on(a)
    first = sub["version"]
    sync = public_sub(sub) | {"expected_version": first}
    assert client.put("/api/me/push/subscription", json=sync, headers=_auth(a)).status_code == 200, "제 세대면 고친다"
    _on(b, sub)  # 같은 기기에서 B 가 켰다
    assert client.put("/api/me/push/subscription", json=sync, headers=_auth(a)).status_code == 409
    assert _status(b, sub)["subscribed"] is True and _status(a, sub)["subscribed"] is False
    _on(a, sub)  # 다시 A — 세대가 또 오른다
    late_off = {"endpoint": sub["endpoint"], "expected_version": first}
    assert client.post("/api/me/push/unsubscribe", json=late_off, headers=_auth(a)).status_code == 200
    assert _status(a, sub) == {"subscribed": True, "version": sub["version"]}, "옛 세대의 해제는 새 세대를 지우지 않는다"
    client.post("/api/me/push/unsubscribe", json={"endpoint": sub["endpoint"]}, headers=_auth(a))
    resync = public_sub(sub) | {"expected_version": sub["version"]}
    assert client.put("/api/me/push/subscription", json=resync, headers=_auth(a)).status_code == 409, \
        "서버가 지운 기기를 재등록이 되살리지 않는다"


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


# --- 중계 소식 → 알림 이벤트 → 기기별 전송 ------------------------------------------------
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


def _deliveries(user_id: int) -> list[PushDelivery]:
    with get_session() as db:
        return list(db.exec(select(PushDelivery).where(PushDelivery.user_id == user_id).order_by(PushDelivery.id)).all())


def _posts(rows):
    return [row for row in rows if row.dedupe_key.startswith("post:")]


class Service:
    """푸시 서비스 흉내 — 엔드포인트마다 정해 둔 답을 차례로(없으면 받음). 보낸 기록을 남긴다."""

    def __init__(self, answers: dict | None = None):
        self.answers = {key: list(value) for key, value in (answers or {}).items()}
        self.log = []
        self.lock = threading.Lock()

    def __call__(self, subscription, data, *, ttl, urgency, **kwargs):
        payload = json.loads(data)
        with self.lock:
            self.log.append({"endpoint": subscription["endpoint"], "event": payload.get("event"), "ttl": ttl,
                             "payload": payload, "urgency": urgency})
            queue = self.answers.get(subscription["endpoint"])
            answer = queue.pop(0) if queue else web_push.SENT
        return answer() if callable(answer) else answer

    def sent_to(self, sub: dict) -> list:
        return [item["event"] for item in self.log if item["endpoint"] == sub["endpoint"]]


_bases = itertools.count(1)


def _base() -> int:
    """시험마다 따로 쓰는 먼 시각 — 앞 시험이 남긴 전송은 이 시각엔 이미 만료라 서로 섞이지 않는다."""
    return 10**13 + next(_bases) * 10**10


def _event(user_id: int, key: str, now_ms: int) -> int:
    facts = {"id": 77, "user_id": user_id, "base": "BTC", "status": "running"}
    say = {"key": key, "kind": "fill", "hl": "샀어요!", "at_ms": now_ms, "nums": "100.00 USDT"}
    with get_session() as db:
        assert push_alerts.enqueue_says(db, facts, [say], now_ms=now_ms) == 1
        db.commit()
        return db.exec(select(PushOutbox.id).where(PushOutbox.user_id == user_id,
                                                   PushOutbox.dedupe_key == f"s77:{key}")).one()


def _delivery(event_id: int, sub: dict) -> PushDelivery:
    with get_session() as db:
        return db.exec(select(PushDelivery).where(PushDelivery.event_id == event_id,
                                                  PushDelivery.endpoint == sub["endpoint"])).one()


def _event_row(event_id: int) -> PushOutbox:
    with get_session() as db:
        return db.get(PushOutbox, event_id)


def test_agent_commentary_reaches_every_device_once(vapid_on):
    """중계 말풍선 전부(시작 · 매수 · 기사 · 종료 …)가 알림 이벤트로, 켜 둔 기기마다 전송 한 줄씩 — 기사는 계정마다 한 번."""
    token = _signup()
    user_id = _user_id(token)
    key = _key(token)
    phone, laptop = _on(token), _on(token)
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
        assert fill.expires_ms == fill.created_ms + push_alerts.EVENT_TTL_MS, "만료 시각은 쌓을 때 고정"
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
        events = _rows(user_id)
        assert len(_deliveries(user_id)) == len(events) * 2, "이벤트마다 켜 둔 기기 두 대"

        service = Service({laptop["endpoint"]: [web_push.GONE]})
        result = push_alerts.dispatch(now_ms=now + 3_000, send=service)
        assert result["claimed"] == len(events) * 2
        assert result["sent"] == len(events) and result["gone"] == 1, "노트북은 첫 전송에서 끝난 구독 — 나머지는 취소"
        assert sorted(service.sent_to(phone)) == sorted(row.id for row in events)
        assert len(service.sent_to(laptop)) == 1, "끝난 구독(410) 뒤의 전송은 보내지 않는다"
        sent = next(item for item in service.log if item["event"] == posts[0].id and item["endpoint"] == phone["endpoint"])
        assert {k: v for k, v in sent["payload"].items() if k != "event"} == payload and sent["urgency"] == "high"
        assert push_alerts.dispatch(now_ms=now + 4_000, send=service) == {"claimed": 0}, "끝난 전송은 다시 안 간다"
        with get_session() as db:
            left = db.exec(select(PushSubscription.endpoint).where(PushSubscription.user_id == user_id)).all()
        assert left == [phone["endpoint"]], "끝난 구독(410)은 지운다"
        assert {row.result for row in _rows(user_id)} == {"delivered"}
        assert {d.state for d in _deliveries(user_id) if d.endpoint == laptop["endpoint"]} <= {"failed", "cancelled"}
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


def test_the_quiet_bubble_stays_in_the_chat(vapid_on):
    """'조용~하네요'(45분 동안 별일 없음)는 채팅에만 — 앱 알림으로 쌓지 않는다."""
    token = _signup()
    user_id = _user_id(token)
    _on(token)
    base = _base()
    facts = {"id": 78, "user_id": user_id, "base": "BTC", "status": "running"}
    quiet = {"key": "quiet:1", "kind": "quiet", "hl": "조용~하네요. 숨 고르는 중이에요", "at_ms": base, "nums": "1 ~ 2 · 45분"}
    band = {"key": "loss:e100:2", "kind": "loss", "hl": "어어? -2% 넘었어요", "at_ms": base, "nums": "-2.1%"}
    with get_session() as db:
        assert push_alerts.enqueue_says(db, facts, [quiet, band], now_ms=base) == 1
        db.commit()
    assert [row.dedupe_key for row in _rows(user_id)] == ["s78:loss:e100:2"]


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


# --- 규칙 1: 기기 하나의 실패가 다른 기기의 기회를 쓰지 않는다 ------------------------------------
def test_one_device_failing_never_spends_another_devices_tries(vapid_on):
    token = _signup()
    user_id = _user_id(token)
    broken, slow = _on(token), _on(token)
    base = _base()
    event = _event(user_id, "fill:independent", base)
    # 고장 난 기기는 늘 막히고, 다른 기기는 처음에 '15분 기다려' 뒤 받는다.
    service = Service({broken["endpoint"]: [web_push.RETRY] * 10, slow["endpoint"]: [web_push._retry(900)]})
    at = base
    for _ in range(12):
        push_alerts.dispatch(now_ms=at, send=service)
        at += 120_000
    assert _delivery(event, broken).state == "gave_up" and _delivery(event, broken).attempts == push_alerts.MAX_ATTEMPTS
    assert _delivery(event, slow).state == "accepted" and _delivery(event, slow).attempts == 2, \
        "고장 난 기기가 시도를 다 써도 다른 기기는 제 차례에 받는다"
    assert _event_row(event).result == "delivered"


def test_a_device_turned_on_later_does_not_get_earlier_alerts(vapid_on):
    """규칙 4 — 대상 기기는 쌓을 때 정한다."""
    token = _signup()
    user_id = _user_id(token)
    phone = _on(token)
    base = _base()
    event = _event(user_id, "fill:before-tablet", base)
    tablet = _on(token)
    service = Service()
    push_alerts.dispatch(now_ms=base + 1_000, send=service)
    assert service.sent_to(phone) == [event] and service.sent_to(tablet) == []


def test_switching_accounts_cancels_the_old_accounts_pending_alerts(vapid_on):
    """규칙 4 — 같은 기기를 다른 계정이 가져가면 앞 계정 앞으로 남은 전송은 취소된다(새 주인에게 가지 않는다)."""
    a, b = _signup(), _signup()
    sub = _on(a)
    base = _base()
    old = _event(_user_id(a), "fill:a-only", base)
    push_alerts.dispatch(now_ms=base, send=Service({sub["endpoint"]: [web_push.RETRY]}))  # 다시 보내기 대기
    _on(b, sub)
    assert _delivery(old, sub).state == "cancelled"
    new = _event(_user_id(b), "fill:b", base + 10_000)
    service = Service()
    push_alerts.dispatch(now_ms=base + 120_000, send=service)
    assert service.sent_to(sub) == [new], "B 의 기기에 A 의 알림이 가지 않는다"
    assert _event_row(old).result == "cancelled"


# --- 규칙 2: 늦게 끝난 옛 바퀴 · 죽은 바퀴 -----------------------------------------------------------
def test_a_crash_while_sending_does_not_lose_the_alert(vapid_on):
    token = _signup()
    user_id = _user_id(token)
    sub = _on(token)
    base = _base()
    event = _event(user_id, "fill:crash", base)

    class Crash(BaseException):
        pass

    def dying(*args, **kwargs):
        raise Crash()
    with pytest.raises(Crash):
        push_alerts.dispatch(now_ms=base + 1_000, send=dying)
    service = Service()
    assert push_alerts.dispatch(now_ms=base + 2_000, send=service) == {"claimed": 0}, "맡은 동안 다른 바퀴가 겹쳐 보내지 않는다"
    result = push_alerts.dispatch(now_ms=base + 1_000 + push_alerts.LEASE_MS, send=service)
    assert (result["claimed"], result["sent"]) == (1, 1) and service.sent_to(sub) == [event]
    assert _event_row(event).result == "delivered"


def test_a_late_pass_cannot_overwrite_the_pass_that_took_over(vapid_on):
    """보내다 맡은 시간이 지나 다른 바퀴가 다시 맡아 끝냈으면, 늦게 끝난 옛 바퀴의 결과는 쓰지 않는다(토큰이 다르다)."""
    token = _signup()
    user_id = _user_id(token)
    phone, laptop = _on(token), _on(token)
    base = _base()
    event = _event(user_id, "fill:fence", base)
    took_over = base + 1_000 + push_alerts.LEASE_MS
    second = []
    lock = threading.Lock()

    def new_pass(subscription, data, **kwargs):
        return web_push.SENT if subscription["endpoint"] == phone["endpoint"] else web_push.RETRY

    def old_pass(subscription, data, **kwargs):
        with lock:
            run = not second
            second.append(1)
        if run:  # 옛 바퀴가 보내는 동안 맡은 시간이 지나고, 새 바퀴가 다시 맡는다
            second.append(push_alerts.dispatch(now_ms=took_over, send=new_pass))
        return web_push.RETRY
    old = push_alerts.dispatch(now_ms=base + 1_000, send=old_pass)
    assert old["lost"] == 2, "옛 바퀴의 결과는 쓰지 않는다"
    assert _delivery(event, phone).state == "accepted", "새 바퀴가 적은 '전화는 받음'이 남는다"
    retry = _delivery(event, laptop)
    assert retry.state == "pending" and retry.next_ms == took_over + push_alerts.RETRY_WAIT_MS[1], "다음 시각도 새 바퀴 것"
    service = Service()
    push_alerts.dispatch(now_ms=retry.next_ms, send=service)
    assert service.sent_to(laptop) == [event] and service.sent_to(phone) == [], "받은 전화에는 다시 보내지 않는다"


def test_a_late_410_for_an_old_generation_does_not_remove_the_new_one(vapid_on):
    """SQLite 는 지운 id 를 다시 쓴다 — 같은 id 를 받은 새 세대가 옛 세대의 410 으로 지워지지 않게(id · 세대 · 주소가 다 맞을 때만)."""
    token = _signup()
    old = _on(token)
    with get_session() as db:
        row = db.exec(select(PushSubscription).where(PushSubscription.endpoint == old["endpoint"])).one()
        key = (row.id, row.version, row.endpoint)
        new = device()
        row.endpoint, row.p256dh, row.auth, row.version = new["endpoint"], new["keys"]["p256dh"], new["keys"]["auth"], row.version + 1
        db.add(row)
        db.commit()
    with get_session() as db:
        push_alerts._device_results(db, accepted={}, holds={}, failed={}, gone={key: True}, now_ms=1)
        db.commit()
    assert _status(token, new)["subscribed"] is True


# --- 규칙 3: 엔드포인트 대기와 만료 시각 ------------------------------------------------------------
def test_an_endpoint_is_sent_one_at_a_time_and_stops_when_asked_to_wait(vapid_on):
    """같은 엔드포인트는 차례로 — 첫 응답이 '10분 기다려'면 같은 바퀴의 뒤 전송도 보내지 않는다(시도로 세지 않는다)."""
    token = _signup()
    user_id = _user_id(token)
    sub = _on(token)
    base = _base()
    first, second = _event(user_id, "fill:one", base), _event(user_id, "fill:two", base)
    service = Service({sub["endpoint"]: [web_push._retry(600)]})
    result = push_alerts.dispatch(now_ms=base, send=service)
    assert service.sent_to(sub) == [first] and result["held"] == 1
    assert _delivery(second, sub).attempts == 0 and _delivery(second, sub).next_ms == base + 600_000
    assert push_alerts.dispatch(now_ms=base + 599_000, send=service) == {"claimed": 0}, "대기 전에는 맡지도 않는다"
    push_alerts.dispatch(now_ms=base + 600_000, send=service)
    assert service.sent_to(sub) == [first, first, second], "대기가 끝나면 쌓인 차례대로"


def test_a_new_alert_and_a_test_push_both_wait_for_the_device(vapid_on):
    """대기는 엔드포인트에 — 그사이 새로 쌓인 알림도, 시험 알림도 그 시각 전에는 보내지 않는다(RFC 8030 §8.4)."""
    token = _signup()
    user_id = _user_id(token)
    sub = _on(token)
    base = _base()
    _event(user_id, "fill:first", base)
    push_alerts.dispatch(now_ms=base, send=Service({sub["endpoint"]: [web_push._retry(600)]}))
    later = _event(user_id, "fill:second", base + 1_000)
    service = Service()
    assert push_alerts.dispatch(now_ms=base + 1_000, send=service) == {"claimed": 0}
    assert push_alerts.send_test(user_id, now_ms=base + 2_000, send=service) == {"sent": 0, "devices": 1, "retry": 1}
    assert service.log == [], "새 알림 · 시험 알림 모두 보내지 않았다"
    assert _delivery(later, sub).attempts == 0
    push_alerts.dispatch(now_ms=base + 600_000, send=service)
    assert len(service.sent_to(sub)) == 2, "대기가 끝나면 첫 알림 · 새 알림"
    with get_session() as db:
        test_states = db.exec(select(PushDelivery.state).join(PushOutbox, PushOutbox.id == PushDelivery.event_id).where(
            PushOutbox.user_id == user_id, PushOutbox.dedupe_key.startswith("test:"))).all()
    assert test_states == ["expired"], "시험 알림(5분)은 10분 대기가 끝나기 전에 만료 — 늦게 울리지 않는다"


def test_a_wait_is_written_the_moment_it_arrives(vapid_on, monkeypatch):
    """한 바퀴가 기기 A 에서 '기다려'를 받고 다른 기기로 보내는 사이 — 다른 바퀴가 A 에 새 알림을 보내지 않는다
    (대기를 바퀴 끝이 아니라 받는 즉시 적는다)."""
    monkeypatch.setattr(push_alerts, "SEND_THREADS", 1)
    token = _signup()
    user_id = _user_id(token)
    first, second = _on(token), _on(token)
    base = _base()
    _event(user_id, "fill:wait-now", base)
    inner = Service()

    def outer(subscription, data, **kwargs):
        if subscription["endpoint"] == first["endpoint"]:
            return web_push._retry(600)
        # 두 번째 기기로 보내는 동안 새 알림이 쌓이고 다른 바퀴가 돈다
        _event(user_id, "fill:wait-now-2", base)
        push_alerts.dispatch(now_ms=base, send=inner)
        return web_push.SENT
    push_alerts.dispatch(now_ms=base, send=outer)
    assert inner.sent_to(first) == [], "다른 바퀴도 A 의 대기를 안다"
    assert len(inner.sent_to(second)) == 1


def test_a_success_never_clears_a_newer_wait(vapid_on):
    """대기는 더 늦은 값으로만 — 늦게 온 '받음'이 그 뒤에 받은 '기다려'를 지우지 않는다."""
    token = _signup()
    sub = _on(token)
    with get_session() as db:
        row = db.exec(select(PushSubscription).where(PushSubscription.endpoint == sub["endpoint"])).one()
        key = (row.id, row.version, row.endpoint)
    with get_session() as db:
        push_alerts._device_results(db, accepted={}, holds={key: 5_000}, failed={}, gone={}, now_ms=1)
        push_alerts._device_results(db, accepted={key: 2}, holds={key: 3_000}, failed={}, gone={}, now_ms=2)
        db.commit()
    with get_session() as db:
        assert db.get(PushSubscription, key[0]).next_send_ms == 5_000


def test_waits_count_from_the_response_not_the_start_of_a_slow_batch(vapid_on, send_clock):
    """큰 묶음을 보내는 데 500초가 걸린 뒤 600초 기다리라는 답을 받으면 — 그 답을 받은 때부터 600초."""
    token = _signup()
    user_id = _user_id(token)
    sub = _on(token)
    base = _base()
    event = _event(user_id, "fill:slow-batch", base)

    def slow(*args, **kwargs):
        send_clock["s"] += 500
        return web_push._retry(600)
    push_alerts.dispatch(now_ms=base, send=slow)
    with get_session() as db:
        held = db.exec(select(PushSubscription.next_send_ms).where(PushSubscription.endpoint == sub["endpoint"])).one()
    assert held == base + 1_100_000 and _delivery(event, sub).next_ms == base + 1_100_000
    assert push_alerts.dispatch(now_ms=base + 700_000, send=Service()) == {"claimed": 0}, "답을 받고 100초 만에 다시 보내지 않는다"


def test_the_lifetime_is_fixed_and_the_ttl_is_what_is_left(vapid_on):
    """만료 시각은 쌓을 때 고정 — 다시 보내기가 수명을 늘리지 않고, 푸시 서비스에는 남은 시간만 TTL 로 준다."""
    token = _signup()
    user_id = _user_id(token)
    sub = _on(token)
    base = _base()
    event = _event(user_id, "fill:lifetime", base)
    service = Service({sub["endpoint"]: [web_push._retry(1500), web_push._retry(1500), web_push._retry(1500)]})
    at = base
    for _ in range(5):
        push_alerts.dispatch(now_ms=at, send=service)
        at += 1_500_000
    ttls = [item["ttl"] for item in service.log]
    assert ttls == [3600, 2100, 600], "다시 보낼 때마다 남은 시간만(35분 · 10분)"
    assert _delivery(event, sub).state == "expired", "네 번째 시각이 만료 뒤라 보내지 않고 끝낸다"
    assert _event_row(event).result == "expired"


def test_retry_after_from_the_push_service_is_read(vapid_on):
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


# --- 시험 알림 · 거절 반복 -----------------------------------------------------------------------
def test_the_test_push_goes_the_same_way_and_tells_results_apart(vapid_on):
    token = _signup()
    user_id = _user_id(token)
    assert client.post("/api/me/push/test", headers=_auth(token)).status_code == 404
    sub = _on(token)
    base = _base()
    service = Service()
    assert push_alerts.send_test(user_id, now_ms=base, send=service) == {"sent": 1, "devices": 1, "retry": 0}
    assert service.log[0]["payload"]["url"] == "/agents" and service.log[0]["ttl"] == push_alerts.TEST_TTL_MS // 1000
    with pytest.raises(Exception):
        push_alerts.send_test(user_id, now_ms=base + 1_000, send=service)
    assert push_alerts.send_test(user_id, now_ms=base + 20_000, send=lambda *a, **k: web_push.RETRY) == {
        "sent": 0, "devices": 1, "retry": 1}
    assert push_alerts.send_test(user_id, now_ms=base + 200_000, send=lambda *a, **k: web_push.GONE) == {
        "sent": 0, "devices": 1, "retry": 0}
    assert _status(token, sub)["subscribed"] is False, "끝난 구독(410)은 지운다 — 화면은 다시 읽어 '꺼짐'으로"


def test_repeated_rejections_drop_the_device(vapid_on):
    token = _signup()
    user_id = _user_id(token)
    sub = _on(token)
    base = _base()
    events = [_event(user_id, f"fill:reject-{index}", base) for index in range(push_alerts.FAIL_LIMIT + 2)]
    push_alerts.dispatch(now_ms=base, send=lambda *a, **k: web_push.FAILED)
    assert _status(token, sub)["subscribed"] is False, "연달아 거절되면 지운다"
    assert {_delivery(event, sub).state for event in events} <= {"failed", "cancelled"}


# --- 업그레이드 · 탈퇴 ---------------------------------------------------------------------------
def test_an_older_schema_is_upgraded_on_boot(tmp_path, monkeypatch, vapid_on):
    """이전 표(구독에 세대 · 대기 없음, 이벤트에 만료 없음)가 있는 DB — 기동 보정이 칸을 더하고 기기별 전송 표를 만든다."""
    from sqlalchemy import create_engine, inspect
    from app import db as db_mod
    engine = create_engine(f"sqlite:///{tmp_path / 'older-push.db'}")
    monkeypatch.setattr(db_mod, "_engine", engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE pushoutbox (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            dedupe_key VARCHAR NOT NULL, payload_json VARCHAR NOT NULL DEFAULT '', created_ms BIGINT NOT NULL DEFAULT 0,
            sent_ms BIGINT NOT NULL DEFAULT 0, lease_ms BIGINT NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            done_json VARCHAR NOT NULL DEFAULT '')""")
        conn.exec_driver_sql("""CREATE TABLE pushsubscription (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            endpoint VARCHAR NOT NULL, p256dh VARCHAR NOT NULL, auth VARCHAR NOT NULL, platform VARCHAR NOT NULL DEFAULT '',
            created_ms BIGINT NOT NULL DEFAULT 0, updated_ms BIGINT NOT NULL DEFAULT 0, last_ok_ms BIGINT NOT NULL DEFAULT 0,
            fail_count INTEGER NOT NULL DEFAULT 0)""")
        conn.exec_driver_sql("CREATE UNIQUE INDEX ux_pushoutbox_user_key ON pushoutbox (user_id, dedupe_key)")
        conn.exec_driver_sql("CREATE UNIQUE INDEX ux_pushsubscription_endpoint ON pushsubscription (endpoint)")
        kept = device()
        conn.exec_driver_sql("INSERT INTO pushsubscription (id, user_id, endpoint, p256dh, auth) VALUES (1, 41, ?, ?, ?)",
                             (kept["endpoint"], kept["keys"]["p256dh"], kept["keys"]["auth"]))
    try:
        db_mod.init_db()
        db_mod.init_db()
        assert {"version", "next_send_ms"} <= {col["name"] for col in inspect(engine).get_columns("pushsubscription")}
        assert "expires_ms" in {col["name"] for col in inspect(engine).get_columns("pushoutbox")}
        assert "pushdelivery" in inspect(engine).get_table_names()
        assert push_alerts.status(41, kept["endpoint"]) == {"subscribed": True, "version": 0}, "켜 둔 기기는 그대로"
        base = _base()
        _event(41, "fill:after-upgrade", base)
        service = Service()
        push_alerts.dispatch(now_ms=base, send=service)
        assert len(service.sent_to(kept)) == 1, "업그레이드 뒤에도 보낸다"
    finally:
        engine.dispose()
    # 운영(Postgres) 기동 보정도 같은 칸을 더한다 — 마이그레이션과 같은 정의.
    from tests.test_db_startup_migrations import current_schema
    state = current_schema()
    for table, column in (("pushsubscription", "version"), ("pushsubscription", "next_send_ms"),
                          ("pushoutbox", "expires_ms"), ("pushoutbox", "result")):
        state["columns"].pop((table, column))
    statements = db_mod._pg_migration_statements(state)
    assert [s for s in statements if "push" in s] == [
        "ALTER TABLE pushoutbox ADD COLUMN IF NOT EXISTS expires_ms BIGINT NOT NULL DEFAULT 0",
        "ALTER TABLE pushoutbox ADD COLUMN IF NOT EXISTS result VARCHAR NOT NULL DEFAULT ''",
        "ALTER TABLE pushsubscription ADD COLUMN IF NOT EXISTS next_send_ms BIGINT NOT NULL DEFAULT 0",
        "ALTER TABLE pushsubscription ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0",
    ]


def test_leaving_the_service_removes_devices_and_history(vapid_on):
    token = _signup()
    user_id = _user_id(token)
    _on(token)
    _event(user_id, "fill:leaving", _base())
    r = client.request("DELETE", "/api/me/account", headers=_auth(token),
                       json={"confirmation": "탈퇴", "password": "password123"})
    assert r.status_code == 200, r.text
    with get_session() as db:
        for model in (PushSubscription, PushOutbox, PushDelivery):
            assert db.exec(select(model).where(model.user_id == user_id)).first() is None, model.__name__


# --- 한꺼번에: 계정 전환 · 두 바퀴 · 429 · 죽음 · 시험 알림을 섞어도 규칙이 지켜지는지 -------------------------
@pytest.mark.parametrize("seed", [3, 11, 29])
def test_rules_hold_when_everything_happens_at_once(vapid_on, send_clock, monkeypatch, seed):
    """무작위로 섞은 60 걸음 뒤 보낸 기록 전체로 확인한다.
    1. 기기 하나의 실패가 다른 기기의 시도를 쓰지 않는다(전송마다 시도 ≤ MAX_ATTEMPTS).
    2. 끝난 전송은 되살아나지 않고, 계정이 바뀐 기기에는 앞 계정의 알림이 가지 않는다.
    3. 모든 발송이 엔드포인트 대기 · 만료 시각을 지키고 TTL 은 남은 시간이다.
    5. 시간이 흐르면 모든 전송 · 이벤트가 끝난다(죽은 바퀴가 남긴 것도)."""
    monkeypatch.setattr(push_alerts, "SEND_THREADS", 1)  # 같은 seed 면 같은 순서
    rng = random.Random(seed)
    users = [_user_id(token) for token in (_signup(), _signup())]
    t0 = _base()
    now = lambda: t0 + int(send_clock["s"] * 1000)  # noqa: E731 — 바퀴 안의 경과까지 한 시계로
    owners: dict[str, tuple] = {}     # 엔드포인트 → (계정, 세대)
    subs: dict[str, dict] = {}
    moved: dict[str, list] = {}       # 엔드포인트 → [(시각, 새 계정)]
    holds: dict[str, int] = {}        # 엔드포인트 → 푸시 서비스가 기다리라고 한 시각
    events: dict[int, dict] = {}      # 이벤트 → {user, expires, recipients}
    log = []
    extra = {"resends": 0}
    terminal: dict[int, str] = {}

    def register(user, sub=None):
        sub = sub or device()
        result = push_alerts.subscribe(user, sub["endpoint"], sub["keys"]["p256dh"], sub["keys"]["auth"], now_ms=now())
        if sub["endpoint"] in owners and owners[sub["endpoint"]][0] != user:
            moved.setdefault(sub["endpoint"], []).append((now(), user))
        owners[sub["endpoint"]] = (user, result["version"])
        subs[sub["endpoint"]] = sub

    def answer():
        roll = rng.random()
        if roll < 0.6:
            return web_push.SENT
        if roll < 0.75:
            return web_push.RETRY
        if roll < 0.87:
            return web_push._retry(rng.choice([30, 120, 900, 2400]))
        if roll < 0.94:
            return web_push.FAILED
        return web_push.GONE

    def request(subscription, data, ttl):
        """보낸 순간을 적고 푸시 서비스의 답을 고른다."""
        event = json.loads(data)["event"]
        endpoint = subscription["endpoint"]
        log.append({"endpoint": endpoint, "event": event, "ttl": ttl, "at": now(), "hold": holds.get(endpoint, 0)})
        return answer()

    def receive(subscription, outcome):
        """답이 앱에 닿은 순간 — Retry-After 는 받은 때부터 센다(RFC 8030 §8.4)."""
        endpoint = subscription["endpoint"]
        if outcome == web_push.RETRY and getattr(outcome, "retry_after_s", 0):
            holds[endpoint] = max(holds.get(endpoint, 0), now() + outcome.retry_after_s * 1000)
        if outcome == web_push.GONE:
            owners.pop(endpoint, None)
        return outcome

    def service(subscription, data, *, ttl, urgency, **kwargs):
        return receive(subscription, request(subscription, data, ttl))

    for user in users:
        register(user)
        register(user)
    for step in range(60):
        send_clock["s"] += rng.choice([5, 30, 90, 240, 600])
        action = rng.random()
        if action < 0.3:
            user = rng.choice(users)
            recipients = {e for e, (u, _) in owners.items() if u == user}
            facts = {"id": 77, "user_id": user, "base": "BTC", "status": "running"}
            say = {"key": f"fill:sim{seed}-{step}", "kind": "fill", "hl": "샀어요!", "at_ms": now(), "nums": "1"}
            with get_session() as db:
                queued = push_alerts.enqueue_says(db, facts, [say], now_ms=now())
                db.commit()
                event = db.exec(select(PushOutbox.id).where(PushOutbox.user_id == user, PushOutbox.dedupe_key ==
                                                            f"s77:fill:sim{seed}-{step}")).first()
            assert (event is not None) == bool(queued) == bool(recipients), "기기가 없으면 쌓지 않는다"
            if event is not None:
                events[event] = {"user": user, "created": now(), "expires": now() + push_alerts.EVENT_TTL_MS,
                                 "recipients": recipients}
        elif action < 0.6:
            push_alerts.dispatch(now_ms=now(), send=service)
        elif action < 0.68:  # 두 바퀴 — 옛 바퀴가 첫 전송 중에 멈춘 사이 맡은 시간이 지나 새 바퀴가 돈다
            fired = []

            def stalled(subscription, data, *, ttl, **kwargs):
                outcome = request(subscription, data, ttl)
                if not fired:  # 첫 답이 늦게 닿는다(바퀴가 멈춤) — 그사이 맡은 시간이 지나 새 바퀴가 돈다
                    fired.append(1)
                    send_clock["s"] += push_alerts.LEASE_MS / 1000 + 1
                    push_alerts.dispatch(now_ms=now(), send=service)
                    extra["resends"] += 1
                return receive(subscription, outcome)
            push_alerts.dispatch(now_ms=now(), send=stalled)
        elif action < 0.74:  # 바퀴가 보내다 죽는다
            class Crash(BaseException):
                pass

            def dying(*args, **kwargs):
                raise Crash()
            try:
                push_alerts.dispatch(now_ms=now(), send=dying)
            except Crash:
                extra["resends"] += 1
        elif action < 0.84:  # 같은 기기를 다른 계정이 가져간다
            if owners:
                endpoint = rng.choice(sorted(owners))
                register(rng.choice(users), subs[endpoint])
        elif action < 0.92:
            register(rng.choice(users))
        else:
            user = rng.choice(users)
            recipients = {e for e, (u, _) in owners.items() if u == user}
            created = now()
            try:
                result = push_alerts.send_test(user, now_ms=created, send=service)
            except HTTPException:
                continue
            with get_session() as db:
                test_event = db.exec(select(PushOutbox.id).where(PushOutbox.user_id == user)
                                     .order_by(PushOutbox.id.desc())).first()
            events[test_event] = {"user": user, "created": created, "expires": created + push_alerts.TEST_TTL_MS,
                                  "recipients": recipients}
            assert result["devices"] == len(recipients) >= result["sent"]
        with get_session() as db:
            for delivery_id, state in db.exec(select(PushDelivery.id, PushDelivery.state).where(
                    PushDelivery.user_id.in_(users))).all():
                if delivery_id in terminal:
                    assert state == terminal[delivery_id], f"끝난 전송이 되살아났다: {delivery_id} {terminal[delivery_id]}→{state}"
                elif state not in push_alerts.LIVE:
                    terminal[delivery_id] = state

    send_clock["s"] += 3 * 3600  # 모든 수명이 지나도록
    push_alerts.dispatch(now_ms=now(), send=service)
    with get_session() as db:
        left = db.exec(select(PushDelivery.state).where(PushDelivery.user_id.in_(users),
                                                        PushDelivery.state.in_(push_alerts.LIVE))).all()
        assert left == [], "시간이 흐르면 모든 전송이 끝난다"
        unfinished = db.exec(select(PushOutbox.id).where(PushOutbox.user_id.in_(users), PushOutbox.sent_ms == 0)).all()
        assert unfinished == [], "모든 이벤트에 끝난 까닭이 적힌다"
        attempts = db.exec(select(func.max(PushDelivery.attempts)).where(PushDelivery.user_id.in_(users))).one()
    assert (attempts or 0) <= push_alerts.MAX_ATTEMPTS, "규칙 1 — 전송마다 제 시도만 센다"
    sends = {}
    for item in log:
        info = events.get(item["event"])
        assert info is not None, item
        assert item["at"] >= item["hold"], f"규칙 3 — 기다리라고 한 시각 전에 보냈다: {item}"
        assert item["at"] < info["expires"] and item["ttl"] == (info["expires"] - item["at"]) // 1000 >= 1, \
            f"규칙 3 — 만료 뒤 · 남은 시간보다 긴 TTL: {item}"
        assert item["endpoint"] in info["recipients"], f"규칙 4 — 쌓을 때 정한 기기만: {item}"
        for moved_at, _ in moved.get(item["endpoint"], []):
            assert not (info["created"] < moved_at <= item["at"]), \
                f"규칙 2 — 쌓은 뒤 다른 계정으로 넘어간 기기에 그 알림이 갔다: {item}"
        key = (item["event"], item["endpoint"])
        sends[key] = sends.get(key, 0) + 1
    assert max(sends.values(), default=0) <= push_alerts.MAX_ATTEMPTS + extra["resends"]
