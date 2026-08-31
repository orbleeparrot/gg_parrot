import { useEffect, useMemo, useRef, useState } from "react";
import {
  AGENT_MODULE_MAP,
  AGENT_MODULES,
  accessFor,
  buildAgentEvents,
} from "../features/agents/registry.js";
const PLAN_LABELS = { free: "FREE", plus: "PLUS", pro: "PRO" };
const SECOND_MS = 1000;
const MINUTE_MS = 60 * SECOND_MS;
const HOUR_MS = 60 * MINUTE_MS;
const KST_OFFSET_MS = 9 * HOUR_MS;
const AGENT_AVATAR_SOURCES = {
  calm: "/brand/agent/ggparrot-agent-calm-v1.svg",
  curious: "/brand/agent/ggparrot-agent-curious-v1.svg",
  focused: "/brand/agent/ggparrot-agent-focused-v1.svg",
  warning: "/brand/agent/ggparrot-agent-warning-v1.svg",
  critical: "/brand/agent/ggparrot-agent-critical-v1.svg",
  signal: "/brand/agent/ggparrot-agent-signal-v1.svg",
};

function avatarForEvent(event) {
  if (AGENT_AVATAR_SOURCES[event.expression]) return AGENT_AVATAR_SOURCES[event.expression];
  if (event.severity === "critical") return AGENT_AVATAR_SOURCES.critical;
  if (event.severity === "warning" || event.severity === "watch") return AGENT_AVATAR_SOURCES.warning;
  if (event.severity === "signal") return AGENT_AVATAR_SOURCES.signal;
  if (event.module === "position_news") return AGENT_AVATAR_SOURCES.curious;
  if (event.module === "risk" || event.module === "strategy") return AGENT_AVATAR_SOURCES.focused;
  return AGENT_AVATAR_SOURCES.calm;
}

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

  const elapsed = Math.max(0, now - parsed);
  const elapsedSeconds = Math.floor(elapsed / SECOND_MS);
  if (elapsedSeconds < 60) return `${Math.max(1, elapsedSeconds)}초 전`;
  if (elapsedSeconds < 60 * 60) return `${Math.floor(elapsedSeconds / 60)}분 전`;
  if (elapsedSeconds === 60 * 60) return "1시간 전";
  return absoluteActivityTime(parsed, fallback);
}

function ModuleTabs({ selected, onSelect, entitlements }) {
  const items = [{ key: "all", label: "전체" }, ...AGENT_MODULES];
  return (
    <div className="agent-chat-tabs" role="group" aria-label="에이전트 대화 종류">
      {items.map((item) => {
        const access = item.key === "all" ? "enabled" : accessFor(item, entitlements);
        const plan = item.minimumPlan && item.minimumPlan !== "free"
          ? `, ${PLAN_LABELS[item.minimumPlan]} 구독 기능`
          : "";
        const availability = access === "planned" ? ", 준비 중" : "";
        return (
          <button
            key={item.key}
            type="button"
            className={selected === item.key ? "is-active" : ""}
            aria-pressed={selected === item.key}
            aria-label={`${item.label}${plan}${availability}`}
            onClick={() => onSelect(item.key)}
          >
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
  featureStates = {},
  observedAt,
  entitlements = null,
  onUpgrade,
}) {
  const [filter, setFilter] = useState("all");
  const [newMessageCount, setNewMessageCount] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const logRef = useRef(null);
  const followLatestRef = useRef(true);
  const previousIdsRef = useRef(new Set());
  const events = useMemo(() => {
    const combined = buildAgentEvents({
      symbol,
      macro,
      session,
      candles,
      interval,
      observedAt,
      featureStates,
    });
    return combined
      .filter((event) => accessFor(AGENT_MODULE_MAP.get(event.module), entitlements) === "enabled")
      .sort((left, right) => eventTime(right.occurredAt) - eventTime(left.occurredAt));
  }, [candles, entitlements, featureStates, interval, macro, observedAt, session, symbol]);

  const selectedModule = filter === "all" ? null : AGENT_MODULE_MAP.get(filter);
  const selectedAccess = selectedModule ? accessFor(selectedModule, entitlements) : "enabled";
  const selectedFeatureState = selectedModule ? featureStates[selectedModule.key] : null;
  const isFeatureLoading = selectedModule
    ? selectedFeatureState?.status === "loading"
    : Object.values(featureStates).some((state) => state?.status === "loading");
  const visible = useMemo(() => {
    const filtered = filter === "all" ? events : events.filter((event) => event.module === filter);
    return filtered.slice(0, 24).reverse();
  }, [events, filter]);
  const hasRelativeTime = visible.some((event) => {
    const occurredAt = eventTime(event.occurredAt);
    const elapsedSeconds = Math.floor(Math.max(0, now - occurredAt) / SECOND_MS);
    return occurredAt && elapsedSeconds <= HOUR_MS / SECOND_MS;
  });
  const messageKey = visible.map((event) => event.id).join("|");

  useEffect(() => {
    if (!hasRelativeTime) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), SECOND_MS);
    return () => window.clearInterval(timer);
  }, [hasRelativeTime]);

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
    <aside className="agent-chat" aria-label={`${symbol} 껄무새 에이전트 기록`}>
      <header className="agent-chat-head">
        <div className="agent-chat-controls">
          <ModuleTabs selected={filter} onSelect={selectFilter} entitlements={entitlements} />
        </div>
      </header>

      <div className="agent-chat-stream">
        <div
          ref={logRef}
          className="agent-chat-log"
          role="log"
          tabIndex={0}
          aria-label="에이전트 관측 기록"
          aria-busy={isFeatureLoading}
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
              const plan = module?.minimumPlan || "free";
              const planLabel = PLAN_LABELS[plan] || String(plan).toUpperCase();
              const hasTierBadge = plan !== "free";
              return (
                <article
                  key={event.id}
                  className={`agent-message is-${event.severity}`}
                  data-plan={plan}
                >
                  <span className="agent-message-avatar" aria-hidden="true">
                    <img src={avatarForEvent(event)} alt="" width="80" height="80" draggable="false" decoding="async" />
                  </span>
                  <div className="agent-message-bubble">
                    {hasTierBadge ? (
                      <span className="agent-tier-badge" data-plan={plan} aria-hidden="true">{planLabel}</span>
                    ) : null}
                    <span className="sr-only">
                      {hasTierBadge ? `${planLabel} 구독 기능. ` : ""}
                      {module?.label || event.module}{severityLabel ? `. ${severityLabel}` : ""}.
                    </span>
                    <p className="agent-message-primary">{event.title}</p>
                    {event.summary ? <p className="agent-message-summary">{event.summary}</p> : null}
                    {event.detail ? <p className="agent-message-detail">{event.detailLabel || "판단 근거"} · {event.detail}</p> : null}
                    <footer>
                      {event.sourceUrl ? (
                        <a href={event.sourceUrl} target="_blank" rel="noopener noreferrer" aria-label={`${event.sourceLabel} 원문 보기, 새 창`}>
                          {event.sourceLabel} · 원문 보기 ↗
                        </a>
                      ) : (
                        <span>{event.sourceLabel}</span>
                      )}
                      <time
                        className="num"
                        dateTime={eventTime(event.occurredAt) ? new Date(eventTime(event.occurredAt)).toISOString() : undefined}
                        aria-live="off"
                      >
                        <span aria-hidden="true">{activityTime(event.occurredAt, now, event.fallbackTime)}</span>
                        <span className="sr-only">발생 시각 {absoluteActivityTime(event.occurredAt, event.fallbackTime)}</span>
                      </time>
                    </footer>
                  </div>
                </article>
              );
            })
          ) : (
            <div className="agent-stream-empty">
              <strong>새 소식이 없어요.</strong>
            </div>
          )}

          {isFeatureLoading && selectedAccess === "enabled" ? (
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
