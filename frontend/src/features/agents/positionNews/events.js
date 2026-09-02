function impactPresentation(effect) {
  if (effect === "favorable") return { severity: "signal", expression: "signal" };
  if (effect === "unfavorable") return { severity: "warning", expression: "warning" };
  return { severity: "info", expression: "curious" };
}

export const positionNewsModule = {
  key: "position_news",
  label: "맞춤 뉴스",
  entitlement: "agent.position_news",
  minimumPlan: "free",
  availability: "live",
  buildEvents({ featureStates }) {
    const data = featureStates?.position_news?.data;
    if (!data) return [];

    const context = data.context || {};
    const sideLabel = context.position_side === "short" ? "숏 포지션" : "롱 포지션";
    const updatedAt = data.updated_at || 0;
    const overviewText = data.overview?.text || "";
    const newsItems = data.items || [];
    const collectionStatus = data.collection?.status || "";
    if (data.analysis_status === "pending" || collectionStatus === "pending") {
      return [{
        id: `position-news-pending-${context.session_id || "session"}-${context.asset_symbol || "asset"}`,
        module: "position_news",
        severity: "info",
        expression: "curious",
        title: `${context.coin_name || context.asset_symbol || "선택 종목"} 뉴스 수집 대기 중`,
        summary: overviewText || "첫 중앙 수집이 끝나면 자동으로 표시됩니다.",
        occurredAt: Date.now(),
        fallbackTime: "수집 대기",
        sourceLabel: "중앙 뉴스 수집 상태",
      }];
    }
    const events = [];

    newsItems.forEach((item, index) => {
      const presentation = impactPresentation(item.position_effect);
      events.push({
        id: `position-news-${item.id || item.url || index}`,
        module: "position_news",
        severity: presentation.severity,
        expression: presentation.expression,
        title: item.title || `${sideLabel} 관련 뉴스`,
        summary: item.position_label || `${sideLabel} 유불리 판단이 어려운 뉴스`,
        detail: item.reason || "",
        occurredAt: item.published || updatedAt,
        fallbackTime: "최근 수집",
        sourceLabel: item.source || "원문",
        sourceUrl: item.url || "",
      });
    });
    return events;
  },
};
