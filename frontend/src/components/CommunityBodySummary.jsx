export default function CommunityBodySummary({ summary }) {
  if (!summary) return null;
  return (
    <span className={`community-body-summary is-${summary.status}`}
      data-community-summary-status={summary.status} aria-live="off">
      <span className="community-body-summary-label">{summary.label}</span>
      {summary.text ? <span className="community-body-summary-text">{summary.text}</span> : null}
    </span>
  );
}
