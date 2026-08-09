"""껄무새 매크로 실행기 (Macro Runner)

코딩을 모르는 회원도 더블클릭 한 번으로 매크로를 돌릴 수 있게 만든 GUI 실행기.
터미널·파이썬 설치가 필요 없는 단일 exe 로 배포한다(PyInstaller, 아래 빌드 안내 참고).

화면 구성(요청 사양)
  ① 매크로 파일 선택        — 빌더에서 내려받은 .ggm.json
  ② 바이낸스 실거래 여부     — 체크 시 메인넷(실제 자금), 해제 시 테스트넷(가짜 자금)
  ③ 바이낸스 API 키 / 시크릿 — 로컬 메모리에서만 사용(서버 전송·저장·로깅 안 함)
  ④ 껄무새 회원 키           — 마이페이지에서 발급. 이 키로만 서버에 상태를 올린다.

서버로 나가는 것: 회원 키 + 구동 상태(요약/현재가/포지션/손익)뿐.
              거래소 API 키/시크릿은 절대 서버로 보내지 않는다.

원격 종료: 마이페이지의 종료 버튼이 서버에 플래그를 세우면, 이 실행기가 다음
          하트비트에서 받아 (a) 매크로만 종료 또는 (b) 청산 후 종료 한다.

⚠️ 실거래(메인넷)는 실제 자금이 움직인다. 손익 책임은 사용자 본인에게 있으며,
   본 도구는 투자 조언이 아니다.

──────────────────────────────────────────────────────────────────────
빌드(단일 exe) — 개발자용
  pip install -r requirements.txt pyinstaller
  pyinstaller --onefile --noconsole --name ggparrot-runner macro_runner.py
  → dist/ggparrot-runner.exe

서버 주소는 환경변수 GGP_SERVER_BASE 로 바꿀 수 있다(기본: 배포 서버).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import requests
except ImportError:  # 사용자에게 친절히 안내
    requests = None

try:  # package import (tests) / direct script import (PyInstaller build)
    from .protocol import (
        PROTOCOL_CLAIM_PATH,
        PROTOCOL_SCHEME,
        ProtocolLaunchError,
        parse_protocol_args,
    )
except ImportError:
    from protocol import (
        PROTOCOL_CLAIM_PATH,
        PROTOCOL_SCHEME,
        ProtocolLaunchError,
        parse_protocol_args,
    )

# ==================================================================
#  설정
# ==================================================================
SERVER_BASE = os.environ.get("GGP_SERVER_BASE", "https://gg-parrot.onrender.com").rstrip("/")
MAX_ORDER_USDT = float(os.environ.get("MAX_ORDER_USDT", "100"))   # 1회 주문 상한(USDT)
ORDER_CAP_BASIS = os.environ.get("ORDER_CAP_BASIS", "notional").lower()  # notional | margin
DEFAULT_TP_PCT = 3.0
MAX_RETRIES = 3
POLL_SECONDS = 5.0

APP_TITLE = "껄무새 매크로 실행기"

_STABLE_EXE_PARTS = ("GGParrot", "ggparrot-runner.exe")


def _install_protocol_handler_for_current_user() -> bool:
    """Copy a frozen Windows build to a stable path and register its URI verb.

    Registration is per-user (HKCU), so it neither requires elevation nor
    changes another Windows account.  Every failure is contained: protocol
    convenience must never prevent the ordinary runner UI from opening.
    """

    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return True
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return False

    source = Path(sys.executable).resolve()
    stable = Path(local_app_data).joinpath(*_STABLE_EXE_PARTS)
    staged = stable.with_name(f".{stable.name}.new")
    try:
        stable.parent.mkdir(parents=True, exist_ok=True)
        same_path = os.path.normcase(str(source)) == os.path.normcase(str(stable.resolve()))
        if not same_path:
            # Stage then replace so a partial copy can never become the shell
            # handler.  An already-running old stable binary can make replace
            # fail on Windows; that is intentionally non-fatal.
            shutil.copy2(source, staged)
            os.replace(staged, stable)

        import winreg

        protocol_root = rf"Software\Classes\{PROTOCOL_SCHEME}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, protocol_root) as key:
            winreg.SetValueEx(
                key, None, 0, winreg.REG_SZ, "URL:GGParrot Runner Protocol"
            )
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, protocol_root + r"\DefaultIcon"
        ) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, f'"{stable}",0')
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, protocol_root + r"\shell\open\command"
        ) as key:
            command = f'"{stable}" --protocol "%1"'
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)
        return True
    except Exception:
        # Do not print paths or command-line values in a noconsole build.  The
        # caller shows only a generic, non-sensitive warning inside the app.
        return False
    finally:
        try:
            if staged.exists():
                staged.unlink()
        except OSError:
            pass


# ==================================================================
#  거래 엔진 (백엔드 realtrade 봇과 동일한 의미 — 현물/선물 실주문)
# ==================================================================
def _round_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    d = Decimal(str(step))
    return float((Decimal(str(qty)) / d).to_integral_value(rounding=ROUND_DOWN) * d)


def _parse_filters(info: dict) -> tuple[float, float]:
    step, min_notional = 0.0, 0.0
    for f in (info or {}).get("filters", []):
        if f["filterType"] in ("LOT_SIZE", "MARKET_LOT_SIZE") and not step:
            step = float(f["stepSize"])
        elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            min_notional = float(f.get("minNotional", f.get("notional", 0)) or 0)
    return step, min_notional


def _decide_market(side: str, leverage: int) -> str:
    return "futures" if (side == "short" or leverage > 1) else "spot"


def _base_asset(symbol: str) -> str:
    for q in ("USDT", "BUSD", "USDC", "FDUSD"):
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol


def _order_qty(price, step, min_notional, budget, leverage, market) -> tuple[float, float]:
    cap = min(budget, MAX_ORDER_USDT)
    if market == "futures" and ORDER_CAP_BASIS == "margin":
        notional = cap * leverage
    else:
        notional = cap
    qty = _round_step(notional / price, step)
    return qty, qty * price


def _strategy_targets(macro: dict) -> dict:
    p = macro.get("params", {})
    risk = macro.get("risk", {})
    rule = macro.get("rule_type", "A")
    tp = p.get("take_profit_pct") or p.get("take_profit") or p.get("tp")
    return {
        "rule": rule,
        "tp_pct": float(tp) if tp else None,
        "sl_pct": float(risk["stop_loss_pct"]) if risk.get("stop_loss_pct") else None,
        "buy_price": float(p["buy_price"]) if p.get("buy_price") else None,
        "sell_price": float(p["sell_price"]) if p.get("sell_price") else None,
        "invest_ratio": float(risk.get("invest_ratio", 1.0)),
        "capital": float(p.get("initial_capital", 0) or 0),
        "risk": risk,
    }


class RiskGuard:
    """공통 리스크 3종: 일일 최대손실 / 최대 보유시간 / 재진입 금지."""

    def __init__(self, risk: dict, base_capital: float) -> None:
        self.daily_max_loss = risk.get("daily_max_loss_pct")
        self.max_holding_hours = risk.get("max_holding_hours")
        self.cooldown_minutes = float(risk.get("cooldown_minutes") or 0)
        self.base = base_capital if base_capital > 0 else MAX_ORDER_USDT
        self._day = None
        self._day_pnl = 0.0
        self._halted_day = None
        self._entry_time = None
        self._cooldown_until = 0.0

    def describe(self) -> str:
        bits = []
        if self.daily_max_loss:
            bits.append(f"일일최대손실 {self.daily_max_loss}%")
        if self.max_holding_hours:
            bits.append(f"최대보유 {self.max_holding_hours}h")
        if self.cooldown_minutes:
            bits.append(f"재진입금지 {self.cooldown_minutes}분")
        return " · ".join(bits) if bits else "설정 없음"

    def roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._day_pnl = 0.0
            if self._halted_day and self._halted_day != today:
                self._halted_day = None

    def _daily_loss_pct(self, unrealized: float = 0.0) -> float:
        return (self._day_pnl + unrealized) / self.base * 100.0

    def entry_blocked(self):
        if self._halted_day and self._halted_day == self._day:
            return True, f"일일 최대손실({self.daily_max_loss}%) 도달 → 오늘은 신규 진입 중단"
        remain = self._cooldown_until - time.time()
        if remain > 0:
            return True, f"손절 후 재진입 금지 {remain/60:.1f}분 남음"
        return False, ""

    def force_close(self, unrealized: float):
        if self.max_holding_hours and self._entry_time is not None:
            held_h = (time.time() - self._entry_time) / 3600.0
            if held_h >= float(self.max_holding_hours):
                return True, f"최대 보유시간 {self.max_holding_hours}h 초과({held_h:.1f}h)"
        if self.daily_max_loss:
            dd = self._daily_loss_pct(unrealized)
            if dd <= -float(self.daily_max_loss):
                return True, f"일일 최대손실 도달({dd:.2f}% ≤ -{self.daily_max_loss}%)"
        return False, ""

    def on_entry(self) -> None:
        self._entry_time = time.time()

    def on_exit(self, pnl_usdt: float, was_stop: bool) -> None:
        self._day_pnl += pnl_usdt
        self._entry_time = None
        if was_stop and self.cooldown_minutes > 0:
            self._cooldown_until = time.time() + self.cooldown_minutes * 60.0
        if self.daily_max_loss and self._daily_loss_pct() <= -float(self.daily_max_loss):
            self._halted_day = self._day


def _was_stop_exit(t, price, entry, side) -> bool:
    if t["sl_pct"] is None or entry <= 0:
        return False
    if side == "long":
        return price <= entry * (1 - t["sl_pct"] / 100.0)
    return price >= entry * (1 + t["sl_pct"] / 100.0)


def _should_enter(t, price, side) -> bool:
    if t["rule"] == "B":
        if side == "long" and t["buy_price"]:
            return price <= t["buy_price"]
        if side == "short" and t["sell_price"]:
            return price >= t["sell_price"]
    return True


def _should_exit(t, price, entry, side) -> bool:
    tp = t["tp_pct"] if t["tp_pct"] is not None else (None if t["rule"] == "B" else DEFAULT_TP_PCT)
    if side == "long":
        if t["rule"] == "B" and t["sell_price"] and price >= t["sell_price"]:
            return True
        if tp is not None and price >= entry * (1 + tp / 100.0):
            return True
        if t["sl_pct"] is not None and price <= entry * (1 - t["sl_pct"] / 100.0):
            return True
    else:
        if t["rule"] == "B" and t["buy_price"] and price <= t["buy_price"]:
            return True
        if tp is not None and price <= entry * (1 - tp / 100.0):
            return True
        if t["sl_pct"] is not None and price >= entry * (1 + t["sl_pct"] / 100.0):
            return True
    return False


def _pnl_usdt(qty, entry, price, side) -> float:
    return qty * (price - entry) if side == "long" else qty * (entry - price)


def _pnl_pct(entry, price, side) -> float:
    if entry <= 0:
        return 0.0
    move = (price - entry) / entry * 100.0
    return move if side == "long" else -move


# ==================================================================
#  서버 연동 클라이언트 (회원 키로 인증; API 키는 절대 안 보냄)
# ==================================================================
class ServerClient:
    def __init__(self, runner_key: str) -> None:
        self.base = SERVER_BASE
        self.key = runner_key.strip()
        self.session_id = None
        self._headers = {"X-Runner-Key": self.key, "Content-Type": "application/json"}

    def start(self, payload: dict) -> dict:
        r = requests.post(f"{self.base}/api/runner/start", json=payload,
                          headers=self._headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        self.session_id = data.get("session_id")
        return data

    def heartbeat(self, snapshot: dict) -> str:
        """상태를 올리고 종료명령(continue|stop_only|close_and_stop)을 받는다.
        네트워크 오류 시엔 'continue' 로 간주(로컬 매매는 계속, 로컬 종료는 항상 가능)."""
        if self.session_id is None:
            return "continue"
        body = dict(snapshot)
        body["session_id"] = self.session_id
        try:
            r = requests.post(f"{self.base}/api/runner/heartbeat", json=body,
                              headers=self._headers, timeout=10)
            r.raise_for_status()
            return r.json().get("action", "continue")
        except Exception:
            return "continue"

    def stopped(self, status: str = "stopped", note: str = "") -> None:
        if self.session_id is None:
            return
        try:
            requests.post(
                f"{self.base}/api/runner/stopped",
                json={"session_id": self.session_id, "status": status, "note": note},
                headers=self._headers, timeout=10,
            )
        except Exception:
            pass


# ==================================================================
#  봇 실행 스레드 (GUI 를 막지 않도록 별도 스레드에서 구동)
# ==================================================================
class BotThread(threading.Thread):
    """한 매크로를 실제로 구동하는 워커. GUI 콜백으로 로그/상태를 전달한다.

    종료 경로는 두 가지:
      * 로컬(GUI 종료 버튼)  → set_command()
      * 원격(마이페이지)      → heartbeat 응답 action
    두 경우 모두 stop_only(포지션 유지) / close_and_stop(청산 후) 을 지원한다.
    """

    def __init__(self, macro: dict, api_key: str, api_secret: str, testnet: bool,
                 server: ServerClient, on_log, on_status, on_finish) -> None:
        super().__init__(daemon=True)
        self.macro = macro
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.server = server
        self.on_log = on_log
        self.on_status = on_status
        self.on_finish = on_finish

        self._command = None  # None | "stop_only" | "close_and_stop"
        self._lock = threading.Lock()
        self._wake = threading.Event()  # 종료 명령 시 sleep 을 즉시 깨움

        # 매매 상태
        self.symbol = str(macro.get("symbol", "BTCUSDT")).upper()
        self.side = str(macro.get("position_side", "long")).lower()
        self.leverage = max(1, int(macro.get("leverage", 1) or 1))
        self.market = _decide_market(self.side, self.leverage)
        self.client = None
        self.step = 0.0
        self.in_position = False
        self.entry_price = 0.0
        self.held_qty = 0.0
        self.realized = 0.0

    # --- GUI → 스레드 명령 --------------------------------------
    def set_command(self, mode: str) -> None:
        with self._lock:
            if self._command is None:
                self._command = mode
        self._wake.set()

    def _get_command(self):
        with self._lock:
            return self._command

    def log(self, msg: str) -> None:
        self.on_log(msg)

    def _sleep(self, seconds: float) -> None:
        # 종료 명령이 오면 즉시 깨어나도록 이벤트 기반 대기.
        self._wake.wait(timeout=seconds)

    # --- 시장별 어댑터 (현물/선물 공통 루프) ---------------------
    def _connect(self) -> bool:
        try:
            from binance.client import Client
        except ImportError:
            self.log("python-binance 가 없어요. requirements 설치 후 다시 실행하세요.")
            return False
        try:
            self.client = Client(self.api_key, self.api_secret, testnet=self.testnet)
            if self.market == "futures":
                bal = self._fut_usdt_balance()
                self.log(f"연결 성공 · 선물 USDT 증거금: {bal if bal is not None else '조회 실패'}")
            else:
                acc = self.client.get_account()
                usdt = next((b for b in acc["balances"] if b["asset"] == "USDT"), None)
                self.log(f"연결 성공 · 현물 USDT 잔고: {usdt['free'] if usdt else '조회 실패'}")
        except Exception as exc:
            self.log(f"연결/인증 실패: {exc}")
            if "-2015" in str(exc) or "Invalid API-key" in str(exc):
                where = ("선물 testnet(binancefuture.com)" if self.market == "futures"
                         else "현물 testnet(binance.vision)") if self.testnet else "메인넷"
                self.log(f"  → 이 매크로는 {self.market} 시장이에요. {where} 키인지, IP 제한/권한을 확인하세요.")
            return False
        return True

    def _fut_symbol_info(self):
        try:
            for s in self.client.futures_exchange_info().get("symbols", []):
                if s.get("symbol") == self.symbol:
                    return s
        except Exception as exc:
            self.log(f"선물 심볼정보 조회 실패: {exc}")
        return None

    def _fut_usdt_balance(self):
        try:
            for b in self.client.futures_account_balance():
                if b.get("asset") == "USDT":
                    return float(b.get("balance", 0))
        except Exception:
            return None
        return 0.0

    def _price(self) -> float:
        if self.market == "futures":
            return float(self.client.futures_symbol_ticker(symbol=self.symbol)["price"])
        return float(self.client.get_symbol_ticker(symbol=self.symbol)["price"])

    def _place(self, side_word: str, qty: float, reduce_only: bool = False) -> bool:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if self.market == "futures":
                    kwargs = dict(symbol=self.symbol, side=side_word, type="MARKET", quantity=qty)
                    if reduce_only:
                        kwargs["reduceOnly"] = "true"
                    order = self.client.futures_create_order(**kwargs)
                else:
                    order = self.client.create_order(symbol=self.symbol, side=side_word,
                                                     type="MARKET", quantity=qty)
                self.log(f"  ✓ {side_word}{' (청산)' if reduce_only else ''} 체결: "
                         f"id={order.get('orderId')} 수량={qty} 상태={order.get('status')}")
                return True
            except Exception as exc:
                self.log(f"  ✗ {side_word} 주문 실패({attempt}/{MAX_RETRIES}): {exc}")
                if attempt < MAX_RETRIES:
                    time.sleep(1.0)
        return False

    def _prepare(self) -> bool:
        """시장별 심볼정보/레버리지 세팅. 성공 시 True."""
        if self.market == "futures":
            info = self._fut_symbol_info()
            if not info:
                self.log(f"[오류] '{self.symbol}' 은 (테스트넷) 선물에 없어요. 심볼을 바꾸세요.")
                return False
            self.step, _ = _parse_filters(info)
            try:
                self.client.futures_change_margin_type(symbol=self.symbol, marginType="ISOLATED")
            except Exception:
                pass
            try:
                self.client.futures_change_leverage(symbol=self.symbol, leverage=self.leverage)
            except Exception as exc:
                self.log(f"⚠ 레버리지 {self.leverage}배 설정 실패({exc}). 계정 기본값으로 진행.")
        else:
            if self.side == "short":
                self.log("현물은 숏을 지원하지 않아요. 선물 매크로를 쓰세요.")
                return False
            info = self.client.get_symbol_info(self.symbol)
            if not info:
                self.log(f"[오류] '{self.symbol}' 은 (테스트넷) 현물에 없어요. 심볼을 바꾸세요.")
                return False
            self.step, _ = _parse_filters(info)
        return True

    def _close_position(self) -> bool:
        """보유 포지션을 시장가로 정리. 성공 시 True."""
        if not self.in_position or self.held_qty <= 0:
            return True
        close_word = "SELL" if self.side == "long" else "BUY"
        reduce = self.market == "futures"
        ok = self._place(close_word, _round_step(self.held_qty, self.step), reduce_only=reduce)
        if ok:
            self.in_position = False
        return ok

    # --- 메인 루프 ----------------------------------------------
    def run(self) -> None:
        status = "stopped"
        note = ""
        try:
            if not self._connect() or not self._prepare():
                status, note = "error", "연결/심볼 준비 실패 — 로그 확인"
                return
            t = _strategy_targets(self.macro)
            guard = RiskGuard(t["risk"], t["capital"] * t["invest_ratio"] if t["capital"] else 0.0)
            self.log(f"공통 리스크: {guard.describe()} · 주문 상한 {MAX_ORDER_USDT} USDT · "
                     f"{POLL_SECONDS:.0f}초마다 평가")

            while True:
                # 1) 종료 명령 확인 (로컬 또는 직전 원격)
                cmd = self._get_command()
                if cmd:
                    note = self._finish_position(cmd)
                    status = "stopped"
                    return

                # 2) 시세
                try:
                    price = self._price()
                except Exception as exc:
                    self.log(f"  일시 오류(시세): {exc} — {POLL_SECONDS:.0f}초 후 재시도")
                    self._sleep(POLL_SECONDS)
                    continue
                guard.roll_day()

                # 3) 진입/청산 로직
                if not self.in_position:
                    blocked, why = guard.entry_blocked()
                    if blocked:
                        self.log(f"  ⏸ 진입 보류: {why}")
                    elif _should_enter(t, price, self.side):
                        budget = t["capital"] * t["invest_ratio"] if t["capital"] else MAX_ORDER_USDT
                        qty, notional = _order_qty(price, self.step, 0, budget, self.leverage, self.market)
                        if qty > 0:
                            open_word = "BUY" if self.side == "long" else "SELL"
                            self.log(f"[진입] {price} → {open_word} {qty} {self.symbol}")
                            if self._place(open_word, qty):
                                self.in_position, self.entry_price, self.held_qty = True, price, qty
                                guard.on_entry()
                else:
                    unreal = _pnl_usdt(self.held_qty, self.entry_price, price, self.side)
                    forced, why = guard.force_close(unreal)
                    if forced or _should_exit(t, price, self.entry_price, self.side):
                        tag = f"[강제청산: {why}]" if forced else "[청산 신호]"
                        self.log(f"{tag} {price} (진입 {self.entry_price})")
                        if self._close_position():
                            pnl = _pnl_usdt(self.held_qty, self.entry_price, price, self.side)
                            self.realized += pnl
                            self.log(f"  손익 {_pnl_pct(self.entry_price, price, self.side):+.2f}% "
                                     f"({pnl:+.2f} USDT) · 누적 {self.realized:+.2f} USDT")
                            was_stop = not forced and _was_stop_exit(t, price, self.entry_price, self.side)
                            self.entry_price, self.held_qty = 0.0, 0.0
                            guard.on_exit(pnl, was_stop)

                # 4) 상태 스냅샷 + 하트비트
                unreal_pct = _pnl_pct(self.entry_price, price, self.side) if self.in_position else 0.0
                snap = {
                    "in_position": self.in_position,
                    "last_price": price,
                    "entry_price": self.entry_price,
                    "position_qty": self.held_qty,
                    "realized_pnl": self.realized,
                    "unrealized_pct": unreal_pct,
                }
                self.on_status(snap)
                action = self.server.heartbeat(snap)
                if action in ("stop_only", "close_and_stop"):
                    self.log(f"원격 종료 명령 수신: {action}")
                    self.set_command(action)

                # 5) 대기(종료 명령 시 즉시 깨어남)
                self._sleep(POLL_SECONDS)
        except Exception as exc:
            status, note = "error", f"예기치 못한 오류: {exc}"
            self.log(note)
        finally:
            self.server.stopped(status, note)
            self.on_finish(status, note)

    def _finish_position(self, mode: str) -> str:
        """종료 시 포지션 처리. 반환값은 서버/화면에 남길 note."""
        if mode == "close_and_stop":
            if self.in_position:
                self.log("청산 후 종료 요청 — 보유 포지션을 정리합니다.")
                if self._close_position():
                    return "청산 완료 후 종료"
                self.log("⚠ 청산 주문이 실패했어요. 거래소에서 직접 확인하세요.")
                return "청산 실패 — 포지션 남음"
            return "포지션 없이 종료"
        # stop_only
        if self.in_position:
            self.log("매크로만 종료 — 열린 포지션은 그대로 둡니다. 거래소에서 직접 관리하세요.")
            return "매크로만 종료 — 포지션 유지"
        return "종료"


# ==================================================================
#  GUI
# ==================================================================
class RunnerApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        protocol_ticket: str | None = None,
        register_protocol: bool = False,
        startup_warning: str = "",
    ) -> None:
        self.root = root
        self.bot: BotThread | None = None
        self.macro: dict | None = None
        self.macro_path = tk.StringVar(value="")
        self.live = tk.BooleanVar(value=False)   # 실거래(메인넷) 여부
        self.api_key = tk.StringVar(value="")
        self.api_secret = tk.StringVar(value="")
        self.member_key = tk.StringVar(value=os.environ.get("GGP_MEMBER_KEY", ""))
        self._build()
        if startup_warning:
            self._log(startup_warning)
        if register_protocol:
            self.root.after(0, self._begin_protocol_registration)
        if protocol_ticket:
            # Let Tk render its first frame before any launch work begins.  The
            # actual HTTPS claim runs on a worker thread below.
            self.root.after(0, self._begin_protocol_claim, protocol_ticket)

    # --- 화면 구성 ----------------------------------------------
    def _build(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("640x680")
        pad = dict(padx=12, pady=6)

        head = ttk.Label(self.root, text="🦜 껄무새 매크로 실행기", font=("맑은 고딕", 15, "bold"))
        head.pack(anchor="w", **pad)

        # ① 매크로 파일
        f1 = ttk.LabelFrame(self.root, text="① 매크로 파일 (.ggm.json)")
        f1.pack(fill="x", **pad)
        row = ttk.Frame(f1); row.pack(fill="x", padx=8, pady=8)
        ttk.Entry(row, textvariable=self.macro_path, state="readonly").pack(side="left", fill="x", expand=True)
        self.pick_btn = ttk.Button(row, text="파일 선택", command=self._pick_file)
        self.pick_btn.pack(side="left", padx=(8, 0))
        self.macro_summary = ttk.Label(f1, text="아직 선택 안 됨", foreground="#666")
        self.macro_summary.pack(anchor="w", padx=8, pady=(0, 8))

        # ② 실거래 여부
        f2 = ttk.LabelFrame(self.root, text="② 바이낸스 실거래 여부")
        f2.pack(fill="x", **pad)
        ttk.Checkbutton(f2, text="실거래(메인넷) 사용 — 체크하면 실제 자금이 움직여요",
                        variable=self.live, command=self._on_live_toggle).pack(anchor="w", padx=8, pady=8)
        self.live_note = ttk.Label(f2, text="현재: 테스트넷 (가짜 자금)", foreground="#0a0")
        self.live_note.pack(anchor="w", padx=8, pady=(0, 8))

        # ③ API 키
        f3 = ttk.LabelFrame(self.root, text="③ 바이낸스 API 키 / 시크릿 (로컬에서만 사용·서버 전송 안 함)")
        f3.pack(fill="x", **pad)
        ttk.Label(f3, text="API Key").pack(anchor="w", padx=8, pady=(8, 0))
        ttk.Entry(f3, textvariable=self.api_key).pack(fill="x", padx=8)
        ttk.Label(f3, text="API Secret").pack(anchor="w", padx=8, pady=(6, 0))
        ttk.Entry(f3, textvariable=self.api_secret, show="•").pack(fill="x", padx=8, pady=(0, 8))

        # ④ 회원 키
        f4 = ttk.LabelFrame(self.root, text="④ 껄무새 회원 키 (마이페이지에서 발급)")
        f4.pack(fill="x", **pad)
        ttk.Entry(f4, textvariable=self.member_key).pack(fill="x", padx=8, pady=8)

        # 실행/종료 버튼
        btns = ttk.Frame(self.root); btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="▶ 매크로 시작", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■ 매크로만 종료", command=lambda: self._stop("stop_only"),
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.close_btn = ttk.Button(btns, text="■ 청산 후 종료", command=lambda: self._stop("close_and_stop"),
                                    state="disabled")
        self.close_btn.pack(side="left", padx=(8, 0))
        self.status_lbl = ttk.Label(btns, text="대기 중", foreground="#666")
        self.status_lbl.pack(side="right")

        # 로그
        ttk.Label(self.root, text="실행 로그").pack(anchor="w", padx=12)
        self.log_box = tk.Text(self.root, height=14, wrap="word", state="disabled",
                               bg="#0b0e14", fg="#c9d1d9", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        if requests is None:
            self._log("⚠ 'requests' 모듈이 없어요. requirements.txt 를 설치해 주세요.")

    # --- 이벤트 -------------------------------------------------
    def _on_live_toggle(self) -> None:
        if self.live.get():
            self.live_note.config(text="현재: ⚠ 메인넷 (실제 자금이 움직입니다)", foreground="#c00")
        else:
            self.live_note.config(text="현재: 테스트넷 (가짜 자금)", foreground="#0a0")

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="매크로 파일 선택",
            filetypes=[("껄무새 매크로", "*.json *.ggm.json"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                macro = json.load(f)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"매크로 파일을 읽지 못했어요:\n{exc}")
            return
        try:
            self._apply_macro(macro, path)
        except ValueError:
            messagebox.showerror(APP_TITLE, "올바른 껄무새 매크로 파일이 아니에요.")
            return

    def _apply_macro(self, macro: dict, source_label: str) -> None:
        """Apply either a local file or a claimed web macro to the same UI."""

        if not isinstance(macro, dict) or not str(macro.get("symbol", "")).strip():
            raise ValueError("invalid macro")
        try:
            lev = max(1, int(macro.get("leverage", 1) or 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid macro leverage") from exc
        self.macro = dict(macro)
        self.macro_path.set(source_label)
        side = str(macro.get("position_side", "long"))
        market = _decide_market(side.lower(), lev)
        summary = macro.get("human_summary", "") or f"{macro['symbol']} · {side}"
        self.macro_summary.config(
            text=f"{macro['symbol']} · {market} · {side}{' · '+str(lev)+'배' if lev>1 else ''}\n{summary}")

    def _begin_protocol_claim(self, ticket: str) -> None:
        """Claim a browser launch ticket without blocking Tk's event loop."""

        if requests is None:
            self._protocol_claim_failed()
            return
        self.status_lbl.config(text="웹 연결 확인 중…", foreground="#c60")
        self.start_btn.config(state="disabled")
        self.pick_btn.config(state="disabled")
        self._log("웹에서 선택한 매크로를 안전하게 연결하고 있어요.")
        threading.Thread(
            target=self._claim_protocol_ticket,
            args=(ticket,),
            daemon=True,
            name="ggparrot-launch-claim",
        ).start()

    def _begin_protocol_registration(self) -> None:
        """Install the per-user URI handler off the Tk main thread."""

        threading.Thread(
            target=self._register_protocol_worker,
            daemon=True,
            name="ggparrot-protocol-install",
        ).start()

    def _register_protocol_worker(self) -> None:
        if not _install_protocol_handler_for_current_user():
            self.root.after(
                0,
                self._log,
                "브라우저 빠른 연결을 준비하지 못했지만 실행기는 그대로 사용할 수 있어요.",
            )

    def _claim_protocol_ticket(self, ticket: str) -> None:
        try:
            response = requests.post(
                f"{SERVER_BASE}{PROTOCOL_CLAIM_PATH}",
                json={"ticket": ticket},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            macro = payload.get("macro") if isinstance(payload, dict) else None
            runner_key = payload.get("runner_key") if isinstance(payload, dict) else None
            if (
                not isinstance(macro, dict)
                or not str(macro.get("symbol", "")).strip()
                or not isinstance(runner_key, str)
                or not runner_key.strip()
            ):
                raise ValueError("invalid launch response")
        except Exception:
            # Do not surface exception text: HTTP/proxy errors occasionally
            # include request data, and launch tickets/account keys must never
            # be copied into the GUI log.
            self.root.after(0, self._protocol_claim_failed)
            return
        self.root.after(0, self._apply_protocol_claim, macro, runner_key)

    def _apply_protocol_claim(self, macro: dict, runner_key: str) -> None:
        try:
            self._apply_macro(macro, "웹에서 연결한 내 매크로")
        except ValueError:
            self._protocol_claim_failed()
            return

        # A web launch may only prepare the form.  Exchange credentials never
        # arrive through the URI/server, testnet is restored explicitly, and
        # _start() is intentionally not called here.
        self.api_key.set("")
        self.api_secret.set("")
        self.member_key.set(runner_key.strip())
        self.live.set(False)
        self._on_live_toggle()
        self.pick_btn.config(state="normal")
        self.start_btn.config(state="normal")
        self.status_lbl.config(text="웹 연결됨 · 시작 전", foreground="#0a0")
        self._log("웹 매크로와 껄무새 계정을 연결했어요. 테스트넷 설정을 확인한 뒤 직접 시작해 주세요.")
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def _protocol_claim_failed(self) -> None:
        self.pick_btn.config(state="normal")
        self.start_btn.config(state="normal")
        self.status_lbl.config(text="웹 연결 실패", foreground="#c00")
        self._log("웹 연결 요청을 확인하지 못했어요. 사이트로 돌아가 다시 시도해 주세요.")

    def _start(self) -> None:
        if requests is None:
            messagebox.showerror(APP_TITLE, "'requests' 모듈이 필요해요. requirements 설치 후 실행하세요.")
            return
        if not self.macro:
            messagebox.showwarning(APP_TITLE, "먼저 매크로 파일을 선택하세요.")
            return
        if not self.api_key.get().strip() or not self.api_secret.get().strip():
            messagebox.showwarning(APP_TITLE, "API Key/Secret 을 입력하세요.")
            return
        if not self.member_key.get().strip():
            messagebox.showwarning(APP_TITLE, "껄무새 회원 키를 입력하세요.")
            return

        testnet = not self.live.get()
        if not testnet:  # 메인넷: 실제 자금 확인
            side = str(self.macro.get("position_side", "long"))
            if not messagebox.askyesno(
                APP_TITLE,
                "⚠ 실거래(메인넷)로 실행합니다.\n\n실제 자금으로 주문이 실행돼요. "
                f"({self.macro.get('symbol')} · {side})\n계속할까요?"):
                return

        server = ServerClient(self.member_key.get())
        side = str(self.macro.get("position_side", "long")).lower()
        lev = max(1, int(self.macro.get("leverage", 1) or 1))
        payload = {
            "symbol": str(self.macro.get("symbol", "")).upper(),
            "position_side": side,
            "leverage": lev,
            "market": _decide_market(side, lev),
            "testnet": testnet,
            "human_summary": self.macro.get("human_summary", ""),
        }
        try:
            server.start(payload)
        except Exception as exc:
            msg = str(exc)
            if "401" in msg:
                msg = "회원 키가 유효하지 않아요. 마이페이지에서 키를 확인하세요."
            messagebox.showerror(APP_TITLE, f"서버 연결 실패:\n{msg}")
            return

        self._log(f"세션 시작 (id={server.session_id}) · {payload['symbol']} · {payload['market']} · "
                  f"{'메인넷' if not testnet else '테스트넷'}")
        self.bot = BotThread(
            self.macro, self.api_key.get().strip(), self.api_secret.get().strip(),
            testnet, server,
            on_log=self._log_threadsafe,
            on_status=self._status_threadsafe,
            on_finish=self._finish_threadsafe,
        )
        self.bot.start()
        self._set_running(True)

    def _stop(self, mode: str) -> None:
        if not self.bot:
            return
        label = "청산 후 종료" if mode == "close_and_stop" else "매크로만 종료"
        if not messagebox.askyesno(APP_TITLE, f"{label} 할까요?"):
            return
        self.bot.set_command(mode)
        self.status_lbl.config(text="종료 처리 중…", foreground="#c60")

    # --- 스레드-세이프 콜백 (GUI 는 메인스레드에서만 갱신) --------
    def _log_threadsafe(self, msg: str) -> None:
        self.root.after(0, self._log, msg)

    def _status_threadsafe(self, snap: dict) -> None:
        self.root.after(0, self._render_status, snap)

    def _finish_threadsafe(self, status: str, note: str) -> None:
        self.root.after(0, self._on_finish, status, note)

    def _log(self, msg: str) -> None:
        self.log_box.config(state="normal")
        self.log_box.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _render_status(self, snap: dict) -> None:
        pos = "보유" if snap.get("in_position") else "무포지션"
        self.status_lbl.config(
            text=f"실행 중 · {snap.get('last_price', 0):g} · {pos} · "
                 f"누적 {snap.get('realized_pnl', 0):+.2f} USDT",
            foreground="#0a0")

    def _on_finish(self, status: str, note: str) -> None:
        self._log(f"종료됨 ({status}){' · ' + note if note else ''}")
        self.status_lbl.config(text=f"종료됨 · {note or status}", foreground="#666")
        self._set_running(False)
        self.bot = None

    def _set_running(self, running: bool) -> None:
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        self.close_btn.config(state="normal" if running else "disabled")


def main() -> None:
    args = sys.argv[1:]
    protocol_ticket = None
    startup_warning = ""
    protocol_requested = "--protocol" in args
    try:
        protocol_ticket = parse_protocol_args(args)
    except ProtocolLaunchError:
        # Never echo the malformed URI: it can contain secrets supplied by an
        # untrusted page.  Open the ordinary UI and explain the safe recovery.
        startup_warning = "올바르지 않은 웹 연결 요청은 무시했어요. 사이트에서 다시 시도해 주세요."

    root = tk.Tk()
    try:
        # 고해상도 화면 선명하게 (Windows)
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    RunnerApp(
        root,
        protocol_ticket=protocol_ticket,
        register_protocol=not protocol_requested and protocol_ticket is None,
        startup_warning=startup_warning,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
