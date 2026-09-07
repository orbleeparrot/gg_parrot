function impactPresentation(effect) {
  if (effect === "favorable") return { severity: "signal", expression: "signal" };
  if (effect === "unfavorable") return { severity: "warning", expression: "warning" };
  return { severity: "info", expression: "curious" };
}

function normalizeIdentity(value) {
  return String(value ?? "").normalize("NFKC").replace(/\s+/g, " ").trim().toLowerCase();
}

function articleIdentity(item) {
  if (item.id != null && String(item.id).trim()) return String(item.id);
  const sourceUrl = String(item.url || "").trim();
  if (sourceUrl) {
    try {
      const url = new URL(sourceUrl);
      url.hash = "";
      for (const key of [...url.searchParams.keys()]) {
        if (/^utm_/i.test(key) || ["fbclid", "gclid"].includes(key.toLowerCase())) url.searchParams.delete(key);
      }
      url.searchParams.sort();
      return url.href;
    } catch {
      return sourceUrl;
    }
  }
  const title = normalizeIdentity(item.original_title || item.title_original || item.title);
  if (!title) return null;
  const published = item.published ? Date.parse(item.published) : NaN;
  return `article:${JSON.stringify([
    title, normalizeIdentity(item.source),
    Number.isFinite(published) ? published : normalizeIdentity(item.published),
  ])}`;
}

export const positionNewsModule = {
  key: "position_news",
  label: "맞춤 뉴스",
  entitlement: "agent.position_news",
  minimumPlan: "free",
  availability: "live",
  buildEvents({ featureStates, alertConditions = {} }) {
    const state = featureStates?.position_news;
    const data = featureStates?.position_news?.data;
    const failed = state?.status === "error" || data?.collection?.status === "error";
    const stale = data?.collection?.freshness === "stale";
    const retrying = ["loading", "idle"].includes(state?.status) && alertConditions["news-connection"];
    const statusEvents = retrying ? [{
      conditionKey: "news-connection", conditionValue: alertConditions["news-connection"], silent: true,
    }] : failed || stale ? [{
      id: "position-news-connection", module: "position_news", severity: "warning",
      conditionKey: "news-connection", conditionValue: failed ? "error" : "stale",
      title: failed ? "뉴스 연결 재시도 중" : "뉴스 갱신 지연",
      summary: "마지막 수집 결과를 유지하며 새 기사를 다시 확인하고 있어요.",
      occurredAt: data?.collection?.last_attempt_at || 0, sourceLabel: "뉴스 수집 상태",
    }] : [];
    if (!data) return statusEvents;

    const newsItems = Array.isArray(data.items) ? data.items : [];
    const events = [...statusEvents];

    newsItems.forEach((item) => {
      if (!item) return;
      const identity = articleIdentity(item);
      if (!identity) return;
      const presentation = impactPresentation(item.position_effect);
      events.push({
        id: `position-news-${identity}`,
        module: "position_news",
        severity: presentation.severity,
        expression: presentation.expression,
        title: item.title || "관련 뉴스",
        summary: item.summary || "",
        occurredAt: item.published || 0,
        fallbackTime: "최근 수집",
        sourceLabel: item.source || "원문",
        sourceUrl: item.url || "",
      });
    });
    return events;
  },
};
