import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { chipTarget, faceSrc, kstClock, kstDay, subText, withDayRows } from "../lib/agentCommentary.js";
import { Icon } from "./icons.jsx";
import "./AgentCommentary.css";

// 껄무새 중계 — 말풍선은 늘 같은 모양: 굵은 한 줄(껄무새 말투) · 숫자 한 줄 · 짧은 한마디, 아래에 근거 칩과 시각.
// 글자 · 말할 순간은 서버(agent_commentary.py)가 정한다. 화면은 그리기 · 따라 내리기만 맡는다.
// 말풍선에 [청산하고 종료] 같은 버튼은 두지 않는다 — 대화를 읽다 실수로 누르면 실제 포지션이 정리된다(2026-10-17).

function Chip({ chip, sessionId }) {
  const target = chipTarget(chip, sessionId);
  const className = `agent-say-chip is-${["chart", "comm", "news", "macro"].includes(chip.kind) ? chip.kind : "macro"}`;
  if (target?.external) {
    return (
      <a className={className} href={target.external} target="_blank" rel="noopener noreferrer" aria-label={`${chip.label} 원문 보기, 새 창`}>
        {chip.label}
      </a>
    );
  }
  if (target?.internal) return <Link className={className} to={target.internal}>{chip.label}</Link>;
  return <span className={className}>{chip.label}</span>;
}

function Post({ post }) {
  const who = [post.author || post.source, post.published_ms ? `${kstDay(post.published_ms)} ${kstClock(post.published_ms)}` : ""]
    .filter(Boolean).join(" · ");
  return (
    <blockquote className={`agent-say-post is-${post.kind === "news" ? "news" : "comm"}`}>
      <p className="agent-say-post-title">“{post.title}”</p>
      {post.summary ? <p className="agent-say-post-summary">{post.summary}</p> : null}
      {who ? <p className="agent-say-post-who">{who}</p> : null}
    </blockquote>
  );
}

function Saying({ item, sessionId }) {
  const tone = item.tone === "warn" || item.tone === "risk" ? item.tone : "";
  const numsTone = item.nums_tone === "up" || item.nums_tone === "down" ? item.nums_tone : "";
  return (
    <article className={`agent-say${tone ? ` is-${tone}` : ""}`}>
      <span className="agent-say-face" aria-hidden="true">
        <img src={faceSrc(item.face)} alt="" width="80" height="80" draggable="false" decoding="async" />
      </span>
      <div className="agent-say-stack">
        <div className="agent-say-bubble">
          <p className="agent-say-hl">{item.hl}</p>
          {item.post ? <Post post={item.post} /> : null}
          {item.nums ? <p className={`agent-say-nums num${numsTone ? ` is-${numsTone}` : ""}`}>{item.nums}</p> : null}
          {(item.subs || []).map((raw, index) => {
            const sub = subText(raw);
            return sub.t || sub.b ? (
              <p key={index} className="agent-say-sub">{sub.b ? <b>{sub.b}</b> : null}{sub.t}</p>
            ) : null;
          })}
        </div>
        <div className="agent-say-meta">
          {(item.chips || []).map((chip, index) => <Chip key={index} chip={chip} sessionId={sessionId} />)}
          <time className="num" dateTime={item.at_ms ? new Date(item.at_ms).toISOString() : undefined}>{kstClock(item.at_ms)}</time>
        </div>
      </div>
    </article>
  );
}

export default function AgentCommentary({ session, commentary }) {
  const sessionId = session?.session_id;
  const items = useMemo(() => commentary?.items || [], [commentary?.items]);
  const rows = useMemo(() => withDayRows(items), [items]);
  const [newCount, setNewCount] = useState(0);
  const logRef = useRef(null);
  const followRef = useRef(true);
  const seenRef = useRef(0);

  useEffect(() => {
    const node = logRef.current;
    if (!node) return;
    const lastId = items.length ? items[items.length - 1].id : 0;
    const added = seenRef.current ? items.filter((item) => item.id > seenRef.current).length : 0;
    seenRef.current = Math.max(seenRef.current, lastId);
    if (followRef.current || !added) {
      node.scrollTop = node.scrollHeight;
      setNewCount(0);
    } else {
      setNewCount((count) => count + added);
    }
  }, [items]);

  function trackScroll() {
    const node = logRef.current;
    if (!node) return;
    followRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 72;
    if (followRef.current) setNewCount(0);
  }

  function scrollToLatest() {
    const node = logRef.current;
    if (!node) return;
    followRef.current = true;
    setNewCount(0);
    node.scrollTop = node.scrollHeight;
    node.focus({ preventScroll: true });
  }

  return (
    <div className="agent-chat-stream flex flex-col">
      <div ref={logRef} className="agent-chat-log agent-say-log flex-1" role="log" aria-relevant="additions" tabIndex={0}
        aria-label="껄무새 중계" onScroll={trackScroll}>
        {rows.length ? rows.map((row) => (row.type === "day"
          ? <p key={row.key} className="agent-say-day">{row.label}</p>
          : (
            <Saying key={row.key} item={row.item} sessionId={sessionId} />
          )))
          : !commentary?.loaded ? (
            <div className="agent-checking" role="status">
              <i className="agent-live-dot is-checking" aria-hidden="true" />
              중계를 불러오는 중…
            </div>
          ) : (
            <div className="agent-stream-empty" role="status">
              <strong>{commentary?.error ? "중계를 불러오지 못했어요. 잠시 뒤 다시 불러올게요." : "껄무새가 지켜보는 중이에요. 일이 생기면 여기서 말할게요."}</strong>
            </div>
          )}
      </div>
      {newCount > 0 ? (
        <button type="button" className="agent-new-message" onClick={scrollToLatest}>
          새 소식 {newCount}개 <Icon name="arrowDown" size={14} className="ui-icon-inline" />
        </button>
      ) : null}
    </div>
  );
}
