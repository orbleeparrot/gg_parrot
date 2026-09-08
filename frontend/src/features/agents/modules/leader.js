export const leaderModule = {
  key: "whale_activity",
  label: "고래 체결",
  entitlement: "agent.leader_signal",
  minimumPlan: "pro",
  availability: "live",
  buildEvents({ featureStates, alertConditions = {} }) {
    const state = featureStates?.whale_activity;
    const data = state?.data;
    if (data?.status === "stopped") return [];
    const threshold = Number(data?.threshold_quote) || 0;
    const trades = (Array.isArray(data?.items) ? data.items : []).filter((item) => (
      item && ["buy", "sell"].includes(item.side)
      && Number.isFinite(Number(item.notional)) && Number(item.notional) > 0
      && Number(item.notional) >= threshold
      && Number.isFinite(Number(item.quantity)) && Number(item.quantity) > 0
      && Number.isFinite(Number(item.price)) && Number(item.price) > 0
    ));
    const events = trades.map((item) => ({
      id: `large-trade-${data.market || "spot"}-${data.symbol}-${item.id ?? [item.occurred_at || 0, item.side, item.price, item.quantity].join(":")}`,
      module: "whale_activity", severity: "info",
      title: `${data.symbol} 대규모 ${item.side === "buy" ? "매수" : "매도"} 체결`,
      summary: `${Number(item.notional).toLocaleString(undefined, { maximumFractionDigits: 0 })} ${data.quote_asset} · ${Number(item.quantity).toLocaleString()}개 · 체결가 ${item.price}. 특정 투자자의 보유량 변화는 알 수 없어요.`,
      occurredAt: item.occurred_at || 0, sourceLabel: `바이낸스 ${data.market === "futures" ? "선물" : "현물"} 공개 체결 표본`,
    }));
    const failed = state?.error || data?.status === "unavailable" || data?.collection?.status === "error";
    const stale = data?.stale || data?.collection?.freshness === "stale";
    const pending = !data || ["pending", "collecting"].includes(data.status)
      || ["pending", "collecting"].includes(data.collection?.status);
    if (failed || stale) {
      events.unshift({id: "large-trades-unavailable", module: "whale_activity", severity: "warning",
        conditionKey: "whale-connection", conditionValue: "unavailable",
        title: "대규모 체결 연결 확인 중", summary: "데이터 소스를 다시 확인하고 있어요.",
        occurredAt: data?.collection?.last_attempt_at || data?.observed_at || 0});
    } else if (pending && alertConditions["whale-connection"]) {
      // A collector retry is not a recovered source. Keep one incident until
      // a successful snapshot arrives, even while the shared cache is pending.
      events.unshift({conditionKey: "whale-connection",
        conditionValue: alertConditions["whale-connection"], silent: true});
    }
    return events;
  },
};
