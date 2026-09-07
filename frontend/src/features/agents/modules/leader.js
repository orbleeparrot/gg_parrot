export const leaderModule = {
  key: "whale_activity",
  label: "고래 체결",
  entitlement: "agent.leader_signal",
  minimumPlan: "pro",
  availability: "live",
  buildEvents({ featureStates }) {
    const state = featureStates?.whale_activity;
    const data = state?.data;
    if (!data && !state?.error) return [];
    const events = (data?.items || []).map((item) => ({
      id: `large-trade-${item.id}`, module: "whale_activity", severity: "info",
      title: `${data.symbol} 대규모 ${item.side === "buy" ? "매수" : "매도"} 체결`,
      summary: `${Number(item.notional).toLocaleString(undefined, { maximumFractionDigits: 0 })} ${data.quote_asset} · ${Number(item.quantity).toLocaleString()}개 · 체결가 ${item.price}. 특정 투자자의 보유량 변화는 알 수 없어요.`,
      occurredAt: item.occurred_at, sourceLabel: `바이낸스 ${data.market === "futures" ? "선물" : "현물"} 공개 체결 표본`,
    }));
    if (state.error || data?.status === "unavailable" || data?.stale) {
      events.unshift({id: "large-trades-unavailable", module: "whale_activity", severity: "warning",
        title: "대규모 체결 연결 확인 중", summary: "데이터 소스를 다시 확인하고 있어요.", occurredAt: data?.observed_at || Date.now()});
    } else if (data?.status === "empty") {
      events.push({id: "large-trades-empty", module: "whale_activity", severity: "info",
        title: "대규모 체결 감시 중", summary: `${data.disclaimer} 기준 금액 ${Number(data.threshold_quote).toLocaleString()} ${data.quote_asset} 이상 체결은 아직 없어요.`,
        occurredAt: data.observed_at, sourceLabel: "바이낸스 공개 체결 표본"});
    }
    return events;
  },
};
