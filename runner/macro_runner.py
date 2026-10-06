"""껄무새 매크로 실행기 (Macro Runner)

코딩을 모르는 회원도 더블클릭 한 번으로 매크로를 돌릴 수 있게 만든 GUI 실행기.
터미널·파이썬 설치가 필요 없는 단일 exe 로 배포한다(PyInstaller, 아래 빌드 안내 참고).

화면 구성(요청 사양)
  ① 매크로 파일 선택        — 빌더에서 내려받은 .ggm.json
  ② 실행 모드               — 모의(주문 삼킴) / 테스트넷(바이낸스만) / 실전(실제 자금). 기본은 모의
  ③ 거래소별 API 키/시크릿  — 바이낸스·업비트·빗썸 각각. 쓰는 키는 매크로의 거래소가 고른다
                             (로컬 메모리·이 PC 저장만, 서버 전송·로깅 안 함)
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
import queue
import shutil
import sys
import threading
import time
import uuid
from collections import deque
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:  # package import (tests) / direct script import (PyInstaller build)
    from . import credentials as credentials_mod
except ImportError:
    import credentials as credentials_mod

try:  # package import (tests) / direct script import (PyInstaller build)
    from . import brokers
except ImportError:
    import brokers

try:
    import requests
except ImportError:  # 사용자에게 친절히 안내
    requests = None

try:  # package import (tests) / direct script import (PyInstaller build)
    from .protocol import (
        PROTOCOL_CLAIM_PATH,
        PROTOCOL_SCHEME,
        ProtocolLaunch,
        ProtocolLaunchError,
        parse_protocol_launch,
    )
    from .installation import (
        RUNNER_VERSION,
        files_identical,
        protocol_install_target,
    )
    from .single_instance import (
        InstanceCommand,
        RunnerSingleInstance,
        SingleInstanceError,
    )
except ImportError:
    from protocol import (
        PROTOCOL_CLAIM_PATH,
        PROTOCOL_SCHEME,
        ProtocolLaunch,
        ProtocolLaunchError,
        parse_protocol_launch,
    )
    from installation import (
        RUNNER_VERSION,
        files_identical,
        protocol_install_target,
    )
    from single_instance import (
        InstanceCommand,
        RunnerSingleInstance,
        SingleInstanceError,
    )

# ==================================================================
#  설정
# ==================================================================
SERVER_BASE = os.environ.get("GGP_SERVER_BASE", "https://gg-parrot.onrender.com").rstrip("/")
LOCAL_SERVER_BASE = "http://127.0.0.1:8000"
MAX_ORDER_USDT = float(os.environ.get("MAX_ORDER_USDT", "100"))   # 1회 주문 상한(USDT)
MAX_ORDER_KRW = float(os.environ.get("MAX_ORDER_KRW", "150000"))  # 1회 주문 상한(KRW) — 100 USDT 와 비슷한 자리수
ORDER_CAP_BASIS = os.environ.get("ORDER_CAP_BASIS", "notional").lower()  # notional | margin
MAX_RETRIES = 3


def quote_of(symbol: str) -> str:
    """호가 통화 — 국내는 `KRW-BTC`, 바이낸스는 `BTCUSDT`."""
    return "KRW" if str(symbol).upper().startswith("KRW-") else "USDT"


def order_cap(quote: str) -> float:
    """1 회 주문 상한. 상한은 늘 통화와 함께 다닌다 — 떼어 두면 다음에 또 틀린다."""
    return MAX_ORDER_KRW if str(quote).upper() == "KRW" else MAX_ORDER_USDT


# --- 실행 모드 ---------------------------------------------------------------
# 기본값은 늘 모의다. 국내 거래소에는 테스트넷이 없어서(바이낸스만 있다) 모의가 유일한 연습 수단이고,
# 실수로 실전이 먼저 돌아가는 일을 기본값으로 막는다.
MODE_MOCK, MODE_TESTNET, MODE_LIVE = "mock", "testnet", "live"
RUN_MODES = (MODE_MOCK, MODE_TESTNET, MODE_LIVE)
MODE_LABELS = {MODE_MOCK: "모의", MODE_TESTNET: "테스트넷", MODE_LIVE: "실전"}
# 테스트넷은 바이낸스 전용이다 — 국내는 모의·실전 둘뿐.
TESTNET_EXCHANGES = ("binance",)
# 국내 원화 현물 거래소는 brokers 가 아는 목록을 그대로 쓴다 — 어댑터가 있는 곳과 어긋나면 안 된다.
DOMESTIC_EXCHANGES = tuple(brokers.DOMESTIC_LABELS)
# 이 실행기가 주문을 낼 수 있는 거래소 전부. 자격증명 파일이 칸을 가진 목록과 같아야 한다.
KNOWN_EXCHANGES = ("binance",) + DOMESTIC_EXCHANGES
EXCHANGE_LABELS = {"binance": "바이낸스", **brokers.DOMESTIC_LABELS}


def run_mode_of(raw) -> str:
    """모르는 값은 모의로 접는다 — 설정이 깨졌을 때 실전으로 떨어지면 안 된다."""
    mode = str(raw or "").strip().lower()
    return mode if mode in RUN_MODES else MODE_MOCK


def exchange_of(macro: dict) -> str:
    """매크로가 고른 거래소. 키도 브로커도 이 값 하나로 갈린다."""
    return str((macro or {}).get("exchange", "binance") or "binance").lower()


def exchange_label(exchange: str) -> str:
    return EXCHANGE_LABELS.get(str(exchange).lower(), str(exchange))


def known_exchange(exchange: str) -> bool:
    """이 실행기가 주문을 낼 수 있는 거래소인가.

    모르는 이름을 바이낸스로 바꿔치지 않는다 — 오타 난 `upbit` 가 바이낸스 키로 돌면
    사용자가 의도하지 않은 시장에 주문이 들어간다. 모르면 시작을 막고 이름을 보여 준다.
    서버 쪽 `exchange` 는 정해진 세 값만 받으므로, 걸러 내지 않으면 사용자는 422 를
    '서버 연결 실패' 로만 보게 된다.
    """
    return str(exchange).lower() in KNOWN_EXCHANGES


def credential_pair(credentials: dict, exchange: str) -> dict | None:
    """저장된 자격증명(v2)에서 이 거래소의 키 한 쌍. 없으면 None.

    자격증명 파일에는 키를 적은 거래소만 들어 있다 — 그래서 `[name]` 이 아니라 `.get(name)` 이다.
    시크릿이 비어 있으면 '아직 안 넣었다' 와 같게 본다(한쪽만 적은 칸도 그대로 저장되므로).
    """
    pair = ((credentials or {}).get("exchanges") or {}).get(str(exchange).lower())
    if not isinstance(pair, dict):
        return None
    key, secret = str(pair.get("api_key") or "").strip(), str(pair.get("api_secret") or "").strip()
    return {"api_key": key, "api_secret": secret} if key and secret else None


def _session_payload(macro: dict, *, testnet: bool, mode: str) -> dict:
    """서버에 올리는 세션 시작 payload.

    거래소 키·시크릿은 어떤 경로로도 여기에 들어가지 않는다 — 실행기가 서버에 보내는 것은
    회원 키와 구동 상태뿐이다. 매크로 출처(서명 · user_macro_id)는 GUI 가 따로 얹는다.
    """
    if not macro:
        raise ValueError("macro is required")
    side = str(macro.get("position_side", "long")).lower()
    lev = max(1, int(macro.get("leverage", 1) or 1))
    exchange = exchange_of(macro)
    return {
        "symbol": str(macro.get("symbol", "")).upper(),
        "exchange": exchange,
        "position_side": side,
        "leverage": lev,
        "market": market_of(exchange, side, lev),
        "testnet": testnet,
        # 모의도 '가짜 자금' 이므로 testnet 과 함께 보낸다. 서버가 아직 안 읽는 값이지만
        # 세션 기록에 연습과 실전이 섞이지 않게 하려면 실행기가 먼저 말해야 한다.
        "mode": run_mode_of(mode),
        "human_summary": macro.get("human_summary", ""),
        # 서버가 세션에 기록하고 오래된 버전을 거절할 수 있게 실어 보낸다.
        "runner_version": RUNNER_VERSION,
        # 매크로 원문 — 마이페이지 실시간 차트에 빌더와 동일한 전략 보조지표를
        # 그리는 데 쓰인다. 거래소 키/시크릿은 여기에 포함되지 않는다.
        "macro": macro,
    }


POLL_SECONDS = 5.0
# 실행 로그를 서버에 올리는 버퍼 상한(heartbeat 한 번에 실어 보내는 최대 줄 수).
EVENT_BUFFER_MAX = 100


def _event_kind(msg: str) -> str:
    """로그 한 줄을 서버 이벤트 종류로 분류한다(문의 대응용 색인)."""
    m = msg.strip()
    if m.startswith("[진입]") or m.startswith("[청산") or m.startswith("[강제청산") or m.startswith("[손절]"):
        return "order"
    if "체결" in m or m.lstrip().startswith("손익"):
        return "fill"
    if "오류" in m or "실패" in m:
        return "error"
    if "종료" in m:
        return "stop"
    if "진입 보류" in m or "신호" in m:
        return "signal"
    return "info"

_LOG_ICONS = {"signal": "🔔", "order": "▶", "fill": "✓", "error": "⚠", "stop": "■", "info": "·"}


def _log_style(msg: str) -> tuple[str, str]:
    """로그 한 줄의 (태그, 아이콘) — 종류별 색·아이콘으로 읽기 쉽게 그린다."""
    kind = _event_kind(msg)
    return kind, _LOG_ICONS.get(kind, "·")


# --- 화면 색·글꼴 (껄무새 웹과 같은 톤) ---------------------------------------
UI = {
    "bg": "#F6F7F9", "card": "#FFFFFF", "border": "#E5E7EB", "ink": "#111827", "muted": "#6B7280",
    "brand": "#F5C542", "brand_hover": "#EAB308", "brand_ink": "#1F1300",
    "ok": "#16A34A", "warn": "#D97706", "danger": "#DC2626",
    "log_bg": "#0F172A", "log_fg": "#CBD5E1",
}
FONT = "맑은 고딕"
APP_TITLE = f"껄무새 매크로 실행기 v{RUNNER_VERSION}"


def _bring_window_to_front(root: tk.Tk) -> None:
    """Restore and foreground the already-running Tk window, best effort."""

    try:
        root.deiconify()
        root.update_idletasks()
        root.lift()
        # Windows may refuse a direct SetForegroundWindow depending on which
        # process owns foreground permission. A short topmost pulse still
        # makes the user-requested existing window visible without leaving it
        # pinned above other apps.
        root.attributes("-topmost", True)

        def release_topmost() -> None:
            try:
                root.attributes("-topmost", False)
            except tk.TclError:
                pass

        root.after(180, release_topmost)
        root.focus_force()
    except tk.TclError:
        return

    if sys.platform != "win32":
        return
    try:
        from ctypes import c_int, windll, wintypes

        user32 = windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetLastActivePopup.argtypes = [wintypes.HWND]
        user32.GetLastActivePopup.restype = wintypes.HWND
        user32.ShowWindowAsync.argtypes = [wintypes.HWND, c_int]
        user32.ShowWindowAsync.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        hwnd = int(root.winfo_id())
        hwnd = int(user32.GetAncestor(hwnd, 2) or hwnd)  # GA_ROOT
        user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
        popup = int(user32.GetLastActivePopup(hwnd) or hwnd)
        user32.BringWindowToTop(popup)
        user32.SetForegroundWindow(popup)
    except Exception:
        # Tk's lift/topmost path above is the portable fallback.
        pass


def _show_native_runner_error(message: str) -> None:
    """Show a pre-Tk error in the noconsole Windows executable."""

    if sys.platform == "win32":
        try:
            from ctypes import windll

            windll.user32.MessageBoxW(0, message, APP_TITLE, 0x10)  # MB_ICONERROR
            return
        except Exception:
            pass
    # This path is useful only for source-mode diagnostics; the released
    # executable has no console and uses the MessageBox above.
    try:
        print(message, file=sys.stderr)
    except Exception:
        pass


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
    # Never overwrite the old, possibly running, fixed-path runner.  Each
    # release owns an immutable directory and the registry is switched only
    # after the new executable has been copied and verified completely.
    stable = protocol_install_target(local_app_data)
    staged = stable.with_name(f".{stable.name}.{os.getpid()}.new")
    try:
        stable.parent.mkdir(parents=True, exist_ok=True)
        same_path = os.path.normcase(str(source)) == os.path.normcase(str(stable.resolve()))
        if not same_path:
            if not stable.exists() or not files_identical(source, stable):
                # Stage then replace so a partial copy can never become the
                # shell handler. Old release directories are never modified
                # or deleted, so an in-use older executable cannot block v5.
                shutil.copy2(source, staged)
                if not files_identical(source, staged):
                    return False
                os.replace(staged, stable)
            if not files_identical(source, stable):
                return False

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
# '팔 수 없는 티끌' 의 기준. 거래소가 수량 단위(step)를 주지 않는 국내 마켓에서 쓴다 —
# brokers 가 매도 수량을 소수 8자리로 내리므로, 그보다 작은 잔여는 어떤 주문으로도 털 수 없다.
# 값을 베끼지 않고 brokers 의 한계를 그대로 읽어 둘이 어긋나지 않게 한다.
DUST_FLOOR = float(brokers._VOLUME_DECIMALS)  # 0.00000001


def _round_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    d = Decimal(str(step))
    return float((Decimal(str(qty)) / d).to_integral_value(rounding=ROUND_DOWN) * d)


def _decide_market(side: str, leverage: int) -> str:
    return "futures" if (side == "short" or leverage > 1) else "spot"


def market_of(exchange: str, side: str, leverage: int) -> str:
    """거래소까지 본 시장. 국내(업비트·빗썸)는 원화 현물뿐이라 방향·레버리지로 선물이 되지 않는다.

    섞어 두면 레버리지가 적힌 국내 매크로가 '선물' 로 읽혀 있지도 않은 선물 경로를 탄다.
    """
    if str(exchange).lower() != "binance":
        return "spot"
    return _decide_market(side, leverage)


def _base_asset(symbol: str) -> str:
    for q in ("USDT", "BUSD", "USDC", "FDUSD"):
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol


def _positive(value) -> float:
    """숫자로 읽히지 않거나 0 이하면 0. 매크로 params 는 사용자 JSON 이라 무엇이든 들어올 수 있다."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 and number == number and number != float("inf") else 0.0


def _money(amount: float, quote: str) -> str:
    """돈은 통화와 함께 쓴다. 원화에는 보여 줄 만한 소수부가 없다 — 1원 아래는 거래소에도 없다."""
    unit = str(quote or "USDT").upper()
    return f"{amount:+,.0f} {unit}" if unit == "KRW" else f"{amount:+,.2f} {unit}"


def _notional_for(budget, leverage, market, *, quote="USDT") -> float:
    """진입 예산에서 실제로 거래소에 나가는 주문 금액. 상한을 먼저 씌우고, 선물·증거금 기준이면 레버리지를 곱한다.

    _order_qty 에서 떼어 낸 이유: 리허설도 같은 금액을 알아야 한다. 두 곳에서 따로 세면
    '연습은 통과했는데 실제 주문은 거절' 또는 그 반대가 생긴다.
    """
    cap = min(budget, order_cap(quote))
    return cap * leverage if (market == "futures" and ORDER_CAP_BASIS == "margin") else cap


def _smallest_entry_notional(macro: dict, leverage, market, *, quote="USDT") -> float:
    """이 매크로의 진입 한 건이 쓸 수 있는 가장 작은 주문 금액.

    명령의 notional_frac 은 서버가 신호마다 정하므로 세션 시작에는 알 수 없다. 대신 매크로 자신의
    설정에서 '가장 작은 진입' 을 읽는다 — 리허설이 실제보다 큰 금액을 보면(업비트 검증 주문은 잔고까지
    본다) 잘 돌아갈 세션을 시작도 못 하게 막고, 큰 금액으로 최소 주문 금액을 통과시키면 정작 모든
    신호가 under_min_total 로 거절된다. 가장 작은 쪽을 보면 두 방향 다 막힌다.
    """
    params = (macro or {}).get("params") or {}
    risk = (macro or {}).get("risk") or {}
    capital = _positive(params.get("initial_capital")) or order_cap(quote)
    budget = capital * (_positive(risk.get("invest_ratio")) or 1.0)
    rule = str((macro or {}).get("rule_type") or "A").upper()
    if rule == "D":  # 그리드는 한 칸씩 산다 — 한 칸 금액이 진입 한 건이다
        per_grid = _positive(params.get("per_grid_invest"))
        grids = int(_positive(params.get("grid_count")))
        budget = per_grid or (budget / grids if grids else budget)
    elif rule == "H":  # 마틴게일: 기본 주문과 (배수가 1 미만이면 더 작아지는) 추가 주문 중 작은 쪽
        base = _positive(params.get("base_order_size")) or budget
        safety = _positive(params.get("safety_order_size"))
        scale = _positive(params.get("safety_order_volume_scale")) or 1.0
        steps = int(_positive(params.get("max_safety_orders")))
        if safety and steps:
            safety *= min(1.0, scale) ** max(0, steps - 1)
            base = min(base, safety)
        budget = base
    return _notional_for(budget, leverage, market, quote=quote)


def _order_qty(price, step, min_notional, budget, leverage, market, *, quote="USDT") -> tuple[float, float]:
    notional = _notional_for(budget, leverage, market, quote=quote)
    qty = _round_step(notional / price, step)
    return qty, qty * price


def _strategy_targets(macro: dict) -> dict:
    """로컬에 남는 값만 — 진입/청산 판단은 서버(v8)가 하고, 여기선 안전망(손절·리스크)과 자본만 본다."""
    p = macro.get("params", {})
    risk = macro.get("risk", {})
    return {
        "rule": macro.get("rule_type", "A"),
        "sl_pct": float(risk["stop_loss_pct"]) if risk.get("stop_loss_pct") else None,
        "invest_ratio": float(risk.get("invest_ratio", 1.0)),
        "capital": float(p.get("initial_capital", 0) or 0),
        "risk": risk,
    }


class RiskGuard:
    """공통 리스크 3종: 일일 최대손실 / 최대 보유시간 / 재진입 금지."""

    def __init__(self, risk: dict, base_capital: float, *, cap: float = MAX_ORDER_USDT) -> None:
        self.daily_max_loss = risk.get("daily_max_loss_pct")
        self.max_holding_hours = risk.get("max_holding_hours")
        self.cooldown_minutes = float(risk.get("cooldown_minutes") or 0)
        self.base = base_capital if base_capital > 0 else cap
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

    def on_partial_exit(self, pnl_usdt: float) -> None:
        """부분 청산(서버 명령): 실현손익만 일일 집계에 더하고 보유시간은 그대로."""
        self._day_pnl += pnl_usdt
        if self.daily_max_loss and self._daily_loss_pct() <= -float(self.daily_max_loss):
            self._halted_day = self._day


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
    def __init__(self, runner_key: str, *, base: str = SERVER_BASE) -> None:
        self.base = base.rstrip("/")
        self.key = runner_key.strip()
        self.session_id = None
        self.macro_origin = ""
        self._headers = {"X-Runner-Key": self.key, "Content-Type": "application/json"}
        # 실행 창 로그를 서버에도 남긴다 — heartbeat 에 실어 보내고, 실패하면 다음에 다시.
        self._events: list[dict] = []
        self._events_lock = threading.Lock()
        # 명령 실행 결과(ack). 전송에 실패하면 다음 heartbeat 앞머리에 다시 실어 보낸다.
        self.unsent_acks: list[dict] = []

    def push_event(self, kind: str, message: str) -> None:
        with self._events_lock:
            self._events.append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "kind": kind,
                "message": str(message)[:300],
            })
            if len(self._events) > EVENT_BUFFER_MAX:
                del self._events[:-EVENT_BUFFER_MAX]

    def _drain_events(self) -> list[dict]:
        with self._events_lock:
            batch, self._events = self._events, []
        return batch

    def _requeue_events(self, batch: list[dict]) -> None:
        if not batch:
            return
        with self._events_lock:
            self._events = (batch + self._events)[-EVENT_BUFFER_MAX:]

    def start(self, payload: dict) -> dict:
        r = requests.post(f"{self.base}/api/runner/start", json=payload,
                          headers=self._headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        self.session_id = data.get("session_id")
        self.macro_origin = str(data.get("macro_origin") or "")
        return data

    def heartbeat(self, snapshot: dict, acks: list[dict] | None = None) -> dict:
        """상태와 명령 실행 결과(acks)를 올리고 {"action", "commands"} 를 받는다.
        action 은 continue|stop_only|close_and_stop, commands 는 서버 전략이 낸 미실행 주문 명령(seq 순).
        네트워크 오류면 action="continue", commands=[], offline=True — 진입은 멈추고(명령 없음) 로컬 안전망만 돈다."""
        if self.session_id is None:
            return {"action": "continue", "commands": []}
        body = dict(snapshot)
        body["session_id"] = self.session_id
        events = self._drain_events()
        body["events"] = events
        pending = list(getattr(self, "unsent_acks", None) or []) + list(acks or [])
        self.unsent_acks = []
        body["acks"] = pending
        try:
            r = requests.post(f"{self.base}/api/runner/heartbeat", json=body,
                              headers=self._headers, timeout=10)
            r.raise_for_status()
            data = r.json() or {}
            return {"action": data.get("action", "continue"), "commands": list(data.get("commands") or [])}
        except Exception:
            self._requeue_events(events)
            self.unsent_acks = pending
            return {"action": "continue", "commands": [], "offline": True}

    def stopped(self, status: str = "stopped", note: str = "", snapshot: dict | None = None) -> bool:
        if self.session_id is None:
            return True
        # Repeating the same terminal snapshot is idempotent. A deploy or a
        # transient response failure must not silently lose the final state.
        body = {
            "session_id": self.session_id, "status": status, "note": note, "snapshot": snapshot,
            "events": self._drain_events(),
        }
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    f"{self.base}/api/runner/stopped", json=body,
                    headers=self._headers, timeout=10,
                )
                if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                    return False
                response.raise_for_status()
                return True
            except Exception:
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(attempt + 1)
        return False


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

    # 거래소와 실행 모드는 __init__ 이 늘 채운다. 클래스 기본값은 가장 안전한 쪽 —
    # 바이낸스(= 수량 주문)와 모의(= 주문 없음)다. 둘 중 하나를 못 채운 경로가 생겨도
    # 금액 주문이나 실전 주문으로 새지 않는다.
    exchange = "binance"
    mode = MODE_MOCK

    def __init__(self, macro: dict, credentials: dict, mode: str,
                 server: ServerClient, on_log, on_status, on_finish) -> None:
        super().__init__(daemon=True)
        self.macro = macro
        # 자격증명 v2 전체({"exchanges": {거래소: 키 한 쌍}})를 그대로 들고 있는다.
        # 어느 쌍을 쓸지는 매크로의 거래소가 정한다 — 사용자에게 묻지 않는다.
        self.credentials = credentials or {}
        self.exchange = exchange_of(macro)
        self.mode = run_mode_of(mode)
        self.server = server
        self.on_log = on_log
        self.on_status = on_status
        self.on_finish = on_finish

        self._command = None  # None | "stop_only" | "close_and_stop"
        self._lock = threading.Lock()
        self._wake = threading.Event()  # 종료 명령 시 sleep 을 즉시 깨움

        # 매매 상태
        self.symbol = str(macro.get("symbol", "BTCUSDT")).upper()
        self.quote = quote_of(self.symbol)  # 주문 상한·자본 기본값이 이 통화를 따른다 — __init__ 시그니처를 바꿔도 이 줄은 symbol 뒤에 반드시 남겨야 한다
        self.side = str(macro.get("position_side", "long")).lower()
        self.leverage = max(1, int(macro.get("leverage", 1) or 1))
        self.market = market_of(self.exchange, self.side, self.leverage)
        self.broker = None
        self.step = 0.0
        self.in_position = False
        self.entry_price = 0.0
        self.held_qty = 0.0
        self.realized = 0.0
        self.position_uncertain = False
        self.position_dust_qty = 0.0

        # v8 서버 신호: 명령의 notional_frac 은 매크로 초기자본 기준. 실행 결과는 다음 heartbeat 의 acks 로 보고.
        self.capital = float((macro.get("params") or {}).get("initial_capital") or 0) or order_cap(self.quote)
        self._done_command_ids: deque = deque(maxlen=200)  # ok 로 ack 한 명령 id — 재전송돼도 다시 실행하지 않는다
        self.pending_acks: list[dict] = []
        self._offline_logged = False

    @property
    def domestic(self) -> bool:
        """국내 원화 현물 거래소인가.

        브로커와 키를 고른 바로 그 값(exchange) 하나로 판단한다. 호가 통화(KRW)로 가르면
        '국내' 가 세 곳에서 서로 다르게 정해지고, 원화 호가 바이낸스 심볼 같은 경우에
        수량 주문이어야 할 것이 금액 주문으로 나간다.
        """
        return self.exchange in DOMESTIC_EXCHANGES

    @property
    def testnet(self) -> bool:
        """테스트넷 주소를 쓰는가. 모드에서 나오는 값이라 따로 들고 있지 않는다 —
        둘을 각각 보관하면 국내 매크로에서 '테스트넷인데 실전 주소' 처럼 어긋날 수 있다.
        테스트넷은 바이낸스에만 있다(국내는 모의·실전 둘뿐)."""
        return self.mode == MODE_TESTNET and self.exchange in TESTNET_EXCHANGES

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
        try:
            self.server.push_event(_event_kind(msg), msg)
        except Exception:
            pass  # 로그 전송은 매매를 막지 않는다

    def _sleep(self, seconds: float) -> None:
        # 종료 명령이 오면 즉시 깨어나도록 이벤트 기반 대기.
        self._wake.wait(timeout=seconds)

    # --- 시장별 어댑터 (현물/선물 공통 루프) ---------------------
    def _connect(self) -> bool:
        """매크로의 거래소로 어댑터를 만든다. 모의 모드면 그 어댑터를 MockBroker 로 감싼다.

        계정 확인은 여기서 하지 않는다 — 키 · 허용 IP · 권한 · 최소 금액은 리허설이 한 번에 보고,
        그 결과로 세션을 멈출지는 모드가 정한다(_rehearse). 연결 단계에서 서명 호출을 하면
        모의 모드가 키 없이 돌 수 없다.
        """
        label = exchange_label(self.exchange)
        if not known_exchange(self.exchange):
            self.log(f"[오류] 이 매크로의 거래소({self.exchange})는 이 실행기가 모르는 거래소예요. "
                     f"쓸 수 있는 거래소: {' · '.join(exchange_label(name) for name in KNOWN_EXCHANGES)}.")
            return False
        pair = credential_pair(self.credentials, self.exchange)
        if pair is None:
            if self.mode != MODE_MOCK:
                self.log(f"[오류] {label} API 키가 없어요. 이 매크로는 {label} 매크로라 "
                         f"{label} 키·시크릿을 넣어야 시작할 수 있어요.")
                return False
            # 연습은 키 없이도 돌아야 한다 — 주문은 MockBroker 가 삼키고, 시세만 공개 경로로 읽는다.
            self.log(f"{label} API 키가 없어 모의 모드로만 돌립니다(주문은 보내지 않아요).")
            pair = {"api_key": "", "api_secret": ""}
        if self.exchange != "binance" and (self.side != "long" or self.leverage > 1):
            self.log(f"[오류] {label} 는 원화 현물(롱 · 1배)만 돼요. 선물·숏 매크로는 바이낸스로 만드세요.")
            return False
        try:
            broker = self._build_broker(pair)
        except ImportError:
            self.log("python-binance 가 없어요. requirements 설치 후 다시 실행하세요.")
            return False
        except Exception as exc:
            self.log(f"{label} 연결 준비 실패: {exc}")
            return False
        # 모의는 바깥에서 감싼다 — 어느 거래소든 같은 안전망을 쓰게 하려는 것이 MockBroker 의 존재 이유다.
        self.broker = brokers.MockBroker(broker, log=self.log) if self.mode == MODE_MOCK else broker
        self.log(f"거래소 {label} · 실행 모드 {MODE_LABELS[self.mode]}"
                 f"{' (테스트넷 주소)' if self.testnet else ''}")
        return True

    def _build_broker(self, pair: dict):
        """거래소별 어댑터 하나. 바이낸스만 python-binance 클라이언트를 쓴다."""
        if self.exchange == "binance":
            from binance.client import Client
            return brokers.BinanceBroker(
                Client(pair["api_key"], pair["api_secret"], testnet=self.testnet),
                market=self.market, symbol=self.symbol, side=self.side,
                testnet=self.testnet, leverage=self.leverage, log=self.log)
        return brokers.DomesticBroker(pair["api_key"], pair["api_secret"],
                                      exchange=self.exchange, symbol=self.symbol, log=self.log)

    def _price(self) -> float:
        return self.broker.price()

    def _place(self, side_word: str, qty: float, reduce_only: bool = False,
               *, notional: float = 0.0) -> bool:
        """시장가 주문 하나를 넣고 체결을 확인한 뒤 보유 상태를 갱신한다.

        포지션 방향(롱=BUY, 숏=SELL)과 같은 주문은 보유 중이라도 '추가 진입'(가중평균), 반대 방향 또는
        reduce_only 는 '청산'이다. 보유 수량보다 적게 파는 청산은 부분 청산 — 남는 수량이 있어도 정상.

        notional 은 원화 금액이다. 업비트·빗썸의 시장가 매수는 수량이 아니라 '얼마치' 로 내는
        주문이라서, 수량으로 보내면 어댑터가 거절해 진입이 통째로 막힌다. 매도는 어느 거래소든 수량이다.
        금액 주문은 국내 거래소(domestic)의 진입에서만 쓴다 — 거래소로 가르지 않으면 바이낸스
        주문에 수량이 빠진 채(quantity=None) 나갈 수 있다.
        """
        open_word = "BUY" if self.side == "long" else "SELL"
        closing = reduce_only or (self.in_position and side_word != open_word)
        sellable = _round_step(self.held_qty, self.step) if self.held_qty > 0 else 0.0
        # step 은 국내에서 0 이다(금액 주문이라 수량 단위가 없다). 0 으로 나누지 않고, 양수라고 가정하지도 않는다.
        partial_close = closing and sellable > 0 and (sellable - qty) >= (self.step or 1e-12)
        client_id = "ggp-" + uuid.uuid4().hex[:28]
        by_money = self.domestic and not closing and notional > 0
        # 제출 · 재조정 · 평균가 · 수수료는 브로커가 한다. 봇은 그 결과로 장부만 쓴다.
        order = self.broker.submit(side_word, base_qty=None if by_money else qty,
                                   notional=notional if by_money else None,
                                   reduce_only=reduce_only,
                                   closing=closing, client_id=client_id)
        executed, average = order.executed_qty, order.avg_price
        acquired, fees_known = order.acquired_qty, order.fees_known
        self._last_fill_qty, self._last_fill_price = executed, average
        self.position_uncertain = order.status not in brokers.TERMINAL_STATUSES or (
            order.status == "FILLED" and not (executed and average)
        ) or bool(executed and not average) or not fees_known
        dust_only = False
        if executed:
            if closing:
                closed_qty = min(self.held_qty, executed)
                if average and self.entry_price:
                    self.realized += _pnl_usdt(closed_qty, self.entry_price, average, self.side)
                self.held_qty = max(0.0, self.held_qty - executed)
                if self.held_qty < 1e-12:
                    self.held_qty = 0.0
                if (self.market == "spot" and order.status == "FILLED"
                        and 0 < self.held_qty < (self.step or DUST_FLOOR) and not self.position_uncertain):
                    # LOT_SIZE cannot sell this residue. Keep its amount in the
                    # status note; never use unrelated account holdings to pad it.
                    # 국내는 step 이 0 이라 이 기준이 없으면 안전망이 꺼진다 — 매도 수량을 8자리로
                    # 내리면서 남는 1e-8 미만 티끌에 '체결 확인 실패' 로 세션이 오류로 끝난다.
                    self.position_dust_qty = getattr(self, "position_dust_qty", 0.0) + self.held_qty
                    self.log(f"최소 주문 단위 미만 잔여 수량: {self.position_dust_qty:.12g} {self.symbol}")
                    dust_only = True
                if (not self.held_qty or dust_only) and not self.position_uncertain:
                    self.entry_price = 0.0
            elif self.in_position and self.held_qty > 0 and self.entry_price > 0 and average:
                # 추가 진입(그리드·마틴게일): 보유 수량을 더하고 진입가는 가중평균.
                prev_qty, prev_entry = self.held_qty, self.entry_price
                self.held_qty = prev_qty + acquired
                self.entry_price = (prev_qty * prev_entry + acquired * average) / self.held_qty
            else:
                self.entry_price, self.held_qty = average, acquired
        # Unknown submission may have opened a real position even if the order
        # query failed. Never turn that uncertainty into a flat snapshot.
        self.in_position = (self.held_qty > 0 and not dust_only) or self.position_uncertain
        if order.status != "FILLED" or self.position_uncertain or (closing and self.in_position and not partial_close):
            # 사람이 거래소에서 직접 확인할 때 쓰는 문구다. 판정은 접은 status 로 하고, 보고는 거래소가 쓴 낱말로 한다.
            raise RuntimeError(f"주문 상태 {order.raw_status} — 체결 완료를 확인하지 못했습니다. 거래소에서 주문과 포지션을 확인하세요.")
        self.log(f"  ✓ {side_word}{' (청산)' if reduce_only else ''} 체결: id={order.order_id} 수량={executed}")
        return True

    def _prepare(self) -> bool:
        """시장별 심볼정보/레버리지 세팅과 주문 전 리허설. 성공 시 True."""
        if self.market != "futures" and self.side == "short":
            self.log("현물은 숏을 지원하지 않아요. 선물 매크로를 쓰세요.")
            return False
        try:
            ready = self.broker.ensure_ready()
        except brokers.DomesticAccessError as exc:
            # 국내 준비는 서명이 필요한 조회다 — 여기서 가장 먼저 키 · 허용 IP 에 걸린다. '심볼이 없다' 가
            # 아니라 리허설과 같은 문장 · 같은 정책(실전은 멈추고 모의는 계속)으로 보고한다.
            return self._rehearsal_failed(str(exc))
        if not ready:
            where = "선물" if self.market == "futures" else "현물"
            # 테스트넷이 있는 거래소는 바이낸스뿐이다. 국내 매크로에 '(테스트넷)' 을 붙이면
            # 있지도 않은 환경을 가리키게 된다.
            net = "(테스트넷) " if self.testnet else ""
            self.log(f"[오류] '{self.symbol}' 은 {net}{exchange_label(self.exchange)} {where}에 없어요. 심볼을 바꾸세요.")
            return False
        # 국내 원화 마켓은 수량 단위가 0 이다(금액으로 주문한다). 0 을 '아직 못 읽었다' 로 보면 안 된다.
        self.step = self.broker.order_rules().step
        return self._rehearse()

    def _entry_notional(self) -> float:
        """리허설이 확인할 주문 크기 — 이 매크로의 진입 한 건이 실제로 쓸 가장 작은 금액.

        self.capital(= 초기자본) 을 그대로 쓰지 않는다. 실제 주문은 투입비율 · 그리드 한 칸 ·
        마틴게일 기본 주문으로 더 작아지므로, 초기자본으로 연습하면 최소 주문 금액 미달을 못 잡고
        (모든 신호가 under_min_total 로 거절된다), 업비트 검증 주문은 잔고까지 보므로 돌아갈
        세션을 시작도 못 하게 막는다.
        """
        return _smallest_entry_notional(self.macro, self.leverage, self.market, quote=self.quote)

    def _rehearse(self) -> bool:
        """세션 시작에 한 번. 돈을 쓰지 않고 키 · 허용 IP · 권한 · 최소 주문 금액을 확인한다.

        브로커는 결과만 돌려주고, 멈출지 말지는 여기서 정한다:
          * 실전 · 테스트넷 — 실패하면 주문을 한 건도 내지 않고 세션을 끝낸다.
          * 모의            — 실패를 기록만 하고 계속한다. 키 없이 돌지 않으면 연습이 아니다.
        """
        notional = self._entry_notional()
        try:
            ok, reason = self.broker.rehearse(notional=notional)
        except Exception as exc:
            ok, reason = False, f"리허설 도중 오류가 났어요: {exc}"
        if ok:
            self.log(f"주문 전 확인: {reason}")
            return True
        return self._rehearsal_failed(reason)

    def _rehearsal_failed(self, reason: str) -> bool:
        """주문 전 확인이 통과하지 못했을 때의 보고와 정책. 준비 단계의 키 · IP 실패도 여기로 온다 —
        사용자에게는 같은 사정이고, 멈출지 말지의 기준도 같다."""
        self.log(f"[오류] 주문 전 확인 실패 — {reason}")
        if self.exchange == "binance" and ("-2015" in reason or "-2014" in reason or "키" in reason):
            where = ("선물 testnet(binancefuture.com)" if self.market == "futures"
                     else "현물 testnet(binance.vision)") if self.testnet else "메인넷"
            self.log(f"  → 이 매크로는 {self.market} 시장이에요. {where} 키인지, IP 제한/권한을 확인하세요.")
        if self.mode == MODE_MOCK:
            self.log("모의 모드라 주문 없이 계속합니다. 실전으로 바꾸기 전에 위 이유를 고쳐 주세요.")
            return True
        self.log("주문을 보내지 않고 종료합니다. 위 내용을 고친 뒤 다시 시작하세요.")
        return False

    def _close_position(self) -> bool:
        """보유 포지션을 시장가로 정리. 성공 시 True."""
        if getattr(self, "position_uncertain", False):
            raise RuntimeError("포지션을 확인하지 못해 추가 주문을 보내지 않습니다. 거래소에서 주문과 포지션을 확인하세요.")
        if not self.in_position or self.held_qty <= 0:
            return True
        close_word = "SELL" if self.side == "long" else "BUY"
        reduce = self.market == "futures"
        return self._place(close_word, _round_step(self.held_qty, self.step), reduce_only=reduce)

    # --- v8 서버 신호: 로컬 안전망 + 명령 실행 ------------------------
    def _local_stop_loss(self, price: float) -> bool:
        """서버와 끊겨도 손절은 된다 — 로컬에 남긴 유일한 청산 판단. 서버 손절 명령이 뒤에 오면 보유 0 이라 무주문 ack."""
        sl = (self.macro.get("risk") or {}).get("stop_loss_pct")
        if not sl or not self.in_position or self.entry_price <= 0:
            return False
        sl = float(sl) / 100.0
        return price <= self.entry_price * (1 - sl) if self.side == "long" else price >= self.entry_price * (1 + sl)

    def _execute_command(self, cmd: dict, price: float) -> dict:
        """서버 명령 하나를 실행하고 ack 를 만든다.

        성공(ok) 으로 ack 한 id 는 다시 와도 실행하지 않고 ok 로 재응답한다(서버가 ack 를 놓친 경우).
        실패한 명령은 기록하지 않는다 — 서버가 청산 실패를 같은 id 로 재전송하면 다시 실행해야 한다.
        """
        cid = cmd.get("id")
        ack = {"command_id": cid, "ok": True, "executed_qty": 0.0, "fill_price": 0.0, "error": ""}
        if cid in self._done_command_ids:
            return ack
        action = str(cmd.get("action", "")).lower()
        reason = str(cmd.get("reason") or "")
        uncertain_before = getattr(self, "position_uncertain", False)
        try:
            if action not in ("buy", "short", "sell", "cover"):
                raise RuntimeError(f"알 수 없는 명령 {action}")
            if action not in (("buy", "sell") if self.side == "long" else ("short", "cover")):
                # 숏 매크로에 buy 가 오면 실제 롱이 열리고 봇은 그걸 청산할 수 없다 — 주문 없이 거절.
                raise RuntimeError(f"매크로 방향과 맞지 않는 명령이에요: {action}")
            if uncertain_before:
                raise RuntimeError("포지션을 확인하지 못해 추가 주문을 보내지 않습니다. 거래소에서 주문과 포지션을 확인하세요.")
            if action in ("buy", "short"):
                notional = min(self.capital * float(cmd.get("notional_frac") or 0.0), order_cap(self.quote))
                qty, _ = _order_qty(price, self.step, 0, notional, self.leverage, self.market, quote=self.quote)
                if qty <= 0:
                    raise RuntimeError("주문 수량이 최소 단위보다 작아요.")
                word = "BUY" if action == "buy" else "SELL"
                self.log(f"[신호] {reason} → {word} {qty} {self.symbol} @ {price}")
                prev_qty, prev_entry = (self.held_qty, self.entry_price) if self.in_position else (0.0, 0.0)
                # 국내 진입은 수량이 아니라 방금 구한 금액으로 낸다 — 수량은 로그·장부 계산용이다.
                # 바이낸스 경로는 금액을 넘기지 않는다(수량 주문이고, 넘기면 _place 가 금액 주문으로 갈린다).
                extra = {"notional": notional} if self.domestic else {}
                if not self._place(word, qty, **extra):
                    raise RuntimeError("주문이 체결되지 않았어요.")
                filled_qty, filled_px = self._last_fill_qty, self._last_fill_price
                if prev_qty > 0 and filled_qty > 0:
                    # 추가 진입(그리드·마틴게일): _place 가 더한 수량으로 진입가를 가중평균한다.
                    added = self.held_qty - prev_qty
                    if added <= 0:
                        added = filled_qty
                        self.held_qty = prev_qty + added
                    self.entry_price = (prev_qty * prev_entry + added * filled_px) / self.held_qty
                    self.in_position = True
                ack.update(executed_qty=filled_qty, fill_price=filled_px)
            else:  # sell / cover
                if not self.in_position or self.held_qty <= 0:
                    self.log(f"[신호] {reason} → 보유 없음, 건너뜀")
                    self._done_command_ids.append(cid)
                    return ack
                frac = float(cmd.get("qty_frac") or 1.0)
                self.log(f"[신호] {reason} → {'전량' if frac >= 0.999 else f'{frac:.0%}'} 청산 @ {price}")
                if frac >= 0.999:
                    if not self._close_position():
                        raise RuntimeError("청산 주문이 체결되지 않았어요.")
                else:
                    qty = _round_step(self.held_qty * frac, self.step)
                    if qty <= 0:
                        raise RuntimeError("부분 청산 수량이 최소 단위보다 작아요.")
                    word = "SELL" if self.side == "long" else "BUY"
                    if not self._place(word, qty, reduce_only=(self.market == "futures")):
                        raise RuntimeError("부분 청산 주문이 체결되지 않았어요.")
                ack.update(executed_qty=self._last_fill_qty, fill_price=self._last_fill_price)
        except Exception as exc:
            if getattr(self, "position_uncertain", False) and not uncertain_before:
                # 이 주문으로 포지션이 불확실해졌다 — 명령을 계속 받을 상태가 아니다. 루프를 끝내고
                # 오류 상태로 종료 보고한다(사용자가 거래소에서 확인). 서버 명령은 만료로 정리된다.
                raise
            ack.update(ok=False, error=str(exc)[:200])
            self.log(f"  ⚠ 명령 실행 실패: {exc}")
        if ack["ok"]:
            self._done_command_ids.append(cid)
        return ack

    def _snapshot(self) -> dict:
        price = getattr(self, "last_price", 0.0)
        return {
            "in_position": self.in_position,
            "position_uncertain": getattr(self, "position_uncertain", False),
            "last_price": price,
            "entry_price": self.entry_price if self.in_position else 0.0,
            "position_qty": self.held_qty if self.in_position else getattr(self, "position_dust_qty", 0.0),
            "realized_pnl": self.realized,
            "unrealized_pct": _pnl_pct(self.entry_price, price, self.side) if self.in_position and price else 0.0,
            "note": "포지션 확인 필요 · 거래소에서 주문과 포지션을 확인하세요." if getattr(self, "position_uncertain", False) else self._dust_note(),
        }

    def _dust_note(self) -> str:
        dust = getattr(self, "position_dust_qty", 0.0)
        return f"최소 주문 단위 미만 잔여 수량 {dust:.12g} {self.symbol}" if dust else ""

    # --- 메인 루프 ----------------------------------------------
    def run(self) -> None:
        status = "stopped"
        note = ""
        try:
            if not self._connect() or not self._prepare():
                status, note = "error", "연결 · 심볼 준비 · 주문 전 확인 실패 — 로그 확인"
                return
            t = _strategy_targets(self.macro)
            guard = RiskGuard(t["risk"], t["capital"] * t["invest_ratio"] if t["capital"] else 0.0,
                              cap=order_cap(self.quote))
            self.log(f"공통 리스크: {guard.describe()} · 주문 상한 {order_cap(self.quote):,.0f} {self.quote} · "
                     f"{POLL_SECONDS:.0f}초마다 평가")

            while True:
                # 1) 종료 명령 확인 (로컬 또는 직전 원격)
                cmd = self._get_command()
                if cmd:
                    note = self._finish_position(cmd)
                    status = "error" if cmd == "close_and_stop" and self.in_position else "stopped"
                    return

                # 2) 시세
                try:
                    price = self._price()
                    self.last_price = price
                except Exception as exc:
                    self.log(f"  일시 오류(시세): {exc} — {POLL_SECONDS:.0f}초 후 재시도")
                    snapshot = {**self._snapshot(), "note": "시세 연결 재시도 중 · 마지막 확인 가격"}
                    reply = self.server.heartbeat(snapshot, acks=self.pending_acks)
                    self.pending_acks = []
                    action = reply.get("action", "continue")
                    if action in ("stop_only", "close_and_stop"):
                        self.set_command(action)
                    self._sleep(POLL_SECONDS)
                    continue
                guard.roll_day()

                # 3) 로컬 안전망 — 일일 손실·최대 보유시간(RiskGuard) + 손절. 진입 판단은 서버가 한다.
                if self.in_position:
                    unreal = _pnl_usdt(self.held_qty, self.entry_price, price, self.side)
                    forced, why = guard.force_close(unreal)
                    stop = self._local_stop_loss(price)
                    if forced or stop:
                        self.log(f"[{'강제청산: ' + why if forced else '손절'}] {price} (진입 {self.entry_price})")
                        entry_price, realized_before = self.entry_price, self.realized
                        if self._close_position():
                            pnl = self.realized - realized_before
                            # 돈은 이 세션의 호가 통화로 쓴다 — 실행기 창은 국내 실전 사용자가
                            # 실제로 보고 있는 화면이고, 원화 손익을 USDT 로 읽히게 두면 안 된다.
                            self.log(f"  손익 {_pnl_pct(entry_price, self._last_fill_price, self.side):+.2f}% "
                                     f"({_money(pnl, self.quote)}) · 누적 {_money(self.realized, self.quote)}")
                            guard.on_exit(pnl, was_stop=stop)

                # 4) 하트비트 — 상태·ack 를 올리고 명령을 받아 순서대로 실행
                snap = self._snapshot()
                self.on_status(snap)
                reply = self.server.heartbeat(snap, acks=self.pending_acks)
                self.pending_acks = []
                if reply.get("offline"):
                    if not self._offline_logged:
                        self.log("서버 연결 재시도 중 — 신호 대기(진입 없음, 손절만 로컬에서 봅니다)")
                        self._offline_logged = True
                else:
                    self._offline_logged = False
                action = reply.get("action", "continue")
                if action in ("stop_only", "close_and_stop"):
                    self.log(f"원격 종료 명령 수신: {action}")
                    self.set_command(action)
                else:
                    for cmd in reply.get("commands") or []:
                        kind = str(cmd.get("action", "")).lower()
                        is_entry = kind in ("buy", "short")
                        blocked, why = guard.entry_blocked()
                        if blocked and is_entry:
                            self.log(f"  ⏸ 진입 보류: {why}")
                            self.pending_acks.append({"command_id": cmd.get("id"), "ok": False, "executed_qty": 0.0,
                                                      "fill_price": 0.0, "error": f"로컬 리스크 보류: {why}"})
                            continue
                        realized_before, was_flat = self.realized, not self.in_position
                        ack = self._execute_command(cmd, price)
                        if ack["ok"] and ack["executed_qty"] > 0:
                            # 서버 명령의 결과도 로컬 안전망(일일 손실·보유시간)에 반영한다.
                            if is_entry:
                                if was_flat:  # 추가 진입은 보유시간 기준을 바꾸지 않는다
                                    guard.on_entry()
                            elif not self.in_position:
                                guard.on_exit(self.realized - realized_before, was_stop=False)
                            else:
                                guard.on_partial_exit(self.realized - realized_before)
                        self.pending_acks.append(ack)

                # 5) 대기(종료 명령 시 즉시 깨어남)
                self._sleep(POLL_SECONDS)
        except Exception as exc:
            status, note = "error", f"예기치 못한 오류: {exc}"
            self.log(note)
        finally:
            if self._dust_note():
                note = f"{note} · {self._dust_note()}"
            if self.server.stopped(status, note, snapshot=self._snapshot()) is False:
                note += " · 서버 종료 상태 전송 실패"
                self.log("서버에 종료 결과를 전송하지 못했어요. 내 에이전트의 상태가 지연될 수 있어요.")
            self.on_finish(status, note)

    def _build_start_payload(self, testnet: bool) -> dict:
        """이 세션을 서버에 알리는 payload. 거래소 키·시크릿은 들어가지 않는다.

        GUI(RunnerApp)도 같은 함수로 만든다 — 한쪽에만 거래소·모드가 실리면 마이페이지의
        세션 기록이 실행기와 어긋난다. 매크로 출처(서명 · user_macro_id)는 GUI 만 아는 값이라 거기서 얹는다.
        """
        return _session_payload(self.macro, testnet=testnet, mode=self.mode)

    def _finish_position(self, mode: str) -> str:
        """종료 시 포지션 처리. 반환값은 서버/화면에 남길 note."""
        if mode == "close_and_stop":
            if self.in_position:
                self.log("청산 후 종료 요청 — 보유 포지션을 정리합니다.")
                # A failed quote must not prevent a requested close. Keep the
                # last known price for the estimate; position confirmation comes
                # from the order result, not from this price.
                try:
                    self.last_price = self._price()
                except Exception:
                    pass
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
        protocol_launch: ProtocolLaunch | None = None,
        register_protocol: bool = False,
        startup_warning: str = "",
    ) -> None:
        self.root = root
        self.bot: BotThread | None = None
        self.macro: dict | None = None
        # Set only when the macro came from an authenticated web launch. A
        # manually opened file has no UserMacro row to associate with a run.
        self.user_macro_id: int | None = None
        # 파일에 동봉된 서명(`_sig`)과 출처. 서버가 서명을 검증해 원본/수정본을 가른다.
        self.macro_sig: dict | None = None
        self.macro_source = ""
        self.macro_path = tk.StringVar(value="")
        # 실행 모드 — 모의 · 테스트넷 · 실전. 기본은 늘 모의다.
        self.mode = tk.StringVar(value=MODE_MOCK)
        # 거래소별 키 칸. 바이낸스 칸은 옛 이름(api_key · api_secret)을 그대로 쓴다.
        self.api_key = tk.StringVar(value="")
        self.api_secret = tk.StringVar(value="")
        self.key_vars = {"binance": (self.api_key, self.api_secret)}
        for name in credentials_mod.EXCHANGES:
            if name not in self.key_vars:
                self.key_vars[name] = (tk.StringVar(value=""), tk.StringVar(value=""))
        self.member_key = tk.StringVar(value=os.environ.get("GGP_MEMBER_KEY", ""))
        # 이 PC에 키 기억하기 — DPAPI 파일이 있으면 칸을 채우고 체크를 켠다. 못 풀면(다른 PC) 빈 칸.
        self.remember = tk.BooleanVar(value=False)
        self.credentials_path = credentials_mod.default_path()
        self._remembered = credentials_mod.load(self.credentials_path) if credentials_mod.supported() else None
        self._apply_remembered_credentials(self._remembered)
        self.server_base = SERVER_BASE
        self._protocol_claim_busy = False
        self._protocol_registration_thread: threading.Thread | None = None
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        if startup_warning:
            self._log(startup_warning)
        if register_protocol:
            self.root.after(0, self._begin_protocol_registration)
        if protocol_launch:
            # Let Tk render its first frame before any launch work begins.  The
            # actual HTTP(S) claim runs on a worker thread below.
            self.root.after(0, self._begin_protocol_claim, protocol_launch)

    def _apply_remembered_credentials(self, remembered: dict | None) -> None:
        """저장된 자격증명(v2)을 거래소별 칸에 채운다.

        파일에는 키를 적어 둔 거래소만 들어 있다 — 없는 이름을 `[...]` 로 꺼내면 창이 아예 열리지 않는다.
        모르는 거래소 이름은 조용히 버린다(옛·새 형식이 섞여도 창은 열려야 한다).
        """
        if not remembered:
            return
        for name, pair in (remembered.get("exchanges") or {}).items():
            target = self.key_vars.get(name)
            if target and isinstance(pair, dict):
                target[0].set(str(pair.get("api_key") or ""))
                target[1].set(str(pair.get("api_secret") or ""))
        if remembered.get("member_key") and not self.member_key.get():
            self.member_key.set(remembered["member_key"])
        self.remember.set(True)

    # --- 화면 구성 ----------------------------------------------
    def _apply_theme(self) -> None:
        """ttk 'clam' 위에 껄무새 톤을 입힌다 — 노랑 포인트, 흰 카드, 연회색 바탕."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(bg=UI["bg"])
        style.configure(".", background=UI["bg"], foreground=UI["ink"], font=(FONT, 10))
        style.configure("Card.TFrame", background=UI["card"])
        style.configure("Card.TLabel", background=UI["card"], foreground=UI["ink"])
        style.configure("CardTitle.TLabel", background=UI["card"], foreground=UI["ink"], font=(FONT, 11, "bold"))
        style.configure("CardMuted.TLabel", background=UI["card"], foreground=UI["muted"], font=(FONT, 9))
        style.configure("Title.TLabel", background=UI["bg"], foreground=UI["ink"], font=(FONT, 16, "bold"))
        style.configure("Badge.TLabel", background=UI["brand"], foreground=UI["brand_ink"], font=(FONT, 9, "bold"), padding=(8, 2))
        style.configure("Status.TLabel", background=UI["bg"], foreground=UI["muted"], font=(FONT, 10, "bold"))
        style.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=UI["border"], lightcolor=UI["border"],
                        darkcolor=UI["border"], padding=6)
        style.configure("TCheckbutton", background=UI["card"], foreground=UI["ink"])
        style.map("TCheckbutton", background=[("active", UI["card"])])
        style.configure("Primary.TButton", background=UI["brand"], foreground=UI["brand_ink"], bordercolor=UI["brand"],
                        font=(FONT, 10, "bold"), padding=(14, 7))
        style.map("Primary.TButton", background=[("active", UI["brand_hover"]), ("disabled", "#FBE9A6")],
                  foreground=[("disabled", "#8A7A3A")])
        style.configure("Ghost.TButton", background=UI["card"], foreground=UI["ink"], bordercolor=UI["border"], padding=(12, 7))
        style.map("Ghost.TButton", background=[("active", "#F3F4F6"), ("disabled", UI["card"])],
                  foreground=[("disabled", "#B0B5BE")])
        style.configure("Danger.TButton", background=UI["card"], foreground=UI["danger"], bordercolor=UI["border"], padding=(12, 7))
        style.map("Danger.TButton", background=[("active", "#FEF2F2"), ("disabled", UI["card"])],
                  foreground=[("disabled", "#E8B4B4")])

    def _card(self, parent, title: str, desc: str = "") -> ttk.Frame:
        """흰 카드 한 장: 제목 + 한 줄 설명. 내용은 돌려주는 프레임에 붙인다."""
        outer = tk.Frame(parent, bg=UI["border"], padx=1, pady=1)  # 1px 테두리
        outer.pack(fill="x", padx=16, pady=(0, 10))
        card = ttk.Frame(outer, style="Card.TFrame", padding=(14, 10, 14, 12))
        card.pack(fill="x")
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
        if desc:
            ttk.Label(card, text=desc, style="CardMuted.TLabel").pack(anchor="w", pady=(0, 6))
        return card

    def _build(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("680x760")
        self.root.minsize(600, 620)
        self._apply_theme()

        # 헤더: 이름 · 버전 배지 · 상태
        head = ttk.Frame(self.root, padding=(16, 14, 16, 8))
        head.pack(fill="x")
        ttk.Label(head, text="🦜 껄무새 매크로 실행기", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=f"v{RUNNER_VERSION}", style="Badge.TLabel").pack(side="left", padx=(10, 0), pady=(3, 0))
        self.status_lbl = ttk.Label(head, text="● 대기 중", style="Status.TLabel", foreground=UI["muted"])
        self.status_lbl.pack(side="right")

        # 매크로
        f1 = self._card(self.root, "매크로", "웹에서 내려받은 .ggm.json 파일을 고르거나, 웹의 '실행기로 열기'로 바로 연결돼요.")
        row = ttk.Frame(f1, style="Card.TFrame"); row.pack(fill="x")
        ttk.Entry(row, textvariable=self.macro_path, state="readonly").pack(side="left", fill="x", expand=True)
        self.pick_btn = ttk.Button(row, text="파일 선택", style="Ghost.TButton", command=self._pick_file)
        self.pick_btn.pack(side="left", padx=(8, 0))
        self.macro_summary = ttk.Label(f1, text="아직 선택 안 됨", style="CardMuted.TLabel")
        self.macro_summary.pack(anchor="w", pady=(6, 0))

        # 실행 모드
        f2 = self._card(self.root, "실행 모드", "기본은 모의(주문을 보내지 않고 연습)예요. 실전은 직접 골라야 켜져요.")
        modes = ttk.Frame(f2, style="Card.TFrame"); modes.pack(fill="x")
        self.mode_buttons = {}
        for name in RUN_MODES:
            button = ttk.Radiobutton(modes, text=MODE_LABELS[name], value=name, variable=self.mode,
                                     command=self._on_mode_change)
            button.pack(side="left", padx=(0, 14))
            self.mode_buttons[name] = button
        self.live_note = ttk.Label(f2, text="", style="Card.TLabel", foreground=UI["ok"])
        self.live_note.pack(anchor="w", pady=(4, 0))
        self._on_mode_change()

        # 키 — 거래소별로 한 쌍. 어느 쌍을 쓸지는 매크로의 거래소가 고른다(사용자에게 묻지 않는다).
        f3 = self._card(self.root, "거래소 API 키 · 껄무새 회원 키",
                        "키는 이 PC에서만 쓰이고 서버로 보내지 않아요. 매크로의 거래소에 맞는 칸만 채우면 돼요.")
        grid = ttk.Frame(f3, style="Card.TFrame"); grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        rows = []
        for name in credentials_mod.EXCHANGES:
            key_var, secret_var = self.key_vars[name]
            rows.append((f"{exchange_label(name)} 키", key_var, ""))
            rows.append((f"{exchange_label(name)} 시크릿", secret_var, "•"))
        rows.append(("껄무새 회원 키", self.member_key, ""))
        for i, (label, var, show) in enumerate(rows):
            ttk.Label(grid, text=label, style="Card.TLabel", width=14).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(grid, textvariable=var, show=show).grid(row=i, column=1, sticky="ew", pady=3)
        if credentials_mod.supported():
            remember_row = ttk.Frame(f3, style="Card.TFrame"); remember_row.pack(fill="x", pady=(8, 0))
            ttk.Checkbutton(remember_row, text="이 PC에 키 기억하기 (Windows 계정으로 암호화)",
                            variable=self.remember).pack(side="left")
            ttk.Button(remember_row, text="저장된 키 지우기", style="Ghost.TButton",
                       command=self._forget_credentials).pack(side="right")

        # 실행/종료 버튼
        btns = ttk.Frame(self.root, padding=(16, 2, 16, 8))
        btns.pack(fill="x")
        self.start_btn = ttk.Button(btns, text="▶  매크로 시작", style="Primary.TButton", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="매크로만 종료", style="Ghost.TButton",
                                   command=lambda: self._stop("stop_only"), state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.close_btn = ttk.Button(btns, text="청산 후 종료", style="Danger.TButton",
                                    command=lambda: self._stop("close_and_stop"), state="disabled")
        self.close_btn.pack(side="left", padx=(8, 0))

        # 로그
        log_card = tk.Frame(self.root, bg=UI["border"], padx=1, pady=1)
        log_card.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        log_head = tk.Frame(log_card, bg=UI["log_bg"])
        log_head.pack(fill="x")
        tk.Label(log_head, text="실행 로그", bg=UI["log_bg"], fg="#94A3B8", font=(FONT, 9, "bold"),
                 padx=12, pady=6).pack(side="left")
        self.log_box = tk.Text(log_card, height=12, wrap="word", state="disabled", bd=0, highlightthickness=0,
                               bg=UI["log_bg"], fg=UI["log_fg"], font=("Consolas", 9), padx=12, pady=6,
                               spacing1=1, spacing3=1)
        self.log_box.pack(fill="both", expand=True)
        self.log_box.tag_configure("time", foreground="#64748B")
        self.log_box.tag_configure("signal", foreground="#93C5FD")
        self.log_box.tag_configure("order", foreground=UI["brand"])
        self.log_box.tag_configure("fill", foreground="#86EFAC")
        self.log_box.tag_configure("error", foreground="#FCA5A5")
        self.log_box.tag_configure("stop", foreground="#94A3B8")
        self.log_box.tag_configure("info", foreground=UI["log_fg"])

        if requests is None:
            self._log("⚠ 'requests' 모듈이 없어요. requirements.txt 를 설치해 주세요.")

    # --- 이벤트 -------------------------------------------------
    def _run_mode(self) -> str:
        return run_mode_of(self.mode.get())

    def _on_mode_change(self) -> None:
        """고른 모드를 한 줄로 설명한다. 실전만 빨강 — 돈이 움직이는 모드는 하나뿐이어야 보인다."""
        mode = self._run_mode()
        notes = {
            MODE_MOCK: ("현재: 모의 (주문을 보내지 않고 현재가로 연습해요)", UI["ok"]),
            MODE_TESTNET: ("현재: 테스트넷 (바이낸스 가짜 자금)", UI["ok"]),
            MODE_LIVE: ("현재: ⚠ 실전 (실제 자금이 움직여요)", UI["danger"]),
        }
        text, color = notes[mode]
        self.live_note.config(text=text, foreground=color)

    def _sync_mode_choices(self) -> None:
        """국내 매크로에서는 테스트넷 칸을 숨긴다 — 업비트·빗썸에는 테스트넷이 없다.

        숨기는 것만으로는 모자라서, 이미 테스트넷이 골라져 있었으면 모의로 되돌린다.
        """
        button = getattr(self, "mode_buttons", {}).get(MODE_TESTNET)
        if button is None:
            return
        allowed = exchange_of(self.macro) in TESTNET_EXCHANGES if self.macro else True
        if allowed:
            # winfo_manager() 가 빈 문자열이면 pack 에서 빠진 상태다. winfo_ismapped() 로 보면
            # 창이 아직 화면에 뜨지 않았을 때(숨은 창)도 '빠졌다' 로 읽혀 매번 다시 붙인다.
            if not button.winfo_manager():
                button.pack(side="left", padx=(0, 14), before=self.mode_buttons[MODE_LIVE])
            return
        button.pack_forget()
        if self._run_mode() == MODE_TESTNET:
            self.mode.set(MODE_MOCK)
        self._on_mode_change()

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
            self._apply_local_macro(macro, path)
        except ValueError:
            messagebox.showerror(APP_TITLE, "올바른 껄무새 매크로 파일이 아니에요.")
            return

    def _apply_local_macro(self, macro: dict, source_label: str) -> None:
        """Apply a file-backed macro without associating it with UserMacro."""

        # Clear first so a failed/manual replacement can never submit the ID
        # of a previously claimed web macro with different strategy contents.
        self.user_macro_id = None
        self.macro_sig = None
        self.macro_source = "file"
        self._render_macro_selection(macro, source_label)
        sig = macro.get("_sig") if isinstance(macro, dict) else None
        self.macro_sig = dict(sig) if isinstance(sig, dict) else None
        if self.macro_sig is None:
            self._log("이 매크로 파일엔 서명이 없어요(옛 파일). 서버에는 '서명 없는 파일'로 남아요.")

    def _apply_claimed_macro(
        self,
        macro: dict,
        source_label: str,
        user_macro_id: int | None,
    ) -> None:
        """Apply a web-claimed macro and retain its optional UserMacro ID."""

        self.user_macro_id = None
        self.macro_sig = None
        self.macro_source = "web"
        if user_macro_id is not None and (
            isinstance(user_macro_id, bool)
            or not isinstance(user_macro_id, int)
            or user_macro_id <= 0
        ):
            raise ValueError("invalid user macro id")
        self._render_macro_selection(macro, source_label)
        self.user_macro_id = user_macro_id

    def _render_macro_selection(self, macro: dict, source_label: str) -> None:
        """Validate and render macro fields shared by both selection sources."""

        if not isinstance(macro, dict) or not str(macro.get("symbol", "")).strip():
            raise ValueError("invalid macro")
        try:
            lev = max(1, int(macro.get("leverage", 1) or 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid macro leverage") from exc
        self.macro = {k: v for k, v in macro.items() if k != "_sig"}
        self.macro_path.set(source_label)
        side = str(macro.get("position_side", "long"))
        exchange = exchange_of(macro)
        market = market_of(exchange, side.lower(), lev)
        summary = macro.get("human_summary", "") or f"{macro['symbol']} · {side}"
        self.macro_summary.config(
            text=f"{exchange_label(exchange)} · {macro['symbol']} · {market} · "
                 f"{side}{' · '+str(lev)+'배' if lev>1 else ''}\n{summary}")
        # 거래소가 바뀌면 고를 수 있는 모드도 바뀐다(국내는 테스트넷이 없다).
        self._sync_mode_choices()

    def _begin_protocol_claim(self, launch: ProtocolLaunch) -> None:
        """Claim a browser launch ticket without blocking Tk's event loop."""

        if self.bot is not None:
            self._log("현재 매크로가 실행 중이에요. 종료한 뒤 사이트에서 다시 연결해 주세요.")
            try:
                self.root.bell()
            except tk.TclError:
                pass
            return
        if self._protocol_claim_busy:
            self._log("이미 웹 매크로를 연결하고 있어요. 잠시만 기다려 주세요.")
            return
        # The old ID must not survive while a replacement claim is pending or
        # after that claim fails. The old macro may remain visible, but it will
        # then start as an unassociated/manual run rather than a wrong one.
        self.user_macro_id = None
        self._protocol_claim_busy = True
        if requests is None:
            self._protocol_claim_failed()
            return
        self.status_lbl.config(text="● 웹 연결 확인 중…", foreground=UI["warn"])
        self.start_btn.config(state="disabled")
        self.pick_btn.config(state="disabled")
        self._log("웹에서 선택한 매크로를 안전하게 연결하고 있어요.")
        threading.Thread(
            target=self._claim_protocol_ticket,
            args=(launch,),
            daemon=True,
            name="ggparrot-launch-claim",
        ).start()

    def _begin_protocol_registration(self) -> None:
        """Install the per-user URI handler off the Tk main thread."""

        self.status_lbl.config(text=f"● v{RUNNER_VERSION} 연결 준비 중…", foreground=UI["warn"])
        self._log(f"브라우저 빠른 연결을 v{RUNNER_VERSION}로 준비하고 있어요.")
        self._protocol_registration_thread = threading.Thread(
            target=self._register_protocol_worker,
            # Registration must finish even if the user closes the first
            # launch window immediately after opening the downloaded runner.
            daemon=False,
            name="ggparrot-protocol-install",
        )
        self._protocol_registration_thread.start()

    def _register_protocol_worker(self) -> None:
        success = _install_protocol_handler_for_current_user()
        try:
            self.root.after(
                0,
                self._finish_protocol_registration,
                success,
            )
        except (RuntimeError, tk.TclError):
            # The non-daemon worker still completed the durable registration;
            # there is simply no longer a Tk window in which to report it.
            pass

    def _finish_protocol_registration(self, success: bool) -> None:
        if success:
            self.status_lbl.config(
                text=f"● 브라우저 연결 준비됨 · v{RUNNER_VERSION}",
                foreground=UI["ok"],
            )
            self._log(f"브라우저 빠른 연결 준비가 끝났어요. (v{RUNNER_VERSION})")
            return
        self.status_lbl.config(text="● 브라우저 연결 준비 실패", foreground=UI["danger"])
        self._log("브라우저 빠른 연결을 준비하지 못했지만 실행기는 그대로 사용할 수 있어요.")

    def _claim_protocol_ticket(self, launch: ProtocolLaunch) -> None:
        claim_base = (
            LOCAL_SERVER_BASE
            if launch.environment == "local"
            else SERVER_BASE
        )
        try:
            response = requests.post(
                f"{claim_base}{PROTOCOL_CLAIM_PATH}",
                json={"ticket": launch.ticket, "runner_version": RUNNER_VERSION},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            macro = payload.get("macro") if isinstance(payload, dict) else None
            runner_key = payload.get("runner_key") if isinstance(payload, dict) else None
            user_macro_id = payload.get("user_macro_id") if isinstance(payload, dict) else None
            if (
                not isinstance(macro, dict)
                or not str(macro.get("symbol", "")).strip()
                or not isinstance(runner_key, str)
                or not runner_key.strip()
                or (
                    user_macro_id is not None
                    and (
                        isinstance(user_macro_id, bool)
                        or not isinstance(user_macro_id, int)
                        or user_macro_id <= 0
                    )
                )
            ):
                raise ValueError("invalid launch response")
        except Exception:
            # Do not surface exception text: HTTP/proxy errors occasionally
            # include request data, and launch tickets/account keys must never
            # be copied into the GUI log.
            self.root.after(0, self._protocol_claim_failed)
            return
        self.root.after(
            0,
            self._apply_protocol_claim,
            macro,
            runner_key,
            claim_base,
            user_macro_id,
        )

    def _apply_protocol_claim(
        self,
        macro: dict,
        runner_key: str,
        claim_base: str,
        user_macro_id: int | None = None,
    ) -> None:
        self._protocol_claim_busy = False
        if self.bot is not None:
            # The request was accepted while idle, but a local start may have
            # completed before the network response arrived. Never replace
            # the strategy/account fields underneath an active bot.
            self.user_macro_id = None
            self._log("매크로가 실행 중이어서 새 웹 연결을 적용하지 않았어요.")
            self._set_running(True)
            return
        try:
            self._apply_claimed_macro(
                macro,
                "웹에서 연결한 내 매크로",
                user_macro_id,
            )
        except ValueError:
            self._protocol_claim_failed()
            return

        # A web launch may only prepare the form.  Exchange credentials never
        # arrive through the URI/server, the run mode is restored explicitly, and
        # _start() is intentionally not called here.
        # 기억해 둔 거래소 키가 있으면 그대로 두고, 없으면 빈 칸(웹 연결은 키를 실어 오지 않는다).
        if not self._remembered:
            for key_var, secret_var in self.key_vars.values():
                key_var.set("")
                secret_var.set("")
        self.member_key.set(runner_key.strip())
        self.server_base = claim_base
        self.mode.set(MODE_MOCK)
        self._on_mode_change()
        self.pick_btn.config(state="normal")
        self.start_btn.config(state="normal")
        self.status_lbl.config(text="● 웹 연결됨 · 시작 전", foreground=UI["ok"])
        self._log("웹 매크로와 껄무새 계정을 연결했어요. 실행 모드(기본 모의)를 확인한 뒤 직접 시작해 주세요.")
        _bring_window_to_front(self.root)

    def _protocol_claim_failed(self) -> None:
        self._protocol_claim_busy = False
        self.user_macro_id = None
        running = self.bot is not None
        self.pick_btn.config(state="disabled" if running else "normal")
        self.start_btn.config(state="disabled" if running else "normal")
        self.status_lbl.config(text="● 웹 연결 실패", foreground=UI["danger"])
        self._log("웹 연결 요청을 확인하지 못했어요. 사이트로 돌아가 다시 시도해 주세요.")

    def handle_external_activation(self, launch: ProtocolLaunch | None) -> None:
        """Handle a second executable invocation inside the existing UI."""

        _bring_window_to_front(self.root)
        if launch is None:
            return
        if self.bot is not None:
            self._log("현재 매크로가 실행 중이에요. 종료한 뒤 사이트에서 새로 연결해 주세요.")
            try:
                self.root.bell()
            except tk.TclError:
                pass
            return
        self._begin_protocol_claim(launch)

    def _on_window_close(self) -> None:
        if self.bot is not None:
            _bring_window_to_front(self.root)
            messagebox.showwarning(
                APP_TITLE,
                "매크로가 실행 중이에요.\n'매크로만 종료' 또는 '청산 후 종료'를 먼저 눌러 주세요.",
            )
            return
        self.root.destroy()

    def _start(self) -> None:
        if self.bot is not None:
            messagebox.showwarning(APP_TITLE, "이미 매크로가 실행 중이에요.")
            return
        if self._protocol_claim_busy:
            messagebox.showwarning(APP_TITLE, "웹 매크로 연결이 끝날 때까지 잠시만 기다려 주세요.")
            return
        if requests is None:
            messagebox.showerror(APP_TITLE, "'requests' 모듈이 필요해요. requirements 설치 후 실행하세요.")
            return
        if not self.macro:
            messagebox.showwarning(APP_TITLE, "먼저 매크로 파일을 선택하세요.")
            return
        mode = self._run_mode()
        exchange = exchange_of(self.macro)
        label = exchange_label(exchange)
        if not known_exchange(exchange):
            # 서버에 세션을 만들기 전에 막는다 — 서버는 정해진 세 값만 받아서, 여기서 넘기면
            # 사용자는 거래소 이름 대신 '서버 연결 실패' 만 보게 된다.
            messagebox.showwarning(
                APP_TITLE,
                f"이 매크로의 거래소({exchange})는 이 실행기가 모르는 거래소예요.\n\n"
                f"쓸 수 있는 거래소: {' · '.join(exchange_label(name) for name in KNOWN_EXCHANGES)}.\n"
                "웹에서 매크로를 다시 받아 주세요.")
            return
        if mode == MODE_TESTNET and exchange not in TESTNET_EXCHANGES:
            # 국내 거래소에는 테스트넷이 없다 — 실전으로 올려 버리지 않고 모의로 접는다.
            mode = MODE_MOCK
            self.mode.set(mode)
            self._on_mode_change()
            self._log(f"{label} 는 테스트넷이 없어요. 모의 모드로 바꿨어요.")
        # 쓰는 키는 매크로의 거래소가 고른다. 다른 거래소 칸이 비어 있어도 시작을 막지 않는다.
        # 모의는 키 없이도 돌아야 하므로(주문을 보내지 않는다) 이 관문을 지나간다.
        if mode != MODE_MOCK and credential_pair(self._credential_values(), exchange) is None:
            messagebox.showwarning(
                APP_TITLE,
                f"이 매크로는 {label} 매크로예요.\n\n{label} API Key/Secret 을 입력하세요.")
            return
        if not self.member_key.get().strip():
            messagebox.showwarning(APP_TITLE, "껄무새 회원 키를 입력하세요.")
            return

        testnet = mode != MODE_LIVE
        if mode == MODE_LIVE:  # 실전: 실제 자금 확인
            side = str(self.macro.get("position_side", "long"))
            if not messagebox.askyesno(
                APP_TITLE,
                f"⚠ 실전({label})으로 실행합니다.\n\n실제 자금으로 주문이 실행돼요. "
                f"({self.macro.get('symbol')} · {side})\n계속할까요?"):
                return

        self._persist_credentials()
        server = ServerClient(self.member_key.get(), base=self.server_base)
        payload = self._build_start_payload(testnet, mode)
        try:
            started = server.start(payload)
        except Exception as exc:
            msg = str(exc)
            if "401" in msg:
                msg = "회원 키가 유효하지 않아요. 마이페이지에서 키를 확인하세요."
            messagebox.showerror(APP_TITLE, f"서버 연결 실패:\n{msg}")
            return

        self._log(f"세션 시작 (id={server.session_id}) · {label} · {payload['symbol']} · "
                  f"{payload['market']} · {MODE_LABELS[mode]}")
        origin_label = str((started.get("macro_origin_label") if isinstance(started, dict) else "") or "")
        if origin_label:
            self._log(f"매크로 출처: {origin_label}" + (f" · 지문 {started.get('macro_digest')}" if started.get("macro_digest") else ""))
        if server.macro_origin == "file_modified":
            # 웹에서 받은 뒤 손으로 고친 파일. 돌리는 건 막지 않되, 문의 시 지원 대상이
            # 아니라는 걸 사용자도 알게 한다(서버 세션에도 '수정된 파일'로 남는다).
            self._log("⚠ 이 매크로 파일은 웹에서 받은 원본과 달라요. 로컬에서 수정된 설정으로 실행돼요.")
            messagebox.showwarning(
                APP_TITLE,
                "이 매크로 파일은 껄무새에서 받은 원본과 내용이 달라요.\n\n"
                "로컬에서 수정한 설정 그대로 실행되며, 내 에이전트 화면에 '수정된 파일'로 표시돼요.\n"
                "웹 설정과 다르게 동작해도 껄무새의 오류가 아닐 수 있어요.",
            )
        self.bot = BotThread(
            self.macro, self._credential_values(), mode, server,
            on_log=self._log_threadsafe,
            on_status=self._status_threadsafe,
            on_finish=self._finish_threadsafe,
        )
        self.bot.start()
        self._set_running(True)

    def _credential_values(self) -> dict:
        """지금 칸에 적힌 값을 자격증명 v2 모양으로 모은다 — 저장과 봇이 같은 모양을 본다."""
        exchanges = {}
        for name, (key_var, secret_var) in self.key_vars.items():
            key, secret = key_var.get().strip(), secret_var.get().strip()
            if key or secret:
                exchanges[name] = {"api_key": key, "api_secret": secret}
        return {"version": 2, "member_key": self.member_key.get().strip(), "exchanges": exchanges}

    def _persist_credentials(self) -> None:
        """'기억하기' 체크대로 저장/삭제. 실패해도 매매를 막지 않는다(로그만)."""
        if not credentials_mod.supported():
            return
        try:
            values = self._credential_values()
            credentials_mod.apply_choice(self.credentials_path, bool(self.remember.get()), values)
            self._remembered = values if self.remember.get() else None
            if self.remember.get():
                self._log("키를 이 PC에 저장했어요 (Windows 계정으로 암호화 · 서버 전송 없음).")
        except Exception as exc:
            self._log(f"⚠ 키 저장에 실패했어요: {exc}")

    def _forget_credentials(self) -> None:
        credentials_mod.clear(self.credentials_path)
        self._remembered = None
        self.remember.set(False)
        for key_var, secret_var in self.key_vars.values():
            key_var.set("")
            secret_var.set("")
        self._log("저장된 키를 지웠어요.")

    def _build_start_payload(self, testnet: bool, mode: str) -> dict:
        """Build the server payload without ever including exchange secrets.

        세션 부분은 BotThread 와 같은 함수(_session_payload)가 만든다. 여기서는 GUI 만 아는
        매크로 출처(파일 서명 · user_macro_id)를 얹는다.
        mode 에 기본값을 두지 않는다 — 빼먹은 호출이 실전 세션을 '모의' 로 기록하게 된다.
        """
        payload = _session_payload(self.macro, testnet=testnet, mode=mode)
        if self.user_macro_id is not None:
            payload["user_macro_id"] = self.user_macro_id
        # 파일 서명 — 서버가 검증해 세션 출처(원본/수정본)를 남긴다. 티켓 경로엔 없다.
        macro_sig = getattr(self, "macro_sig", None)
        if macro_sig is not None:
            payload["macro_sig"] = macro_sig
        macro_source = getattr(self, "macro_source", "")
        if macro_source:
            payload["macro_source"] = macro_source
        return payload

    def _stop(self, mode: str) -> None:
        if not self.bot:
            return
        label = "청산 후 종료" if mode == "close_and_stop" else "매크로만 종료"
        if not messagebox.askyesno(APP_TITLE, f"{label} 할까요?"):
            return
        self.bot.set_command(mode)
        self.status_lbl.config(text="● 종료 처리 중…", foreground=UI["warn"])

    # --- 스레드-세이프 콜백 (GUI 는 메인스레드에서만 갱신) --------
    def _log_threadsafe(self, msg: str) -> None:
        self.root.after(0, self._log, msg)

    def _status_threadsafe(self, snap: dict) -> None:
        self.root.after(0, self._render_status, snap)

    def _finish_threadsafe(self, status: str, note: str) -> None:
        self.root.after(0, self._on_finish, status, note)

    def _log(self, msg: str) -> None:
        tag, icon = _log_style(msg)
        self.log_box.config(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S  "), ("time",))
        text = msg.strip()
        if text.startswith(icon):  # 메시지가 이미 같은 기호로 시작하면 두 번 붙이지 않는다
            text = text[len(icon):].strip()
        self.log_box.insert("end", f"{icon} {text}\n", (tag,))
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _render_status(self, snap: dict) -> None:
        pos = "보유" if snap.get("in_position") else "무포지션"
        self.status_lbl.config(
            text=f"● 실행 중 · {snap.get('last_price', 0):g} · {pos} · "
                 f"누적 {_money(snap.get('realized_pnl', 0) or 0.0, self._quote())}",
            foreground=UI["ok"])

    def _quote(self) -> str:
        """화면에 쓰는 호가 통화. 돌고 있는 봇이 정한 값을 먼저 쓰고, 없으면 고른 매크로의 심볼에서 읽는다.

        봇과 따로 계산하지 않는다 — 두 곳에서 세면 원화 세션의 창에 USDT 가 남는다.
        """
        bot_quote = getattr(getattr(self, "bot", None), "quote", "")
        return bot_quote or quote_of(str((self.macro or {}).get("symbol") or ""))

    def _on_finish(self, status: str, note: str) -> None:
        self._log(f"종료됨 ({status}){' · ' + note if note else ''}")
        self.status_lbl.config(text=f"● 종료됨 · {note or status}", foreground=UI["muted"])
        self._set_running(False)
        self.bot = None

    def _set_running(self, running: bool) -> None:
        self.start_btn.config(state="disabled" if running else "normal")
        self.pick_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        self.close_btn.config(state="normal" if running else "disabled")


def main() -> None:
    args = sys.argv[1:]
    protocol_launch = None
    startup_warning = ""
    protocol_requested = "--protocol" in args
    try:
        protocol_launch = parse_protocol_launch(args)
    except ProtocolLaunchError:
        # Never echo the malformed URI: it can contain secrets supplied by an
        # untrusted page.  Open the ordinary UI and explain the safe recovery.
        startup_warning = "올바르지 않은 웹 연결 요청은 무시했어요. 사이트에서 다시 시도해 주세요."

    instance: RunnerSingleInstance | None = None
    # The Windows shell must create a short-lived process to deliver a custom
    # URI. v5 forwards that request through an authenticated named pipe and
    # exits before Tk is constructed, leaving exactly one visible runner.
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            instance = RunnerSingleInstance.acquire()
        except SingleInstanceError:
            _show_native_runner_error(
                "실행기 연결 통로를 준비하지 못했어요. 열려 있는 실행기를 모두 닫은 뒤 다시 열어 주세요."
            )
            return

        if instance is not None and not instance.is_primary:
            # A user may manually reopen v5 after an older executable has
            # overwritten the HKCU protocol handler. Repair registration even
            # though this helper process will hand off to the existing v5 UI.
            registration_repaired = True
            if not protocol_requested:
                registration_repaired = _install_protocol_handler_for_current_user()
            command = (
                InstanceCommand.launch_protocol(protocol_launch)
                if protocol_launch is not None
                else InstanceCommand.activate()
            )
            try:
                acknowledgement = instance.handoff(command)
            except SingleInstanceError:
                _show_native_runner_error(
                    "이미 열린 실행기에 연결하지 못했어요. 기존 창을 직접 확인하거나 모든 실행기를 닫은 뒤 다시 열어 주세요."
                )
                return
            if not acknowledgement.accepted:
                _show_native_runner_error(
                    "이미 열린 실행기가 새 요청을 받을 수 없어요. 잠시 후 다시 시도해 주세요."
                )
            elif not registration_repaired:
                _show_native_runner_error(
                    "기존 실행기 창은 열었지만 웹 연결 등록을 갱신하지 못했어요. 모든 실행기를 닫은 뒤 v5를 다시 열어 주세요."
                )
            return

    root = tk.Tk()
    try:
        # 고해상도 화면 선명하게 (Windows)
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = RunnerApp(
        root,
        protocol_launch=protocol_launch,
        register_protocol=not protocol_requested and protocol_launch is None,
        startup_warning=startup_warning,
    )

    if instance is not None:
        def drain_instance_commands() -> None:
            while True:
                try:
                    command = instance.get_command_nowait()
                except queue.Empty:
                    break
                app.handle_external_activation(command.launch)
            try:
                root.after(80, drain_instance_commands)
            except tk.TclError:
                pass

        root.after(0, drain_instance_commands)

    try:
        root.mainloop()
    finally:
        if instance is not None:
            instance.close()


if __name__ == "__main__":
    main()
