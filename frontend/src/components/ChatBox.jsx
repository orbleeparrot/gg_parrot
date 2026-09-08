import { Fragment, useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "../api.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { getNickname, setNickname } from "../lib/user.js";
import {
  badgeLabel,
  countUnseen,
  firstUnseenId,
  latestMessageId,
  readSeenId,
  writeSeenId,
} from "../lib/chatBadge.js";

// 리더보드 채팅 — 우하단 원형 껄무새 버튼으로 여는 대화록. 목록이 길어도 항상 손에 닿고,
// 닫혀 있는 동안 도착한 메시지는 'N new' 배지와 놀란 표정으로 알린다. 매일 KST 00:00 초기화.
// React 가 메시지 텍스트를 이스케이프하므로 저장된 원문이 HTML 로 실행되지 않는다.
const POLL_MS = 3000;
const FAB_ICON = "/brand/ggparrot-feather-terminal.svg"; // 정사각 깃털 — 원판 안에 잘림 없이 들어간다
const EMPTY_FACE = "/brand/agent/ggparrot-agent-curious-v1.svg";
const HELPER_DEFAULT = "투자 조언이 아니에요. 매매 판단과 책임은 본인에게 있어요.";

export default function ChatBox({ defaultOpen = false }) {
  const panelId = useId();
  const [open, setOpen] = useState(defaultOpen);
  const [items, setItems] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [name, setName] = useState(getNickname());
  const [editingName, setEditingName] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [seenId, setSeenId] = useState(() => readSeenId());
  const [dividerId, setDividerId] = useState(null);
  const dividerReadyRef = useRef(false); // 열린 채로 첫 목록이 오면 그때 한 번 기준을 잡는다
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const nameRef = useRef(null);
  const stickToBottomRef = useRef(true);

  const load = useCallback(async (signal) => {
    const d = await api.chatList({ signal });
    setItems(d.items || []);
    setLoaded(true);
  }, []);
  const refresh = useAdaptivePolling(load, { intervalMs: POLL_MS, maxIntervalMs: 60_000 });

  const latest = latestMessageId(items);
  // 처음 방문(저장된 값 없음)은 지금까지의 대화를 읽음으로 시작한다 — 전부 new 로 뜨지 않게.
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
    if (!open || dividerReadyRef.current || !loaded) return;
    dividerReadyRef.current = true;
    setDividerId(firstUnseenId(items, seenId));
  }, [items, loaded, open, seenId]);

  useEffect(() => {
    if (open && listRef.current && stickToBottomRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [items, open]);

  useEffect(() => {
    if (!open) return undefined;
    stickToBottomRef.current = true;
    const target = name.trim() ? inputRef : nameRef;
    const frame = window.requestAnimationFrame(() => target.current?.focus({ preventScroll: true }));
    const onKeyDown = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
    };
    // 열릴 때 한 번만 포커스한다 — 이름 편집 중 매 입력마다 옮기지 않게.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function toggle() {
    if (!open) {
      setDividerId(firstUnseenId(items, seenId)); // 열기 직전의 읽음 기준으로 구분선
      dividerReadyRef.current = true;
    } else {
      dividerReadyRef.current = false;
    }
    setOpen(!open);
  }

  function commitName() {
    if (!name.trim()) return;
    setNickname(name.trim());
    setEditingName(false);
    inputRef.current?.focus();
  }

  async function send(e) {
    e.preventDefault();
    setError("");
    if (!text.trim()) return;
    if (!name.trim()) {
      setError("먼저 아이디를 정해 주세요.");
      nameRef.current?.focus();
      return;
    }
    setBusy(true);
    try {
      setNickname(name.trim());
      await api.chatPost(name.trim(), text.trim());
      setText("");
      stickToBottomRef.current = true;
      refresh();
    } catch (err) {
      setError(String(err.message || err)); // 429 rate limit surfaces here
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  const nameNeeded = !name.trim() || editingName;

  return (
    <div className="chat-float">
      {open ? (
        <section id={panelId} className="chat-sheet" role="dialog" aria-label="리더보드 채팅">
          <header className="chat-head">
            <div className="chat-head-title">
              <h3>리더보드 채팅</h3>
              <p><span className="num">{items.length}</span>개 · KST 00:00 초기화</p>
            </div>
            <button type="button" className="chat-close" onClick={() => setOpen(false)} aria-label="채팅 닫기">
              <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 4 8 8M12 4l-8 8" /></svg>
            </button>
          </header>

          <div
            ref={listRef}
            role="log"
            aria-label="리더보드 채팅 메시지"
            aria-busy={!loaded}
            onScroll={(event) => {
              const element = event.currentTarget;
              stickToBottomRef.current =
                element.scrollHeight - element.scrollTop - element.clientHeight < 40;
            }}
            className="chat-log"
          >
            {!loaded ? (
              <div className="chat-skeleton" aria-hidden="true"><i /><i /><i /></div>
            ) : items.length === 0 ? (
              <div className="chat-empty">
                <img src={EMPTY_FACE} alt="" width="56" height="56" draggable="false" />
                <strong>아직 조용해요.</strong>
                <span>오늘 첫 채팅을 남겨봐요.</span>
              </div>
            ) : (
              items.map((m, index) => {
                const previous = index > 0 ? items[index - 1] : null;
                const showDivider = dividerId != null && m.id === dividerId;
                const continued = !showDivider && !!previous && previous.username === m.username;
                const mine = !!name.trim() && m.username === name.trim();
                const initial = String(m.username || "?").trim().charAt(0).toUpperCase() || "?";
                return (
                  <Fragment key={m.id}>
                    {showDivider ? (
                      <div className="chat-divider" role="separator" aria-label="여기부터 새 메시지"><span>새 메시지</span></div>
                    ) : null}
                    {/* 메신저 말풍선 — 남은 왼쪽(아바타·이름·회색), 나는 오른쪽(노랑, 이름 없음). */}
                    <article className={`chat-row${mine ? " is-mine" : ""}${continued ? " is-continued" : ""}`} aria-label={`${m.username}, ${m.created_kst}`}>
                      {!mine ? (
                        <span className="chat-avatar" aria-hidden="true">{continued ? "" : initial}</span>
                      ) : null}
                      <div className="chat-row-body">
                        {!mine && !continued ? <span className="chat-row-name">{m.username}</span> : null}
                        <div className="chat-bubble-line">
                          <p className="chat-bubble">{m.text}</p>
                          <time className="num">{m.created_kst}</time>
                        </div>
                      </div>
                    </article>
                  </Fragment>
                );
              })
            )}
          </div>

          <form onSubmit={send} className="chat-composer">
            {nameNeeded ? (
              <input
                ref={nameRef}
                value={name}
                aria-label="채팅 아이디"
                onChange={(e) => setName(e.target.value)}
                onBlur={() => { if (name.trim()) commitName(); }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitName(); } }}
                maxLength={24}
                placeholder="아이디"
                className="chat-field chat-field-name"
              />
            ) : (
              <button
                type="button"
                className="chat-name-chip"
                onClick={() => setEditingName(true)}
                aria-label={`아이디 ${name}, 바꾸기`}
                title="아이디 바꾸기"
              >
                {name}
              </button>
            )}
            <input
              ref={inputRef}
              value={text}
              aria-label="채팅 메시지"
              aria-invalid={error ? true : undefined}
              onChange={(e) => setText(e.target.value)}
              maxLength={300}
              placeholder="메시지"
              className="chat-field"
              disabled={busy}
            />
            <button type="submit" className="chat-send" disabled={busy || !text.trim()} aria-label={busy ? "보내는 중" : "전송"}>
              {busy ? (
                <span className="chat-spinner" aria-hidden="true" />
              ) : (
                <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></svg>
              )}
            </button>
          </form>
          <p className={`chat-helper${error ? " is-error" : ""}`} role={error ? "alert" : undefined}>
            {error || HELPER_DEFAULT}
          </p>
        </section>
      ) : null}

      <button
        type="button"
        className={`chat-fab${open ? " is-open" : ""}${unseen ? " has-news" : ""}`}
        onClick={toggle}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={open ? "채팅 닫기" : badge ? `채팅 열기, 새 메시지 ${unseen}개` : "채팅 열기"}
      >
        <img src={FAB_ICON} alt="" width="34" height="34" draggable="false" decoding="async" />
        <span className="chat-fab-label" aria-hidden="true">Chat</span>
        {badge ? <span className="chat-fab-badge num" aria-hidden="true">{badge}</span> : null}
      </button>
    </div>
  );
}
