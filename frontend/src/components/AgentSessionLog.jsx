import {
  MAX_SESSION_LOG_ITEMS,
  SESSION_LOG_CATEGORIES,
  formatSessionLogTime,
  isSessionLogError,
  matchesSessionLogCategory,
  safeSessionLogUrl,
  sessionLogCategory,
  sessionLogKindLabel,
  sessionLogSourceLabel,
  sessionLogTimestamp,
} from "../features/agents/sessionLogs.js";
import { Icon } from "./icons.jsx";
import "./AgentSessionLog.css";

const EMPTY_STATE = { items: [], status: "idle", error: "", hasMore: false };

function text(value, fallback = "", limit = 1600) {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, limit) : fallback;
}

function LogTime({ value, label }) {
  const formatted = formatSessionLogTime(value);
  const valid = formatted !== "시각 확인 불가";
  return <span className="agent-session-log-time">
    {label ? <span>{label} </span> : null}
    <time className="num" dateTime={valid ? new Date(value).toISOString() : undefined}>{formatted}</time>
  </span>;
}

function NewsDetails({ details }) {
  const provided = details?.articles ?? details?.examples;
  const articles = Array.isArray(provided) ? provided.filter((item) => item && typeof item === "object").slice(0, 25) : [];
  const givenCount = details?.article_count ?? details?.count;
  const count = Number.isSafeInteger(givenCount) && givenCount >= 0 ? givenCount : articles.length;
  if (!articles.length) return null;
  return <details className="agent-session-log-news-details">
    <summary>제공 기사 목록 <span className="num">{count}</span>건</summary>
    <ol>
      {articles.map((article, index) => {
        const url = safeSessionLogUrl(article.url);
        const title = text(article.title, "제목 확인 불가", 500);
        const published = formatSessionLogTime(article.published);
        return <li key={`${url || title}-${index}`}>
          {url ? <a href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
            {title}<Icon name="arrowUpRight" size={14} />
          </a> : <span className="agent-session-log-article-title">{title}</span>}
          <span className="agent-session-log-article-meta">
            {text(article.source, "출처 확인 불가", 120)}
            {published !== "시각 확인 불가" ? <span> · 게시 {published}</span> : null}
          </span>
        </li>;
      })}
    </ol>
    {count > articles.length ? <p className="agent-session-log-article-meta">기사 목록 중 {articles.length}건을 표시해요.</p> : null}
  </details>;
}

function SessionLogRow({ item }) {
  const category = sessionLogCategory(item);
  const occurredAt = sessionLogTimestamp(item);
  const receivedAt = sessionLogTimestamp({ received_at: item.received_at });
  const delayed = occurredAt !== null && receivedAt !== null && Math.abs(receivedAt - occurredAt) > 5000;
  return <li className={`agent-session-log-entry is-${category}${isSessionLogError(item) ? " is-error" : ""}`} data-log-id={String(item.id)}>
    <div className="agent-session-log-entry-meta">
      <span className="agent-session-log-kind">{sessionLogKindLabel(item)}</span>
      <span className="agent-session-log-source">{sessionLogSourceLabel(item)}</span>
    </div>
    <LogTime value={occurredAt} />
    {delayed ? <LogTime value={receivedAt} label="서버 수신" /> : null}
    <p className="agent-session-log-message">{text(item.message, "내용 확인 불가")}</p>
    {category === "news" ? <NewsDetails details={item.details} /> : null}
  </li>;
}

export default function AgentSessionLog({
  session, state = EMPTY_STATE, category = "all", onCategoryChange, onRefresh, onLoadMore,
}) {
  const items = Array.isArray(state.items) ? state.items : [];
  const loading = state.status === "loading" || state.status === "idle";
  const busy = loading || !!state.loadingMore;
  const error = !!state.error || state.status === "error";
  const visible = items.filter((item) => matchesSessionLogCategory(item, category));
  const cap = Number.isSafeInteger(state.retention?.event_cap) && state.retention.event_cap > 0
    ? state.retention.event_cap : MAX_SESSION_LOG_ITEMS;
  const newsCap = Number.isSafeInteger(state.retention?.news_cap) && state.retention.news_cap > 0
    ? state.retention.news_cap : null;
  const activeLabel = SESSION_LOG_CATEGORIES.find(({ key }) => key === category)?.label || "전체";

  return <aside className="agent-chat agent-session-log" aria-label={`${session?.symbol || "매크로"} 활동 로그`}>
    <header className="agent-session-log-head">
      <div className="agent-session-log-title-row">
        <div>
          <h2 className="t-label">활동 로그</h2>
          <p className="t-caption">서버 수신 순 · KST</p>
        </div>
        <button type="button" className="btn btn-s btn-secondary" disabled={busy} onClick={onRefresh}>
          {loading ? "확인 중…" : "새로고침"}
        </button>
      </div>
      <div className="agent-session-log-filters" role="group" aria-label="활동 로그 종류">
        {SESSION_LOG_CATEGORIES.map(({ key, label }) => <button type="button" key={key}
          aria-pressed={category === key} className={category === key ? "is-active" : ""}
          onClick={() => onCategoryChange?.(key)}>{label}</button>)}
      </div>
    </header>

    <div className="agent-session-log-body" role="region" tabIndex={0} aria-label={`${activeLabel} 활동 기록`} aria-busy={busy}>
      {error ? <div className="agent-session-log-error" role="status">
        <Icon name="triangleAlert" size={18} />
        <p>{items.length ? "활동 로그 갱신에 실패했어요. 마지막 확인 기록은 유지해요." : "활동 로그를 불러오지 못했어요."}</p>
        <button type="button" className="btn btn-s btn-secondary" disabled={busy} onClick={onRefresh}>다시 시도</button>
      </div> : null}
      {visible.length ? <ol className="agent-session-log-list" aria-label="저장된 활동 기록">
        {visible.map((item) => <SessionLogRow key={String(item.id)} item={item} />)}
      </ol> : loading ? <p className="agent-session-log-empty" role="status">저장된 활동 로그를 확인하고 있어요.</p>
        : !error ? <div className="agent-session-log-empty">
          <p>{category === "all" ? "아직 저장된 활동 기록이 없어요." : `${activeLabel} 기록이 아직 없어요.`}</p>
          <p className="t-caption">주문·체결과 뉴스 응답 수신이 기록되면 여기서 확인할 수 있어요.</p>
        </div> : null}
      {state.hasMore ? <button type="button" className="btn btn-m btn-secondary agent-session-log-more"
        disabled={busy} onClick={onLoadMore}>{state.loadingMore ? "이전 기록 확인 중…" : "이전 기록 더 보기"}</button> : null}
    </div>

    <footer className="agent-session-log-foot">
      <p>뉴스 기록은 응답 수신 확인이며 실제 열람·매매 근거를 뜻하지 않아요.</p>
      <p>세션별 최대 <span className="num">{cap}</span>개 보관{newsCap ? <> · 뉴스 최대 <span className="num">{newsCap}</span>개</> : null}
        {items.length >= MAX_SESSION_LOG_ITEMS ? " · 한 번에 표시 가능한 기록 수에 도달했어요." : ""}</p>
      {state.retention?.history_days && state.retention?.history_keep ? <p>종료 기록은 최근 {state.retention.history_keep}개·{state.retention.history_days}일 기준으로 정리해요. 보관한 세션은 자동 정리에서 제외돼요.</p> : null}
    </footer>
  </aside>;
}
