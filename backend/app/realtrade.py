"""Builds the downloadable 'real-trade' bundle.

SCOPE (v9): the bundled ``bot.py`` connects to Binance and places **real orders**.
It supports both markets:

  * **Spot** (현물) — long only, default for long/leverage-1 macros.
  * **USDT-M Futures** (선물) — long AND short, with the macro's leverage. Used
    automatically whenever the macro needs a short position or leverage > 1.

Default is the **testnet** (fake funds); the flow/API calls mirror live trading so
a user can switch to mainnet themselves by flipping one constant (USE_TESTNET) —
the code never enables mainnet automatically. No withdraw/transfer feature, and API
keys are used in-memory only (never stored/sent/logged).
"""
from __future__ import annotations

import io
import json
import zipfile

from .engine import Macro, human_summary

# --- bot.py (verbatim; settings come from the bundled macro.json) --------
_BOT_PY = r'''"""
[실구동] 코인 매크로 봇 — 바이낸스 현물(Spot) + 선물(USDT-M Futures)

⚠️ 기본값은 테스트넷(가짜 자금)입니다. 실제 자산은 움직이지 않습니다.
  * API 키는 메모리에서만 사용하며 파일 저장·네트워크 전송·로깅하지 않습니다.
  * 출금(withdraw)/이체 기능은 없습니다. 주문(진입/청산)만 합니다.
  * 메인넷 전환은 사용자가 직접(USE_TESTNET=False). 코드가 자동 전환하지 않습니다.

시장(현물/선물) 자동 선택:
  * 숏(공매도) 또는 레버리지>1 이 필요한 매크로 → USDT-M 선물로 실행(숏/레버리지 지원).
  * 그 외(롱·레버리지 1) → 현물로 실행.
  * MARKET 상수로 강제 지정도 가능("auto"/"spot"/"futures").

실행:  python bot.py   (또는 run.bat 더블클릭)
동봉된 macro.json 의 매크로 설정(rule_type/params/risk/symbol/position_side/leverage)을 따릅니다.

참고: 이 봇은 백테스트/페이퍼 엔진의 '진입 + 익절/손절/밴드' 부분을 실거래로
옮긴 단순화 버전입니다. 지표 전략(RSI/볼린저/MA 등)은 해당 매크로의 익절/손절
목표로 근사 실행합니다. 흐름·주문 호출은 실거래와 동일합니다.
"""
from __future__ import annotations

import getpass
import json
import os
import sys
import time
from decimal import Decimal, ROUND_DOWN

# ==================================================================
#  단 하나의 전환 스위치. 메인넷(실제 자금) 전환 시 여기만 False 로.
#  README-run.txt 의 "메인넷 전환 가이드"를 반드시 먼저 읽으세요.
USE_TESTNET = True
# ==================================================================

# 시장 선택: "auto"(숏/레버리지면 선물, 아니면 현물) | "spot" | "futures"
MARKET = os.environ.get("BOT_MARKET", "auto").lower()

# 1회 주문 상한의 기준(선물에서만 의미 있음):
#   "notional" → 상한 = 포지션 명목가치(총 노출). 사용 증거금 = 상한 / 레버리지. (안전·권장)
#   "margin"   → 상한 = 실제 투입 증거금(내 돈). 노출 = 상한 × 레버리지.
ORDER_CAP_BASIS = os.environ.get("ORDER_CAP_BASIS", "notional").lower()

# 안전장치 상수 (메인넷 전환 대비 코드에 항상 포함)
MAX_ORDER_USDT = float(os.environ.get("MAX_ORDER_USDT", "100"))   # 1회 주문 상한(USDT)
POLL_SECONDS = float(os.environ.get("BOT_POLL_SECONDS", "5"))     # 시세 폴링 주기(초)
DEFAULT_TP_PCT = 3.0   # 매크로에 익절 목표가 없을 때 기본 익절률(%)
MAX_RETRIES = 3

HERE = os.path.dirname(os.path.abspath(__file__))


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def load_macro() -> dict:
    with open(os.path.join(HERE, "macro.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def banner(symbol: str, market: str, side: str, leverage: int) -> None:
    net = "TESTNET (가짜 자금)" if USE_TESTNET else "!!! MAINNET (실제 자금) !!!"
    mkt = "선물(USDT-M Futures)" if market == "futures" else "현물(Spot)"
    lev = f" · 레버리지 {leverage}배" if market == "futures" else ""
    print("=" * 60)
    print(f"   *** {net} ***")
    print(f"   시장: {mkt} · 심볼: {symbol} · 포지션: {side.upper()}{lev}")
    print(f"   1회 주문 상한: {MAX_ORDER_USDT} USDT ({ORDER_CAP_BASIS})")
    print("   진입/청산 주문만 실행 · 출금 기능 없음 · 키는 저장/전송 안 함")
    print("=" * 60)


def _round_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    d = Decimal(str(step))
    return float((Decimal(str(qty)) / d).to_integral_value(rounding=ROUND_DOWN) * d)


def _parse_filters(info: dict) -> tuple[float, float]:
    """(lot step size, min notional) so orders satisfy exchange rules.

    Works for both spot symbol_info and a futures exchange-info symbol entry.
    """
    step, min_notional = 0.0, 0.0
    for f in (info or {}).get("filters", []):
        if f["filterType"] in ("LOT_SIZE", "MARKET_LOT_SIZE") and not step:
            step = float(f["stepSize"])
        elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            min_notional = float(f.get("minNotional", f.get("notional", 0)) or 0)
    return step, min_notional


def _decide_market(side: str, leverage: int) -> str:
    """'spot' or 'futures'. Short/leverage require futures; MARKET can force it."""
    needs_futures = side == "short" or leverage > 1
    if MARKET == "spot":
        return "spot"
    if MARKET == "futures":
        return "futures"
    return "futures" if needs_futures else "spot"


def _order_qty(price: float, step: float, min_notional: float,
               budget: float, leverage: int, market: str) -> tuple[float, float]:
    """Return (qty, notional). Enforces the 1회 주문 상한 per ORDER_CAP_BASIS.

    Spot: notional == the USDT spent. Futures: notional == position value; the
    margin actually committed is notional / leverage.
    """
    cap = min(budget, MAX_ORDER_USDT)
    if market == "futures" and ORDER_CAP_BASIS == "margin":
        notional = cap * leverage           # cap is the margin → scale up to exposure
    else:
        notional = cap                      # cap is the notional (spot spend / futures value)
    qty = _round_step(notional / price, step)
    return qty, qty * price


def _strategy_targets(macro: dict) -> dict:
    """Derive entry/exit levels from the macro (simplified live executor)."""
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
        "risk": risk,  # 공통 리스크 3종 (RiskGuard 가 사용)
    }


class RiskGuard:
    """공통 리스크 관리 3종 (백테스트/페이퍼 엔진과 같은 의미).

      * 일일 최대손실 — 당일 손익이 -N% 도달 시 그날은 청산 후 신규 진입 중단
      * 최대 보유시간 — 진입 후 N시간 초과 시 강제 청산
      * 재진입 금지   — 손절로 청산한 뒤 N분간 재진입 차단

    엔진은 봉 시각으로 판정하지만 봇은 실시간이라 실제 시계(time.time)를 씁니다.
    기준 자본은 macro 의 initial_capital(없으면 1회 주문 상한)입니다.
    """

    def __init__(self, risk: dict, base_capital: float) -> None:
        self.daily_max_loss = risk.get("daily_max_loss_pct")
        self.max_holding_hours = risk.get("max_holding_hours")
        self.cooldown_minutes = float(risk.get("cooldown_minutes") or 0)
        self.base = base_capital if base_capital > 0 else MAX_ORDER_USDT
        self._day = None
        self._day_pnl = 0.0        # 당일 실현손익(USDT)
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
        """날짜가 바뀌면 당일 손익과 중단 상태를 초기화."""
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._day_pnl = 0.0
            if self._halted_day and self._halted_day != today:
                self._halted_day = None

    def _daily_loss_pct(self, unrealized: float = 0.0) -> float:
        return (self._day_pnl + unrealized) / self.base * 100.0

    def entry_blocked(self):
        """(차단여부, 사유). 진입 직전에 호출."""
        if self._halted_day and self._halted_day == self._day:
            return True, f"일일 최대손실({self.daily_max_loss}%) 도달 → 오늘은 신규 진입 중단"
        remain = self._cooldown_until - time.time()
        if remain > 0:
            return True, f"손절 후 재진입 금지 {remain/60:.1f}분 남음"
        return False, ""

    def force_close(self, unrealized: float):
        """(강제청산 여부, 사유). 보유 중일 때 매 주기 호출."""
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
            print(f"  ⏳ 손절 → {self.cooldown_minutes:.0f}분간 재진입 금지")
        if self.daily_max_loss and self._daily_loss_pct() <= -float(self.daily_max_loss):
            self._halted_day = self._day
            print(f"  🛑 일일 최대손실 도달 → 오늘은 더 이상 진입하지 않습니다")


def _was_stop_exit(t: dict, price: float, entry: float, side: str) -> bool:
    """청산이 손절 때문이었는지(=쿨다운 대상) 판정."""
    if t["sl_pct"] is None or entry <= 0:
        return False
    if side == "long":
        return price <= entry * (1 - t["sl_pct"] / 100.0)
    return price >= entry * (1 + t["sl_pct"] / 100.0)


def _should_enter(t: dict, price: float, side: str) -> bool:
    if t["rule"] == "B":
        # limit band: long buys at/below buy_price; short sells at/above sell_price.
        if side == "long" and t["buy_price"]:
            return price <= t["buy_price"]
        if side == "short" and t["sell_price"]:
            return price >= t["sell_price"]
    return True                          # others: enter when flat


def _should_exit(t: dict, price: float, entry: float, side: str) -> bool:
    """Direction-aware take-profit / stop-loss / band exit."""
    tp = t["tp_pct"] if t["tp_pct"] is not None else (None if t["rule"] == "B" else DEFAULT_TP_PCT)
    if side == "long":
        if t["rule"] == "B" and t["sell_price"] and price >= t["sell_price"]:
            return True
        if tp is not None and price >= entry * (1 + tp / 100.0):
            return True
        if t["sl_pct"] is not None and price <= entry * (1 - t["sl_pct"] / 100.0):
            return True
    else:  # short: profit when price falls, stop when price rises
        if t["rule"] == "B" and t["buy_price"] and price <= t["buy_price"]:
            return True
        if tp is not None and price <= entry * (1 - tp / 100.0):
            return True
        if t["sl_pct"] is not None and price >= entry * (1 + t["sl_pct"] / 100.0):
            return True
    return False


def _pnl_usdt(qty: float, entry: float, price: float, side: str) -> float:
    """Realized USDT PnL for a closed position (notional-based; leverage-agnostic)."""
    return qty * (price - entry) if side == "long" else qty * (entry - price)


def _pnl_pct(entry: float, price: float, side: str) -> float:
    if entry <= 0:
        return 0.0
    move = (price - entry) / entry * 100.0
    return move if side == "long" else -move


def _base_asset(symbol: str) -> str:
    for q in ("USDT", "BUSD", "USDC", "FDUSD"):
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol


# ================================================================
#  SPOT (현물) — 롱 전용
# ================================================================
def _spot_balances(client, base: str):
    """(USDT free, base free) from the spot account. (None, None) on error."""
    try:
        u = client.get_asset_balance(asset="USDT")
        b = client.get_asset_balance(asset=base)
        return (float(u["free"]) if u else 0.0), (float(b["free"]) if b else 0.0)
    except Exception:
        return None, None


def _spot_place(client, symbol: str, side: str, qty: float):
    """Place a spot MARKET order with limited retries. Returns response or None."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            order = client.create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)
            fills = order.get("fills", [])
            avg = fills[0]["price"] if fills else "?"
            print(f"  ✓ {side} 주문 체결: id={order.get('orderId')} 수량={qty} 체결가~{avg} "
                  f"상태={order.get('status')}")
            return order
        except Exception as exc:  # BinanceAPIException 등
            print(f"  ✗ {side} 주문 실패({attempt}/{MAX_RETRIES}): {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(1.0)
    print("  주문을 포기합니다(재시도 초과).")
    return None


def run_spot(client, macro: dict, symbol: str, t: dict) -> None:
    base = _base_asset(symbol)
    info = client.get_symbol_info(symbol)
    if not info:
        print(f"\n[오류] '{symbol}' 은(는) (테스트넷) 현물에 상장되어 있지 않습니다.")
        print("  테스트넷은 일부 주요 종목만 지원합니다(급등 잡코인은 대개 없음).")
        print("  예: BTCUSDT, ETHUSDT, BNBUSDT, LTCUSDT, XRPUSDT, TRXUSDT, ADAUSDT")
        print("  → macro.json 의 \"symbol\" 을 위 종목으로 바꾸거나, 숏/레버리지면 선물로 실행하세요.")
        return
    step, min_notional = _parse_filters(info)
    guard = RiskGuard(t["risk"], t["capital"] * t["invest_ratio"] if t["capital"] else 0.0)
    print(f"공통 리스크: {guard.describe()}")
    print(f"주문 상한 {MAX_ORDER_USDT} USDT · {POLL_SECONDS}초마다 평가. Ctrl+C 로 안전 종료.\n")

    in_position, entry_price, held_qty, realized = False, 0.0, 0.0, 0.0
    try:
        while True:
            try:
                price = float(client.get_symbol_ticker(symbol=symbol)["price"])
            except Exception as exc:
                print(f"  일시 오류(시세 조회): {exc} — {POLL_SECONDS}초 후 재시도")
                time.sleep(POLL_SECONDS)
                continue
            guard.roll_day()
            uf, bf = _spot_balances(client, base)
            if in_position and entry_price > 0:
                pos = f"보유 {held_qty} {base}(진입가 {entry_price})"
                pnl = f"손익 {_pnl_pct(entry_price, price, 'long'):+.2f}%"
            else:
                pos, pnl = "포지션 없음", "손익 -"
            bal = (f"USDT {uf:.2f} / {base} {bf:.6f}" if uf is not None else "잔고 조회 실패")
            print(f"[{time.strftime('%H:%M:%S')}] {symbol} 현재가 {price} | {pos} | {pnl} "
                  f"| 누적 {realized:+.2f} USDT | 잔고 {bal}")
            if not in_position:
                blocked, why = guard.entry_blocked()
                if blocked:
                    print(f"  ⏸ 진입 보류: {why}")
                elif _should_enter(t, price, "long"):
                    budget = t["capital"] * t["invest_ratio"] if t["capital"] else MAX_ORDER_USDT
                    qty, notional = _order_qty(price, step, min_notional, budget, 1, "spot")
                    if qty <= 0 or notional < min_notional:
                        print(f"  (주문 최소금액 미만: 필요≥{min_notional} USDT, 대기)")
                    else:
                        print(f"[진입 신호] 현재가 {price} → 매수 {qty} {symbol}")
                        if _spot_place(client, symbol, "BUY", qty):
                            in_position, entry_price, held_qty = True, price, qty
                            guard.on_entry()
            else:
                unreal = _pnl_usdt(held_qty, entry_price, price, "long")
                forced, why = guard.force_close(unreal)
                if forced or _should_exit(t, price, entry_price, "long"):
                    tag = f"[강제청산: {why}]" if forced else "[청산 신호]"
                    print(f"{tag} 현재가 {price} (진입 {entry_price}) → 매도 {held_qty}")
                    if _spot_place(client, symbol, "SELL", _round_step(held_qty, step)):
                        pnl_usdt = _pnl_usdt(held_qty, entry_price, price, "long")
                        realized += pnl_usdt
                        print(f"  이번 거래 손익: {_pnl_pct(entry_price, price, 'long'):+.2f}% "
                              f"({pnl_usdt:+.2f} USDT) · 누적 {realized:+.2f} USDT")
                        was_stop = not forced and _was_stop_exit(t, price, entry_price, "long")
                        in_position, entry_price, held_qty = False, 0.0, 0.0
                        guard.on_exit(pnl_usdt, was_stop)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n종료 요청(Ctrl+C).")
        if in_position:
            print(f"⚠ 아직 보유 중인 포지션이 있습니다: {held_qty} {symbol} (진입가 {entry_price}). "
                  f"필요하면 거래소에서 직접 정리하세요.")
        print("봇을 종료합니다.")


# ================================================================
#  FUTURES (선물, USDT-M) — 롱/숏 + 레버리지
# ================================================================
def _fut_symbol_info(client, symbol: str) -> dict | None:
    try:
        for s in client.futures_exchange_info().get("symbols", []):
            if s.get("symbol") == symbol:
                return s
    except Exception as exc:
        print(f"  선물 심볼정보 조회 실패: {exc}")
    return None


def _fut_usdt_balance(client) -> float | None:
    try:
        for b in client.futures_account_balance():
            if b.get("asset") == "USDT":
                return float(b.get("balance", 0))
    except Exception:
        return None
    return 0.0


def _fut_price(client, symbol: str) -> float:
    return float(client.futures_symbol_ticker(symbol=symbol)["price"])


def _fut_place(client, symbol: str, side: str, qty: float, reduce_only: bool = False):
    """USDT-M MARKET order (one-way mode). Returns response or None."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            kwargs = dict(symbol=symbol, side=side, type="MARKET", quantity=qty)
            if reduce_only:
                kwargs["reduceOnly"] = "true"
            order = client.futures_create_order(**kwargs)
            tag = " (청산)" if reduce_only else ""
            print(f"  ✓ {side}{tag} 주문 접수: id={order.get('orderId')} 수량={qty} "
                  f"상태={order.get('status')}")
            return order
        except Exception as exc:
            print(f"  ✗ {side} 주문 실패({attempt}/{MAX_RETRIES}): {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(1.0)
    print("  주문을 포기합니다(재시도 초과).")
    return None


def run_futures(client, macro: dict, symbol: str, t: dict, side: str, leverage: int) -> None:
    info = _fut_symbol_info(client, symbol)
    if not info:
        print(f"\n[오류] '{symbol}' 은(는) (테스트넷) USDT-M 선물에 상장되어 있지 않습니다.")
        print("  예: BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT, ADAUSDT, SOLUSDT")
        print("  → macro.json 의 \"symbol\" 을 선물 상장 종목으로 바꾼 뒤 다시 실행하세요.")
        return
    step, min_notional = _parse_filters(info)

    # 격리 마진 + 레버리지 설정(이미 설정돼 있으면 예외 무시).
    try:
        client.futures_change_margin_type(symbol=symbol, marginType="ISOLATED")
    except Exception:
        pass  # -4046: No need to change margin type (이미 격리)
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as exc:
        print(f"⚠ 레버리지 {leverage}배 설정 실패({exc}). 계정 기본 레버리지로 진행합니다.")

    open_side = "BUY" if side == "long" else "SELL"    # 진입
    close_side = "SELL" if side == "long" else "BUY"   # 청산
    margin_note = ("증거금 = 상한/레버리지" if ORDER_CAP_BASIS == "notional"
                   else "노출 = 상한×레버리지")
    guard = RiskGuard(t["risk"], t["capital"] * t["invest_ratio"] if t["capital"] else 0.0)
    print(f"공통 리스크: {guard.describe()}")
    print(f"주문 상한 {MAX_ORDER_USDT} USDT ({ORDER_CAP_BASIS}, {margin_note}) · "
          f"{POLL_SECONDS}초마다 평가. Ctrl+C 로 안전 종료.\n")

    in_position, entry_price, held_qty, realized = False, 0.0, 0.0, 0.0
    try:
        while True:
            try:
                price = _fut_price(client, symbol)
            except Exception as exc:
                print(f"  일시 오류(시세 조회): {exc} — {POLL_SECONDS}초 후 재시도")
                time.sleep(POLL_SECONDS)
                continue
            guard.roll_day()
            ub = _fut_usdt_balance(client)
            if in_position and entry_price > 0:
                pos = f"{side.upper()} {held_qty} {symbol}(진입가 {entry_price})"
                pnl = f"손익 {_pnl_pct(entry_price, price, side):+.2f}% (레버리지 후 {_pnl_pct(entry_price, price, side) * leverage:+.2f}%)"
            else:
                pos, pnl = "포지션 없음", "손익 -"
            bal = (f"USDT 증거금 {ub:.2f}" if ub is not None else "잔고 조회 실패")
            print(f"[{time.strftime('%H:%M:%S')}] {symbol} 현재가 {price} | {pos} | {pnl} "
                  f"| 누적 {realized:+.2f} USDT | {bal}")
            if not in_position:
                blocked, why = guard.entry_blocked()
                if blocked:
                    print(f"  ⏸ 진입 보류: {why}")
                elif _should_enter(t, price, side):
                    budget = t["capital"] * t["invest_ratio"] if t["capital"] else MAX_ORDER_USDT
                    qty, notional = _order_qty(price, step, min_notional, budget, leverage, "futures")
                    if qty <= 0 or notional < min_notional:
                        print(f"  (주문 최소금액 미만: 필요≥{min_notional} USDT, 대기)")
                    else:
                        margin = notional / leverage
                        print(f"[진입 신호] 현재가 {price} → {open_side} {qty} {symbol} "
                              f"(명목 {notional:.2f} USDT · 증거금 ~{margin:.2f} USDT · {leverage}배)")
                        if _fut_place(client, symbol, open_side, qty):
                            in_position, entry_price, held_qty = True, price, qty
                            guard.on_entry()
            else:
                unreal = _pnl_usdt(held_qty, entry_price, price, side)
                forced, why = guard.force_close(unreal)
                if forced or _should_exit(t, price, entry_price, side):
                    tag = f"[강제청산: {why}]" if forced else "[청산 신호]"
                    print(f"{tag} 현재가 {price} (진입 {entry_price}) → {close_side} {held_qty}")
                    if _fut_place(client, symbol, close_side, _round_step(held_qty, step), reduce_only=True):
                        pnl_usdt = _pnl_usdt(held_qty, entry_price, price, side)
                        realized += pnl_usdt
                        print(f"  이번 거래 손익: {_pnl_pct(entry_price, price, side):+.2f}% "
                              f"({pnl_usdt:+.2f} USDT) · 누적 {realized:+.2f} USDT")
                        was_stop = not forced and _was_stop_exit(t, price, entry_price, side)
                        in_position, entry_price, held_qty = False, 0.0, 0.0
                        guard.on_exit(pnl_usdt, was_stop)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n종료 요청(Ctrl+C).")
        if in_position:
            print(f"⚠ 아직 열린 포지션이 있습니다: {side.upper()} {held_qty} {symbol} (진입가 {entry_price}). "
                  f"필요하면 거래소에서 직접 청산하세요(reduceOnly {close_side}).")
        print("봇을 종료합니다.")


def main() -> None:
    _utf8_stdout()
    macro = load_macro()
    symbol = str(macro.get("symbol", "BTCUSDT")).upper()
    side = str(macro.get("position_side", "long")).lower()
    leverage = max(1, int(macro.get("leverage", 1) or 1))
    market = _decide_market(side, leverage)

    banner(symbol, market, side, leverage)
    print(macro.get("human_summary", ""))
    print()

    if market == "spot" and side == "short":
        print("현물(spot)은 숏(공매도)을 지원하지 않습니다. 선물로 실행하거나 롱 매크로를 쓰세요.")
        return

    # 키 입력: 메모리에서만 사용. 저장/전송/로깅하지 않음.
    key_kind = "선물" if market == "futures" else "현물"
    print(f"[{key_kind} {'TESTNET' if USE_TESTNET else 'MAINNET'}] API 키를 입력하세요.")
    if USE_TESTNET:
        src = ("https://testnet.binancefuture.com" if market == "futures"
               else "https://testnet.binance.vision")
        print(f"  (이 시장의 테스트넷 키는 {src} 에서 발급합니다. 두 사이트 키는 서로 호환되지 않습니다.)")
    api_key = input("API Key: ").strip()
    api_secret = getpass.getpass("API Secret (입력 숨김): ").strip()
    if not api_key or not api_secret:
        print("키가 비어 있어 종료합니다.")
        return

    try:
        from binance.client import Client
    except ImportError:
        print("python-binance 가 설치되어 있지 않습니다.  pip install -r requirements.txt 후 다시 실행하세요.")
        return

    try:
        client = Client(api_key, api_secret, testnet=USE_TESTNET)
        if market == "futures":
            bal = _fut_usdt_balance(client)
            print(f"연결 성공. 선물 USDT 증거금: {bal if bal is not None else '조회 실패'}")
        else:
            account = client.get_account()
            usdt = next((b for b in account["balances"] if b["asset"] == "USDT"), None)
            print(f"연결 성공. 현물 USDT 잔고: {usdt['free'] if usdt else '조회 실패'}")
    except Exception as exc:
        print(f"연결/인증 실패: {exc}")
        if "-2015" in str(exc) or "Invalid API-key" in str(exc):
            print("점검하세요:")
            if market == "futures":
                print("  ① 선물은 반드시 https://testnet.binancefuture.com (USDT-M) 키여야 합니다.")
                print("     - 현물 testnet.binance.vision 키, 실거래 키는 선물 테스트넷에서 동작하지 않습니다.")
            else:
                print("  ① 현물은 반드시 https://testnet.binance.vision (Spot) 키여야 합니다.")
            print("  ② 키에 IP 제한을 걸었다면 현재 IP를 허용하거나 제한을 해제하세요.")
            print("  ③ 테스트넷이 초기화되면 기존 키가 만료됩니다 → 키를 재발급하세요.")
            print("  ④ Secret 을 공백 없이 정확히 입력했는지 확인하세요.")
        return

    t = _strategy_targets(macro)
    print(f"전략: rule={t['rule']} 방향={side} 익절={t['tp_pct']}% 손절={t['sl_pct']}% "
          f"밴드=({t['buy_price']},{t['sell_price']}) 투입비율={t['invest_ratio']*100:.0f}%")
    if side == "short" and t["sl_pct"] is None:
        print("⚠ 숏 포지션인데 손절(stop_loss_pct)이 없습니다. 손실이 무제한일 수 있어 매우 위험합니다.")

    if market == "futures":
        run_futures(client, macro, symbol, t, side, leverage)
    else:
        run_spot(client, macro, symbol, t)


if __name__ == "__main__":
    main()
'''

# --- requirements.txt ----------------------------------------------------
_REQUIREMENTS_TXT = """# 봇 실행에 필요한 최소 의존성
python-binance>=1.0.19
"""

# --- run.bat (robust Windows launcher) -----------------------------------
# IMPORTANT: cmd.exe parses .bat files using the system OEM code page (e.g. cp949
# on Korean Windows), NOT UTF-8 — `chcp 65001` only changes console *output*.
# So this file is kept ASCII-only; Korean guidance lives in README-run.txt.
# It is written to the zip with CRLF line endings (build_bundle) for cmd.exe.
_RUN_BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   Coin Macro Bot (Binance Spot + USDT-M Futures)
echo   * Default is TESTNET (fake funds). No real assets are moved.
echo   * Short / leverage macros run on Futures automatically.
echo   * Korean guide: open README-run.txt
echo ============================================================
echo.

REM Find Python: try "py -3", then "python", then "python3"
set "PYEXE="
py -3 --version >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python --version >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  python3 --version >nul 2>nul && set "PYEXE=python3"
)
if not defined PYEXE (
  echo [ERROR] Python was not found.
  echo   Install Python 3.10+ from https://www.python.org/downloads/
  echo   and check "Add Python to PATH" during setup, then run again.
  echo.
  pause
  exit /b 1
)
echo Using Python: %PYEXE%
echo.

echo [1/2] Installing dependency (python-binance)...
%PYEXE% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo [ERROR] Dependency install failed. Check your internet and Python.
  echo   Manual:  %PYEXE% -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)
echo.

echo [2/2] Starting bot...
%PYEXE% "%~dp0bot.py"
if errorlevel 1 (
  echo.
  echo [ERROR] The bot exited with an error. See the messages above.
)

echo.
echo === Finished. Press any key to close this window. ===
pause >nul
"""

# --- run.ps1 (optional PowerShell launcher; ASCII for PS 5.1 safety) -----
_RUN_PS1 = """# Coin Macro Bot launcher (PowerShell). Korean guide: README-run.txt
# If blocked by execution policy, run this first in PowerShell:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# or just double-click run.bat instead.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Python {
  foreach ($cand in @("py -3", "python", "python3")) {
    try {
      $exe, $rest = $cand.Split(" ", 2)
      & $exe $rest --version *> $null
      if ($LASTEXITCODE -eq 0) { return $cand }
    } catch {}
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Write-Host "[ERROR] Python not found. Install from https://www.python.org/downloads/ then retry."
  Read-Host "Press Enter to exit"; exit 1
}
Write-Host "Using Python: $py"
Invoke-Expression "$py -m pip install -r `"$PSScriptRoot\\requirements.txt`""
Invoke-Expression "$py `"$PSScriptRoot\\bot.py`""
Read-Host "Press Enter to exit"
"""

# --- run.command (macOS double-click launcher; ASCII, LF, executable) ----
# Written to the zip with LF endings and a 0o755 mode so Finder treats it as an
# executable a user can double-click (it opens Terminal). Korean guidance lives
# in README-run.txt so this stays ASCII-only. Shebang forces /bin/bash (present
# on macOS even though the default interactive shell is zsh).
_RUN_COMMAND = """#!/bin/bash
# Coin Macro Bot launcher (macOS). Korean guide: README-run.txt
# Double-click in Finder. If macOS blocks it ("cannot be opened" / "unidentified
# developer"), right-click the file > Open once, or in Terminal run:
#     xattr -d com.apple.quarantine run.command ; chmod +x run.command
# You can also just run:  bash run.command
cd "$(dirname "$0")" || exit 1

echo "============================================================"
echo "  Coin Macro Bot (Binance Spot + USDT-M Futures)"
echo "  * Default is TESTNET (fake funds). No real assets are moved."
echo "  * Short / leverage macros run on Futures automatically."
echo "  * Korean guide: open README-run.txt"
echo "============================================================"
echo

# Find Python 3: prefer python3, then python (macOS has no python2 by default).
PYEXE=""
if command -v python3 >/dev/null 2>&1; then
  PYEXE="python3"
elif command -v python >/dev/null 2>&1; then
  PYEXE="python"
fi
if [ -z "$PYEXE" ]; then
  echo "[ERROR] Python 3 was not found."
  echo "  Install it from https://www.python.org/downloads/ (or: brew install python)"
  echo "  then double-click run.command again."
  echo
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi
echo "Using Python: $PYEXE"
echo

echo "[1/2] Installing dependency (python-binance)..."
"$PYEXE" -m pip install -r requirements.txt
if [ $? -ne 0 ]; then
  echo "  pip failed; retrying with --user ..."
  "$PYEXE" -m pip install --user -r requirements.txt
fi
echo

echo "[2/2] Starting bot..."
"$PYEXE" bot.py

echo
read -n 1 -s -r -p "=== Finished. Press any key to close. ==="
"""

_README_TXT = """코인 매크로 봇 (바이낸스 현물+선물 실구동)
==========================================

⚠️ 기본은 테스트넷(가짜 자금)입니다. 실제 자산은 움직이지 않습니다.

이 봇은 매크로에 따라 시장을 자동 선택합니다.
  - 숏(공매도) 또는 레버리지>1 → 선물(USDT-M Futures)로 실행(숏/레버리지 지원)
  - 롱·레버리지 1                → 현물(Spot)로 실행
  - 환경변수 BOT_MARKET=spot|futures|auto 로 강제 지정도 가능(기본 auto)

가장 쉬운 실행 — Windows (2단계)
  ① Python 3.10+ 설치 (https://www.python.org/downloads/, 설치 시 "Add Python to PATH" 체크)
  ② run.bat 더블클릭  → 의존성 설치 후 봇 실행 → API 키 입력

가장 쉬운 실행 — macOS (2단계)
  ① Python 3.10+ 설치 (https://www.python.org/downloads/ 또는 터미널에서 brew install python)
  ② run.command 더블클릭  → 의존성 설치 후 봇 실행 → API 키 입력
     · 처음 열 때 "확인되지 않은 개발자"라고 막히면: run.command 를 우클릭 → "열기"를 한 번 눌러요.
     · 그래도 막히면 터미널에서:
         cd (이 폴더로 이동)
         xattr -d com.apple.quarantine run.command ; chmod +x run.command
         ./run.command
       또는 그냥:  bash run.command

수동 실행 (런처가 막힐 때 · Windows/macOS 공통)
  1) 이 폴더에서:  pip install -r requirements.txt   (macOS는 pip3)
  2) 그다음:       python bot.py                      (macOS는 python3)
  (Windows PowerShell 사용 시 run.ps1 도 있습니다. 실행정책 막히면 안내 문구 참고.)

포함 파일
  - run.bat / run.ps1 : Windows 실행 런처(파이썬 탐색 → 의존성 설치 → bot.py)
  - run.command       : macOS 실행 런처(더블클릭, 파이썬 탐색 → 의존성 설치 → bot.py)
  - bot.py            : 실구동 봇(현물/선물 실제 주문, 기본은 테스트넷 가짜 자금)
  - requirements.txt  : python-binance
  - macro.json        : 이 봇이 따를 매크로 설정(방향/레버리지 포함)
  - README-run.txt    : 이 안내 파일

매크로 요약
  {summary}

------------------------------------------------------------
테스트넷 키 발급 & 실행
------------------------------------------------------------
* 현물(롱, 레버리지 1) 매크로:
  1) https://testnet.binance.vision 접속 → 로그인 → "Generate HMAC_SHA256 Key"
* 선물(숏 또는 레버리지>1) 매크로:
  1) https://testnet.binancefuture.com 접속 → 로그인 → API Key/Secret 발급
  ※ 두 테스트넷은 별개입니다. 현물 키로 선물을, 선물 키로 현물을 실행할 수 없습니다.
     봇이 시장을 자동 선택하므로, 그 시장에 맞는 테스트넷 키를 입력하세요.

  2) run.bat 실행 → 프롬프트에 해당 시장의 Key/Secret 입력
  3) 봇이 연결되어 잔고를 조회하고, 매크로 조건에 따라 실제 주문(진입/청산)을
     테스트넷에 전송합니다. 주문ID/상태가 콘솔에 출력됩니다.
     (키는 메모리에서만 사용하며 저장/전송/로깅하지 않습니다.)

------------------------------------------------------------
1회 주문 상한(MAX_ORDER_USDT) 이해하기
------------------------------------------------------------
  - 현물: 상한 = 1회에 쓰는 USDT(매수 금액).
  - 선물: 기본은 "명목가치(포지션 총 노출)" 기준입니다.
        예) 상한 100 USDT, 레버리지 10배 → 노출 100 USDT, 사용 증거금 ~10 USDT.
     환경변수 ORDER_CAP_BASIS=margin 으로 바꾸면 상한이 "증거금(내 돈)" 기준이 되어
     노출이 레버리지 배수만큼 커집니다(상한 100 × 10배 = 노출 1,000 USDT). 주의.

------------------------------------------------------------
⚠️ 메인넷(실제 자금) 전환 가이드 — 사용자가 직접, 신중히
------------------------------------------------------------
이 봇은 기본이 테스트넷이며 자동으로 메인넷을 켜지 않습니다. 실거래로 전환하려면:

  1) bot.py 상단의 스위치 한 곳만 바꿉니다:
        USE_TESTNET = True   →   USE_TESTNET = False
     (바꿀 곳은 이 상수 하나뿐입니다.)
  2) 메인넷 API 키는 바이낸스 본 사이트에서 발급하되,
        - 현물 매크로는 "현물 거래(Spot Trading)" 권한만,
        - 선물 매크로는 "선물 거래(Futures)" 권한만 켜고,
        - "출금(Withdraw)" 권한은 반드시 끕니다.
        - 선물 실거래는 계정에서 선물 지갑 활성화 + USDT 증거금 이체가 필요합니다.
  3) 반드시 소액부터. 환경변수로 1회 주문 상한을 낮추세요:
        (Windows) set MAX_ORDER_USDT=10  후 run.bat 실행
        (macOS)   export MAX_ORDER_USDT=10  후 ./run.command (또는 bash run.command) 실행
     레버리지는 위험을 배수로 키웁니다. 처음엔 레버리지를 낮게, 상한도 낮게.
  4) 시장가 주문은 슬리피지가 있을 수 있습니다. 소액으로 흐름을 확인하세요.
  5) 선물 숏은 손실이 이론상 무제한입니다. 손절(stop_loss)을 반드시 설정하세요.

책임 고지
  - 실거래는 사용자 PC에서 사용자 본인의 API 키로 실행됩니다.
  - 본 도구는 투자 조언이 아니며, 수익을 보장하지 않습니다.
  - 레버리지/숏 거래는 원금 초과 손실(청산)이 발생할 수 있습니다.
  - 실거래로 인한 손익 책임은 전적으로 사용자 본인에게 있습니다.
  - 회사/서비스 맥락에서 실서비스 배포 전에는 법무·컴플라이언스 검토가 필요합니다.

이 봇은 진입/청산 주문만 하며 출금/이체 기능이 없습니다.
"""


def build_bundle(macro: Macro) -> bytes:
    """Return a zip: run.bat, run.ps1, run.command, bot.py, requirements.txt,
    macro.json, README. Windows launchers use CRLF; the macOS launcher is written
    with LF and a 0o755 mode so Finder can double-click it."""
    if macro.exchange != "binance":
        raise ValueError("국내 거래소 매크로의 실행기 직접 연결은 지원하지 않습니다")
    summary = human_summary(macro)
    macro_payload = macro.model_dump(mode="json")
    macro_payload["human_summary"] = summary

    # Windows launchers/read-me use CRLF; cmd.exe is picky about LF-only .bat.
    def crlf(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\n", "\r\n")

    def _executable_info(name: str) -> zipfile.ZipInfo:
        # Mark the entry rwxr-xr-x so it stays executable after unzip on macOS.
        info = zipfile.ZipInfo(name)
        info.external_attr = 0o755 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        return info

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("run.bat", crlf(_RUN_BAT))
        zf.writestr("run.ps1", crlf(_RUN_PS1))
        # LF endings (a CRLF shebang breaks on macOS) + executable bit.
        zf.writestr(_executable_info("run.command"), _RUN_COMMAND)
        zf.writestr("bot.py", _BOT_PY)
        zf.writestr("requirements.txt", _REQUIREMENTS_TXT)
        zf.writestr("macro.json", json.dumps(macro_payload, ensure_ascii=False, indent=2))
        zf.writestr("README-run.txt", crlf(_README_TXT.format(summary=summary)))
    return buf.getvalue()
