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
    const state = featureStates?.position_news;
    const data = featureStates?.position_news?.data;
    const failed = state?.status === "error" || data?.collection?.status === "error";
    const stale = data?.collection?.freshness === "stale";
    const statusEvents = failed || stale ? [{
      id: "position-news-connection", module: "position_news", severity: "warning",
      title: failed ? "뉴스 연결 재시도 중" : "뉴스 갱신 지연",
      summary: "마지막 수집 결과를 유지하며 새 기사를 다시 확인하고 있어요.",
      occurredAt: data?.collection?.last_attempt_at || Date.now(), sourceLabel: "뉴스 수집 상태",
    }] : [];
    if (!data) return statusEvents;

    const context = data.context || {};
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
    const events = [...statusEvents];
    if (data.analysis_status === "empty" || (!newsItems.length && collectionStatus === "empty")) {
      events.push({id: `position-news-empty-${context.asset_symbol}`, module: "position_news", severity: "info",
        title: `${context.coin_name || context.asset_symbol || "선택 종목"} 관련 뉴스 없음`,
        summary: overviewText || "검색을 완료했어요. 새 기사가 있으면 자동으로 표시됩니다.",
        occurredAt: data.collection?.last_attempt_at || updatedAt, sourceLabel: "뉴스 수집 상태"});
    }

    newsItems.forEach((item, index) => {
      const presentation = impactPresentation(item.position_effect);
      events.push({
        id: `position-news-${item.id || item.url || index}`,
        module: "position_news",
        severity: presentation.severity,
        expression: presentation.expression,
        title: item.title || "관련 뉴스",
        summary: item.summary || "",
        occurredAt: item.published || updatedAt,
        fallbackTime: "최근 수집",
        sourceLabel: item.source || "원문",
        sourceUrl: item.url || "",
      });
    });
    return events;
  },
};
