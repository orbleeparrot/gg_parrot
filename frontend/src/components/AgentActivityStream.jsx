import { useEffect, useMemo, useRef, useState } from "react";
import { AGENT_MODULE_MAP, accessFor } from "../features/agents/registry.js";
import { leaderModule, whaleActivityNotice } from "../features/agents/modules/leader.js";
import { advanceActivityTimeline, emptyActivityTimeline } from "../features/agents/activityTimeline.js";
import { countNewObservations } from "../features/agents/positionNews/presentation.js";
import RunResultScreen from "./RunResultScreen.jsx";
import AgentCommentary from "./AgentCommentary.jsx";
import { Icon } from "./icons.jsx";

// 오른쪽 '에이전트' 칸 — 중계(껄무새가 서버에서 만든 말풍선) · 고래 동향 · 활동 로그.
// 2026-10-17 전에는 실행 상태 · 실행 로그 · 맞춤 뉴스 · 위험 감시 · 전략 조건을 브라우저가 따로 만들어 쌓았고,
// 화면을 열 때마다 같은 관측을 새로 저장해 '평가손실 주의'가 열 번 넘게 쌓였다. 이제 그 일은 서버 중계가 한 번만 한다.
const PLAN_LABELS = { free: "FREE", plus: "PLUS", pro: "PRO" };
const EMPTY_FEATURE_STATES = {};
const WHALE_MODULES = [leaderModule];
const SECOND_MS = 1000;
const HOUR_MS = 60 * 60 * SECOND_MS;
const KST_OFFSET_MS = 9 * HOUR_MS;
const WHALE_AVATAR = "/brand/agent/ggparrot-agent-curious-v1.svg";
const WARNING_AVATAR = "/brand/agent/ggparrot-agent-warning-v1.svg";

function eventTime(value, fallback = 0) {
  if (typeof value === "number") return value;
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : fallback;
}

function absoluteActivityTime(value, fallback = "방금 확인") {
  const parsed = eventTime(value);
  if (!parsed) return fallback;
  const date = new Date(parsed + KST_OFFSET_MS);
  const twoDigits = (number) => String(number).padStart(2, "0");
  return `${twoDigits(date.getUTCFullYear() % 100)}.${twoDigits(date.getUTCMonth() + 1)}.${twoDigits(date.getUTCDate())}. ${twoDigits(date.getUTCHours())}:${twoDigits(date.getUTCMinutes())}`;
}

function activityTime(value, now, fallback = "방금 확인") {
  const parsed = eventTime(value);
  if (!parsed) return fallback;
  const elapsedSeconds = Math.floor(Math.max(0, now - parsed) / SECOND_MS);
  if (elapsedSeconds < 60) return `${Math.max(1, elapsedSeconds)}초 전`;
  if (elapsedSeconds < 60 * 60) return `${Math.floor(elapsedSeconds / 60)}분 전`;
  return absoluteActivityTime(parsed, fallback);
}

function ModuleTabs({ selected, onSelect, entitlements, hasActivityLog }) {
  const items = [{ key: "all", label: "중계" }, leaderModule,
    ...(hasActivityLog ? [{ key: "activity_logs", label: "활동 로그" }] : [])];
  return (
    <div className="agent-chat-tabs" role="group" aria-label="에이전트 화면 종류">
      {items.map((item) => {
        const access = item.key === "all" || item.key === "activity_logs" ? "enabled" : accessFor(item, entitlements);
        const plan = item.minimumPlan && item.minimumPlan !== "free" ? `, ${PLAN_LABELS[item.minimumPlan]} 구독 기능` : "";
        const availability = access === "planned" ? ", 준비 중" : "";
        return (
          <button key={item.key} type="button" className={selected === item.key ? "is-active" : ""}
            aria-pressed={selected === item.key} aria-label={`${item.label}${plan}${availability}`}
            onClick={() => onSelect(item.key)}>
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

function RestrictedModule({ module, state, onUpgrade }) {
  const plan = PLAN_LABELS[module.minimumPlan] || "상위";
  return (
    <div className="agent-module-restricted">
      <span>{state === "planned" ? "기능 준비 중" : "구독 권한 필요"}</span>
      <strong>{module.label} · {plan}</strong>
      <p>{state === "planned" ? "곧 제공할 기능이에요." : `${plan} 구독에서 사용할 수 있어요.`}</p>
      {state === "locked" && onUpgrade ? (
        <button type="button" onClick={onUpgrade} className="btn btn-s btn-secondary">{plan} 기능 보기</button>
      ) : null}
    </div>
  );
}

// 고래 동향(PRO) — 대규모 체결 · 지갑 잔고 변화. 서버 중계에 아직 넣지 않은 자료라 이 탭에 따로 둔다.
function WhaleFeed({ symbol, session, featureStates, entitlements, onUpgrade }) {
  const [timeline, setTimeline] = useState(() => emptyActivityTimeline(session?.session_id));
  const [now, setNow] = useState(() => Date.now());
  const [newMessageCount, setNewMessageCount] = useState(0);
  const logRef = useRef(null);
  const followLatestRef = useRef(true);
  const previousIdsRef = useRef(new Set());
  const initialRef = useRef(true);
  const context = useMemo(() => ({ symbol, session, featureStates, receivedAt: Date.now() }), [featureStates, session, symbol]);
  useEffect(() => { setTimeline((previous) => advanceActivityTimeline(previous, context, WHALE_MODULES)); }, [context]);

  const access = accessFor(leaderModule, entitlements);
  const events = useMemo(() => (timeline.sessionId === session?.session_id ? timeline.events : [])
    .slice().sort((left, right) => eventTime(right.occurredAt) - eventTime(left.occurredAt)), [session?.session_id, timeline]);
  const visible = useMemo(() => events.slice(0, 24).reverse(), [events]);
  const notice = whaleActivityNotice(featureStates.whale_activity);
  const loading = featureStates.whale_activity?.status === "loading" && !featureStates.whale_activity?.data;

  useEffect(() => {
    if (!visible.length) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 30 * SECOND_MS);
    return () => window.clearInterval(timer);
  }, [visible.length]);

  useEffect(() => {
    const node = logRef.current;
    if (!node) return;
    const added = initialRef.current ? 0 : countNewObservations(visible, previousIdsRef.current);
    for (const event of events) previousIdsRef.current.add(event.id);
    if (initialRef.current || followLatestRef.current) {
      node.scrollTop = node.scrollHeight;
      setNewMessageCount(0);
    } else if (added > 0) {
      setNewMessageCount((count) => count + added);
    }
    if (events.length) initialRef.current = false;
  }, [events, visible]);

  function trackScroll() {
    const node = logRef.current;
    if (!node) return;
    followLatestRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 72;
    if (followLatestRef.current) setNewMessageCount(0);
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
    <div className="agent-chat-stream flex flex-col">
      <div ref={logRef} className="agent-chat-log flex-1" role="log" aria-relevant="additions" tabIndex={0}
        aria-label="고래 동향" aria-busy={loading} onScroll={trackScroll}>
        {access !== "enabled" ? <RestrictedModule module={leaderModule} state={access} onUpgrade={onUpgrade} />
          : visible.length ? visible.map((event) => (
            <article key={event.id} className={`agent-message is-${event.severity}`} data-plan="pro">
              <span className="agent-message-avatar" aria-hidden="true">
                <img src={event.severity === "warning" ? WARNING_AVATAR : WHALE_AVATAR} alt="" width="80" height="80" draggable="false" decoding="async" />
              </span>
              <div className="agent-message-bubble">
                <p className="agent-message-primary">{event.title}</p>
                {event.summary ? <p className="agent-message-summary">{event.summary}</p> : null}
                {event.detail ? <p className="agent-message-detail">{event.detailLabel || "판단 근거"} · {event.detail}</p> : null}
                <footer>
                  {event.sourceUrl ? (
                    <a href={event.sourceUrl} target="_blank" rel="noopener noreferrer" aria-label={`${event.sourceLabel} 원문 보기, 새 창`}>
                      {event.sourceLabel} · 원문 보기 <Icon name="arrowUpRight" size={14} className="ui-icon-inline" />
                    </a>
                  ) : <span>{event.sourceLabel}</span>}
                  <time className="num" dateTime={eventTime(event.occurredAt) ? new Date(eventTime(event.occurredAt)).toISOString() : undefined}>
                    {activityTime(event.occurredAt, now, event.fallbackTime)}
                  </time>
                </footer>
              </div>
            </article>
          )) : (
            <div className="agent-stream-empty" role="status" data-whale-status="">
              <strong>{notice || "새 소식이 없어요."}</strong>
            </div>
          )}
        {visible.length > 0 && notice ? <div className="agent-checking" role="status" data-whale-status="">{notice}</div> : null}
      </div>
      {newMessageCount > 0 ? (
        <button type="button" className="agent-new-message" onClick={scrollToLatest}>
          새 소식 {newMessageCount}개 <Icon name="arrowDown" size={14} className="ui-icon-inline" />
        </button>
      ) : null}
    </div>
  );
}

export default function AgentActivityStream({
  symbol,
  session,
  featureStates = EMPTY_FEATURE_STATES,
  entitlements = null,
  onUpgrade,
  selectedView,
  onSelectView,
  activityLog,
  commentary,
  onCloseAndStop,
}) {
  const [localFilter, setFilter] = useState("all");
  const filter = selectedView ?? localFilter;
  // 실행이 끝나면 결과 화면이 기본이고, 기록은 원할 때만 연다.
  const [showLog, setShowLog] = useState(false);

  function selectFilter(nextFilter) {
    setFilter(nextFilter);
    onSelectView?.(nextFilter);
  }

  // 종료 '요청' 시점부터 결과 화면으로 넘어간다 — 실행기 확정을 기다리는 동안 채팅에
  // 알림만 쌓이고 화면이 그대로이던 문제.
  const ended = !!session && (session.status !== "running" || !!session.stopping);
  useEffect(() => {
    if (ended) setShowLog(false);
  }, [ended]);
  if (ended && !showLog && filter !== "activity_logs") {
    return (
      <aside className="agent-chat" aria-label={`${symbol} 실행 결과`}>
        <RunResultScreen session={session} onShowLog={() => setShowLog(true)} />
      </aside>
    );
  }

  const known = filter === "all" || filter === "activity_logs" || filter === leaderModule.key;
  const view = known ? filter : "all";
  return (
    <aside className="agent-chat" aria-label={`${symbol} 껄무새 에이전트`}>
      <header className="agent-chat-head">
        <div className={`agent-chat-controls${ended ? " has-result" : ""}`}>
          <ModuleTabs selected={view} onSelect={selectFilter} entitlements={entitlements} hasActivityLog={!!activityLog} />
          {ended ? (
            // 왼쪽 분기 탭과 같은 트랙·글자로 — 다른 부품처럼 보이지 않게.
            <div className="agent-chat-tabs agent-chat-result-toggle" role="group" aria-label="결과 화면">
              <button type="button" onClick={() => { setShowLog(false); selectFilter("all"); }}>결과 보기</button>
            </div>
          ) : null}
        </div>
      </header>
      {view === "activity_logs" && activityLog ? activityLog
        : view === leaderModule.key && AGENT_MODULE_MAP.get(leaderModule.key)
          ? <WhaleFeed symbol={symbol} session={session} featureStates={featureStates} entitlements={entitlements} onUpgrade={onUpgrade} />
          : <AgentCommentary session={session} commentary={commentary} onCloseAndStop={ended ? null : onCloseAndStop} />}
    </aside>
  );
}
