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
  buildEvents({ candles, session, interval }) {
    const closed = (candles || []).filter((bar) => bar?.closed !== false && safeNumber(bar?.o) > 0);
    if (!closed.length) return [];
    const recent = closed.slice(-20);
    const latest = recent[recent.length - 1];
    const ranges = recent.map((bar) => ((safeNumber(bar.h) - safeNumber(bar.l)) / safeNumber(bar.o, 1)) * 100);
    const averageRange = ranges.reduce((sum, value) => sum + value, 0) / ranges.length;
    const latestChange = ((safeNumber(latest.c) - safeNumber(latest.o)) / safeNumber(latest.o, 1)) * 100;
    const elevated = averageRange >= 2 || Math.abs(latestChange) >= 3;
    const events = [{
      id: `risk-volatility-${latest.t}-${interval}`,
      module: "risk",
      severity: elevated ? "warning" : "info",
      expression: elevated ? "warning" : "focused",
      title: elevated ? "변동성 확대 감지" : "변동성 점검 완료",
      summary: `${interval} · 평균 변동폭 ${averageRange.toFixed(2)}% · 최근 봉 ${latestChange >= 0 ? "+" : ""}${latestChange.toFixed(2)}%`,
      occurredAt: latest.t,
      sourceLabel: "바이낸스 확정 봉",
    }];

    if (session?.status === "running" && session.in_position) {
      const unrealized = safeNumber(session.unrealized_pct);
      events.push({
        id: `risk-position-${session.session_id}-${Math.round(unrealized * 10)}`,
        module: "risk",
        severity: unrealized <= -2 ? "critical" : unrealized < 0 ? "watch" : "info",
        expression: unrealized <= -2 ? "critical" : unrealized < 0 ? "warning" : "focused",
        title: unrealized <= -2 ? "평가손실 주의" : "포지션 위험 점검",
        summary: `현재 평가손익 ${unrealized >= 0 ? "+" : ""}${unrealized.toFixed(2)}%`,
        occurredAt: Date.now(),
        sourceLabel: "현재 보유 포지션",
      });
    }
    return events;
  },
};
