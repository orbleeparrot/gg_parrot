"""웹 '매크로 만들기 가이드'가 실행기 화면을 따라 보여 주도록 지금 단계만 알린다.

웹 [실행기 열기] 로 들어온 실행기만 보낸다(그 연결의 launch_id 와 회원 키가 있을 때).
보내는 것은 단계 이름 · 키 칸이 찼는지(참/거짓) · 검사 세 줄의 상태뿐이다 —
거래소 키 · 시크릿 · IP 주소 · 오류 문장은 어떤 경로로도 실리지 않는다.
보내기 실패는 조용히 버린다. 실행기 동작은 이 보고에 기대지 않는다.
"""
from __future__ import annotations

import threading
import time

STAGES = (
    "ready",        # 실행기 첫 화면(매크로 실린 상태)
    "closed",       # 실행기 창을 닫았다 — 웹은 처음의 [실행기 열기] 화면으로 돌아간다(v16+)
    "ask",          # '키를 발급 받은 적 있나요?'
    "ip",           # 1 · 허용 IP 등록
    "perm",         # 2 · 권한 선택 · 키 발급
    "keys",         # 3 · 키 복사
    "existing",     # 이미 발급 받은 키 넣기
    "manual",       # 직접 입력
    "checking",     # 연결 확인 중
    "result_ok",    # 연결 완료
    "result_fail",  # 확인 필요
    "started",      # 매크로 시작(세션 생성)
)
PATHS = ("", "new", "existing")
CHECK_STATES = ("ok", "bad", "wait", "na")
PROBLEMS = ("", "ip", "auth", "order", "order_size", "network", "other")
EXCHANGES = ("binance", "upbit", "bithumb")
ENVS = ("", "testnet", "demo", "live")  # 바이낸스 도우미가 안내하는 사이트(v19+). 국내는 "" — 보내지 않는다.
PROGRESS_PATH = "/api/runner/launch-tickets/{launch_id}/progress"
# 웹 연결 하나에 대해 보고하는 최대 시간. 그 뒤로는 가이드 화면도 이미 닫혔다고 본다.
REPORT_WINDOW_SECONDS = 2 * 60 * 60


def progress_payload(stage: str, exchange: str, *, path: str = "", slots=None, checks=None,
                     problem: str = "", connected: bool = False, env: str = "") -> dict:
    """서버가 받는 모양 그대로 만든다. 모르는 값은 보내지 않고 ValueError — 호출 쪽 버그를 숨기지 않는다."""
    if stage not in STAGES or exchange not in EXCHANGES or path not in PATHS or problem not in PROBLEMS or env not in ENVS:
        raise ValueError("unknown progress field")
    if env and exchange != "binance":
        raise ValueError("env is binance-only")
    slots = slots or {}
    checks = checks or {}
    payload = {
        "stage": stage,
        "exchange": exchange,
        "path": path,
        "slots": {"access": bool(slots.get("access")), "secret": bool(slots.get("secret"))},
        "checks": {},
        "problem": problem,
        "connected": bool(connected),
    }
    if env:
        # 빈 값은 싣지 않는다 — env 를 모르는 옛 서버(extra=forbid)에도 국내 보고가 그대로 통한다.
        payload["env"] = env
    for row in ("auth", "ip", "order"):
        value = checks.get(row, "wait")
        if value not in CHECK_STATES:
            raise ValueError("unknown check state")
        payload["checks"][row] = value
    return payload


class SetupProgressReporter:
    """가장 최근 상태 하나만 보낸다. 같은 상태는 다시 보내지 않고, 밀린 상태는 최신 것으로 덮는다."""

    def __init__(self, base: str, runner_key: str, launch_id: int, *, post=None, clock=time.monotonic):
        if not isinstance(launch_id, int) or isinstance(launch_id, bool) or launch_id <= 0:
            raise ValueError("launch_id must be a positive integer")
        self._url = base.rstrip("/") + PROGRESS_PATH.format(launch_id=launch_id)
        self._headers = {"X-Runner-Key": runner_key}
        self._post = post
        self._clock = clock
        self._deadline = clock() + REPORT_WINDOW_SECONDS
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending = None
        self._last_sent = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="ggparrot-setup-progress")
        self._thread.start()

    def report(self, payload: dict) -> None:
        with self._lock:
            if self._closed or self._clock() > self._deadline:
                return
            if payload == self._last_sent or payload == self._pending:
                return
            self._pending = dict(payload)
        self._wake.set()

    def finish(self, payload: dict, wait: float = 1.5) -> None:
        """마지막 상태(창 닫힘)를 보내고 닫는다. 창이 닫히기 전 잠깐(wait 초)만 기다린다 — 못 보내도 닫힌다."""
        with self._lock:
            if not self._closed and self._clock() <= self._deadline:
                self._pending = dict(payload)
            self._closed = True
        self._wake.set()
        self._thread.join(wait)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._wake.set()

    def _send(self, payload: dict) -> bool:
        try:
            post = self._post
            if post is None:
                import requests
                post = requests.post
            response = post(self._url, json=payload, headers=self._headers, timeout=5)
            return 200 <= int(getattr(response, "status_code", 0)) < 300
        except Exception:
            # 네트워크 · 프록시 오류 문구에는 요청 정보가 섞일 수 있다 — 아무것도 남기지 않는다.
            return False

    def _run(self) -> None:
        while True:
            self._wake.wait()
            with self._lock:
                self._wake.clear()
                payload, self._pending = self._pending, None
                closed = self._closed
            if payload is not None and self._send(payload):
                with self._lock:
                    self._last_sent = payload
            if closed:
                with self._lock:
                    if self._pending is None:
                        return
