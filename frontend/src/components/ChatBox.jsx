import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { getNickname, setNickname } from "../lib/user.js";

// Leaderboard chat: daily (KST) message board. Polls every ~3s. React escapes
// message text on render, so stored raw text can't inject HTML.
const POLL_MS = 3000;

export default function ChatBox() {
  const [items, setItems] = useState([]);
  const [name, setName] = useState(getNickname());
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const listRef = useRef(null);
  const stickToBottomRef = useRef(true);

  const load = useCallback(async (signal) => {
    const d = await api.chatList({ signal });
    setItems(d.items || []);
  }, []);
  const refresh = useAdaptivePolling(load, {
    intervalMs: POLL_MS,
    maxIntervalMs: 60_000,
  });

  useEffect(() => {
    if (listRef.current && stickToBottomRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [items]);

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
    <section className="mt-10 pt-5 border-t border-slate-200">
      <div className="flex items-center justify-between mb-3">
        <h3 className="t-h4 text-slate-900">리더보드 채팅</h3>
        <span className="t-caption text-slate-500">매일 KST 00:00 초기화</span>
      </div>

      {/* 메시지 목록도 카드가 아니라 스크롤 영역 — 위아래 괘선으로만 가둔다(§1-3). */}
      <div
        ref={listRef}
        role="log"
        aria-label="리더보드 채팅 메시지"
        onScroll={(event) => {
          const element = event.currentTarget;
          stickToBottomRef.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 40;
        }}
        className="h-64 overflow-y-auto border-y border-slate-200 py-3 space-y-2"
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

      <form onSubmit={send} className="mt-3 flex flex-wrap gap-2">
        <input
          value={name}
          aria-label="채팅 아이디"
          onChange={(e) => setName(e.target.value)}
          maxLength={24}
          placeholder="아이디"
          className="field field-sm w-24 sm:w-28"
        />
        <input
          value={text}
          aria-label="채팅 메시지"
          onChange={(e) => setText(e.target.value)}
          maxLength={300}
          placeholder="메시지 입력 (최대 300자)"
          className="field field-sm flex-1 min-w-[8rem]"
        />
        <button type="submit" disabled={busy} className="btn btn-m btn-secondary">
          전송
        </button>
      </form>
      {error && <div className="mt-2 t-caption text-amber-700" role="alert">{error}</div>}
      <p className="mt-2 t-caption text-slate-500">
        채팅 내용은 투자 조언이 아니고, 매매 판단과 책임은 본인에게 있어요.
      </p>
    </section>
  );
}
