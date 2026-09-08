import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "../api.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { getAuthUser, getToken, useAuth } from "../lib/auth.js";
import { badgeLabel, chatScope, firstUnseenId, isOwnMessage, sameAuthor } from "../lib/chatBadge.js";
import { STICKERS, stickerFromText, stickerText } from "../lib/chatStickers.js";
import { chatUnseenCount, getChatFeed, markChatSeen, observeChat, receiveChat, receiveChatPost, setChatLoadError, visibleChatReadId } from "../lib/chatStore.js";
import "./ChatBox.css";

const POLL_MS = 3000;
const FAB_ICON = "/brand/ggparrot-feather-terminal.svg";
const EMPTY_FACE = "/brand/agent/ggparrot-agent-curious-v1.svg";

function kstClock(now = Date.now()) {
  const date = new Date(now + 9 * 60 * 60 * 1000);
  return `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
}

// A member switch remounts transient UI and aborts the previous member's requests.
// Feed/read state lives outside the route, in a separate cache for each account.
export default function ChatBox(props) {
  const { token, user } = useAuth();
  const member = token && user?.id != null ? user : null;
  const scope = chatScope(member?.id);
  return <MemberChatBox key={scope} {...props} member={member} scope={scope} />;
}

function MemberChatBox({ member, scope, defaultOpen = false, defaultStickerTray = false }) {
  const panelId = useId();
  const subscribe = useCallback((listener) => observeChat(scope, listener), [scope]);
  const snapshot = useCallback(() => getChatFeed(scope), [scope]);
  const feed = useSyncExternalStore(subscribe, snapshot, snapshot);
  const { items, loaded, seenId, loadError } = feed;
  const [open, setOpen] = useState(defaultOpen);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [readRetry, setReadRetry] = useState(0);
  const [dividerId, setDividerId] = useState(null);
  const [stickerOpen, setStickerOpen] = useState(defaultStickerTray && !!member);
  const [atBottom, setAtBottom] = useState(true);
  const dividerReadyRef = useRef(false);
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const stickToBottomRef = useRef(true);
  const mountedRef = useRef(false);
  const pendingRequestsRef = useRef(new Set());
  const historyPositionRef = useRef(null);
  const syncedSeenRef = useRef(-1);
  const serverSeenRef = useRef(-1);
  const syncingSeenRef = useRef(false);

  const isCurrent = useCallback(() => mountedRef.current && chatScope(getToken() ? getAuthUser()?.id : null) === scope, [scope]);
  useEffect(() => {
    mountedRef.current = true;
    const pending = pendingRequestsRef.current;
    return () => {
      mountedRef.current = false;
      pending.forEach((controller) => controller.abort());
    };
  }, []);

  const load = useCallback(async (signal) => {
    const data = await api.chatList({ signal, seenId: getChatFeed(scope).seenId });
    if (signal.aborted || !isCurrent()) return;
    serverSeenRef.current = Math.max(serverSeenRef.current, Number(data.server_seen_id) || 0);
    receiveChat(scope, data);
  }, [isCurrent, scope]);
  const refresh = useAdaptivePolling(load, {
    intervalMs: POLL_MS, maxIntervalMs: 60_000, pollKey: scope,
    onError: (reason) => { if (isCurrent() && reason?.name !== "AbortError") setChatLoadError(scope, reason); },
  });

  // Cross-tab reads and overlapping history queries can make a count snapshot
  // obsolete. Request the current cursor immediately when its missing range
  // cannot be reconstructed from the messages already loaded in this tab.
  useEffect(() => {
    if (loaded && (feed.countNeedsRefresh || (seenId > feed.unreadSeenId && seenId < feed.latestId))) refresh();
  }, [feed.countNeedsRefresh, feed.latestId, feed.unreadSeenId, loaded, refresh, seenId]);

  // A local cursor can be ahead of another device. Keep failed writes pending
  // until a PUT or persisted server acknowledgement succeeds. Polls retry them.
  useEffect(() => {
    if (!member || !loaded || seenId == null) return;
    if (seenId <= Math.max(syncedSeenRef.current, serverSeenRef.current)) {
      setReadError("");
      return;
    }
    if (syncingSeenRef.current) return;
    syncingSeenRef.current = true;
    const controller = new AbortController();
    pendingRequestsRef.current.add(controller);
    api.chatRead(seenId, { signal: controller.signal }).then((data) => {
      if (controller.signal.aborted || !isCurrent()) return;
      syncedSeenRef.current = Math.max(syncedSeenRef.current, Number(data.seen_id) || seenId);
      markChatSeen(scope, data.seen_id ?? seenId);
      setReadError("");
    }).catch(() => {
      if (!controller.signal.aborted && isCurrent()) setReadError("읽음 상태를 저장하지 못했어요.");
    }).finally(() => {
      pendingRequestsRef.current.delete(controller);
      syncingSeenRef.current = false;
    });
  }, [feed.responseRevision, isCurrent, loaded, member, readRetry, scope, seenId]);

  const unseen = chatUnseenCount(feed, member?.id);
  const badge = badgeLabel(unseen);

  const markVisibleBottom = useCallback(() => {
    const element = listRef.current;
    if (!open || !loaded || !element || document.hidden || !isCurrent()) return;
    const rect = element.getBoundingClientRect();
    const bottom = element.scrollHeight - element.scrollTop - element.clientHeight <= 3;
    const visible = element.clientHeight > 0 && rect.top >= 0 && rect.bottom <= window.innerHeight + 2;
    if (bottom && visible) markChatSeen(scope, visibleChatReadId(getChatFeed(scope)));
  }, [isCurrent, loaded, open, scope]);

  useLayoutEffect(() => {
    if (!open || !loaded) return undefined;
    if (!dividerReadyRef.current) {
      dividerReadyRef.current = true;
      setDividerId(firstUnseenId(items, seenId, member?.id));
    }
    const element = listRef.current;
    if (element) {
      const position = historyPositionRef.current;
      if (position) {
        element.scrollTop = position.top + element.scrollHeight - position.height;
        historyPositionRef.current = null;
      } else if (stickToBottomRef.current) {
        element.scrollTop = element.scrollHeight;
      }
      setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight <= 3);
    }
    const frame = window.requestAnimationFrame(markVisibleBottom);
    return () => window.cancelAnimationFrame(frame);
  }, [items, loaded, markVisibleBottom, member?.id, open, seenId]);

  useEffect(() => {
    const onVisible = () => {
      if (!document.hidden) window.requestAnimationFrame(markVisibleBottom);
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("resize", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("resize", onVisible);
    };
  }, [markVisibleBottom]);

  const close = useCallback(() => {
    dividerReadyRef.current = false;
    stickToBottomRef.current = true;
    setDividerId(null);
    setStickerOpen(false);
    setOpen(false);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }));
    const onKeyDown = (event) => {
      if (event.key !== "Escape") return;
      if (stickerOpen) setStickerOpen(false);
      else close();
    };
    const onPointerDown = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) close();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [close, open, stickerOpen]);

  function toggle() {
    if (open) close();
    else {
      dividerReadyRef.current = loaded;
      setDividerId(loaded ? firstUnseenId(items, seenId, member?.id) : null);
      stickToBottomRef.current = true;
      setOpen(true);
    }
  }

  function jumpToLatest() {
    stickToBottomRef.current = true;
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    setAtBottom(true);
    window.requestAnimationFrame(markVisibleBottom);
  }

  async function loadOlder() {
    if (loadingOlder || !feed.hasMore || !feed.oldestId) return;
    const controller = new AbortController();
    pendingRequestsRef.current.add(controller);
    setLoadingOlder(true);
    const beforeId = feed.oldestId;
    try {
      const data = await api.chatList({ signal: controller.signal, beforeId, seenId: getChatFeed(scope).seenId });
      if (controller.signal.aborted || !isCurrent() || Number(getChatFeed(scope).oldestId) !== Number(beforeId)) return;
      const element = listRef.current;
      if (element) historyPositionRef.current = { top: element.scrollTop, height: element.scrollHeight };
      stickToBottomRef.current = false;
      receiveChat(scope, data, { older: true, beforeId });
    } catch (reason) {
      if (!controller.signal.aborted && isCurrent()) setChatLoadError(scope, reason);
    } finally {
      pendingRequestsRef.current.delete(controller);
      if (isCurrent()) setLoadingOlder(false);
    }
  }

  async function post(body) {
    if (!member || busy || !body.trim() || !isCurrent()) return false;
    setError("");
    setBusy(true);
    const controller = new AbortController();
    pendingRequestsRef.current.add(controller);
    try {
      const result = await api.chatPost(body.trim(), { signal: controller.signal });
      if (controller.signal.aborted || !isCurrent()) return false;
      stickToBottomRef.current = true;
      if (result?.message) receiveChatPost(scope, result.message);
      refresh();
      return true;
    } catch (reason) {
      if (!controller.signal.aborted && isCurrent()) setError(String(reason.message || reason));
      return false;
    } finally {
      pendingRequestsRef.current.delete(controller);
      if (isCurrent()) {
        setBusy(false);
        inputRef.current?.focus({ preventScroll: true });
      }
    }
  }

  async function send(event) {
    event.preventDefault();
    if (await post(text)) setText("");
  }

  async function sendSticker(id) {
    setStickerOpen(false);
    await post(stickerText(id));
  }

  return (
    <div className="chat-float" ref={rootRef}>
      {open ? (
        <section id={panelId} className="chat-sheet" role="dialog" aria-label="리더보드 채팅">
          <header className="chat-head">
            <div className="chat-head-title"><h3>리더보드 채팅</h3><p>KST <span className="num">{kstClock()}</span> · 오늘의 대화</p></div>
            <button type="button" className="chat-close" onClick={close} aria-label="채팅 닫기"><svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 4 8 8M12 4l-8 8" /></svg></button>
          </header>
          {loadError || readError ? (
            <div className="chat-status is-error" role="alert">
              <span>{loadError ? "채팅을 불러오지 못했어요. 연결을 확인해 주세요." : readError}</span>
              <button type="button" onClick={() => { setReadRetry((value) => value + 1); refresh(); }}>다시 시도</button>
            </div>
          ) : null}
          <div ref={listRef} role="log" aria-label="리더보드 채팅 메시지" aria-busy={!loaded && !loadError} className="chat-log" onScroll={(event) => {
            const element = event.currentTarget;
            const bottom = element.scrollHeight - element.scrollTop - element.clientHeight <= 3;
            stickToBottomRef.current = bottom;
            setAtBottom(bottom);
            markVisibleBottom();
          }}>
            {loaded && feed.hasMore ? <button type="button" className="chat-history" onClick={loadOlder} disabled={loadingOlder}>{loadingOlder ? "불러오는 중…" : "이전 메시지 더 보기"}</button> : null}
            {!loaded ? (
              loadError ? <p className="chat-helper">대화를 불러오면 여기에 표시됩니다.</p> : <div className="chat-skeleton" aria-hidden="true"><i /><i /><i /></div>
            ) : items.length === 0 ? (
              <div className="chat-empty"><img src={EMPTY_FACE} alt="" width="56" height="56" draggable="false" /><strong>아직 조용해요.</strong><span>오늘 첫 채팅을 남겨봐요.</span></div>
            ) : items.map((message, index) => {
              const previous = index > 0 ? items[index - 1] : null;
              const showDivider = dividerId != null && message.id === dividerId;
              const continued = !showDivider && sameAuthor(previous, message);
              const mine = isOwnMessage(message, member?.id);
              const legacy = message.user_id == null;
              const initial = String(message.username || "?").trim().charAt(0).toUpperCase() || "?";
              const sticker = stickerFromText(message.text);
              return (
                <Fragment key={message.id}>
                  {showDivider ? <div className="chat-divider" role="separator" aria-label="여기부터 새 메시지"><span>새 메시지</span></div> : null}
                  <article className={`chat-row${mine ? " is-mine" : ""}${continued ? " is-continued" : ""}`} aria-label={`${message.username}${legacy ? ", 이전 익명 메시지" : ""}, ${message.created_kst}`}>
                    {!mine ? <span className="chat-avatar" aria-hidden="true">{continued ? "" : initial}</span> : null}
                    <div className="chat-row-body">
                      {!mine && !continued ? <span className="chat-row-name">{message.username}{legacy ? <small className="chat-legacy">이전 익명</small> : null}</span> : null}
                      <div className="chat-bubble-line">
                        {sticker ? <p className="chat-bubble is-sticker"><img src={sticker.src} alt={`${sticker.label} 스티커`} width="92" height="92" draggable="false" decoding="async" /></p> : <p className="chat-bubble">{message.text}</p>}
                        <time className="num">{message.created_kst}</time>
                      </div>
                    </div>
                  </article>
                </Fragment>
              );
            })}
          </div>
          {!atBottom && loaded ? <button type="button" className="chat-jump" onClick={jumpToLatest}>{unseen ? `새 메시지 ${unseen}개 · 아래로` : "최신 메시지로 이동"}</button> : null}
          {member && stickerOpen ? (
            <div className="chat-sticker-tray" role="group" aria-label="스티커 고르기">
              {STICKERS.map((sticker) => <button key={sticker.id} type="button" className="chat-sticker-tile" onClick={() => sendSticker(sticker.id)} disabled={busy} aria-label={`${sticker.label} 스티커 보내기`}><img src={sticker.src} alt="" width="56" height="56" draggable="false" decoding="async" /><span>{sticker.label}</span></button>)}
            </div>
          ) : null}
          {member ? (
            <form onSubmit={send} className="chat-composer">
              <span className="chat-name-chip chat-member-name" aria-label={`로그인 회원 ${member.username}`}><span>{member.username}</span></span>
              <button type="button" className={`chat-sticker-btn${stickerOpen ? " is-on" : ""}`} onClick={() => setStickerOpen((tray) => !tray)} aria-expanded={stickerOpen} aria-label={stickerOpen ? "스티커 닫기" : "스티커 열기"} title="스티커" disabled={busy}><img src={STICKERS[0].src} alt="" width="22" height="22" draggable="false" decoding="async" /></button>
              <input ref={inputRef} value={text} aria-label="채팅 메시지" aria-invalid={error ? true : undefined} onChange={(event) => setText(event.target.value)} maxLength={300} placeholder="메시지" className="chat-field" disabled={busy} />
              <button type="submit" className="chat-send" disabled={busy || !text.trim()} aria-label={busy ? "보내는 중" : "전송"}>{busy ? <span className="chat-spinner" aria-hidden="true" /> : <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></svg>}</button>
            </form>
          ) : <div className="chat-login-prompt"><span>회원으로 로그인하고 대화에 참여해 보세요.</span><a href="/login?next=%2Fleaderboard">로그인</a></div>}
          {error ? <p className="chat-helper is-error" role="alert">{error}</p> : <span className="chat-composer-gap" aria-hidden="true" />}
        </section>
      ) : null}
      <button type="button" className={`chat-fab${open ? " is-open" : ""}${unseen ? " has-news" : ""}`} onClick={toggle} aria-expanded={open} aria-controls={open ? panelId : undefined} aria-label={open ? "채팅 닫기" : badge ? `채팅 열기, 새 메시지 ${unseen}개` : "채팅 열기"}>
        <img src={FAB_ICON} alt="" width="44" height="44" draggable="false" decoding="async" /><span className="chat-fab-label" aria-hidden="true">Chat</span>{badge ? <span className="chat-fab-badge num" aria-hidden="true">{badge}</span> : null}
      </button>
    </div>
  );
}
