import { computeSessionOverlay } from "../../../lib/indicators.js";

export const strategyModule = {
  key: "strategy",
  label: "전략 조건",
  entitlement: "agent.strategy_signal",
  minimumPlan: "pro",
  availability: "live",
  buildEvents({ macro, candles, session, interval }) {
    const bars = candles || [];
    if (!macro || !bars.length) return [];
    const overlay = computeSessionOverlay(
      macro,
      session?.in_position ? session.entry_price : null,
      session?.position_side || macro.position_side,
      bars,
    );
    const lastClosedIndex = bars.reduce((found, bar, index) => (bar?.closed === false ? found : index), -1);
    if (lastClosedIndex < 0) return [];
    const recentFrom = Math.max(0, lastClosedIndex - 19);
    const markers = (overlay?.markers || [])
      .filter((marker) => marker.index >= recentFrom && marker.index <= lastClosedIndex)
      .slice(-3)
      .reverse();

    if (!markers.length) return [];

    return markers.map((marker) => {
      const bar = bars[marker.index];
      // 심각도는 진입/청산(kind)으로 가른다 — 숏 진입은 주문 방향이 매도지만
      // "새로 들어가는" 신호라 청산 경고와 같은 취급을 하면 안 된다.
      const isExit = marker.kind === "exit";
      return {
        id: `strategy-${[
          session?.session_id ?? macro.id ?? "session", macro.rule_type,
          interval || macro.candle_interval || "interval", bar?.t ?? 0,
          marker.kind || "entry", marker.side || session?.position_side || macro.position_side || "long",
        ].map((part) => encodeURIComponent(String(part))).join(":")}`,
        module: "strategy",
        severity: isExit ? "watch" : "signal",
        expression: isExit ? "warning" : "signal",
        title: marker.label || (isExit ? "청산 조건 표시" : "진입 조건 표시"),
        summary: `${interval} 공개 캔들 기준 · 참고 신호`,
        occurredAt: bar?.t || 0,
        sourceLabel: "전략 조건 재계산",
      };
    });
  },
};
