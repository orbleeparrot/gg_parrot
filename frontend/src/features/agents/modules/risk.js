function safeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export const riskModule = {
  key: "risk",
  label: "위험 감시",
  entitlement: "agent.risk",
  minimumPlan: "plus",
  availability: "live",
  buildEvents({ candles, session, interval, alertConditions = {}, receivedAt }) {
    const closed = (candles || []).filter((bar) => bar?.closed !== false && safeNumber(bar?.o) > 0);
    const events = [];
    const volatilityKey = `volatility-${interval}`;
    const previousVolatility = alertConditions[volatilityKey] || 0;
    if (closed.length) {
      const recent = closed.slice(-20);
      const latest = recent[recent.length - 1];
      const ranges = recent.map((bar) => ((safeNumber(bar.h) - safeNumber(bar.l)) / safeNumber(bar.o, 1)) * 100);
      const averageRange = ranges.reduce((sum, value) => sum + value, 0) / ranges.length;
      const latestChange = ((safeNumber(latest.c) - safeNumber(latest.o)) / safeNumber(latest.o, 1)) * 100;
      const elevated = averageRange >= 2 || Math.abs(latestChange) >= 3;
      // Keep the incident armed near its threshold. Tiny oscillations do not
      // repeat the same risk, but a higher risk band still raises an alert.
      if (elevated || (previousVolatility && (averageRange >= 1.5 || Math.abs(latestChange) >= 2))) {
        const level = Math.max(previousVolatility, 1, Math.floor(averageRange / 2), Math.floor(Math.abs(latestChange) / 3));
        events.push({
          id: `risk-volatility-${latest.t}-${interval}-${level}`,
          conditionKey: volatilityKey, conditionValue: level, repeatable: false,
          module: "risk", severity: "warning", expression: "warning",
          title: "변동성 확대 감지",
          summary: `${interval} · 평균 변동폭 ${averageRange.toFixed(2)}% · 최근 봉 ${latestChange >= 0 ? "+" : ""}${latestChange.toFixed(2)}%`,
          occurredAt: latest.t, sourceLabel: "바이낸스 확정 봉",
        });
      }
    } else if (previousVolatility) {
      // Missing candles do not establish that a risk has recovered.
      events.push({ conditionKey: volatilityKey, conditionValue: previousVolatility, silent: true });
    }

    if (session?.status === "running" && session.in_position) {
      const unrealized = session.unrealized_pct == null ? null : safeNumber(session.unrealized_pct, null);
      const previousLoss = alertConditions["position-loss"] || 0;
      if (unrealized !== null && (unrealized <= -2 || (previousLoss && unrealized <= -1.5))) {
        const level = Math.max(previousLoss, 1, Math.floor(-unrealized / 2));
        events.push({
          id: `risk-position-${session.session_id}-${level}`,
          conditionKey: "position-loss", conditionValue: level,
          module: "risk", severity: "critical", expression: "critical",
          title: "평가손실 주의",
          summary: `현재 평가손익 ${unrealized.toFixed(2)}%`,
          occurredAt: receivedAt || Date.now(), sourceLabel: "현재 보유 포지션",
        });
      } else if (unrealized === null && previousLoss) {
        events.push({ conditionKey: "position-loss", conditionValue: previousLoss, silent: true });
      }
    }
    return events;
  },
};
