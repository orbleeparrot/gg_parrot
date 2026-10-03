"""Human-readable one-line summary of a macro (for UI / cards / gallery)."""
from __future__ import annotations

from .schema import Macro, PositionSide, RuleType


def _coin(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("KRW-"):
        return s[4:]
    for quote in ("USDT", "BUSD", "USDC", "USD"):
        if s.endswith(quote):
            return s[: -len(quote)]
    return s


def _num(x: float) -> str:
    """Compact number: drop trailing .0, add thousands separators for ints."""
    f = float(x)
    if f.is_integer():
        return f"{int(f):,}"
    return f"{f:g}"


def human_summary(macro: Macro) -> str:
    coin = _coin(macro.symbol)
    side_ko = "롱" if macro.position_side is PositionSide.LONG else "숏"
    ratio_pct = _num(macro.risk.invest_ratio * 100)
    sl = macro.risk.stop_loss_pct
    p = macro.params

    if macro.rule_type is RuleType.A:
        tp = _num(p["take_profit_pct"])
        if macro.position_side is PositionSide.LONG:
            core = f"평단 대비 +{tp}% 익절"
            if sl:
                core += f" / -{_num(sl)}% 손절"
            core += " 후 재진입"
        else:
            core = f"평단 대비 -{tp}% 하락 익절 / +{_num(sl)}% 상승 손절 후 재진입"
        parts = [coin, side_ko, core, f"자금 {ratio_pct}% 투입"]

    elif macro.rule_type is RuleType.B:
        buy = _num(p["buy_price"])
        sell = _num(p["sell_price"])
        if macro.position_side is PositionSide.LONG:
            core = f"{buy} 이하 매수 / {sell} 이상 매도"
        else:
            core = f"{sell} 이상 숏 진입 / {buy} 이하 청산"
        if sl:
            core += f" · -{_num(sl)}% 손절"
        parts = [coin, side_ko, core, f"자금 {ratio_pct}% 투입"]

    elif macro.rule_type is RuleType.C:  # DCA
        amount = _num(p["amount_per_buy"])
        interval = int(p["interval_days"])
        core = f"{interval}일마다 {amount} 분할매수(DCA)"
        if sl:
            core += f" · -{_num(sl)}% 손절"
        parts = [coin, side_ko, core]

    else:  # D~J
        core = _new_type_core(macro.rule_type, p)
        if sl:
            core += f" · -{_num(sl)}% 손절"
        parts = [coin, side_ko, core, f"자금 {ratio_pct}% 투입"]

    # Leverage is a macro condition: surface the multiplier (1x stays hidden so
    # non-leveraged macros read exactly as before).
    if macro.leverage and macro.leverage > 1:
        mode_ko = "격리" if macro.margin_mode == "isolated" else macro.margin_mode
        parts.append(f"{macro.leverage}배 레버리지({mode_ko})")

    if macro.exchange != "binance":
        from ..exchanges import capabilities
        parts.insert(0, f"{capabilities(macro.exchange)['label']} · KRW 현물")
    return " · ".join(parts)


def _new_type_core(rt: RuleType, p: dict) -> str:
    if rt is RuleType.D:
        return f"{_num(p['lower_price'])}~{_num(p['upper_price'])} 구간 {int(p['grid_count'])}격자 그리드"
    if rt is RuleType.E:
        return f"+{_num(p['activation_profit'])}% 이후 {_num(p['trail_percent'])}% 트레일링 스탑"
    if rt is RuleType.F:
        return f"RSI({int(p['rsi_period'])}) {_num(p['entry_threshold'])}↓ 진입 / {_num(p['exit_threshold'])}↑ 청산"
    if rt is RuleType.G:
        strat = "역추세" if p.get("strategy") == "reversion" else "돌파"
        return f"볼린저({int(p['bb_period'])}, {_num(p['bb_std'])}σ) {strat}"
    if rt is RuleType.H:
        return f"기본 {_num(p['base_order_size'])} + 세이프티 {int(p['max_safety_orders'])}회 물타기 / +{_num(p['take_profit'])}% 익절"
    if rt is RuleType.I:
        return f"변동성 돌파 (k={_num(p['k'])})"
    if rt is RuleType.J:
        return f"{p.get('ma_type', 'SMA')} {int(p['fast_period'])}/{int(p['slow_period'])} 골든크로스"
    if rt is RuleType.K:
        core = f"롱 → −{_num(p['drop_trigger_pct'])}% 하락 시 {_num(p['partial_exit_pct'])}% 매도"
        if p.get("flip_to_short", True):
            core += " 후 숏 전환"
        core += f" (숏 익절 {_num(p['short_take_profit_pct'])}% / 손절 {_num(p['short_stop_loss_pct'])}%)"
        return core
    return rt.value
