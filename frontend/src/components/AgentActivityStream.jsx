import { useEffect, useMemo, useRef, useState } from "react";
import { RULE_TYPES } from "../lib/macro.js";
import { computeSessionOverlay } from "../lib/indicators.js";

// 기능을 추가할 때 화면을 다시 나누지 않고 이 레지스트리에 렌더링 정보와
// entitlement key만 더한다. 실제 권한은 추후 서버 응답으로 판정해야 한다.
const AGENT_MODULES = [
  { key: "execution", label: "실행 상태", entitlement: "agent.execution", minimumPlan: "free", availability: "live" },
  { key: "news", label: "종목 뉴스", entitlement: "agent.news", minimumPlan: "free", availability: "live" },
  { key: "risk", label: "위험 감시", entitlement: "agent.risk", minimumPlan: "plus", availability: "live" },
  { key: "strategy", label: "전략 조건", entitlement: "agent.strategy_signal", minimumPlan: "pro", availability: "live" },
  { key: "leader", label: "대장주 신호", entitlement: "agent.leader_signal", minimumPlan: "pro", availability: "planned" },
];
const AGENT_MODULE_MAP = new Map(AGENT_MODULES.map((module) => [module.key, module]));
const AGENT_AVATAR_SRC = "/brand/navigation/ggparrot-nav-agent.png";
const PLAN_LABELS = { free: "FREE", plus: "PLUS", pro: "PRO" };

function accessFor(module, entitlements) {
  if (!module) return "planned";
  if (module.availability === "planned") return "planned";
  // 아직 구독 API가 연결되지 않은 동안에는 이미 구현된 기본 기능만 연다.
  if (!entitlements) return "enabled";
  return entitlements?.features?.[module.entitlement]?.allowed ? "enabled" : "locked";
}

function safeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function eventTime(value, fallback = 0) {
  if (typeof value === "number") return value;
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : fallback;
}

function clock(value) {
  const parsed = eventTime(value);
  if (!parsed) return "방금 확인";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function buildExecutionEvents(session, observedAt) {
  if (!session) return [];
  const running = session.status === "running";
  return [{
    id: `execution-${session.session_id}-${session.status}-${session.connected}`,
    module: "execution",
    severity: running && !session.connected ? "warning" : "info",
    title: running
      ? (session.connected ? "실행기 연결됨" : "실행기 응답 대기")
      : (session.status === "error" ? "실행 오류" : "실행 종료"),
    summary: running
      ? `${session.testnet ? "테스트넷" : "실거래"} · ${session.in_position ? "포지션 보유 중" : "현재 포지션 없음"}`
      : (session.note || "종료된 실행"),
    occurredAt: observedAt || Date.now(),
    sourceLabel: "실행기 상태",
  }];
}

function buildNewsEvents(news) {
  return (news?.items || []).map((item, index) => ({
    id: `news-${item.url || item.title || index}`,
    module: "news",
    severity: "info",
    title: item.title,
    summary: "",
    occurredAt: item.published || 0,
    displayTime: item.published_display || "최근 수집",
    sourceLabel: item.source || "원문",
    sourceUrl: item.url,
  }));
}

function buildRiskEvents(candles, session, interval) {
  const closed = (candles || []).filter((bar) => bar?.closed !== false && safeNumber(bar?.o) > 0);
  if (!closed.length) return [];
  const recent = closed.slice(-20);
  const latest = recent[recent.length - 1];
  const ranges = recent.map((bar) => ((safeNumber(bar.h) - safeNumber(bar.l)) / safeNumber(bar.o, 1)) * 100);
  const averageRange = ranges.reduce((sum, value) => sum + value, 0) / ranges.length;
  const latestChange = ((safeNumber(latest.c) - safeNumber(latest.o)) / safeNumber(latest.o, 1)) * 100;
  const elevated = averageRange >= 2 || Math.abs(latestChange) >= 3;
  const events = [{
    id: `risk-volatility-${latest.t}-${interval}`,
    module: "risk",
    severity: elevated ? "warning" : "info",
    title: elevated ? "변동성 확대 감지" : "변동성 점검 완료",
    summary: `${interval} · 평균 변동폭 ${averageRange.toFixed(2)}% · 최근 봉 ${latestChange >= 0 ? "+" : ""}${latestChange.toFixed(2)}%`,
    occurredAt: latest.t,
    sourceLabel: "바이낸스 확정 봉",
  }];

  if (session?.status === "running" && session.in_position) {
    const unrealized = safeNumber(session.unrealized_pct);
    events.push({
      id: `risk-position-${session.session_id}-${Math.round(unrealized * 10)}`,
      module: "risk",
      severity: unrealized <= -2 ? "critical" : unrealized < 0 ? "watch" : "info",
      title: unrealized <= -2 ? "평가손실 주의" : "포지션 위험 점검",
      summary: `현재 평가손익 ${unrealized >= 0 ? "+" : ""}${unrealized.toFixed(2)}%`,
      occurredAt: Date.now(),
      sourceLabel: "실행기 포지션",
    });
  }
  return events;
}

function buildStrategyEvents(macro, candles, session, interval) {
  const bars = candles || [];
  if (!macro || !bars.length) return [];
  const overlay = computeSessionOverlay(
    macro,
    session?.in_position ? session.entry_price : null,
    session?.position_side || macro.position_side,
    bars,
  );
  const lastClosedIndex = bars.reduce((found, bar, index) => (bar?.closed === false ? found : index), -1);
  if (lastClosedIndex < 0) return [];
  const recentFrom = Math.max(0, lastClosedIndex - 19);
  const markers = (overlay?.markers || [])
    .filter((marker) => marker.index >= recentFrom && marker.index <= lastClosedIndex)
    .slice(-3)
    .reverse();

  if (!markers.length) {
    const last = bars[lastClosedIndex];
    return [{
      id: `strategy-scan-${macro.rule_type}-${last.t}-${interval}`,
      module: "strategy",
      severity: "info",
      title: "전략 조건 점검 완료",
      summary: `${RULE_TYPES[macro.rule_type]?.label || macro.rule_type} · 최근 20개 봉 · 새 신호 없음`,
      occurredAt: last.t,
      sourceLabel: "전략 조건 재계산",
    }];
  }

  return markers.map((marker) => {
    const bar = bars[marker.index];
    const isSell = marker.kind === "sell";
    return {
      id: `strategy-${macro.rule_type}-${marker.index}-${bar?.t}`,
      module: "strategy",
      severity: isSell ? "watch" : "signal",
      title: marker.label || (isSell ? "매도 조건 표시" : "매수 조건 표시"),
      summary: `${interval} 공개 캔들 기준 · 참고 신호`,
      occurredAt: bar?.t || 0,
      sourceLabel: "전략 조건 재계산",
    };
  });
}

function ModuleFilter({ selected, onSelect, entitlements }) {
  return (
    <label className="agent-chat-filter">
      <span className="sr-only">에이전트 대화 종류 선택</span>
      <select value={selected} onChange={(event) => onSelect(event.target.value)}>
        <option value="all">전체 대화</option>
        {AGENT_MODULES.map((module) => {
          const access = accessFor(module, entitlements);
          const plan = module.minimumPlan === "free" ? "" : ` · ${PLAN_LABELS[module.minimumPlan]}`;
          const suffix = access === "planned" ? `${plan} · 준비 중` : plan;
          return <option key={module.key} value={module.key}>{module.label}{suffix}</option>;
        })}
      </select>
      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 6 4 4 4-4" /></svg>
    </label>
  );
}

function TierBadge({ plan }) {
  if (!plan || plan === "free") return null;
  const label = PLAN_LABELS[plan] || String(plan).toUpperCase();
  return (
    <span
      className="agent-tier-badge"
      data-plan={plan}
      aria-label={`${label} 구독 전용 기능`}
    >
      {label} 전용
    </span>
  );
}

function RestrictedModule({ module, state, onUpgrade }) {
  const plan = PLAN_LABELS[module.minimumPlan] || "상위";
  return (
    <div className="agent-module-restricted">
      <span>{state === "planned" ? "기능 준비 중" : "구독 권한 필요"}</span>
      <strong>{module.label} · {plan}</strong>
      <p>
        {state === "planned"
          ? "곧 제공할 기능입니다."
          : `${plan} 구독에서 사용할 수 있어요.`}
      </p>
      {state === "locked" && onUpgrade ? (
        <button type="button" onClick={onUpgrade} className="btn btn-s btn-secondary">{plan} 기능 보기</button>
      ) : null}
    </div>
  );
}

export default function AgentActivityStream({
  symbol,
  macro,
  session,
  candles,
  interval,
  news,
  newsState,
  observedAt,
  entitlements = null,
  onRefreshNews,
  onUpgrade,
}) {
  const [filter, setFilter] = useState("all");
  const [newMessageCount, setNewMessageCount] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const logRef = useRef(null);
  const followLatestRef = useRef(true);
  const previousIdsRef = useRef(new Set());
  const events = useMemo(() => {
    const combined = [
      ...buildExecutionEvents(session, observedAt),
      ...buildNewsEvents(news),
      ...buildRiskEvents(candles, session, interval),
      ...buildStrategyEvents(macro, candles, session, interval),
    ];
    return combined
      .filter((event) => accessFor(AGENT_MODULE_MAP.get(event.module), entitlements) === "enabled")
      .sort((left, right) => eventTime(right.occurredAt) - eventTime(left.occurredAt))
      .slice(0, 24);
  }, [candles, entitlements, interval, macro, news, observedAt, session]);

  const selectedModule = filter === "all" ? null : AGENT_MODULE_MAP.get(filter);
  const selectedAccess = selectedModule ? accessFor(selectedModule, entitlements) : "enabled";
  const visible = useMemo(() => {
    const filtered = filter === "all" ? events : events.filter((event) => event.module === filter);
    return [...filtered].reverse();
  }, [events, filter]);
  const messageKey = visible.map((event) => event.id).join("|");

  useEffect(() => {
    const node = logRef.current;
    if (!node) return;
    const nextIds = new Set(visible.map((event) => event.id));
    const previousIds = previousIdsRef.current;
    const added = previousIds.size
      ? visible.filter((event) => !previousIds.has(event.id)).length
      : 0;
    previousIdsRef.current = nextIds;

    if (!previousIds.size || followLatestRef.current) {
      node.scrollTop = node.scrollHeight;
      setNewMessageCount(0);
    } else if (added > 0) {
      setNewMessageCount((current) => current + added);
    }
    if (added > 0) setAnnouncement(`새 관측 ${added}개가 도착했어요.`);
  }, [messageKey, visible]);

  function trackScroll() {
    const node = logRef.current;
    if (!node) return;
    followLatestRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 72;
    if (followLatestRef.current) setNewMessageCount(0);
  }

  function selectFilter(nextFilter) {
    followLatestRef.current = true;
    previousIdsRef.current = new Set();
    setNewMessageCount(0);
    setFilter(nextFilter);
  }

  function scrollToLatest() {
    const node = logRef.current;
    if (!node) return;
    followLatestRef.current = true;
    setNewMessageCount(0);
    node.scrollTop = node.scrollHeight;
    node.focus({ preventScroll: true });
  }

  return (
    <aside className="agent-chat" aria-labelledby="agent-chat-title">
      <header className="agent-chat-head">
        <div className="agent-chat-identity">
          <span className="agent-chat-avatar" aria-hidden="true">
            <img src={AGENT_AVATAR_SRC} alt="" width="44" height="44" draggable="false" decoding="async" />
          </span>
          <div>
            <h2 id="agent-chat-title">껄무새 에이전트</h2>
            <p><i className={`agent-live-dot ${newsState === "loading" ? "is-checking" : "is-running"}`} aria-hidden="true" /> <b className="num">{symbol}</b> 관찰 중</p>
          </div>
        </div>
        <div className="agent-chat-controls">
          <ModuleFilter selected={filter} onSelect={selectFilter} entitlements={entitlements} />
          <button type="button" onClick={onRefreshNews} disabled={newsState === "loading"} className="agent-chat-refresh">
            {newsState === "loading" ? "확인 중" : "뉴스 확인"}
          </button>
        </div>
      </header>

      <div className="agent-chat-stream">
        <div
          ref={logRef}
          className="agent-chat-log"
          role="log"
          tabIndex={0}
          aria-label="에이전트 관측 기록"
          aria-busy={newsState === "loading"}
          onScroll={trackScroll}
        >
          {selectedModule && selectedAccess !== "enabled" ? (
            <RestrictedModule module={selectedModule} state={selectedAccess} onUpgrade={onUpgrade} />
          ) : visible.length ? (
            visible.map((event) => {
              const module = AGENT_MODULE_MAP.get(event.module);
              const severityLabel = event.severity === "critical"
                ? "위험"
                : event.severity === "warning" || event.severity === "watch"
                  ? "주의"
                  : event.severity === "signal"
                    ? "조건"
                    : "";
              return (
                <article
                  key={event.id}
                  className={`agent-message is-${event.severity}`}
                  data-plan={module?.minimumPlan || "free"}
                >
                  <span className="agent-message-avatar" aria-hidden="true">
                    <img src={AGENT_AVATAR_SRC} alt="" width="30" height="30" draggable="false" decoding="async" />
                  </span>
                  <div className="agent-message-content">
                    <header className="agent-message-meta">
                      <span>{module?.label || event.module}</span>
                      <TierBadge plan={module?.minimumPlan} />
                      {severityLabel ? <b><i aria-hidden="true" />{severityLabel}</b> : null}
                      <time className="num" dateTime={eventTime(event.occurredAt) ? new Date(eventTime(event.occurredAt)).toISOString() : undefined}>
                        {event.displayTime || clock(event.occurredAt)}
                      </time>
                    </header>
                    <div className="agent-message-bubble">
                      <strong>{event.title}</strong>
                      {event.summary ? <p>{event.summary}</p> : null}
                      <footer>
                        {event.sourceUrl ? (
                          <a href={event.sourceUrl} target="_blank" rel="noopener noreferrer" aria-label={`${event.sourceLabel} 원문 보기, 새 창`}>
                            {event.sourceLabel} · 원문 보기 ↗
                          </a>
                        ) : (
                          <span>{event.sourceLabel}</span>
                        )}
                      </footer>
                    </div>
                  </div>
                </article>
              );
            })
          ) : (
            <div className="agent-stream-empty">
              <strong>새 소식이 없어요.</strong>
            </div>
          )}

          {newsState === "loading" && selectedAccess === "enabled" ? (
            <div className="agent-checking" role="status">
              <i className="agent-live-dot is-checking" aria-hidden="true" />
              새 데이터를 확인하는 중…
            </div>
          ) : null}
        </div>
        {newMessageCount > 0 ? (
          <button type="button" className="agent-new-message" onClick={scrollToLatest}>
            새 관측 {newMessageCount}개 ↓
          </button>
        ) : null}
      </div>

      <span className="sr-only" aria-live="polite" aria-atomic="true">{announcement}</span>
    </aside>
  );
}
