import { useEffect, useRef, useState } from "react";
import {
  MAX_SESSION_LOG_ITEMS, SESSION_LOG_CATEGORIES, SESSION_LOG_COMPONENTS, SESSION_LOG_SEVERITIES,
  formatSessionLogTime, normalizeSessionLogQuery, safeSessionLogData, safeSessionLogUrl,
  sessionLogCategory, sessionLogComponent, sessionLogKindLabel, sessionLogSeverity, sessionLogSourceLabel, sessionLogTimestamp,
} from "../features/agents/sessionLogs.js";
import { Icon } from "./icons.jsx";
import "./AgentSessionLog.css";

const EMPTY_STATE = { items: [], status: "idle", error: "", hasMore: false };
const FIELD_LABELS = {
  outcome: "처리 결과", status: "상태", provenance: "확인 근거", elapsed_ms: "소요 시간(ms)",
  command_id: "명령 ID", order_id: "거래소 주문 ID", client_order_id: "클라이언트 주문 ID",
  exchange: "거래소", symbol: "티커", side: "매수/매도", quantity: "수량", price: "가격", fee: "수수료",
  attempt: "시도 횟수", retry_count: "재시도 횟수", retryable: "재시도 가능", next_retry_at: "다음 재시도",
  http_status: "HTTP 상태", error_code: "오류 코드", error_type: "오류 유형", reason: "사유",
  batch_id: "수집 배치 ID", snapshot_id: "스냅샷 ID", article_id: "기사 ID", source: "출처",
  query: "검색어", found_count: "발견 건수", duplicate_count: "중복 건수", excluded_count: "제외 건수",
  stored_count: "저장 건수", count: "제공 기사 수", article_count: "제공 기사 수",
  collection_status: "수집 상태", translation_status: "번역 상태", analysis_status: "분석 상태",
  reused: "기존 자료 재사용", actual_ai_call: "실제 AI 호출", model: "모델", version: "설정 버전",
  receipt_kind: "수신 확인 유형", runner_version: "실행기 버전", mode: "실행 모드",
  origin: "기록 주체", observed_at: "관측 시각", report_type: "보고 유형",
  executed_qty: "보고된 체결 수량", fill_price: "보고된 체결 가격", requested_qty: "요청 수량",
  requested_notional: "요청 금액", exchange_order_id: "거래소 주문 ID", raw_status: "원본 응답 상태",
  fees_known: "수수료 확인 여부", verification: "검증 범위", ack_source: "결과 보고 주체",
  source_counts: "제공 기사 출처별 건수", source_results: "소스별 처리 결과",
  flow_run_id: "Prefect 흐름 실행 ID", task_run_id: "Prefect 작업 실행 ID", collection_run_id: "수집 회차 ID",
  duration_ms: "처리 시간(ms)", retry_at: "다음 재시도 시각", attempts: "실제 시도 횟수",
  max_attempts: "최대 시도 횟수", command_seq: "명령 순번", action: "주문 동작", signal_price: "신호 시점 가격",
  notional_frac: "투입 금액 비율", qty_frac: "수량 비율", expires_ms: "명령 만료 시각(ms)",
  fetched_count: "가져온 건수", deduped_count: "중복 제거 후 건수", filtered_count: "관련성 필터 후 건수",
  pending_count: "처리 대기 기사 수", cache_hit: "캐시 자료 사용", terminal: "최종 실패 여부",
  collected_at: "수집 시각", last_success_at: "마지막 수집 성공", next_collection_ms: "다음 수집 시각(ms)",
  request_id: "공급자 요청 ID", error: "오류 내용", realized_pnl: "보고된 실현 손익", asset_symbol: "자산 티커",
};
const VERIFIED_SCOPE = {
  runner_reported_not_exchange_verified: "실행기 보고 · 거래소 독립 검증 아님",
  server_command_state: "서버 명령 상태 · 거래소 체결 확인 아님",
  server_signal_not_exchange_order: "서버 조건 신호 · 거래소 주문 아님",
};

function text(value, fallback = "", limit = 1600) {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, limit) : fallback;
}

function LogTime({ value, label, compact = false }) {
  const formatted = formatSessionLogTime(value);
  const valid = formatted !== "시각 확인 불가";
  return <span className="agent-session-log-time">
    {label ? <span>{label} </span> : null}
    <time title={formatted} dateTime={valid ? new Date(sessionLogTimestamp({ ts: value })).toISOString() : undefined}>{compact && valid ? formatted.slice(5, -4).replace(/^\d{2}\.\d{2}/, (date) => date.replace(".", "-")) : formatted}</time>
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
    <ol>{articles.map((article, index) => {
      const url = safeSessionLogUrl(article.url);
      const title = text(article.title, "제목 확인 불가", 500);
      const published = formatSessionLogTime(article.published);
      return <li key={`${url || title}-${index}`}>
        {url ? <a href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
          {title}<Icon name="arrowUpRight" size={14} />
        </a> : <span className="agent-session-log-article-title">{title}</span>}
        <span className="agent-session-log-article-meta">{text(article.source, "출처 확인 불가", 120)}
          {published !== "시각 확인 불가" ? <span> · 게시 {published}</span> : null}</span>
      </li>;
    })}</ol>
    {count > articles.length ? <p className="agent-session-log-article-meta">기사 목록 중 {articles.length}건을 표시해요.</p> : null}
  </details>;
}

function FieldValue({ value, field }) {
  if (value === null || value === undefined || value === "") return "기록되지 않음";
  if (field === "verification" && typeof value === "string" && Object.hasOwn(VERIFIED_SCOPE, value)) return VERIFIED_SCOPE[value];
  if (field === "ack_source" && ["runner", "engine"].includes(value)) return value === "runner" ? "실행기 보고" : "서버 엔진";
  if (field === "action" && ["buy", "sell", "short", "cover"].includes(value)) return { buy: "매수", sell: "매도", short: "공매도", cover: "공매도 청산" }[value];
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "object") return <pre>{JSON.stringify(value, null, 2)}</pre>;
  return String(value);
}

function SessionLogRow({ item, onOperation, onContext, onCopy }) {
  const category = sessionLogCategory(item);
  const level = sessionLogSeverity(item);
  const component = sessionLogComponent(item);
  const details = safeSessionLogData(item.details || {});
  const fields = Object.entries(details).filter(([key]) => !["articles", "examples", "request", "result"].includes(key));
  const structured = level !== "UNKNOWN" || !!item.event_name;
  const receivedOnly = item.time_basis === "server_reception";
  const occurredAt = receivedOnly ? null : item.occurred_at ?? item.ts;
  return <li className={`agent-session-log-entry is-${category}${["ERROR", "FATAL"].includes(level) ? " is-error" : level === "WARN" ? " is-warn" : ""}${item.context_anchor ? " is-anchor" : ""}`} data-log-id={String(item.id)}>
    <details className="agent-session-log-row">
      <summary className="agent-session-log-line">
        <span className="agent-session-log-chevron" aria-hidden="true">▸</span>
        <LogTime value={receivedOnly ? item.received_at : occurredAt} label={receivedOnly ? "수신" : ""} compact />
        <span className={`agent-session-log-level${level === "UNKNOWN" ? " is-unknown" : ""}`} title={level === "UNKNOWN" ? "이전 형식: 실제 수준이 기록되지 않았습니다." : "기록된 로그 수준"}>{level}</span>
        <span className="agent-session-log-component" title={sessionLogSourceLabel(item)}>{component}</span>
        <span className="agent-session-log-kind" title={text(item.event_name, "사건 이름 미기록", 96)}>{sessionLogKindLabel(item)}</span>
        <span className="agent-session-log-message">{text(safeSessionLogData(item.message), "내용 확인 불가")}</span>
      </summary>
      <div className="agent-session-log-expanded">
        <div className="agent-session-log-entry-meta">
          <strong>{text(item.event_name, "이전 형식 · 사건 이름 미기록", 96)}</strong>
          <span>event_id={String(item.id)}</span>
          <span>{sessionLogSourceLabel(item)}</span>
        </div>
        <dl className="agent-session-log-fields">
          <dt>발생 시각</dt><dd>{receivedOnly ? "기록되지 않음 · 서버 수신 시각으로 표시" : <LogTime value={occurredAt} />}</dd>
          <dt>서버 수신</dt><dd><LogTime value={item.received_at} /></dd>
          <dt>작업 ID</dt><dd>{text(item.operation_id, "기록되지 않음", 160)}</dd>
          <dt>수준 / 주체</dt><dd>{level} / {component}{!structured ? " · 상세 필드 미기록" : ""}</dd>
          {fields.map(([key, value]) => <div className="agent-session-log-field" key={key}>
            <dt>{Object.hasOwn(FIELD_LABELS, key) ? FIELD_LABELS[key] : key}</dt><dd><FieldValue value={value} field={key} /></dd>
          </div>)}
        </dl>
        {(details.request || details.result) ? <div className="agent-session-log-request-result">
          <section><h3>기록된 요청</h3><FieldValue value={details.request} /></section>
          <section><h3>기록된 결과</h3><FieldValue value={details.result} /></section>
        </div> : null}
        {category === "news" ? <NewsDetails details={details} /> : null}
        <div className="agent-session-log-row-actions">
          {item.operation_id ? <button type="button" onClick={() => onOperation(item.operation_id)}>같은 작업 보기</button> : null}
          <button type="button" onClick={() => onContext(Number(item.id))}>전후 기록 보기</button>
          <button type="button" onClick={() => onCopy(item)}>이 기록 JSON 복사</button>
        </div>
        <details className="agent-session-log-json"><summary>정제된 원본 JSON</summary>
          <pre>{JSON.stringify(safeSessionLogData(item), null, 2)}</pre></details>
      </div>
    </details>
  </li>;
}

export default function AgentSessionLog({
  session, state = EMPTY_STATE, category = "all", onCategoryChange,
  query = {}, onQueryChange, paused = false, onPauseChange, onRefresh, onLoadMore,
}) {
  const [search, setSearch] = useState(query.q || "");
  const [wrap, setWrap] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const body = useRef(null);
  const queryRef = useRef(query);
  queryRef.current = query;
  useEffect(() => { setSearch(query.q || ""); }, [query.q]);
  useEffect(() => {
    if (search.trim() === (query.q || "")) return undefined;
    const timer = setTimeout(() => onQueryChange?.({ ...queryRef.current, q: search.trim().slice(0, 200), contextId: null }), 350);
    return () => clearTimeout(timer);
  }, [onQueryChange, query.q, search]);
  useEffect(() => {
    if (!paused && body.current) body.current.scrollTop = 0;
  }, [paused, state.key]);
  const items = Array.isArray(state.items) ? state.items : [];
  const loading = state.status === "loading" || state.status === "idle";
  const busy = loading || !!state.loadingMore;
  const error = !!state.error || state.status === "error";
  const normalized = normalizeSessionLogQuery(query);
  const filtered = Object.values(normalized).some(Boolean) || category !== "all";
  const cap = Number.isSafeInteger(state.retention?.event_cap) && state.retention.event_cap > 0 ? state.retention.event_cap : MAX_SESSION_LOG_ITEMS;
  const newsCap = Number.isSafeInteger(state.retention?.news_cap) && state.retention.news_cap > 0 ? state.retention.news_cap : null;
  const activeLabel = SESSION_LOG_CATEGORIES.find(({ key }) => key === category)?.label || "전체";
  const context = state.context || session || {};
  const sourceComponents = [...new Set([...SESSION_LOG_COMPONENTS, ...items.map(sessionLogComponent).filter((value) => value !== "legacy")])].sort();
  const updateQuery = (changes) => onQueryChange?.({ ...query, contextId: null, ...changes });
  const related = (changes) => { onCategoryChange?.("all"); onQueryChange?.(changes); };
  const reset = () => { setSearch(""); onCategoryChange?.("all"); onQueryChange?.({}); };
  const copy = async (item) => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(JSON.stringify(safeSessionLogData(item), null, 2));
      setCopyStatus("이 기록의 정제된 JSON을 복사했어요.");
    } catch { setCopyStatus("복사 권한을 확인해주세요. 정제된 원본 JSON을 펼쳐 직접 복사할 수 있어요."); }
  };
  const exportLoaded = () => {
    const content = { scope: "현재 조건으로 불러온 기록만 · 전체 보관 기록이 아님", session_id: session?.session_id,
      query: normalized, category, exported_at: new Date().toISOString(), items: items.map((item) => safeSessionLogData(item)) };
    const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `macro-${session?.session_id || "session"}-loaded-logs.json`;
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    setCopyStatus(`현재 불러온 ${items.length}행을 내보냈어요. 전체 보관 기록은 아니에요.`);
  };

  return <aside className={`agent-chat agent-session-log${wrap ? " is-wrapped" : ""}`} aria-label={`${session?.symbol || "매크로"} 활동 로그`}>
    <header className="agent-session-log-head">
      <div className="agent-session-log-title-row">
        <div><h2 className="t-label"><span aria-hidden="true">&gt;_ </span>활동 로그</h2>
          <p className="t-caption">{context.exchange || session?.macro?.exchange || "거래소 미기록"} · {context.symbol || session?.symbol || "티커 미기록"} · {context.mode || session?.mode || "모드 미기록"} · 세션 #{session?.session_id}</p>
        </div>
        <button type="button" className="btn btn-s btn-secondary" disabled={busy} onClick={onRefresh}>{loading ? "확인 중…" : "새로고침"}</button>
      </div>
      <div className="agent-session-log-tools">
        <input type="search" value={search} onChange={(event) => setSearch(event.target.value)}
          aria-label="활동 로그 검색" placeholder="보관된 로그 전체 검색 · 메시지 / 사건 / 작업 ID" maxLength={200} />
        <button type="button" aria-pressed={wrap} onClick={() => setWrap((value) => !value)}>줄바꿈</button>
      </div>
      <button type="button" className="agent-session-log-advanced-toggle" aria-expanded={advanced}
        onClick={() => setAdvanced((value) => !value)}>상세 필터{normalized.severity || normalized.component || normalized.sinceMs ? " · 적용 중" : ""}</button>
      <div className={`agent-session-log-query-tools${advanced ? " is-expanded" : ""}`}>
        <label>수준<select aria-label="활동 로그 수준" value={normalized.severity} onChange={(event) => updateQuery({ severity: event.target.value })}>
          <option value="">전체 수준</option>{SESSION_LOG_SEVERITIES.map((level) => <option key={level} value={level}>{level === "UNKNOWN" ? "UNKNOWN · 이전 형식" : level}</option>)}</select></label>
        <label>주체<select aria-label="활동 로그 주체" value={normalized.component} onChange={(event) => updateQuery({ component: event.target.value })}>
          <option value="">전체 주체</option>{sourceComponents.map((component) => <option key={component} value={component}>{component}</option>)}</select></label>
        <label>서버 수신<select aria-label="활동 로그 서버 수신 기간" value={normalized.sinceMs ? "selected" : "all"} onChange={(event) => {
          const hours = Number(event.target.value); updateQuery({ sinceMs: Number.isFinite(hours) && hours > 0 ? Date.now() - hours * 3600000 : null, untilMs: null });
        }}><option value="all">전체 보관 기간</option>{normalized.sinceMs ? <option value="selected">{formatSessionLogTime(normalized.sinceMs).slice(5, -4)} 이후</option> : null}
          <option value="1">최근 1시간</option><option value="24">최근 24시간</option><option value="168">최근 7일</option></select></label>
      </div>
      {normalized.operationId || normalized.contextId || normalized.eventName ? <div className="agent-session-log-scope" role="status">
        <span>{normalized.contextId ? `사건 #${normalized.contextId}의 전후 문맥 · 다른 필터는 적용하지 않음` : normalized.operationId ? `같은 작업: ${normalized.operationId}` : `사건: ${normalized.eventName}`}</span>
        <button type="button" onClick={reset}>조회 조건 해제</button></div> : null}
      <div className="agent-session-log-filters" role="group" aria-label="활동 로그 종류">
        {SESSION_LOG_CATEGORIES.map(({ key, label }) => <button type="button" key={key} aria-pressed={category === key}
          className={category === key ? "is-active" : ""} onClick={() => { if (normalized.contextId) updateQuery({ contextId: null }); onCategoryChange?.(key); }}>{label}</button>)}
      </div>
      <div className="agent-session-log-stream-status">
        <span>{paused ? "화면만 일시정지 · 매크로 계속 실행" : session?.status === "running" ? "10초마다 갱신 · 오류 시 최대 30초 간격" : "종료 기록 · 자동 갱신 없음"}</span>
        <button type="button" aria-pressed={paused} onClick={() => onPauseChange?.(!paused)}>{paused ? "자동 갱신 재개" : "화면 일시정지"}</button>
      </div>
    </header>
    <div ref={body} className="agent-session-log-body" role="region" tabIndex={0} aria-label={`${activeLabel} 활동 기록`} aria-busy={busy}>
      <div className="agent-session-log-columns" aria-hidden="true"><span /> <span>시각 · KST</span><span>수준</span><span>주체</span><span>사건</span><span>메시지 · 행을 펼치면 상세</span></div>
      {error ? <div className="agent-session-log-error" role="status"><Icon name="triangleAlert" size={18} />
        <p>{items.length ? "활동 로그 갱신에 실패했어요. 마지막 확인 기록은 유지해요." : "활동 로그를 불러오지 못했어요."}</p>
        <button type="button" className="btn btn-s btn-secondary" disabled={busy} onClick={onRefresh}>다시 시도</button></div> : null}
      {items.length ? <ol className="agent-session-log-list" aria-label="저장된 활동 기록">
        {items.map((item) => <SessionLogRow key={String(item.id)} item={item} onOperation={(operationId) => related({ operationId })}
          onContext={(contextId) => related({ contextId })} onCopy={copy} />)}</ol>
        : loading ? <p className="agent-session-log-empty" role="status">저장된 활동 로그를 확인하고 있어요.</p>
          : !error ? <div className="agent-session-log-empty"><p>{filtered ? "검색 조건과 일치하는 로그가 없어요." : "아직 저장된 활동 기록이 없어요."}</p>
            <p className="t-caption">보관된 서버 기록에서 검색합니다. 기록되지 않은 상세 정보는 추정하지 않아요.</p></div> : null}
      {state.hasMore ? <button type="button" className="btn btn-m btn-secondary agent-session-log-more" disabled={busy} onClick={onLoadMore}>
        {state.loadingMore ? "이전 기록 확인 중…" : "이전 기록 더 보기"}</button> : null}
    </div>
    <footer className="agent-session-log-foot">
      <div className="agent-session-log-footer-tools"><span>수신 최신순 · 불러온 {items.length}행{state.hasMore ? " · 이전 기록 있음" : ""}</span>
        <button type="button" disabled={!items.length} onClick={exportLoaded}>불러온 기록 JSON 내보내기</button></div>
      {copyStatus ? <p role="status">{copyStatus}</p> : null}
      <p>뉴스 수집 ≠ 제공 ≠ 열람 · 주문 요청 ≠ 접수 ≠ 체결 · UNKNOWN은 실제 수준 미기록</p>
      <details><summary>보관 정책 · 최대 {cap}개{newsCap ? ` / 뉴스 ${newsCap}개` : ""}</summary>
        <p>세션별 최대 {cap}개{newsCap ? ` · 뉴스 최대 ${newsCap}개` : ""} 보관. 검색은 남아 있는 기록에만 적용됩니다.</p>
        {state.retention?.history_days && state.retention?.history_keep ? <p>종료 기록은 최근 {state.retention.history_keep}개·{state.retention.history_days}일 기준으로 정리해요. 보관한 세션은 자동 정리에서 제외돼요.</p> : null}
      </details>
    </footer>
  </aside>;
}
