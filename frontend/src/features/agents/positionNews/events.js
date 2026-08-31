function impactPresentation(effect) {
  if (effect === "favorable") return { severity: "signal", expression: "signal" };
  if (effect === "unfavorable") return { severity: "warning", expression: "warning" };
  return { severity: "info", expression: "curious" };
}

function positionCountSummary(items, sideLabel) {
  if (!items.length) return "";
  const counts = items.reduce((result, item) => {
    const key = item.position_effect;
    if (key === "favorable") result.favorable += 1;
    else if (key === "unfavorable") result.unfavorable += 1;
    else result.undecided += 1;
    return result;
  }, { favorable: 0, unfavorable: 0, undecided: 0 });
  return `${sideLabel} 기준 · 유리 ${counts.favorable}건 · 불리 ${counts.unfavorable}건 · 중립/판단 어려움 ${counts.undecided}건`;
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
    const lastCollectedAt = data.collection?.last_success_at || updatedAt;
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
    const events = overviewText ? [{
      id: `position-news-overview-${context.session_id || "session"}-${data.snapshot_id || updatedAt || "latest"}`,
      module: "position_news",
      severity: "info",
      expression: "curious",
      title: `${context.coin_name || context.asset_symbol || "종목"} 최근 헤드라인 요약`,
      summary: overviewText,
      detail: positionCountSummary(newsItems, sideLabel),
      detailLabel: "분류 요약",
      occurredAt: lastCollectedAt,
      fallbackTime: "최근 수집 확인",
      sourceLabel: "종목 헤드라인 요약 · 매매 지시 아님",
    }] : [];

    newsItems.forEach((item, index) => {
      const presentation = impactPresentation(item.position_effect);
      events.push({
        id: `position-news-${item.id || item.url || index}`,
        module: "position_news",
        severity: presentation.severity,
        expression: presentation.expression,
        title: item.position_label || `${sideLabel} 유불리 판단이 어려운 뉴스`,
        summary: item.title || "",
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
