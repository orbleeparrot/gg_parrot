const holderTimeFormat = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function observationTime(value) {
  const time = typeof value === "number" ? value : typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(time) && time > 0 && time <= 8.64e15 ? time : null;
}

function holderSourceUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && ["eth.blockscout.com", "xrpscan.com", "api.xrpscan.com"].includes(url.hostname)
      && !url.username && !url.password ? url.href : "";
  } catch {
    return "";
  }
}

function holderEvents(onchain) {
  if (onchain?.status !== "ready" || !["PEPE", "WETH", "XRP"].includes(onchain.coin)) return [];
  const coin = onchain.coin;
  return (Array.isArray(onchain.items) ? onchain.items : []).flatMap((item) => {
    if (!item || typeof item.id !== "string" || !new RegExp(`^onchain:${coin}:\\d+$`).test(item.id)) return [];
    const counts = [item.increased_count, item.decreased_count, item.compared_count, item.tracked_count];
    if (!counts.every((value) => Number.isSafeInteger(value) && value >= 0)) return [];
    const [increased, decreased, compared, tracked] = counts;
    if (increased + decreased === 0 || increased + decreased > compared || compared > tracked) return [];
    const observedAt = observationTime(item.occurred_at);
    const previousAt = observationTime(item.previous_observed_at);
    if (!observedAt || !previousAt || previousAt >= observedAt) return [];
    const scope = item.scope || onchain.scope || `${coin}${coin === "WETH" ? " 토큰" : ""} 상위 보유 지갑`;
    const daily = coin === "XRP" || item.daily_source || onchain.daily_source;
    return [{
      id: item.id, module: "whale_activity", severity: "info",
      title: `${coin} 지갑 잔고 변화`,
      summary: `동일 지갑 ${compared}개 비교 · 잔고 증가 ${increased}개 · 감소 ${decreased}개. ${scope}.${daily ? " 일 단위로 갱신되는 잔고 자료입니다." : ""}`,
      detailLabel: "비교 기간",
      detail: `${holderTimeFormat.format(previousAt)} ~ ${holderTimeFormat.format(observedAt)} (한국 시간)`,
      occurredAt: observedAt,
      sourceLabel: item.source_label || onchain.source_label || `${coin} 보유 지갑`,
      sourceUrl: holderSourceUrl(item.source_url || onchain.source_url),
    }];
  });
}

export const leaderModule = {
  key: "whale_activity",
  label: "고래 동향",
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
    events.push(...holderEvents(data?.onchain));
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
