import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { getNickname, setNickname } from "../lib/user.js";
import { badgeLabel, countUnseen, latestMessageId, readSeenId, writeSeenId } from "../lib/chatBadge.js";

// 리더보드 채팅 — 우하단에 떠 있는 버튼으로 연다. 목록이 길어져도 항상 손에 닿고,
// 닫혀 있는 동안 도착한 메시지는 'N new' 배지로 알린다. 매일 KST 00:00 초기화.
// React 가 메시지 텍스트를 이스케이프하므로 저장된 원문이 HTML 로 실행되지 않는다.
const POLL_MS = 3000;

export default function ChatBox({ defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const [items, setItems] = useState([]);
  const [name, setName] = useState(getNickname());
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [seenId, setSeenId] = useState(() => readSeenId());
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const stickToBottomRef = useRef(true);

  const load = useCallback(async (signal) => {
    const d = await api.chatList({ signal });
    setItems(d.items || []);
  }, []);
  const refresh = useAdaptivePolling(load, {
    intervalMs: POLL_MS,
    maxIntervalMs: 60_000,
  });

  const latest = latestMessageId(items);
  // 처음 방문(저장된 값 없음)은 지금까지의 대화를 '읽음'으로 시작한다 — 전부 new 로 뜨지 않게.
  // 열려 있는 동안 도착한 메시지도 바로 읽음 처리한다.
  useEffect(() => {
    if (!items.length) return;
    if (seenId === null || (open && latest > seenId)) {
      setSeenId(latest);
      writeSeenId(latest);
    }
  }, [items.length, latest, open, seenId]);
  const unseen = open ? 0 : countUnseen(items, seenId);
  const badge = badgeLabel(unseen);

  useEffect(() => {
    if (open && listRef.current && stickToBottomRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [items, open]);

  useEffect(() => {
    if (!open) return undefined;
    stickToBottomRef.current = true;
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }));
    const onKeyDown = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function send(e) {
    e.preventDefault();
    setError("");
    if (!text.trim()) return;
    if (!name.trim()) return setError("아이디를 입력하세요.");
    setBusy(true);
    try {
      setNickname(name);
      await api.chatPost(name.trim(), text.trim());
      setText("");
      stickToBottomRef.current = true;
      refresh();
    } catch (err) {
      setError(String(err.message || err)); // 429 rate limit surfaces here
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat-float">
      {open ? (
        <section className="chat-panel" role="dialog" aria-label="리더보드 채팅">
          <header className="chat-panel-head">
            <div>
              <h3 className="t-h4 text-slate-900">리더보드 채팅</h3>
              <span className="t-caption text-slate-500">매일 KST 00:00 초기화</span>
            </div>
            <button type="button" onClick={() => setOpen(false)} className="btn btn-s btn-ghost text-xl leading-none" aria-label="채팅 닫기">×</button>
          </header>

          {/* 메시지 목록은 카드가 아니라 스크롤 영역 — 위아래 괘선으로만 가둔다(§1-3). */}
          <div
            ref={listRef}
            role="log"
            aria-label="리더보드 채팅 메시지"
            onScroll={(event) => {
              const element = event.currentTarget;
              stickToBottomRef.current =
                element.scrollHeight - element.scrollTop - element.clientHeight < 40;
            }}
            className="chat-panel-log"
          >
            {items.length === 0 && (
              <div className="t-small text-slate-500 text-center py-8">아직 메시지가 없어요. 첫 채팅을 남겨봐요.</div>
            )}
            {items.map((m) => (
              <div key={m.id} className="t-small">
                <span className="t-caption text-slate-500 mr-2 num">{m.created_kst}</span>
                <span className="font-bold text-slate-900 mr-2">{m.username}</span>
                <span className="font-medium text-slate-700 break-words">{m.text}</span>
              </div>
            ))}
          </div>

          <form onSubmit={send} className="chat-panel-form">
            <input
              value={name}
              aria-label="채팅 아이디"
              onChange={(e) => setName(e.target.value)}
              maxLength={24}
              placeholder="아이디"
              className="field field-sm chat-panel-name"
            />
            <input
              ref={inputRef}
              value={text}
              aria-label="채팅 메시지"
              onChange={(e) => setText(e.target.value)}
              maxLength={300}
              placeholder="메시지 입력"
              className="field field-sm flex-1 min-w-0"
            />
            <button type="submit" disabled={busy} className="btn btn-m btn-secondary">전송</button>
          </form>
          {error && <div className="chat-panel-error t-caption text-amber-700" role="alert">{error}</div>}
          <p className="chat-panel-note t-caption text-slate-500">
            채팅 내용은 투자 조언이 아니고, 매매 판단과 책임은 본인에게 있어요.
          </p>
        </section>
      ) : null}

      <button
        type="button"
        className={`chat-fab${open ? " is-open" : ""}`}
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-label={open ? "채팅 닫기" : badge ? `채팅 열기, 새 메시지 ${unseen}개` : "채팅 열기"}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.4 3.5A.7.7 0 0 1 4.5 19v-3.1A2.5 2.5 0 0 1 4 13.5v-8Z" />
        </svg>
        <span className="chat-fab-label">{open ? "닫기" : "채팅"}</span>
        {badge ? <span className="chat-fab-badge num" aria-hidden="true">{badge}</span> : null}
      </button>
    </div>
  );
}
