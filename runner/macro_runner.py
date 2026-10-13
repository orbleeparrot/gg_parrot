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

import dataclasses
import json
import inspect
import os
import queue
import shutil
import sys
import threading
import time
import uuid
import webbrowser
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
    from . import assets
    from . import brokers
    from . import connection
    from . import secret_entry
    from . import setup_progress
    from .connection_wizard import ExchangeConnectionWizard
    from .theme import FONT, UI
except ImportError:
    import assets
    import brokers
    import connection
    import secret_entry
    import setup_progress
    from connection_wizard import ExchangeConnectionWizard
    from theme import FONT, UI

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
# 실전 선택 칸 — 빨강은 삭제 버튼처럼 보여 초록(흰 글자 대비 5:1)으로 둔다.
LIVE_SELECTED = "#15803D"
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


# --- 화면 색·글꼴은 theme.py (연결 도우미와 같이 쓴다) ------------------------
# 거래소별 키 칸 이름 — 거래소 발급 창에 적힌 이름 그대로(업비트 Access Key, 빗썸·바이낸스 API Key).
KEY_FIELD_LABELS = {"upbit": ("Access Key", "Secret Key"), "bithumb": ("API Key", "Secret Key"),
                    "binance": ("API Key", "Secret Key")}
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


# 업비트 · 빗썸 매수 수수료를 못 읽었을 때의 보수적인 값 — 브로커의 잔고 확인과 같은 값이어야 한다.
DOMESTIC_BID_FEE_FALLBACK = brokers.DOMESTIC_BID_FEE_FALLBACK


def _bid_fee_rate(broker) -> float:
    """매수 수수료율. 모의 래퍼는 안쪽 브로커의 값을 본다. 못 읽었거나 말이 안 되는 값이면 보수적인 기본값."""
    for holder in (broker, getattr(broker, "_inner", None)):
        fees = getattr(holder, "fees", None)
        if isinstance(fees, dict):
            rate = _positive(fees.get("bid"))
            if 0 < rate < 0.05:
                return rate
    return DOMESTIC_BID_FEE_FALLBACK


_fee_inclusive = brokers.fee_inclusive  # 리허설(브로커)과 실제 주문이 같은 식을 쓴다


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
START_ATTEMPTS = 4
UPDATE_BG = "#FEF3C7"
# 새 버전 받기는 공식 릴리스 주소로만 연다 — 서버 응답에 다른 주소가 와도 따라가지 않는다.
OFFICIAL_RELEASE_PREFIX = "https://github.com/orbleeparrot/gg_parrot/releases/download/runner-v"


def newer_release(info: dict | None, current: str) -> tuple[str, str] | None:
    """다운로드 정보가 이 실행기보다 새 공식 릴리스를 가리키면 (버전, 주소). 아니면 None."""
    if not isinstance(info, dict) or not info.get("available"):
        return None
    version, url = str(info.get("version") or ""), str(info.get("url") or "")
    if not (version.isdigit() and str(current).isdigit()) or int(version) <= int(current):
        return None
    if not url.startswith(OFFICIAL_RELEASE_PREFIX + version + "/"):
        return None
    return version, url
LOG_LINE = "#1E293B"
LOG_SCROLL_STYLE = "Log.Vertical.TScrollbar"


def _style_log_scrollbar(style: ttk.Style, scale: float = 1.0) -> None:
    """실행 로그 스크롤바 — 화살표 없는 얇은 막대, 바탕은 로그와 같은 남색, 손잡이는 짙은 회색."""
    style.layout(LOG_SCROLL_STYLE, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure(LOG_SCROLL_STYLE, troughcolor=UI["log_bg"], background="#334155", bordercolor=UI["log_bg"],
                    lightcolor="#334155", darkcolor="#334155", gripcount=0, relief="flat",
                    arrowsize=max(6, round(8 * scale)))  # clam 은 막대 두께를 arrowsize 로 정한다
    style.map(LOG_SCROLL_STYLE, background=[("pressed", "#64748B"), ("active", "#475569")],
              lightcolor=[("pressed", "#64748B"), ("active", "#475569")],
              darkcolor=[("pressed", "#64748B"), ("active", "#475569")])
LOG_MIN_LINES = 5


class ServerRequestError(Exception):
    """서버가 거절한 요청 — 화면에는 서버가 준 이유(detail)만 보인다(주소 · 헤더는 싣지 않는다)."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(f"{detail} ({status})" if detail else f"서버가 요청을 받지 않았어요 ({status})")


def _server_detail(response) -> str:
    try:
        detail = response.json().get("detail")
    except Exception:
        return ""
    return detail.strip()[:200] if isinstance(detail, str) else ""


def _retry_after_seconds(response) -> float:
    try:
        value = float((getattr(response, "headers", None) or {}).get("Retry-After", 1))
    except (TypeError, ValueError):
        value = 1.0
    return min(max(value, 0.5), 3.0)


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
        # 서버가 '혼잡'(503 database_busy · 429)이면 Retry-After 만큼 쉬고 다시 보낸다. 서버의 시작은 한 트랜잭션이고
        # 혼잡 503 은 DB 연결을 받기 전에 나므로 다시 보내도 세션이 두 번 생기지 않는다.
        for attempt in range(START_ATTEMPTS):
            r = requests.post(f"{self.base}/api/runner/start", json=payload,
                              headers=self._headers, timeout=15)
            status = getattr(r, "status_code", 200)
            if not isinstance(status, int) or status < 400:
                break
            if status in (429, 503) and attempt + 1 < START_ATTEMPTS:
                time.sleep(_retry_after_seconds(r))
                continue
            raise ServerRequestError(status, _server_detail(r))
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
        self._preflight = None

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
            prepared = getattr(self, "_preflight", None)
            broker = prepared.broker if prepared and prepared.broker is not None else self._build_broker(pair)
            if prepared and prepared.broker is not None:
                broker.log = self.log
                if isinstance(broker, brokers.BinanceBroker):
                    # The bounded check timeout must not change live-order HTTP behavior.
                    broker.raw._requests_params = None
        except ImportError:
            self.log("python-binance 가 없어요. requirements 설치 후 다시 실행하세요.")
            return False
        except Exception as exc:
            reason = connection.safe_preflight_reason(str(exc), self.credentials)
            self.log(f"{label} 연결 준비 실패: {reason}")
            return False
        # 모의는 바깥에서 감싼다 — 어느 거래소든 같은 안전망을 쓰게 하려는 것이 MockBroker 의 존재 이유다.
        self.broker = brokers.MockBroker(broker, log=self.log) if self.mode == MODE_MOCK else broker
        self.log(f"거래소 {label} · 실행 모드 {MODE_LABELS[self.mode]}"
                 f"{' (테스트넷 주소)' if self.testnet else ''}")
        return True

    def _build_broker(self, pair: dict, *, preflight_only=False):
        """거래소별 어댑터 하나. 바이낸스만 python-binance 클라이언트를 쓴다."""
        if self.exchange == "binance":
            from binance.client import Client
            client_class, options = Client, {}
            if preflight_only:
                options["requests_params"] = {"timeout": (3, 8)}
                if "ping" in inspect.signature(Client.__init__).parameters:
                    options["ping"] = False
                else:
                    # Supported old SDKs (including 1.0.19) unconditionally call
                    # self.ping() in __init__. Override on this LOCAL subclass,
                    # never monkeypatch the shared class used by live clients.
                    class ConnectionCheckClient(Client):
                        def __init__(self, *args, **kwargs):
                            self._ggp_constructing = True
                            try:
                                super().__init__(*args, **kwargs)
                            finally:
                                self._ggp_constructing = False

                        def ping(self):
                            if self._ggp_constructing:
                                return {}
                            return super().ping()
                    client_class = ConnectionCheckClient
            return brokers.BinanceBroker(
                client_class(pair["api_key"], pair["api_secret"], testnet=self.testnet, **options),
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
        if order.status == "REJECTED" and not executed and self.domestic:
            # 거래소가 오류 이름을 달아 거절했다 — 주문은 나가지 않았다. '확인하지 못했다' 가 아니라 이유를 말한다
            # (예: 빗썸 insufficient_funds → 원화 잔고가 모자라요).
            raise RuntimeError(brokers._explain_domestic_error(self.exchange, 400, str(order.raw_status or ""), ""))
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
        notional = self._entry_budget()
        # 국내는 실제 주문과 같은 금액(수수료를 뺀 금액)으로 최소 주문 금액을 확인한다.
        return _fee_inclusive(notional, _bid_fee_rate(getattr(self, "broker", None))) if self.domestic else notional

    def _rehearsal_kwargs(self) -> dict:
        """리허설 인자. 국내는 수수료 포함 설정 금액(spend)도 준다 — 브로커가 실제 수수료율을 읽고 실제 주문 금액 ·
        필요한 원화를 계산한다(여기서는 아직 수수료율을 모를 수 있어 보수적인 기본값으로 줄인 금액만 안다)."""
        kwargs = {"notional": self._entry_notional()}
        if self.domestic:
            kwargs["spend"] = self._entry_budget()
        return kwargs

    def _entry_budget(self) -> float:
        """진입 한 건에 쓸 설정 금액(국내는 수수료 포함) — 가장 작은 진입. 아래 _entry_notional 의 수수료 전 금액."""
        return _smallest_entry_notional(self.macro, self.leverage, self.market, quote=self.quote)

    def _rehearse(self) -> bool:
        """세션 시작에 한 번. 돈을 쓰지 않고 키 · 허용 IP · 권한 · 최소 주문 금액을 확인한다.

        브로커는 결과만 돌려주고, 멈출지 말지는 여기서 정한다:
          * 실전 · 테스트넷 — 실패하면 주문을 한 건도 내지 않고 세션을 끝낸다.
          * 모의            — 실패를 기록만 하고 계속한다. 키 없이 돌지 않으면 연습이 아니다.
        """
        prepared = getattr(self, "_preflight", None)
        if prepared and prepared.matches(connection.preflight_fingerprint(self.macro, self.mode, self.credentials)):
            self._preflight = None
            self.log(f"주문 전 확인: {prepared.reason}")
            return True
        credentials = getattr(self, "credentials", {})
        try:
            ok, reason = self.broker.rehearse(**self._rehearsal_kwargs())
        except Exception as exc:
            ok, reason = False, f"리허설 도중 오류가 났어요: {connection.safe_preflight_reason(str(exc), credentials)}"
        reason = connection.safe_preflight_reason(reason, credentials)
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
                if self.domestic:
                    # 업비트 · 빗썸은 수수료가 주문 금액과 따로 원화에서 빠진다 — 설정한 금액(예: 20,000원) 안에서
                    # 수수료까지 끝나도록 주문 금액을 줄인다. 바이낸스는 받은 코인에서 떼므로 그대로다.
                    notional = _fee_inclusive(notional, _bid_fee_rate(getattr(self, "broker", None)))
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
def perform_connection_check(macro: dict, credentials: dict, mode: str) -> connection.PreflightResult:
    """Safe standalone check: never create a server run, configure futures or submit an order."""
    fingerprint = connection.preflight_fingerprint(macro, mode, credentials)
    broker = None
    try:
        exchange = exchange_of(macro)
        if not known_exchange(exchange):
            raise ValueError("지원하지 않는 거래소예요. 웹에서 매크로를 다시 받아 주세요.")
        if not str(macro.get("symbol") or "").strip():
            raise ValueError("먼저 매크로 파일을 선택하세요.")
        side, leverage = str(macro.get("position_side", "long")).lower(), max(1, int(macro.get("leverage", 1) or 1))
        if exchange in DOMESTIC_EXCHANGES and (side != "long" or leverage > 1):
            raise ValueError("국내 거래소는 원화 현물 롱 · 1배만 사용할 수 있어요.")
        if mode == MODE_TESTNET and exchange not in TESTNET_EXCHANGES:
            raise ValueError("국내 거래소에는 테스트넷이 없어요. 모의를 선택하세요.")
        if mode == MODE_MOCK:
            return connection.PreflightResult(True, "모의 준비 완료 — 키 없이 연습하며 실제 주문은 보내지 않습니다. 실거래 연결은 검사하지 않았어요.", fingerprint)
        pair = credential_pair(credentials, exchange)
        if pair is None:
            raise ValueError(f"{exchange_label(exchange)} API 키와 시크릿을 모두 입력하세요.")
        probe = BotThread(macro, credentials, mode, None, lambda _msg: None, lambda _snap: None, lambda *_args: None)
        broker = probe._build_broker(pair, preflight_only=True)
        ok, reason = broker.rehearse(**probe._rehearsal_kwargs())
        safe_reason = connection.safe_preflight_reason(reason, credentials)
        name = getattr(broker, "last_error_name", "")
        error_name = name if isinstance(name, str) else ""
        if not ok:
            close_connection_broker(broker)
            broker = None
        return connection.PreflightResult(bool(ok), safe_reason, fingerprint, broker=broker, error_name=error_name)
    except ValueError as exc:
        reason = connection.safe_preflight_reason(str(exc), credentials)
    except Exception:
        # Library/proxy exceptions may contain signed URLs or credentials.
        reason = "연결 검사에 실패했어요. 인터넷 연결·거래소 키·허용 IP를 확인하고 다시 눌러 주세요."
    close_connection_broker(broker)
    return connection.PreflightResult(False, reason, fingerprint)


def reveal_in_canvas(canvas, body, widget) -> None:
    """스크롤 판(canvas) 안의 칸이 보이는 곳 밖에 있으면 보이는 곳까지 굴린다(Tab 으로 들어갔을 때)."""
    try:
        if not canvas.winfo_ismapped():
            return
        top = widget.winfo_rooty() - body.winfo_rooty()
        bottom = top + widget.winfo_height()
        view_top = canvas.canvasy(0)
        view_height = canvas.winfo_height()
        total = max(1, body.winfo_height())
    except (tk.TclError, KeyError):
        return
    if top < view_top:
        canvas.yview_moveto(max(0, top - 12) / total)
    elif bottom > view_top + view_height:
        canvas.yview_moveto(max(0, bottom + 12 - view_height) / total)


def close_connection_broker(broker) -> None:
    if broker is None:
        return
    try:
        if isinstance(broker, brokers.BinanceBroker):
            broker.raw.close_connection()
        elif isinstance(broker, brokers.DomesticBroker):
            broker.session.close()
    except Exception:
        pass


class RunnerApp:
    _exchange_name = staticmethod(exchange_label)

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
        self.macro_path = tk.StringVar(value="선택된 매크로 없음")
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
        self._remember_choices = {name: False for name in self.key_vars}
        self._storage_dirty = {name: False for name in self.key_vars}
        self._applying_credentials = False
        self._storage_note = ""
        loaded = credentials_mod.load_result(self.credentials_path) if credentials_mod.supported() else None
        self._credentials_load_status = loaded.status if loaded else "missing"
        self._remembered = loaded.values if loaded else None
        self._storage_note = self._load_failure_note(self._credentials_load_status)
        self._apply_remembered_credentials(self._remembered)
        self.server_base = SERVER_BASE
        self._protocol_claim_busy = False
        self._protocol_registration_thread: threading.Thread | None = None
        self._preflight = None
        self._connection_busy = False
        self._connection_generation = 0
        self._connection_queue = queue.Queue()
        self._connection_delivery_lock = threading.Lock()
        self._connection_polling = False
        self._connection_poll_id = None
        self._connection_closed = False
        self._public_ip = ""
        self._public_ip_busy = False
        self._public_ip_cache = connection.PublicIPCache()
        self._public_ip_waiters = set()
        self._public_ip_expiry_id = None
        self._connection_wizard = None
        self._wizard_generation = 0
        self._wizard_auto_ip_id = None
        self._status_before_wizard = None
        # 키 카드의 연결 상태(주문 없는 검사). 지문이 지금 칸과 같을 때만 보인다.
        self._key_check = None
        self._key_check_busy_fp = None
        self._key_check_after = None
        self._key_check_generation = 0
        self._storage_error = ""
        # 웹 [실행기 열기]로 들어왔을 때만 생긴다 — 웹 가이드가 실행기 화면을 따라 보여 주게 단계만 알린다.
        self._progress_reporter = None
        self._ui_scale = 1.0
        self._build()
        for name, (key_var, secret_var) in self.key_vars.items():
            for variable in (key_var, secret_var):
                variable.trace_add("write", lambda *_args, exchange=name: self._on_credentials_changed(exchange))
        self.remember.trace_add("write", self._on_remember_choice)
        self.mode.trace_add("write", self._invalidate_preflight)
        self.member_key.trace_add("write", self._invalidate_preflight)
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        if startup_warning:
            self._log(startup_warning)
        if register_protocol:
            self.root.after(0, self._begin_protocol_registration)
            # 실제 실행에서만 새 버전을 물어본다(테스트 대역은 네트워크를 쓰지 않는다).
            self.root.after(1500, self._begin_update_check)
        if protocol_launch:
            # Let Tk render its first frame before any launch work begins.  The
            # actual HTTP(S) claim runs on a worker thread below.
            self.root.after(0, self._begin_protocol_claim, protocol_launch)

    def _begin_update_check(self) -> None:
        """공개 다운로드 정보(키 없이 읽는다)를 한 번 읽어 더 새 공식 버전이 있으면 머리줄 아래에 알린다. 실패는 조용히."""
        result: dict = {}

        def work() -> None:
            try:
                response = requests.get(f"{SERVER_BASE}/api/runner/download/info", timeout=8)
                result["info"] = response.json() if response.ok else None
            except Exception:
                result["info"] = None

        threading.Thread(target=work, daemon=True).start()

        def wait(tries: int = 0) -> None:
            if "info" not in result:
                if tries < 40:
                    self.root.after(250, wait, tries + 1)
                return
            self._show_update(newer_release(result["info"], RUNNER_VERSION))

        self.root.after(250, wait)

    def _show_update(self, newer: tuple[str, str] | None) -> None:
        bar = getattr(self, "update_bar", None)
        if bar is None or newer is None:
            return
        version, url = newer
        try:
            self.update_label.config(text=f"새 실행기 v{version}이 나왔어요")
            self.update_btn.config(command=lambda: webbrowser.open(url))
            bar.pack(fill="x", padx=16, pady=(0, 12), before=self.main_view)
        except tk.TclError:
            pass

    def _apply_remembered_credentials(self, remembered: dict | None, *, replace=False) -> None:
        """저장된 자격증명(v2)을 거래소별 칸에 채운다.

        파일에는 키를 적어 둔 거래소만 들어 있다 — 없는 이름을 `[...]` 로 꺼내면 창이 아예 열리지 않는다.
        모르는 거래소 이름은 조용히 버린다(옛·새 형식이 섞여도 창은 열려야 한다).
        """
        if not remembered:
            return
        self._remembered = json.loads(json.dumps(remembered))
        self._remember_choices = getattr(self, "_remember_choices", {})
        self._storage_dirty = getattr(self, "_storage_dirty", {})
        self._applying_credentials = True
        try:
            for name, target in self.key_vars.items():
                pair = (remembered.get("exchanges") or {}).get(name) or {}
                if pair or replace:
                    target[0].set(str(pair.get("api_key") or ""))
                    target[1].set(str(pair.get("api_secret") or ""))
                self._remember_choices[name] = bool(pair.get("api_key") and pair.get("api_secret"))
                self._storage_dirty[name] = False
            if replace or (remembered.get("member_key") and not self.member_key.get()):
                self.member_key.set(str(remembered.get("member_key") or ""))
            selected = exchange_of(self.macro) if getattr(self, "macro", None) else None
            self.remember.set(self._remember_choices.get(selected, any(self._remember_choices.values()) if selected is None else False))
        finally:
            self._applying_credentials = False
        self._apply_connection_history(remembered)

    @staticmethod
    def _load_failure_note(status):
        return {
            "read_error": "저장 파일을 읽지 못했어요. Windows 계정·파일 권한을 확인한 뒤 다시 불러오세요. 현재 입력은 유지합니다.",
            "decrypt_error": "저장 키를 이 Windows 계정에서 풀지 못했어요. 원래 PC·Windows 계정에서 다시 불러오거나 기존 키를 재입력하세요. Secret을 잃었다면 공식 페이지에서 새로 발급해야 합니다.",
            "invalid_data": "저장 파일이 손상되었거나 형식이 맞지 않아요. 현재 입력은 유지합니다. 이전 파일을 백업한 뒤 새 저장을 선택할 수 있어요.",
        }.get(status, "")

    def _connection_setup_state(self):
        selected = exchange_of(self.macro) if getattr(self, "macro", None) else ""
        current = (self._credential_values().get("exchanges") or {}).get(selected) or {}
        saved = ((getattr(self, "_remembered", None) or {}).get("exchanges") or {}).get(selected) or {}
        complete = bool(current.get("api_key") and current.get("api_secret"))
        kind = "saved" if complete and current == saved else ("existing" if complete else "new")
        dirty = bool(getattr(self, "_storage_dirty", {}).get(selected))
        if getattr(self, "_remember_choices", {}).get(selected) and current and current != saved:
            dirty = True
        note = {"saved": "저장된 키를 불러왔어요 · 재발급 없이 연결 검사", "existing": "기존 키가 입력돼 있어요 · 새 발급 없이 연결 검사", "new": "키가 없거나 입력이 불완전해요 · 기존 키 입력 또는 새 발급"}[kind]
        if dirty:
            note += " · 변경됨 / 저장 필요"
        elif saved and not complete:
            note += " · 저장된 키 입력을 완성하세요"
        return {"kind": kind, "dirty": dirty, "note": note, "remember": bool(getattr(self, "_remember_choices", {}).get(selected, False))}

    def _on_credentials_changed(self, exchange):
        if getattr(self, "_applying_credentials", False):
            return
        pair = self.key_vars[exchange]
        current = {"api_key": pair[0].get().strip(), "api_secret": pair[1].get().strip()}
        saved = ((getattr(self, "_remembered", None) or {}).get("exchanges") or {}).get(exchange) or {}
        self._storage_dirty = getattr(self, "_storage_dirty", {})
        self._storage_dirty[exchange] = current != saved
        if getattr(self, "macro", None) and exchange == exchange_of(self.macro):
            self._storage_note = ""
        self._invalidate_preflight()
        self._refresh_storage_ui()
        self._render_key_status()
        self._schedule_key_check()

    def _on_remember_choice(self, *_args):
        if getattr(self, "_applying_credentials", False) or not getattr(self, "macro", None):
            return
        selected = exchange_of(self.macro)
        self._remember_choices[selected] = bool(self.remember.get())
        saved = ((self._remembered or {}).get("exchanges") or {}).get(selected) or {}
        self._storage_dirty[selected] = bool(self._storage_dirty.get(selected) or bool(saved) != bool(self.remember.get()))
        self._storage_note = ""
        self._refresh_storage_ui()

    def _refresh_storage_ui(self):
        self._render_key_status()
        self._refresh_connection_wizard()

    def _apply_connection_history(self, remembered):
        cache = getattr(self, "_public_ip_cache", None)
        if cache is not None and getattr(self, "macro", None):
            cache.seed_previous(((remembered or {}).get("connection_history") or {}).get(exchange_of(self.macro)))

    def _record_successful_ip_history(self, *, preflight=None, mode=None):
        if not credentials_mod.supported() or not getattr(self, "macro", None):
            return
        selected = exchange_of(self.macro)
        if not getattr(self, "_remember_choices", {}).get(selected):
            return
        args = (self.macro, mode or self._run_mode(), self._credential_values(), self._public_ip_cache.result,
                preflight if preflight is not None else self._preflight)
        if connection.with_successful_ip_history(self._remembered, *args) is None:
            return
        try:
            loaded = credentials_mod.load_result(self.credentials_path)
            if loaded.status != "loaded":
                raise ValueError
            updated = connection.with_successful_ip_history(loaded.values, *args)
            if updated is None:
                return
            credentials_mod.save(self.credentials_path, updated)
            self._remembered = updated
        except Exception:
            self._log("⚠ 과거 조회 IP 기록 저장에 실패했어요. 키 저장 성공 여부와는 별개입니다.")

    # --- 화면 구성 ----------------------------------------------
    def _apply_theme(self) -> None:
        """ttk 'clam' 위에 껄무새 톤을 입힌다 — 노랑 포인트, 흰 카드, 연회색 바탕.

        글꼴 하나 · 크기 셋(15 앱 이름 · 단계 제목 / 11 카드 제목 / 10 나머지 전부) · 굵기 둘.
        """
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(bg=UI["bg"])
        style.configure(".", background=UI["bg"], foreground=UI["ink"], font=(FONT, 10))
        style.configure("Card.TFrame", background=UI["card"])
        style.configure("Card.TLabel", background=UI["card"], foreground=UI["ink"], font=(FONT, 10))
        style.configure("CardTitle.TLabel", background=UI["card"], foreground=UI["ink"], font=(FONT, 11, "bold"))
        style.configure("CardMuted.TLabel", background=UI["card"], foreground=UI["muted"], font=(FONT, 10))
        style.configure("CardHeading.TLabel", background=UI["card"], foreground=UI["ink"], font=(FONT, 15, "bold"))
        style.configure("Title.TLabel", background=UI["bg"], foreground=UI["ink"], font=(FONT, 15, "bold"))
        style.configure("Badge.TLabel", background=UI["brand"], foreground=UI["brand_ink"], font=(FONT, 10, "bold"),
                        padding=(8, 1))
        style.configure("Status.TLabel", background=UI["bg"], foreground=UI["muted"], font=(FONT, 10, "bold"))
        style.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=UI["border"], lightcolor=UI["border"],
                        darkcolor=UI["border"], padding=6)
        style.configure("TCheckbutton", background=UI["card"], foreground=UI["ink"], font=(FONT, 10))
        style.map("TCheckbutton", background=[("active", UI["card"])])
        style.configure("Primary.TButton", background=UI["brand"], foreground=UI["brand_ink"], bordercolor=UI["brand"],
                        font=(FONT, 10, "bold"), padding=(16, 8))
        style.map("Primary.TButton", background=[("active", UI["brand_hover"]), ("disabled", "#FBE9A6")],
                  foreground=[("disabled", "#8A7A3A")])
        for name, ink, active, disabled, pad in (
            ("Ghost.TButton", UI["ink"], "#F3F4F6", "#B0B5BE", (16, 8)),
            ("Danger.TButton", UI["danger"], "#FEF2F2", "#E8B4B4", (16, 8)),
            # 도우미 머리줄의 거래소 바로가기 — Ghost 와 같고 안쪽 여백만 작다.
            ("Shortcut.TButton", UI["ink"], "#F3F4F6", "#B0B5BE", (12, 6)),
        ):
            style.configure(name, background=UI["card"], foreground=ink, bordercolor=UI["border"], font=(FONT, 10),
                            padding=pad)
            style.map(name, background=[("active", active), ("disabled", UI["card"])], foreground=[("disabled", disabled)])

    def _scale(self) -> float:
        """화면 배율(96 DPI = 1). 창 크기 · 그림 좌표에 곱한다 — 글꼴은 포인트라 Tk 가 알아서 키운다."""
        try:
            return max(1.0, float(self.root.winfo_fpixels("1i")) / 96.0)
        except (tk.TclError, TypeError, ValueError):
            return 1.0

    def _card(self, parent, title: str = "") -> ttk.Frame:
        """흰 카드 한 장(1px 테두리 · 안쪽 16 · 카드 사이 12). 제목이 있으면 맨 위에 둔다."""
        outer = tk.Frame(parent, bg=UI["border"], padx=1, pady=1)  # 1px 테두리
        outer.pack(fill="x", padx=16, pady=(0, 12))
        card = ttk.Frame(outer, style="Card.TFrame", padding=16)
        card.pack(fill="x")
        if title:
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, 12))
        return card

    def _build(self) -> None:
        self.root.title(APP_TITLE)
        s = self._ui_scale = self._scale()
        secret_entry.configure_style(ttk.Style(self.root), s)
        width, height = round(680 * s), round(840 * s)
        try:
            # 작은 화면에서는 작업 표시줄을 가리지 않게 줄인다 — 넘치는 칸은 스크롤로 본다.
            height = min(height, max(round(620 * s), self.root.winfo_screenheight() - round(80 * s)))
        except (tk.TclError, TypeError):
            pass
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(round(600 * s), min(round(620 * s), height))
        self._apply_theme()
        _style_log_scrollbar(ttk.Style(self.root), s)  # 스크롤바 모양은 테마마다 따로 — clam 을 고른 뒤에

        # 머리줄: 이름 · 버전 배지 · 상태
        head = ttk.Frame(self.root, padding=(16, 16, 16, 12))
        head.pack(fill="x")
        ttk.Label(head, text="껄무새 매크로 실행기", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=f"v{RUNNER_VERSION}", style="Badge.TLabel").pack(side="left", padx=(8, 0), pady=(2, 0))
        self.status_lbl = ttk.Label(head, text="● 대기 중", style="Status.TLabel", foreground=UI["muted"])
        self.status_lbl.pack(side="right")

        # 새 버전 알림 줄 — 서버가 알려 준 공식 버전이 이 실행기보다 높을 때만 머리줄 아래에 보인다.
        self.update_bar = tk.Frame(self.root, bg=UPDATE_BG, padx=round(16 * s), pady=round(8 * s))
        self.update_label = tk.Label(self.update_bar, text="", bg=UPDATE_BG, fg=UI["ink"], font=(FONT, 10, "bold"))
        self.update_label.pack(side="left")
        self.update_btn = ttk.Button(self.update_bar, text="받기 ↗", style="Primary.TButton")
        self.update_btn.pack(side="right")

        # 메인 화면. 연결 도우미가 열리면 이 판만 도우미로 바뀐다(창 크기 · 머리줄은 그대로).
        self.main_view = ttk.Frame(self.root)
        self.main_view.pack(fill="both", expand=True)
        # 입력 카드는 내용 높이만큼만 차지하고, 남는 높이는 실행 로그가 갖는다(창이 작으면 카드가 스크롤).
        form_area = ttk.Frame(self.main_view)
        form_area.pack(fill="x")
        self.form_canvas = tk.Canvas(form_area, height=240, bg=UI["bg"], highlightthickness=0)
        form_scroll = ttk.Scrollbar(form_area, orient="vertical", command=self.form_canvas.yview)
        self.form_canvas.pack(side="left", fill="both", expand=True)
        self.form_canvas.configure(yscrollcommand=form_scroll.set)
        self.form_body = ttk.Frame(self.form_canvas)
        form_window = self.form_canvas.create_window((0, 0), window=self.form_body, anchor="nw")

        def fit_form_height():
            # 카드 영역 높이 = 내용 높이. 다만 버튼 줄과 로그 최소 높이(LOG_MIN_LINES 줄)는 남긴다.
            try:
                available = self.main_view.winfo_height()
                if available <= 1:
                    return
                reserved = btns.winfo_reqheight() + log_card.winfo_reqheight()
                want = max(round(120 * s), min(self.form_body.winfo_reqheight(), available - reserved))
                if abs(int(self.form_canvas.cget("height")) - want) > 1:
                    self.form_canvas.configure(height=want)
            except (tk.TclError, NameError):
                pass

        def fit_scroll():
            fit_form_height()
            # 스크롤바는 내용이 넘칠 때만 — 다 보이는데 막대가 서 있으면 칸이 잘린 것처럼 읽힌다.
            need = self.form_body.winfo_reqheight() > self.form_canvas.winfo_height() + 1
            if need and not form_scroll.winfo_manager():
                form_scroll.pack(side="right", fill="y", before=self.form_canvas)
            elif not need and form_scroll.winfo_manager():
                form_scroll.pack_forget()
                self.form_canvas.yview_moveto(0)

        self.form_body.bind("<Configure>", lambda _event: (
            self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all")), fit_scroll()))
        self.form_canvas.bind("<Configure>", lambda event: (
            self.form_canvas.itemconfigure(form_window, width=event.width), fit_scroll()))

        def scroll_form(event):
            wizard = getattr(self, "_connection_wizard", None)
            if wizard is not None and not wizard.closed:
                wizard.scroll_wheel(event)  # 도우미가 열려 있으면 도우미 본문을 굴린다
                return
            # Windows Tk 는 휠을 포인터 아래가 아니라 포커스 위젯으로 보낸다 — 포인터 위치로 굴릴 곳을 고른다.
            steps = -1 if event.delta > 0 else 1
            box = getattr(self, "log_box", None)
            if box is not None and box.winfo_ismapped():
                lx, ly = box.winfo_rootx(), box.winfo_rooty()
                if lx <= event.x_root < lx + box.winfo_width() + round(20 * s) and ly <= event.y_root < ly + box.winfo_height():
                    box.yview_scroll(steps * 3, "units")
                    return "break"
            if not self.form_canvas.winfo_ismapped() or not form_scroll.winfo_manager():
                return
            x, y = self.form_canvas.winfo_rootx(), self.form_canvas.winfo_rooty()
            if x <= event.x_root < x + self.form_canvas.winfo_width() and y <= event.y_root < y + self.form_canvas.winfo_height():
                self.form_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self.root.bind("<MouseWheel>", scroll_form)
        self.form_canvas.bind("<Prior>", lambda _event: self.form_canvas.yview_scroll(-1, "pages"))
        self.form_canvas.bind("<Next>", lambda _event: self.form_canvas.yview_scroll(1, "pages"))
        # Tab 으로 들어간 칸이 스크롤 밖이면 보이는 곳까지 굴린다.
        self.root.bind_all("<FocusIn>", self._reveal_focused, add="+")
        # 거래소 브라우저에 다녀온 것을 안다 — 연결 도우미가 실패 뒤 다시 검사 · 다음 단계로 넘길 때 쓴다.
        self._app_away = False
        self.root.bind("<FocusOut>", lambda _event: self.root.after(60, self._note_app_focus), add="+")
        self.root.bind("<FocusIn>", self._on_app_focus_in, add="+")

        # 매크로 — 제목줄이 곧 매크로 이름이다.
        f1 = self._card(self.form_body)
        row = ttk.Frame(f1, style="Card.TFrame")
        row.pack(fill="x")
        self.pick_btn = ttk.Button(row, text="파일 선택", style="Ghost.TButton", command=self._pick_file)
        self.pick_btn.pack(side="right", padx=(12, 0))
        text_col = ttk.Frame(row, style="Card.TFrame")
        text_col.pack(side="left", fill="x", expand=True)
        self.macro_title = ttk.Label(text_col, textvariable=self.macro_path, style="CardTitle.TLabel")
        self.macro_title.pack(anchor="w")
        self.macro_summary = ttk.Label(text_col, text="", style="CardMuted.TLabel", wraplength=round(470 * s),
                                       justify="left")
        self.macro_summary.pack(anchor="w", pady=(4, 0))

        # 실행 모드 — 같은 폭 칸. 국내는 모의 · 실전, 바이낸스는 모의 · 테스트넷 · 실전.
        f2 = self._card(self.form_body, "실행 모드")
        self.mode_bar = tk.Frame(f2, bg=UI["border"], padx=1, pady=1)
        self.mode_bar.pack(fill="x")
        self.mode_buttons = {}
        for name in RUN_MODES:
            self.mode_buttons[name] = tk.Radiobutton(
                self.mode_bar, text=MODE_LABELS[name], value=name, variable=self.mode, command=self._on_mode_change,
                indicatoron=False, bd=0, relief="flat", offrelief="flat", font=(FONT, 10, "bold"), pady=8,
                cursor="hand2", bg=UI["card"], fg=UI["muted"], activebackground="#F3F4F6", selectcolor=UI["brand"],
                highlightthickness=0)
        self._layout_mode_buttons()
        self.live_note = ttk.Label(f2, text="", style="CardMuted.TLabel")
        self.live_note.pack(anchor="w", pady=(8, 0))
        self._on_mode_change()

        # 거래소 API 키 — 카드 하나가 곧 '{거래소} API 키'. 어느 거래소 키를 쓸지는 매크로가 고른다.
        self._build_key_card(self._card(self.form_body))

        # 껄무새 회원 키 — 웹 '실행기 열기'와 기억한 키로 채워진다. 거래소 키와 따로 맨 아래에 둔다.
        f4 = self._card(self.form_body)
        member = ttk.Frame(f4, style="Card.TFrame")
        member.pack(fill="x")
        member.columnconfigure(1, weight=1)
        ttk.Label(member, text="껄무새 회원 키", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 16))
        self.member_entry = secret_entry.secret_entry(member, self.member_key, font=(FONT, 10), scale=s)
        self.member_entry.grid(row=0, column=1, sticky="ew")

        # 실행/종료 버튼
        btns = ttk.Frame(self.main_view, padding=(16, 4, 16, 12))
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
        log_card = tk.Frame(self.main_view, bg=UI["border"], padx=1, pady=1)
        log_card.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        log_head = tk.Frame(log_card, bg=UI["log_bg"])
        log_head.pack(fill="x")
        tk.Label(log_head, text="실행 로그", bg=UI["log_bg"], fg="#94A3B8", font=(FONT, 10, "bold"),
                 padx=16, pady=8).pack(side="left")
        # 제목과 본문 사이 구분선 — 없으면 본문 스크롤바가 중간에서 끊긴 것처럼 보인다.
        tk.Frame(log_card, bg=LOG_LINE, height=1).pack(fill="x")
        log_body = tk.Frame(log_card, bg=UI["log_bg"])
        log_body.pack(fill="both", expand=True)
        self.log_box = tk.Text(log_body, height=LOG_MIN_LINES, wrap="word", state="disabled", bd=0, highlightthickness=0,
                               bg=UI["log_bg"], fg=UI["log_fg"], font=(FONT, 10), padx=16, pady=8,
                               spacing1=1, spacing3=1)
        self.log_scroll = ttk.Scrollbar(log_body, orient="vertical", command=self.log_box.yview, style=LOG_SCROLL_STYLE)
        self.log_box.pack(side="left", fill="both", expand=True)

        def log_yscroll(first, last):
            # 스크롤바는 로그가 넘칠 때만 — 몇 줄뿐인데 밝은 막대가 서 있으면 어색하다.
            self.log_scroll.set(first, last)
            need = float(first) > 0.0 or float(last) < 1.0
            if need and not self.log_scroll.winfo_manager():
                self.log_scroll.pack(side="right", fill="y", padx=(0, 6), pady=6, before=self.log_box)
            elif not need and self.log_scroll.winfo_manager():
                self.log_scroll.pack_forget()
        self.log_box.configure(yscrollcommand=log_yscroll)
        self.main_view.bind("<Configure>", lambda _event: fit_scroll(), add="+")
        self.log_box.tag_configure("time", foreground="#64748B")
        self.log_box.tag_configure("signal", foreground="#93C5FD")
        self.log_box.tag_configure("order", foreground=UI["brand"])
        self.log_box.tag_configure("fill", foreground="#86EFAC")
        self.log_box.tag_configure("error", foreground="#FCA5A5")
        self.log_box.tag_configure("stop", foreground="#94A3B8")
        self.log_box.tag_configure("info", foreground=UI["log_fg"])

        self._sync_key_fields()
        if requests is None:
            self._log("⚠ 'requests' 모듈이 없어요. requirements.txt 를 설치해 주세요.")

    def _build_key_card(self, card) -> None:
        """'{거래소} API 키' 카드. 머리줄(로고 · 이름 · 연결 상태 · [연결 도우미] · ▾)을 누르면 키 칸이 펼쳐진다."""
        bg, s = UI["card"], self._ui_scale
        self.key_card = card
        head = tk.Frame(card, bg=bg, cursor="hand2")
        head.pack(fill="x")
        self.key_head = head
        # 폭이 모자라면 글자가 줄고 ▾ · [연결 도우미]는 남도록 오른쪽 것부터 둔다.
        self.key_chevron = tk.Label(head, text="▾", bg=bg, fg=UI["muted"], font=(FONT, 11))
        self.key_chevron.pack(side="right", padx=(12, 0))
        self.wizard_btn = ttk.Button(head, text="연결 도우미", style="Primary.TButton", command=self._open_connection_wizard)
        self.key_logo = tk.Label(head, bg=bg)
        self.key_title = tk.Label(head, text="거래소 API 키", bg=bg, fg=UI["ink"], font=(FONT, 11, "bold"))
        self.key_title.pack(side="left")
        self.key_status = tk.Label(head, text="", bg=bg, fg=UI["muted"], font=(FONT, 10, "bold"), anchor="w")
        self.key_status.pack(side="left", padx=(12, 0))
        for widget in (head, self.key_logo, self.key_title, self.key_status, self.key_chevron):
            widget.bind("<Button-1>", lambda _event: self._toggle_key_body())
        body = tk.Frame(card, bg=bg)
        body.columnconfigure(0, minsize=round(112 * s))
        body.columnconfigure(1, weight=1)
        self.key_body = body
        self._key_body_open = False
        tk.Frame(body, bg=UI["border"], height=1).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(12, 12))
        self.key_rows = {}
        for name in credentials_mod.EXCHANGES:
            labels = KEY_FIELD_LABELS.get(name, ("API Key", "Secret Key"))
            widgets = []
            for index, (label, variable) in enumerate(zip(labels, self.key_vars[name])):
                caption = ttk.Label(body, text=label, style="Card.TLabel")
                entry = secret_entry.secret_entry(body, variable, font=(FONT, 10), scale=s)
                caption.grid(row=1 + index, column=0, sticky="w", pady=(0, 8))
                entry.grid(row=1 + index, column=1, sticky="ew", pady=(0, 8))
                widgets.extend((caption, entry))
            self.key_rows[name] = widgets
        if credentials_mod.supported():
            self.remember_check = ttk.Checkbutton(body, text="이 PC에 키 기억하기 (Windows 계정으로 암호화)",
                                                  variable=self.remember, command=self._on_remember_toggled)
            self.remember_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.key_note_row = ttk.Frame(body, style="Card.TFrame")
        self.key_note = ttk.Label(self.key_note_row, text="", style="CardMuted.TLabel", wraplength=round(560 * s),
                                  justify="left")
        self.key_note.pack(side="left", fill="x", expand=True)
        self.key_note_action = ttk.Button(self.key_note_row, text="새로 저장", style="Ghost.TButton",
                                          command=self._recover_credentials_storage)

    def _toggle_key_body(self, open_body=None) -> None:
        body = getattr(self, "key_body", None)
        if body is None or not self.macro or exchange_of(self.macro) not in KNOWN_EXCHANGES:
            return
        self._key_body_open = (not self._key_body_open) if open_body is None else bool(open_body)
        if self._key_body_open:
            body.pack(fill="x")
            self.key_chevron.config(text="▴")
        else:
            body.pack_forget()
            self.key_chevron.config(text="▾")

    def _reveal_focused(self, event) -> None:
        widget = event.widget
        if not isinstance(widget, tk.Misc):
            return
        wizard = getattr(self, "_connection_wizard", None)
        pairs = [(getattr(self, "form_canvas", None), getattr(self, "form_body", None))]
        if wizard is not None and not wizard.closed:
            pairs.append((wizard.canvas, wizard.body))
        for canvas, body in pairs:
            if canvas is not None and body is not None and str(widget).startswith(str(body) + "."):
                reveal_in_canvas(canvas, body, widget)
                return

    def _note_app_focus(self) -> None:
        try:
            self._app_away = self.root.focus_get() is None
        except (tk.TclError, KeyError):
            self._app_away = True

    def _on_app_focus_in(self, _event=None) -> None:
        if not getattr(self, "_app_away", False):
            return
        self._app_away = False
        wizard = getattr(self, "_connection_wizard", None)
        if wizard is not None and not wizard.closed:
            wizard.on_return()

    # --- 이벤트 -------------------------------------------------
    def _run_mode(self) -> str:
        return run_mode_of(self.mode.get())

    def _on_mode_change(self) -> None:
        """고른 모드를 한 줄로 설명한다. 실전만 빨강 — 돈이 움직이는 모드는 하나뿐이어야 보인다."""
        mode = self._run_mode()
        notes = {
            MODE_MOCK: ("주문을 보내지 않고 현재가로 연습해요", UI["muted"]),
            MODE_TESTNET: ("바이낸스 테스트넷 가짜 자금으로 주문해요", UI["muted"]),
            MODE_LIVE: ("실제 자금으로 주문해요", UI["danger"]),
        }
        text, color = notes[mode]
        self.live_note.config(text=text, foreground=color)
        for name, button in getattr(self, "mode_buttons", {}).items():
            selected = name == mode
            try:
                button.config(fg=("#FFFFFF" if name == MODE_LIVE else UI["brand_ink"]) if selected else UI["muted"],
                              selectcolor=LIVE_SELECTED if name == MODE_LIVE else UI["brand"])
            except tk.TclError:
                pass
        self._invalidate_preflight()
        # 바이낸스 키는 모드마다 다른 곳(테스트넷 · 실거래)에서 검사한다.
        if getattr(self, "macro", None) and exchange_of(self.macro) in TESTNET_EXCHANGES:
            self._render_key_status()
            self._schedule_key_check()

    def _layout_mode_buttons(self) -> None:
        """보이는 모드만 같은 폭으로 나란히 둔다 — 국내는 2칸, 바이낸스는 3칸."""
        buttons = getattr(self, "mode_buttons", {})
        if not buttons:
            return
        macro = getattr(self, "macro", None)
        allowed = exchange_of(macro) in TESTNET_EXCHANGES if macro else True
        shown = [name for name in RUN_MODES if name != MODE_TESTNET or allowed]
        bar = getattr(self, "mode_bar", None)
        for index in range(len(RUN_MODES)):
            if bar is not None:
                bar.columnconfigure(index, weight=0, uniform="")
        for name in RUN_MODES:
            if name not in shown:
                buttons[name].grid_remove()
        for index, name in enumerate(shown):
            if bar is not None:
                bar.columnconfigure(index, weight=1, uniform="mode")
            buttons[name].grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 1, 0))

    def _sync_key_fields(self) -> None:
        selected = exchange_of(self.macro) if self.macro else None
        for name, widgets in getattr(self, "key_rows", {}).items():
            for widget in widgets:
                widget.grid() if name == selected else widget.grid_remove()
        title = getattr(self, "key_title", None)
        if title is not None:
            known = selected in KNOWN_EXCHANGES
            title.config(text=f"{exchange_label(selected)} API 키" if known else "거래소 API 키")
            image = assets.logo(self.root, selected, 24 * self._ui_scale) if known else None
            self._key_logo_image = image
            if image is None:
                self.key_logo.pack_forget()
            else:
                self.key_logo.config(image=image)
                if not self.key_logo.winfo_manager():
                    self.key_logo.pack(side="left", padx=(0, 8), before=title)
            if not known and getattr(self, "_key_body_open", False):
                self._toggle_key_body(False)
        wizard_btn = getattr(self, "wizard_btn", None)
        if wizard_btn is not None:
            if selected in DOMESTIC_EXCHANGES:
                if not wizard_btn.winfo_manager():
                    wizard_btn.pack(side="right", after=self.key_chevron)
                wizard_btn.config(state="normal" if self.bot is None else "disabled")
            else:
                wizard_btn.pack_forget()
        if hasattr(self, "remember"):
            self._applying_credentials = True
            try:
                self.remember.set(getattr(self, "_remember_choices", {}).get(selected, False))
            finally:
                self._applying_credentials = False
        self._render_key_status()
        self._refresh_storage_ui()

    # --- 키 연결 상태 -------------------------------------------
    def _selected_pair(self):
        macro = getattr(self, "macro", None)
        pair = getattr(self, "key_vars", {}).get(exchange_of(macro)) if macro else None
        if not pair:
            return None
        key, secret = pair[0].get().strip(), pair[1].get().strip()
        return {"api_key": key, "api_secret": secret} if key and secret else None

    def _key_check_mode(self) -> str:
        """키 연결 검사는 실거래 쪽을 본다(주문은 보내지 않는다). 바이낸스 연습 모드면 테스트넷을 본다."""
        macro = getattr(self, "macro", None)
        if macro and exchange_of(macro) in TESTNET_EXCHANGES and self._run_mode() != MODE_LIVE:
            return MODE_TESTNET
        return MODE_LIVE

    def _key_check_snapshot(self):
        macro = json.loads(json.dumps(getattr(self, "macro", None) or {}))
        mode = self._key_check_mode()
        exchange = exchange_of(macro)
        pair = self._selected_pair() or {}
        # 회원 키는 거래소 연결과 상관없다 — 검사 지문에는 고른 거래소의 키 한 쌍만 들어간다.
        credentials = {"version": 2, "member_key": "", "exchanges": {exchange: pair} if pair else {}}
        return macro, mode, credentials, connection.preflight_fingerprint(macro, mode, credentials)

    def _can_key_check(self) -> bool:
        macro = getattr(self, "macro", None)
        return bool(
            macro and known_exchange(exchange_of(macro)) and requests is not None
            and getattr(self, "bot", None) is None and not getattr(self, "_connection_closed", False)
            and not getattr(self, "_protocol_claim_busy", False) and self._selected_pair() is not None
        )

    def _schedule_key_check(self, delay_ms: int = 700) -> None:
        """키가 다 차면 잠시 뒤 저절로 연결을 확인한다(입력 중 매 글자마다 거래소에 묻지 않게 기다린다)."""
        root = getattr(self, "root", None)
        timer = getattr(self, "_key_check_after", None)
        if timer is not None and root is not None:
            try:
                root.after_cancel(timer)
            except (tk.TclError, ValueError):
                pass
        self._key_check_after = None
        if root is None or not self._can_key_check():
            return
        _macro, _mode, _credentials, fingerprint = self._key_check_snapshot()
        check = getattr(self, "_key_check", None)
        if (check and check["fingerprint"] == fingerprint) or getattr(self, "_key_check_busy_fp", None) == fingerprint:
            return
        self._key_check_after = root.after(delay_ms, self._begin_key_check)
        self._render_key_status()

    def _begin_key_check(self, *, force: bool = False) -> bool:
        """지금 칸의 키로 연결을 확인한다(주문 없음). 시작했으면 참."""
        timer = getattr(self, "_key_check_after", None)
        if timer is not None:
            try:
                self.root.after_cancel(timer)
            except (tk.TclError, ValueError):
                pass
        self._key_check_after = None
        if not self._can_key_check():
            self._render_key_status()
            return False
        macro, mode, credentials, fingerprint = self._key_check_snapshot()
        check = getattr(self, "_key_check", None)
        if not force and ((check and check["fingerprint"] == fingerprint)
                          or getattr(self, "_key_check_busy_fp", None) == fingerprint):
            return True
        self._key_check_generation = getattr(self, "_key_check_generation", 0) + 1
        generation = self._key_check_generation
        self._key_check_busy_fp = fingerprint
        self._render_key_status()

        def check_keys():
            result = perform_connection_check(macro, credentials, mode)
            close_connection_broker(result.broker)
            result = dataclasses.replace(result, broker=None)
            with self._connection_delivery_lock:
                if not self._connection_closed:
                    self._connection_queue.put(("keycheck", generation, fingerprint, result))
        threading.Thread(target=check_keys, daemon=True, name="ggparrot-key-check").start()
        self._schedule_connection_poll()
        return True

    def _accept_key_check(self, generation, fingerprint, result) -> None:
        if generation != getattr(self, "_key_check_generation", 0):
            return
        self._key_check_busy_fp = None
        _macro, _mode, _credentials, current = self._key_check_snapshot()
        if fingerprint != current or getattr(self, "bot", None) is not None:
            # 검사하는 동안 키 · 매크로가 바뀌었다 — 이 결과는 지금 칸의 상태가 아니다.
            self._render_key_status()
            self._schedule_key_check()
            self._refresh_connection_wizard()
            return
        self._store_key_check(fingerprint, result)

    def _store_key_check(self, fingerprint, result) -> None:
        exchange = exchange_of(self.macro)
        view = connection.check_view(exchange, result.ok, getattr(result, "error_name", ""))
        self._key_check = {"fingerprint": fingerprint, "result": result, "view": view}
        if view["connected"]:
            self._after_key_connected(result)
        elif getattr(self, "_connection_wizard", None) is None and exchange not in DOMESTIC_EXCHANGES:
            # 바이낸스는 도우미가 없어 막힌 이유를 키 칸 아래에 보인다 — 펼쳐 둔다.
            self._toggle_key_body(True)
        self._render_key_status()
        self._refresh_connection_wizard()

    def _after_key_connected(self, result) -> None:
        """연결이 확인되면: '기억하기' 를 켜 둔 키는 저장하고, 국내 거래소는 이번 주소를 기록해 둔다."""
        remember = getattr(self, "remember", None)
        if not credentials_mod.supported() or not self.macro or remember is None:
            return
        selected = exchange_of(self.macro)
        if bool(remember.get()) and getattr(self, "_storage_dirty", {}).get(selected):
            self._persist_credentials()
        if selected in DOMESTIC_EXCHANGES and getattr(self, "_remember_choices", {}).get(selected):
            macro, mode, credentials, fingerprint = self._key_check_snapshot()
            checked = connection.PreflightResult(True, result.reason, connection.preflight_fingerprint(macro, MODE_LIVE, self._credential_values()))
            self._record_successful_ip_history(preflight=checked, mode=MODE_LIVE)

    def _wizard_check(self) -> dict:
        """연결 도우미가 볼 지금 키의 검사 상태 — idle(검사 못 함) · busy(확인 중) · done(결과 있음)."""
        macro = getattr(self, "macro", None)
        exchange = exchange_of(macro) if macro else ""
        idle = connection.check_view(exchange, False, "")
        if not macro or self._selected_pair() is None:
            return {"state": "idle", "view": idle, "reason": ""}
        fingerprint = self._key_check_snapshot()[3]
        if getattr(self, "_key_check_busy_fp", None) == fingerprint or getattr(self, "_key_check_after", None) is not None:
            return {"state": "busy", "view": None, "reason": ""}
        check = getattr(self, "_key_check", None)
        if check and check["fingerprint"] == fingerprint:
            return {"state": "done", "view": check["view"], "reason": check["result"].reason}
        reason = "requests 모듈이 필요해요. requirements를 설치해 주세요." if requests is None else ""
        return {"state": "idle", "view": idle, "reason": reason}

    def _key_state(self):
        """(종류, 머리줄 글자, 색, 아래 한 줄) — 키 카드가 보여 줄 연결 상태."""
        macro = getattr(self, "macro", None)
        if not macro:
            return "idle", "매크로 선택 전", UI["muted"], ""
        exchange = exchange_of(macro)
        if not known_exchange(exchange):
            return "idle", "지원하지 않는 거래소", UI["danger"], ""
        if self._selected_pair() is None:
            return "missing", "● 연결 안 됨", UI["warn"], ""
        fingerprint = self._key_check_snapshot()[3]
        if getattr(self, "_key_check_busy_fp", None) == fingerprint or getattr(self, "_key_check_after", None) is not None:
            return "checking", "● 확인 중", UI["muted"], ""
        check = getattr(self, "_key_check", None)
        if check and check["fingerprint"] == fingerprint:
            view, reason = check["view"], check["result"].reason
            if view["connected"]:
                return "ok", "✓ 연결됨", UI["ok"], reason if view["problem"] == "order_size" else ""
            if exchange in DOMESTIC_EXCHANGES:
                # 국내는 막힌 곳을 머리줄에 바로 쓴다 — 고치는 길은 [연결 도우미]에 있다.
                return "bad", f"● {view['headline']}", UI["warn"], ""
            return "bad", "● 연결 안 됨", UI["warn"], reason
        return "unknown", "● 확인 전", UI["muted"], ""

    def _render_key_status(self) -> None:
        status = getattr(self, "key_status", None)
        if status is None:
            return
        kind, text, color, note = self._key_state()
        status.config(text=text, fg=color)
        button = getattr(self, "wizard_btn", None)
        if button is not None:
            button.config(style="Ghost.TButton" if kind == "ok" else "Primary.TButton")
        self._set_key_note(note or self._storage_problem_note())
        if getattr(self, "_connection_wizard", None) is None:
            self._report_setup_progress("ready")

    def _storage_problem_note(self) -> str:
        return self._load_failure_note(getattr(self, "_credentials_load_status", "missing")) or getattr(self, "_storage_error", "")

    def _set_key_note(self, text: str) -> None:
        row = getattr(self, "key_note_row", None)
        if row is None:
            return
        if not text:
            row.grid_remove()
            return
        self.key_note.config(text=text)
        failed_load = getattr(self, "_credentials_load_status", "missing") not in ("missing", "loaded")
        if failed_load and not self.key_note_action.winfo_manager():
            self.key_note_action.pack(side="right", padx=(12, 0))
        elif not failed_load:
            self.key_note_action.pack_forget()
        row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _on_remember_toggled(self) -> None:
        """'이 PC에 키 기억하기' — 켜면 바로 저장하고, 끄면 이 거래소 키만 지운다(회원 키 · 다른 거래소는 그대로)."""
        if not credentials_mod.supported() or not self.macro:
            return
        if self.remember.get() and self._selected_pair() is None:
            return  # 키가 다 차면 연결 확인 뒤 저장한다
        if self._credential_operation_busy():
            return
        self._persist_credentials()
        self._render_key_status()

    # --- 연결 도우미(창 안) ---------------------------------------
    def _open_connection_wizard(self, *, stage: str = "ask") -> None:
        if self.bot is not None or self._protocol_claim_busy:
            messagebox.showwarning(APP_TITLE, "실행 중이거나 웹 연결 중에는 거래소 도우미를 열지 않습니다.")
            return
        selected = exchange_of(self.macro) if self.macro else ""
        if selected not in DOMESTIC_EXCHANGES:
            messagebox.showwarning(APP_TITLE, "먼저 업비트 또는 빗썸 매크로를 선택하세요. 바이낸스는 키 칸에 직접 입력해요.")
            return
        existing = getattr(self, "_connection_wizard", None)
        if existing is not None and not existing.closed:
            if stage != "ask":
                existing.show(stage)
            return
        if self._connection_busy:
            # A pending "start after check" intent is superseded by opening
            # setup. Closing the guide must never resurrect that old start.
            self._invalidate_preflight()
        self._wizard_generation += 1
        status = getattr(self, "status_lbl", None)
        if status is not None:
            self._status_before_wizard = (status.cget("text"), status.cget("foreground"))
            status.config(text="● 연결 도우미", foreground=UI["warn"])
        main = getattr(self, "main_view", None)
        if main is not None:
            main.pack_forget()
        self._connection_wizard = ExchangeConnectionWizard(self, selected, self._wizard_generation, self.root, stage=stage)
        self._connection_wizard.frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.root.bind("<Escape>", lambda _event: self._close_connection_wizard())
        self.start_btn.config(state="disabled")
        # Opening this user-requested wizard is the ONLY automatic IP trigger.
        # Selection/startup itself never contacts the IP service.
        self._wizard_auto_ip_id = self.root.after(0, self._auto_public_ip_for_wizard, self._wizard_generation)

    def _auto_public_ip_for_wizard(self, generation: int) -> None:
        self._wizard_auto_ip_id = None
        wizard = getattr(self, "_connection_wizard", None)
        if (wizard is not None and not wizard.closed and wizard.generation == generation
                and wizard.exchange == exchange_of(self.macro) and not self._connection_closed):
            self._begin_public_ip(force=False)

    def _close_connection_wizard(self) -> None:
        wizard = getattr(self, "_connection_wizard", None)
        if wizard is None:
            return
        self._wizard_generation += 1
        self._connection_wizard = None
        timer = getattr(self, "_wizard_auto_ip_id", None)
        if timer is not None:
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass
            self._wizard_auto_ip_id = None
        wizard.closed = True
        try:
            wizard.destroy()
        except Exception:
            pass
        try:
            self.root.unbind("<Escape>")
        except (tk.TclError, AttributeError):
            pass
        main = getattr(self, "main_view", None)
        if main is not None and not main.winfo_manager():
            main.pack(fill="both", expand=True)
        previous, status = getattr(self, "_status_before_wizard", None), getattr(self, "status_lbl", None)
        if previous and status is not None and self.bot is None:
            status.config(text=previous[0], foreground=previous[1])
        self._sync_key_fields()
        self.start_btn.config(state="disabled" if self.bot is not None or self._connection_busy or self._protocol_claim_busy else "normal")
        try:
            self.root.focus_set()
        except (tk.TclError, AttributeError):
            pass

    def _refresh_connection_wizard(self, *, ip_context=None) -> None:
        wizard = getattr(self, "_connection_wizard", None)
        if (wizard is not None and not wizard.closed and wizard.generation == self._wizard_generation
                and wizard.exchange == exchange_of(self.macro)
                and (ip_context is None or ip_context in self._public_ip_waiters)):
            wizard.refresh()

    def _open_exchange_page(self, url_for) -> None:
        selected = exchange_of(self.macro) if self.macro else ""
        wizard = getattr(self, "_connection_wizard", None)
        if wizard is None or wizard.closed or wizard.exchange != selected:
            return
        try:
            opened = webbrowser.open(url_for(selected), new=2)
        except Exception:
            opened = False
        if not opened:
            messagebox.showwarning(APP_TITLE, "기본 브라우저를 열지 못했어요. 거래소 공식 홈페이지의 API 관리 메뉴에서 직접 진행하세요.")

    def _open_exchange_api_page(self) -> None:
        self._open_exchange_page(connection.api_management_url)

    def _open_exchange_home_page(self) -> None:
        self._open_exchange_page(connection.exchange_home_url)

    def _invalidate_preflight(self, *_args) -> None:
        self._connection_generation = getattr(self, "_connection_generation", 0) + 1
        previous = getattr(self, "_preflight", None)
        self._preflight = None
        if previous:
            close_connection_broker(previous.broker)
        self._refresh_connection_wizard()

    def _snapshot_for_connection(self):
        # Capture Tk variables on the MAIN thread; worker functions never read widgets.
        macro = json.loads(json.dumps(self.macro or {}))
        mode, credentials = self._run_mode(), self._credential_values()
        return macro, mode, credentials, connection.preflight_fingerprint(macro, mode, credentials)

    def _begin_connection_check(self, *, start_after=False) -> None:
        """시작 직전 검사(매크로 · 모드 · 키 그대로). 통과해야 매크로가 시작된다 — 검사만으로는 시작하지 않는다."""
        if self.bot is not None or getattr(self, "_connection_busy", False):
            return
        if self._protocol_claim_busy:
            messagebox.showwarning(APP_TITLE, "웹 매크로 연결이 끝난 뒤 확인해 주세요.")
            return
        if not self.macro:
            messagebox.showwarning(APP_TITLE, "먼저 매크로 파일을 선택하세요.")
            return
        self._invalidate_preflight()
        macro, mode, credentials, _fingerprint = self._snapshot_for_connection()
        if mode == MODE_MOCK:
            self._preflight = perform_connection_check(macro, credentials, mode)
            self._refresh_connection_wizard()
            return
        if requests is None:
            self._log("requests 모듈이 필요해요. requirements를 설치해 주세요.")
            return
        self._connection_busy = True
        generation = self._connection_generation
        self.start_btn.config(state="disabled")
        self._log("키 · 허용 IP · 주문 조건을 확인하고 있어요. 실제 주문은 보내지 않아요.")
        self._refresh_connection_wizard()
        # Queue delivery only: no root.after / Tcl calls from a network worker.
        def check():
            result = perform_connection_check(macro, credentials, mode)
            with self._connection_delivery_lock:
                if self._connection_closed:
                    close_connection_broker(result.broker)
                else:
                    self._connection_queue.put(("preflight", generation, result, start_after))
        threading.Thread(target=check, daemon=True).start()
        self._schedule_connection_poll()

    def _public_ip_context(self):
        wizard = getattr(self, "_connection_wizard", None)
        return (self._public_ip_cache.generation, wizard.generation if wizard is not None else 0)

    def _begin_public_ip(self, *, force=True) -> None:
        if self.bot is not None or getattr(self, "_connection_closed", False):
            return
        cache = self._public_ip_cache
        kind, token = cache.begin(force=force)
        context = self._public_ip_context()
        if kind == "cached":
            self._public_ip_waiters = {context}
            self._render_public_ip_result(cache.result)
            self._refresh_connection_wizard(ip_context=context)
            return
        if kind == "request":
            self._public_ip_waiters = {context}
            self._invalidate_preflight()
        else:
            self._public_ip_waiters.add(context)
        self._public_ip_busy = True
        self._public_ip = ""
        self._refresh_connection_wizard(ip_context=context)
        if kind == "shared":
            return
        def detect():
            try:
                address = connection.detect_public_ipv4(requests)
                ok, value = True, address
            except connection.PublicIPError:
                ok, value = False, ""
            with self._connection_delivery_lock:
                if not self._connection_closed:
                    self._connection_queue.put(("ip", token, ok, value))
        threading.Thread(target=detect, daemon=True).start()
        self._schedule_connection_poll()

    def _render_public_ip_result(self, result) -> None:
        self._public_ip_busy = self._public_ip_cache.busy
        copyable = bool(result is not None and result.ok and result.fresh())
        self._public_ip = result.address if copyable else ""
        old_timer = getattr(self, "_public_ip_expiry_id", None)
        if old_timer is not None:
            try:
                self.root.after_cancel(old_timer)
            except tk.TclError:
                pass
            self._public_ip_expiry_id = None
        if result is not None and result.fresh():
            remaining = max(1, int((connection.PUBLIC_IP_MAX_AGE - (time.monotonic() - result.checked_at)) * 1000) + 1)
            self._public_ip_expiry_id = self.root.after(remaining, self._expire_public_ip_result, result)

    def _expire_public_ip_result(self, result) -> None:
        if self._connection_closed or self._public_ip_cache.result is not result:
            return
        if not result.fresh():
            self._public_ip_expiry_id = None
            self._render_public_ip_result(result)
            self._refresh_connection_wizard()

    def _copy_public_ip(self) -> bool:
        """조회 당시 주소(120초 안)만 복사한다. 복사했으면 참."""
        result = self._public_ip_cache.result
        if not result or not result.ok or not result.fresh() or self._public_ip_busy:
            self._render_public_ip_result(result)
            self._refresh_connection_wizard()
            return False
        self.root.clipboard_clear()
        self.root.clipboard_append(result.address)
        self._refresh_connection_wizard()
        return True

    def _schedule_connection_poll(self) -> None:
        if not self._connection_polling and not self._connection_closed:
            self._connection_polling = True
            self._connection_poll_id = self.root.after(100, self._poll_connection_tasks)

    def _poll_connection_tasks(self) -> None:
        self._connection_polling = False
        self._connection_poll_id = None
        if self._connection_closed:
            return
        while True:
            try:
                task = self._connection_queue.get_nowait()
            except queue.Empty:
                break
            if task[0] == "ip":
                _tag, token, ok, value = task
                result = self._public_ip_cache.finish(token, ok, value)
                self._public_ip_busy = self._public_ip_cache.busy
                if result is None:
                    if not self._public_ip_busy:
                        self._render_public_ip_result(None)
                        self._refresh_connection_wizard()
                    continue
                self._render_public_ip_result(result)
                if result.changed or not result.ok:
                    self._invalidate_preflight()
                self._refresh_connection_wizard(ip_context=self._public_ip_context())
                continue
            if task[0] == "keycheck":
                _tag, generation, fingerprint, result = task
                self._accept_key_check(generation, fingerprint, result)
                continue
            _tag, generation, result, start_after = task
            self._connection_busy = False
            running = self.bot is not None
            self.start_btn.config(state="disabled" if running or getattr(self, "_connection_wizard", None) is not None else "normal")
            _macro, _mode, _credentials, fingerprint = self._snapshot_for_connection()
            if generation != self._connection_generation or result.fingerprint != fingerprint or running:
                close_connection_broker(result.broker)
                self._log("검사 중 설정이 바뀌었어요. 다시 확인하세요. 매매는 시작하지 않았습니다.")
                self._refresh_connection_wizard()
                continue
            self._preflight = result
            if self._run_mode() == self._key_check_mode() and self.macro and self._selected_pair() is not None:
                # 시작 직전 검사도 같은 키 · 같은 거래소를 본 것이다 — 키 카드 상태에 그대로 쓴다.
                key_fingerprint = self._key_check_snapshot()[3]
                self._store_key_check(key_fingerprint, dataclasses.replace(result, broker=None))
            if result.ok:
                self._record_successful_ip_history()
            else:
                self._log(result.reason)
                view = connection.check_view(exchange_of(self.macro), False, getattr(result, "error_name", ""))
                if start_after and view["problem"] == "order_size":
                    # 키 · 허용 IP 는 통과했다 — 잔고 · 주문 금액 문제다. 연결 도우미(키 발급 안내)를 열면
                    # '연결 완료' 에 빨간 표시가 붙은 화면이 떠 무엇을 고칠지 알 수 없다. 이유를 그대로 보인다.
                    messagebox.showwarning(APP_TITLE, f"{result.reason}\n\n매매는 시작하지 않았어요.")
                elif (start_after and exchange_of(self.macro) in DOMESTIC_EXCHANGES and getattr(self, "main_view", None) is not None
                        and getattr(self, "_connection_wizard", None) is None):
                    # 오류 창 대신 연결 도우미의 결과 화면으로 — 무엇이 막혔고 어떻게 고치는지가 거기 있다.
                    self._open_connection_wizard(stage="result_fail")
            self._refresh_connection_wizard()
            # Checkboxes/clicking the guide never count as this verified result.
            if result.ok and start_after and getattr(self, "_connection_wizard", None) is None:
                self._start()
        if self._connection_busy or self._public_ip_busy or getattr(self, "_key_check_busy_fp", None) is not None:
            self._schedule_connection_poll()

    def _sync_mode_choices(self) -> None:
        """국내 매크로에서는 테스트넷 칸을 숨긴다 — 업비트·빗썸에는 테스트넷이 없다.

        숨기는 것만으로는 모자라서, 이미 테스트넷이 골라져 있었으면 모의로 되돌린다.
        """
        if getattr(self, "mode_buttons", {}).get(MODE_TESTNET) is None:
            return
        self._layout_mode_buttons()
        allowed = exchange_of(self.macro) in TESTNET_EXCHANGES if self.macro else True
        if allowed:
            return
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
        # 직접 연 파일은 웹 연결과 다른 매크로다 — 웹 가이드에 이 화면을 더 알리지 않는다.
        reporter, self._progress_reporter = getattr(self, "_progress_reporter", None), None
        if reporter is not None:
            reporter.close()
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
        self._close_connection_wizard()
        cache = getattr(self, "_public_ip_cache", None)
        if cache is not None:
            cache.invalidate()
            self._public_ip_waiters = set()
            self._render_public_ip_result(None)
        self.macro = {k: v for k, v in macro.items() if k != "_sig"}
        if getattr(self, "_credentials_load_status", "missing") in ("missing", "loaded"):
            self._storage_note = ""
        self._apply_connection_history(getattr(self, "_remembered", None))
        # 제목은 이름만 — 파일이면 경로를 떼고 파일 이름을 쓴다(긴 경로가 카드를 넘치지 않게).
        self.macro_path.set(os.path.basename(str(source_label).replace("\\", "/")) or str(source_label))
        side = str(macro.get("position_side", "long"))
        exchange = exchange_of(macro)
        market = market_of(exchange, side.lower(), lev)
        # 웹이 붙인 설명(human_summary)이 있을 때만 둘째 줄 — 없을 때 같은 말을 두 번 쓰지 않는다.
        summary = str(macro.get("human_summary", "") or "").strip()
        self.macro_summary.config(
            text=f"{exchange_label(exchange)} · {macro['symbol']} · {market} · "
                 f"{side}{' · '+str(lev)+'배' if lev>1 else ''}" + (f"\n{summary}" if summary else ""))
        # 거래소가 바뀌면 고를 수 있는 모드도 바뀐다(국내는 테스트넷이 없다).
        self._sync_mode_choices()
        self._sync_key_fields()
        self._invalidate_preflight()
        # 키 칸: 바이낸스인데 키가 없으면 펼쳐 두고(도우미가 없다), 그 밖에는 접어 둔다.
        self._toggle_key_body(exchange == "binance" and self._selected_pair() is None)
        self._render_key_status()
        self._schedule_key_check()

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
            launch_id = payload.get("launch_id") if isinstance(payload, dict) else None
            name = payload.get("name") if isinstance(payload, dict) else None
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
        extra = {}
        if isinstance(launch_id, int) and not isinstance(launch_id, bool) and launch_id > 0:
            extra["launch_id"] = launch_id
        if isinstance(name, str) and name.strip():
            extra["name"] = name.strip()[:60]
        self.root.after(
            0,
            self._apply_protocol_claim,
            macro,
            runner_key,
            claim_base,
            user_macro_id,
            *((extra,) if extra else ()),
        )

    def _apply_protocol_claim(
        self,
        macro: dict,
        runner_key: str,
        claim_base: str,
        user_macro_id: int | None = None,
        extra: dict | None = None,
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
        extra = extra or {}
        try:
            self._apply_claimed_macro(
                macro,
                extra.get("name") or "웹에서 연결한 내 매크로",
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
        self._start_progress_reporting(claim_base, runner_key.strip(), extra.get("launch_id"))
        self.pick_btn.config(state="normal")
        self.start_btn.config(state="normal")
        self.status_lbl.config(text="● 웹 연결됨 · 시작 전", foreground=UI["ok"])
        self._log("웹 매크로와 껄무새 계정을 연결했어요. 실행 모드(기본 모의)를 확인한 뒤 직접 시작해 주세요.")
        _bring_window_to_front(self.root)

    def _start_progress_reporting(self, base: str, runner_key: str, launch_id) -> None:
        old = getattr(self, "_progress_reporter", None)
        if old is not None:
            old.close()
        self._progress_reporter = None
        if not launch_id or not runner_key:
            return
        try:
            self._progress_reporter = setup_progress.SetupProgressReporter(base, runner_key, launch_id)
        except ValueError:
            return
        self._report_setup_progress("ready")

    def _report_setup_progress(self, stage: str, *, path: str = "", slots=None, view=None) -> None:
        """웹 가이드에 지금 단계만 알린다. 키 · 시크릿 · 주소 · 오류 문장은 싣지 않는다."""
        reporter = getattr(self, "_progress_reporter", None)
        macro = getattr(self, "macro", None)
        if reporter is None or not macro:
            return
        if stage == "ready" and view is None:
            check = self._wizard_check()
            view = check["view"] if check["state"] == "done" else None
        view = view or {}
        try:
            payload = setup_progress.progress_payload(
                stage, exchange_of(macro), path=path, slots=slots,
                checks={row: view.get(row, "wait") for row in ("auth", "ip", "order")},
                problem=view.get("problem", "") if view else "", connected=bool(view.get("connected")))
        except ValueError:
            return
        reporter.report(payload)

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
        with getattr(self, "_connection_delivery_lock", threading.Lock()):
            self._connection_closed = True
        for name in ("_connection_poll_id", "_public_ip_expiry_id", "_wizard_auto_ip_id", "_key_check_after"):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    self.root.after_cancel(timer)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        self._close_connection_wizard()
        self._invalidate_preflight()
        reporter, self._progress_reporter = getattr(self, "_progress_reporter", None), None
        if reporter is not None:
            # 웹 가이드가 '진행 중' 에 멈춰 있지 않게 닫힘을 알린다 — 웹은 처음의 [실행기 열기] 화면으로 돌아간다.
            macro = getattr(self, "macro", None)
            try:
                payload = setup_progress.progress_payload("closed", exchange_of(macro)) if macro else None
            except ValueError:
                payload = None
            if payload is not None:
                reporter.finish(payload)
            else:
                reporter.close()
        tasks = getattr(self, "_connection_queue", None)
        while tasks is not None:
            try:
                task = tasks.get_nowait()
            except queue.Empty:
                break
            if task[0] == "preflight":
                close_connection_broker(task[2].broker)
        self.root.destroy()

    def _start(self) -> None:
        if getattr(self, "_connection_closed", False):
            return
        if getattr(self, "_start_pending", False):
            return
        if getattr(self, "_connection_wizard", None) is not None:
            messagebox.showwarning(APP_TITLE, "연결 도우미를 닫고 메인 화면의 모드·매크로를 확인한 뒤 직접 시작하세요.")
            return
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
            if exchange in DOMESTIC_EXCHANGES and getattr(self, "main_view", None) is not None:
                # 오류 창 대신 연결 도우미 — '키를 발급 받은 적 있나요?' 부터 이어서 마친다.
                self._open_connection_wizard()
                return
            self._toggle_key_body(True)
            messagebox.showwarning(
                APP_TITLE,
                f"이 매크로는 {label} 매크로예요.\n\n{label} API Key/Secret 을 입력하세요.")
            return
        if not self.member_key.get().strip():
            messagebox.showwarning(APP_TITLE, "껄무새 회원 키를 입력하세요.")
            entry = getattr(self, "member_entry", None)
            if entry is not None:
                entry.focus_set()
            return

        _macro, _mode, _credentials, fingerprint = self._snapshot_for_connection()
        checked = getattr(self, "_preflight", None)
        if mode == MODE_MOCK:
            # This is deliberately synchronous: mock performs NO authenticated request.
            checked = perform_connection_check(_macro, _credentials, mode)
            self._preflight = checked
            if not checked.ok:
                messagebox.showwarning(APP_TITLE, checked.reason)
                return
        elif not checked or not checked.matches(fingerprint):
            self._begin_connection_check(start_after=True)
            return
        checked_generation = getattr(self, "_connection_generation", 0)

        testnet = mode != MODE_LIVE
        if mode == MODE_LIVE:  # 실전: 실제 자금 확인
            side = str(self.macro.get("position_side", "long"))
            if not messagebox.askyesno(
                APP_TITLE,
                f"⚠ 실전({label})으로 실행합니다.\n\n실제 자금으로 주문이 실행돼요. "
                f"({self.macro.get('symbol')} · {side})\n계속할까요?"):
                return

        # Tk modal dialogs keep processing events. A long confirmation or new web
        # claim can expire/replace the checked form while the dialog is open.
        if (getattr(self, "_connection_closed", False) or self._protocol_claim_busy or self.bot is not None
                or getattr(self, "_connection_wizard", None) is not None):
            return
        _current_macro, _current_mode, _current_credentials, current_fingerprint = self._snapshot_for_connection()
        if (self._preflight is not checked
                or getattr(self, "_connection_generation", 0) != checked_generation
                or not checked.matches(current_fingerprint)):
            self._invalidate_preflight()
            self._log("확인 중 설정이 바뀌었거나 검사 유효 시간이 지났어요. 다시 [매크로 시작]을 누르면 새로 확인해요.")
            return

        # Freeze the inputs handed to both server and bot before any later modal.
        start_macro = json.loads(json.dumps(_current_macro))
        start_credentials = json.loads(json.dumps(_current_credentials))
        payload = json.loads(json.dumps(self._build_start_payload(testnet, mode)))
        self._start_pending = True
        try:
            self._launch_checked_run(checked, checked_generation, start_macro, start_credentials, mode, payload, self.server_base)
        finally:
            self._start_pending = False

    def _launch_checked_run(self, checked, checked_generation, start_macro, start_credentials, mode, payload, server_base):
        label = exchange_label(exchange_of(start_macro))
        self._persist_credentials()
        server = ServerClient(start_credentials["member_key"], base=server_base)
        try:
            started = server.start(payload)
        except ServerRequestError as exc:
            # 서버가 받고서 거절했다 — 연결 문제가 아니므로 서버가 준 이유를 그대로 보인다.
            msg = connection.safe_preflight_reason(str(exc), start_credentials)
            if exc.status == 401:
                msg = "회원 키가 유효하지 않아요. 마이페이지에서 키를 확인하세요."
            elif exc.status in (429, 503):
                msg = f"{msg}\n\n몇 번 다시 보냈지만 서버가 계속 바빠요. 잠시 뒤 다시 [매크로 시작]을 눌러 주세요."
            messagebox.showerror(APP_TITLE, f"매크로를 시작하지 못했어요:\n{msg}")
            return
        except Exception as exc:
            msg = connection.safe_preflight_reason(str(exc), start_credentials)
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
        cancelled = (getattr(self, "_connection_closed", False) or self._protocol_claim_busy or self.bot is not None
                     or getattr(self, "_connection_wizard", None) is not None)
        if not cancelled:
            _macro, _mode, _credentials, fingerprint = self._snapshot_for_connection()
            cancelled = (self._preflight is not checked
                         or getattr(self, "_connection_generation", 0) != checked_generation
                         or not checked.matches(fingerprint)
                         or _credentials.get("member_key") != start_credentials.get("member_key"))
        if cancelled:
            # A source-warning modal can process a new macro/claim/key edit.
            # Cancel only the session we just created, before constructing any bot.
            try:
                notified = bool(server.stopped("error", "시작 중 설정이 바뀌거나 검사가 만료되어 실제 주문 없이 취소했습니다."))
            except Exception:
                notified = False
            if self._preflight is checked:
                if not getattr(self, "_connection_closed", False):
                    self._invalidate_preflight()
                else:
                    self._preflight = None
                    close_connection_broker(checked.broker)
            if not getattr(self, "_connection_closed", False):
                self._log("시작 설정이 바뀌어 매매를 시작하지 않았어요. 다시 연결 검사 후 시작하세요.")
                if not notified:
                    self._log("⚠ 실제 매매는 시작하지 않았지만 서버 취소 통보에 실패했어요. 연결 복구 후 내 에이전트의 세션 상태를 확인하세요.")
            return
        self.bot = BotThread(
            start_macro, start_credentials, mode, server,
            on_log=self._log_threadsafe,
            on_status=self._status_threadsafe,
            on_finish=self._finish_threadsafe,
        )
        self.bot._preflight = checked
        # The bot now owns the prepared connection. Input edits must not close it.
        self._preflight = None
        self.bot.start()
        self._set_running(True)
        self._report_setup_progress("started")
        reporter, self._progress_reporter = getattr(self, "_progress_reporter", None), None
        if reporter is not None:
            reporter.close()

    def _credential_values(self) -> dict:
        """지금 칸에 적힌 값을 자격증명 v2 모양으로 모은다 — 저장과 봇이 같은 모양을 본다."""
        exchanges = {}
        for name, (key_var, secret_var) in self.key_vars.items():
            key, secret = key_var.get().strip(), secret_var.get().strip()
            if key or secret:
                exchanges[name] = {"api_key": key, "api_secret": secret}
        return {"version": 2, "member_key": self.member_key.get().strip(), "exchanges": exchanges}

    def _persist_credentials(self) -> bool:
        """Apply only the selected exchange; errors never pretend a save succeeded."""
        if not credentials_mod.supported() or not self.macro:
            return False
        selected = exchange_of(self.macro)
        try:
            values = self._credential_values()
            choice = bool(self.remember.get())
            saved = credentials_mod.apply_exchange_choice(self.credentials_path, selected, choice, values, previous=getattr(self, "_remembered", None))
            self._remembered = saved
            self._credentials_load_status = "loaded" if saved else "missing"
            self._remember_choices = getattr(self, "_remember_choices", {})
            self._remember_choices[selected] = choice
            self._storage_dirty = getattr(self, "_storage_dirty", {})
            self._storage_dirty[selected] = False
            self._storage_note = f"{exchange_label(selected)} 키 {'저장 완료' if choice else '저장 해제 완료'} · 다른 거래소·회원 키 보존 · 서버 전송 없음"
            self._storage_error = ""
            self._log(self._storage_note)
            self._apply_connection_history(saved)
            self._record_successful_ip_history()
            self._refresh_storage_ui()
            return True
        except Exception:
            self._storage_note = "저장 설정을 적용하지 못했어요. 완전한 API·Secret 입력과 Windows 파일 권한을 확인하세요. 기존 파일·현재 입력은 유지합니다. 복원 실패 파일은 백업 복구가 필요해요."
            self._storage_error = self._storage_note
            self._log("⚠ " + self._storage_note)
            self._refresh_storage_ui()
            return False

    def _forget_credentials(self) -> None:
        if self._credential_operation_busy() or not self.macro:
            return
        selected = exchange_of(self.macro)
        try:
            saved = credentials_mod.apply_exchange_choice(self.credentials_path, selected, False, self._credential_values(), previous=getattr(self, "_remembered", None))
        except Exception:
            self._storage_note = "선택 거래소 키 삭제에 실패했어요. 기존 저장 파일·현재 입력을 유지합니다. Windows 권한을 확인하거나 저장 파일 복구 안내를 이용하세요."
            self._refresh_storage_ui()
            messagebox.showerror(APP_TITLE, self._storage_note)
            return
        self._remembered = saved
        self._credentials_load_status = "loaded" if saved else "missing"
        self._remember_choices[selected] = False
        self._storage_dirty[selected] = False
        self._applying_credentials = True
        try:
            for variable in self.key_vars[selected]:
                variable.set("")
            self.remember.set(False)
        finally:
            self._applying_credentials = False
        self._invalidate_preflight()
        self._storage_note = f"{exchange_label(selected)} 키만 지웠어요. 다른 거래소·회원 키는 보존했습니다."
        self._log(self._storage_note)
        self._refresh_storage_ui()

    def _credential_operation_busy(self):
        if (self.bot is not None or self._protocol_claim_busy or getattr(self, "_connection_busy", False)
                or getattr(self, "_start_pending", False) or getattr(self, "_connection_closed", False)):
            messagebox.showwarning(APP_TITLE, "실행·연결 검사·웹 연결 중에는 저장 키를 다시 불러오거나 삭제·복구하지 않습니다.")
            return True
        return False

    def _reload_credentials(self):
        if self._credential_operation_busy():
            return
        dirty = any(getattr(self, "_storage_dirty", {}).values()) or self.member_key.get().strip() != (getattr(self, "_remembered", None) or {}).get("member_key", "")
        generation = getattr(self, "_connection_generation", 0)
        storage_choice = (bool(self.remember.get()), dict(self._remember_choices))
        if dirty:
            if not messagebox.askyesno(APP_TITLE, "저장되지 않은 변경이 있어요. 다시 불러오면 모든 거래소의 키·저장 선택과 회원 키 입력이 저장 파일 값으로 바뀝니다. 계속할까요?"):
                return
            if (getattr(self, "_connection_closed", False) or generation != getattr(self, "_connection_generation", 0)
                    or storage_choice != (bool(self.remember.get()), dict(self._remember_choices))
                    or self._credential_operation_busy()):
                return
        try:
            loaded = credentials_mod.load_result(self.credentials_path)
        except Exception:
            loaded = credentials_mod.CredentialsLoadResult(None, "read_error")
        self._credentials_load_status = loaded.status
        if loaded.status == "loaded":
            self._apply_remembered_credentials(loaded.values, replace=True)
            self._storage_note = "이 Windows 계정의 저장 키를 다시 불러왔어요. 유효성은 연결 검사로 확인하세요."
            self._invalidate_preflight()
        elif loaded.status == "missing":
            self._remembered = None
            self._applying_credentials = True
            try:
                for name, pair in self.key_vars.items():
                    self._remember_choices[name] = False
                    self._storage_dirty[name] = bool(pair[0].get() or pair[1].get())
                self.remember.set(False)
            finally:
                self._applying_credentials = False
            self._storage_note = "저장 파일이 없어요. 현재 입력은 유지합니다. 기존 키를 입력하거나 공식 페이지에서 새로 발급하세요."
        else:
            self._storage_note = self._load_failure_note(loaded.status)
        self._refresh_storage_ui()

    def _recover_credentials_storage(self):
        if self._credential_operation_busy() or not self.macro:
            return
        if not self.remember.get() or credential_pair(self._credential_values(), exchange_of(self.macro)) is None:
            messagebox.showwarning(APP_TITLE, "먼저 선택 거래소의 완전한 API·Secret을 입력하고 '키만 기억하기'를 선택하세요. 이전 파일은 변경하지 않았습니다.")
            return
        generation = getattr(self, "_connection_generation", 0)
        storage_choice = (bool(self.remember.get()), dict(self._remember_choices))
        _macro, _mode, current_credentials, fingerprint = self._snapshot_for_connection()
        if not messagebox.askyesno(APP_TITLE, "읽거나 복원하지 못한 이전 저장 파일을 같은 폴더에 백업하고 선택 거래소 키를 새로 저장할까요?\n\n이전 파일을 해독하거나 키를 복구하는 기능이 아닙니다. 현재 입력을 확인하세요. 다른 거래소의 이전 암호화 키는 백업 파일에만 남습니다."):
            return
        if (getattr(self, "_connection_closed", False) or generation != getattr(self, "_connection_generation", 0)
                or storage_choice != (bool(self.remember.get()), dict(self._remember_choices))
                or self._credential_operation_busy()):
            return
        _macro, _mode, fresh_credentials, fresh_fingerprint = self._snapshot_for_connection()
        if fingerprint != fresh_fingerprint or current_credentials != fresh_credentials:
            return
        try:
            backup = credentials_mod.backup_unreadable(self.credentials_path)
        except Exception:
            self._storage_note = "이전 저장 파일 백업에 실패했어요. 기존 파일·현재 입력은 유지합니다. Windows 파일 권한을 확인하세요."
            self._refresh_storage_ui()
            return
        self._remembered = None
        self._credentials_load_status = "missing"
        selected = exchange_of(self.macro)
        for name, pair in self.key_vars.items():
            if name != selected:
                self._remember_choices[name] = False
                self._storage_dirty[name] = bool(pair[0].get() or pair[1].get())
        if self._persist_credentials():
            self._storage_note += f" · 이전 파일 백업: {backup.name}"
        else:
            self._storage_note = f"이전 파일은 {backup.name}에 백업했지만 새 저장은 실패했어요. 백업과 현재 입력을 유지합니다. 완전한 키·Windows 파일 권한을 확인하세요."
        self._refresh_storage_ui()

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
        self._render_key_status()

    def _set_running(self, running: bool) -> None:
        self.start_btn.config(state="disabled" if running else "normal")
        self.pick_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        self.close_btn.config(state="normal" if running else "disabled")
        button = getattr(self, "wizard_btn", None)
        if button is not None:
            button.config(state="disabled" if running else "normal")


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
