import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "../api.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { getAuthUser, getToken, useAuth } from "../lib/auth.js";
import { badgeLabel, chatScope, firstUnseenId, isOwnMessage, sameAuthor } from "../lib/chatBadge.js";
import { STICKERS, stickerFromText, stickerText } from "../lib/chatStickers.js";
import { macroIdsInText, splitMacroText } from "../lib/chatMacro.js";
import { replyText, stripReplyToken } from "../lib/chatReply.js";
import { applyMacroPick, filterMacros, slashQuery } from "../lib/chatSlash.js";
import { getUserId } from "../lib/user.js";
import ReportDialog from "./ReportDialog.jsx";
import CoinIcon from "./CoinIcon.jsx";
import { chatUnseenCount, getChatFeed, markChatSeen, observeChat, receiveChat, receiveChatPost, setChatLoadError, visibleChatReadId } from "../lib/chatStore.js";
import UserAvatar, { AuthorAvatar } from "./UserAvatar.jsx";
import "./ChatBox.css";

const POLL_MS = 3000;
const FAB_ICON = "/brand/ggparrot-feather-terminal.svg";
const EMPTY_FACE = "/brand/agent/ggparrot-agent-curious-v1.svg";
const PLACEMENT_KEY = "chat:placement";      // 버튼을 끌어다 둔 자리(오른쪽·아래 여백 px) — 이 브라우저에만
const OPACITY_KEY = "chat:opacity";          // 시트 불투명도 0.25~1
const DRAG_THRESHOLD = 6;                    // 이보다 덜 움직이면 클릭
const EDGE = 8;                              // 화면 가장자리 최소 여백
const TOPBAR_FALLBACK = 64;
const MOBILE_PLACEMENT_QUERY = "(max-width: 767px), (max-width: 1099px) and (pointer: coarse)";

function isMobilePlacement() {
  return typeof window !== "undefined" && window.matchMedia(MOBILE_PLACEMENT_QUERY).matches;
}
function subscribeMobilePlacement(listener) {
  const query = window.matchMedia(MOBILE_PLACEMENT_QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

function readStorage(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function writeStorage(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* 저장 못 해도 동작엔 지장 없다 */ }
}
function loadPlacement() {
  try {
    const parsed = JSON.parse(readStorage(PLACEMENT_KEY) || "null");
    if (parsed && Number.isFinite(parsed.right) && Number.isFinite(parsed.bottom)) return { right: parsed.right, bottom: parsed.bottom };
  } catch { /* 깨진 값은 기본 자리 */ }
  return null;
}
function loadOpacity() {
  const value = Number(readStorage(OPACITY_KEY));
  return Number.isFinite(value) && value >= 0.25 && value <= 1 ? value : 1;
}
function topbarHeight() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--site-topbar-h");
  const value = parseFloat(raw);
  return Number.isFinite(value) ? value : TOPBAR_FALLBACK;
}
// 왼쪽 한계 — 사이드바가 고정으로 깔린 화면(≥1100px)에서는 그 오른쪽 끝에서 막는다.
// 좁은 화면의 서랍은 닫혀 있으면 폭이 0 이라 자연히 화면 가장자리가 한계가 된다.
function leftBoundary() {
  const sidebar = document.querySelector(".site-sidebar");
  if (!sidebar) return EDGE;
  const rect = sidebar.getBoundingClientRect();
  const pinned = getComputedStyle(sidebar).position === "fixed" && rect.width > 0 && rect.left <= 1;
  return pinned ? rect.right + EDGE : EDGE;
}
// 버튼(과 열린 시트)이 화면 안에, 사이드바 오른쪽에 남도록 여백을 자른다.
function clampPlacement(placement, root) {
  if (!placement || !root) return placement;
  // offsetWidth/Height 를 쓴다 — 누르는 순간의 :active scale(0.97) 이 섞이면 한계가 몇 px 헐거워진다.
  const fab = root.querySelector(".chat-fab");
  const sheet = root.querySelector(".chat-sheet");
  const width = Math.max(fab?.offsetWidth || 0, sheet?.offsetWidth || 0);
  const above = sheet ? sheet.offsetHeight + 12 : 0;
  const maxRight = Math.max(EDGE, window.innerWidth - width - leftBoundary());
  const maxBottom = Math.max(EDGE, window.innerHeight - (fab?.offsetHeight || 48) - above - topbarHeight() - EDGE);
  return {
    right: Math.round(Math.min(maxRight, Math.max(EDGE, placement.right))),
    bottom: Math.round(Math.min(maxBottom, Math.max(EDGE, placement.bottom))),
  };
}

function kstClock(now = Date.now()) {
  const date = new Date(now + 9 * 60 * 60 * 1000);
  return `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
}

// A member switch remounts transient UI and aborts the previous member's requests.
// Feed/read state lives outside the route, in a separate cache for each account.
// 매크로 언급 카드 — 종목 로고·종목·글쓴이·전략 한 줄. 누르면 리더보드의 그 행으로 간다.
function MacroCard({ card, onClose }) {
  const goToEntry = () => {
    const row = document.getElementById(`leaderboard-entry-${card.entry_id}`);
    if (!row) return;
    onClose?.();
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("is-flash");
    window.setTimeout(() => row.classList.remove("is-flash"), 1600);
  };
  return (
    <button type="button" className="chat-macro" onClick={goToEntry} title="리더보드에서 이 매크로 보기">
      <CoinIcon symbol={card.symbol} size={28} alt="" />
      <span className="chat-macro-body">
        <span className="chat-macro-head">
          <b className="num">{card.symbol}</b>
          <span className="chat-macro-author">{card.is_ai ? "🤖 " : ""}{card.username}</span>
        </span>
        <span className="chat-macro-summary">{card.locked ? "잠긴 매크로 · 리더보드에서 언락하면 전략이 보여요" : card.human_summary || "전략 설명 없음"}</span>
      </span>
    </button>
  );
}

export default function ChatBox(props) {
  const { token, user } = useAuth();
  const member = token && user?.id != null ? user : null;
  const scope = chatScope(member?.id);
  return <MemberChatBox key={scope} {...props} member={member} scope={scope} />;
}

function MemberChatBox({ member, scope, defaultOpen = false, defaultStickerTray = false }) {
  const mobilePlacement = useSyncExternalStore(subscribeMobilePlacement, isMobilePlacement, () => false);
  const panelId = useId();
  const subscribe = useCallback((listener) => observeChat(scope, listener), [scope]);
  const snapshot = useCallback(() => getChatFeed(scope), [scope]);
  const feed = useSyncExternalStore(subscribe, snapshot, snapshot);
  const { items, loaded, seenId, loadError } = feed;
  const [open, setOpen] = useState(defaultOpen);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [menu, setMenu] = useState(null);      // 오른쪽 클릭 메뉴 {x, y, message}
  const [replyTo, setReplyTo] = useState(null); // 답장 대상 메시지
  const [reporting, setReporting] = useState(null); // 신고할 메시지
  const [copiedId, setCopiedId] = useState(0);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [macroList, setMacroList] = useState(null); // 매크로 고르기 목록(한 번 받아 둔다)
  const [pickIndex, setPickIndex] = useState(0);
  const [readError, setReadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [readRetry, setReadRetry] = useState(0);
  const [dividerId, setDividerId] = useState(null);
  const [stickerOpen, setStickerOpen] = useState(defaultStickerTray && !!member);
  const [atBottom, setAtBottom] = useState(true);
  const [placement, setPlacement] = useState(loadPlacement);   // null = CSS 기본 자리(오른쪽 아래)
  const [opacity, setOpacity] = useState(loadOpacity);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef(null);                                  // { id, x, y, right, bottom, moved }
  const suppressClickRef = useRef(false);
  const dividerReadyRef = useRef(false);
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  // 입력칸은 한 줄에서 시작해 글이 길어지면 위로 자란다(최대 5줄, 그 뒤로는 스스로 스크롤).
  useLayoutEffect(() => {
    const field = inputRef.current;
    if (!field || field.tagName !== "TEXTAREA") return;
    field.style.height = "auto";
    const grown = Math.min(field.scrollHeight, 132);
    field.style.height = `${grown}px`;
    // 한 줄일 때는 스크롤 막대를 아예 만들지 않는다(브라우저가 화살표를 그린다).
    field.style.overflowY = field.scrollHeight > 132 ? "auto" : "hidden";
  }, [text, open]);
  const stickToBottomRef = useRef(true);
  const mountedRef = useRef(false);
  const pendingRequestsRef = useRef(new Set());
  const historyPositionRef = useRef(null);
  const syncedSeenRef = useRef(-1);
  const serverSeenRef = useRef(-1);
  const syncingSeenRef = useRef(false);

  useLayoutEffect(() => {
    if (!mobilePlacement) return undefined;
    // A desktop drag may be in progress when a window enters the mobile layout.
    const drag = dragRef.current;
    const fab = rootRef.current?.querySelector(".chat-fab");
    if (drag && fab?.hasPointerCapture?.(drag.id)) fab.releasePointerCapture(drag.id);
    dragRef.current = null;
    suppressClickRef.current = false;
    setDragging(false);
    const root = rootRef.current;
    const viewport = window.visualViewport;
    const fitVisibleViewport = () => {
      if (!root) return;
      const height = viewport?.height || window.innerHeight;
      const obscuredBottom = Math.max(0, window.innerHeight - height - (viewport?.offsetTop || 0));
      root.style.setProperty("--chat-visible-height", `${height}px`);
      root.style.setProperty("--chat-keyboard-offset", `${obscuredBottom}px`);
    };
    fitVisibleViewport();
    viewport?.addEventListener("resize", fitVisibleViewport);
    viewport?.addEventListener("scroll", fitVisibleViewport);
    window.addEventListener("resize", fitVisibleViewport);
    return () => {
      viewport?.removeEventListener("resize", fitVisibleViewport);
      viewport?.removeEventListener("scroll", fitVisibleViewport);
      window.removeEventListener("resize", fitVisibleViewport);
      root?.style.removeProperty("--chat-visible-height");
      root?.style.removeProperty("--chat-keyboard-offset");
    };
  }, [mobilePlacement]);

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
      // 위에 뜬 것부터 닫는다 — 도움말·신고 창이 열려 있으면 채팅은 그대로 둔다.
      if (shortcutsOpen) setShortcutsOpen(false);
      else if (reporting) setReporting(null);
      else if (stickerOpen) setStickerOpen(false);
      else close();
    };
    const onPointerDown = (event) => {
      // body 로 띄운 창(도움말·신고·메시지 메뉴)에서의 클릭은 '바깥'이 아니다.
      if (event.target.closest?.(".scrim, .chat-menu")) return;
      if (rootRef.current && !rootRef.current.contains(event.target)) close();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [close, open, reporting, shortcutsOpen, stickerOpen]);

  // 끌어서 옮기기 — 버튼을 잡고 6px 넘게 움직이면 드래그, 아니면 클릭. 자리는 이 브라우저에 남는다.
  function onFabPointerDown(event) {
    if (isMobilePlacement()) return;
    if (event.button != null && event.button !== 0) return;
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    dragRef.current = {
      id: event.pointerId, x: event.clientX, y: event.clientY, moved: false,
      right: window.innerWidth - rect.right, bottom: window.innerHeight - rect.bottom,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function onFabPointerMove(event) {
    if (isMobilePlacement()) return;
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    if (!drag.moved) { drag.moved = true; setDragging(true); }
    setPlacement(clampPlacement({ right: drag.right - dx, bottom: drag.bottom - dy }, rootRef.current));
  }
  function onFabPointerEnd(event) {
    if (isMobilePlacement()) return;
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    dragRef.current = null;
    if (drag.moved) {
      suppressClickRef.current = true;             // 놓는 순간의 click 은 열고 닫기가 아니다
      setDragging(false);
      setPlacement((current) => { if (current) writeStorage(PLACEMENT_KEY, JSON.stringify(current)); return current; });
    }
  }
  function onFabClick() {
    if (suppressClickRef.current) { suppressClickRef.current = false; return; }
    toggle();
  }
  function changeOpacity(event) {
    const next = Math.min(1, Math.max(0.25, Number(event.target.value) / 100));
    setOpacity(next);
    writeStorage(OPACITY_KEY, String(next));
  }
  // 창 크기가 바뀌거나 시트가 열리면 옮겨 둔 자리가 화면 밖으로 나가지 않게 다시 자른다.
  useLayoutEffect(() => {
    if (mobilePlacement || !placement) return undefined;
    const fit = () => {
      // The resize event can arrive before the matchMedia subscription renders.
      if (isMobilePlacement()) return;
      setPlacement((current) => {
        const next = clampPlacement(current, rootRef.current);
        return next && current && next.right === current.right && next.bottom === current.bottom ? current : next;
      });
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [open, placement != null, mobilePlacement]); // eslint-disable-line react-hooks/exhaustive-deps

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
    const body = replyTo ? replyText(replyTo.id, text) : text;
    if (await post(body)) {
      setText("");
      setReplyTo(null);
    }
  }

  // `/` 로 매크로 고르기 — 오늘 리더보드 목록을 한 번 받아 두고 입력에 따라 걸러 보인다.
  const query = member ? slashQuery(text) : null;
  const picking = query !== null;
  const picks = picking ? filterMacros(macroList || [], query) : [];
  useEffect(() => {
    if (!picking || macroList !== null) return undefined;
    let alive = true;
    api.leaderboard(getUserId())
      .then((data) => { if (alive) setMacroList(data?.entries || data?.items || []); })
      .catch(() => { if (alive) setMacroList([]); });
    return () => { alive = false; };
  }, [picking, macroList]);
  useEffect(() => { setPickIndex(0); }, [query]);

  function pickMacro(entry) {
    setText((current) => applyMacroPick(current, entry.id));
    inputRef.current?.focus();
  }

  // 오른쪽 클릭 메뉴 — 답장·복사·신고. 메뉴는 화면 안으로 접어 넣는다.
  function openMenu(event, message) {
    event.preventDefault();
    const width = 168;
    const height = 132;
    setMenu({
      message,
      x: Math.min(event.clientX, window.innerWidth - width - 8),
      y: Math.min(event.clientY, window.innerHeight - height - 8),
    });
  }

  async function copyMessage(message) {
    const body = stripReplyToken(message.text);
    try {
      await navigator.clipboard.writeText(body);
      setCopiedId(message.id);
      window.setTimeout(() => setCopiedId((current) => (current === message.id ? 0 : current)), 1400);
    } catch {
      window.prompt("메시지 내용이에요. 복사해 주세요.", body);
    }
  }

  useEffect(() => {
    if (!menu) return undefined;
    const close_ = () => setMenu(null);
    const onKey = (event) => { if (event.key === "Escape") { event.preventDefault(); close_(); } };
    document.addEventListener("pointerdown", close_);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close_);
    return () => {
      document.removeEventListener("pointerdown", close_);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close_);
    };
  }, [menu]);

  async function sendSticker(id) {
    setStickerOpen(false);
    await post(stickerText(id));
  }

  return (
    <div className={`chat-float${dragging ? " is-dragging" : ""}${mobilePlacement ? " is-mobile-fixed" : ""}`} ref={rootRef} style={!mobilePlacement && placement ? { right: placement.right, bottom: placement.bottom } : undefined}>
      {open ? (
        <section id={panelId} className="chat-sheet" role="dialog" aria-label="리더보드 채팅" style={opacity < 1 ? { "--chat-sheet-opacity": opacity } : undefined}>
          <header className="chat-head">
            <div className="chat-head-title"><h3>리더보드 채팅</h3><p>KST <span className="num">{kstClock()}</span> · 오늘의 대화</p></div>
            <div className="chat-head-tools">
              <label className="chat-opacity" title={`투명도 ${Math.round((1 - opacity) * 100)}%`}>
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="3" /></svg>
                <input type="range" min="25" max="100" step="5" value={Math.round(opacity * 100)} onChange={changeOpacity} aria-label="채팅창 불투명도" aria-valuetext={`${Math.round(opacity * 100)}%`} />
              </label>
              <button type="button" className="chat-tool" onClick={() => setShortcutsOpen(true)} aria-label="빠른 입력 도움말" title="빠른 입력">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <rect x="2.5" y="6" width="19" height="12" rx="2.5" />
                  <path d="M6 9.5h.01M9.5 9.5h.01M13 9.5h.01M16.5 9.5h.01M6 13h.01M18 13h.01M9 16h6" />
                </svg>
              </button>
              <button type="button" className="chat-close" onClick={close} aria-label="채팅 닫기"><svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 4 8 8M12 4l-8 8" /></svg></button>
            </div>
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
              const body = stripReplyToken(message.text);
              const sticker = stickerFromText(body);
              return (
                <Fragment key={message.id}>
                  {showDivider ? <div className="chat-divider" role="separator" aria-label="여기부터 새 메시지"><span>새 메시지</span></div> : null}
                  <article className={`chat-row${mine ? " is-mine" : ""}${continued ? " is-continued" : ""}`} aria-label={`${message.username}${legacy ? ", 이전 익명 메시지" : ""}, ${message.created_kst}`} onContextMenu={(event) => openMenu(event, message)}>
                    {!mine ? (continued ? <span className="chat-avatar" aria-hidden="true" /> : <AuthorAvatar userId={message.user_id} src={message.avatar_url} name={message.username} size={32} className="chat-avatar" />) : null}
                    <div className="chat-row-body">
                      {!mine && !continued ? <span className="chat-row-name">{message.username}{legacy ? <small className="chat-legacy">이전 익명</small> : null}</span> : null}
                      {message.reply_to ? (
                        <p className="chat-quote"><b>{message.reply_to.username}</b><span>{message.reply_to.excerpt || "내용 없음"}</span></p>
                      ) : null}
                      <div className="chat-bubble-line">
                        {sticker
                          ? <p className="chat-bubble is-sticker"><img src={sticker.src} alt={`${sticker.label} 스티커`} width="92" height="92" draggable="false" decoding="async" /></p>
                          : message.macros?.length
                            ? <div className="chat-bubble is-macro">{splitMacroText(body, message.macros).map((part, partIndex) => (
                                part.type === "text"
                                  ? <p key={`t-${partIndex}`} className="chat-bubble-text">{part.text}</p>
                                  : <MacroCard key={`m-${part.card.entry_id}-${partIndex}`} card={part.card} onClose={close} />
                              ))}</div>
                            : <p className="chat-bubble">{body}</p>}
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
          {/* `/` 매크로 고르기 — 입력칸 위에 뜬다. 고르면 [macro:id] 가 본문에 들어간다. */}
          {picking ? (
            <div className="chat-picker" role="listbox" aria-label="매크로 고르기">
              {macroList === null ? (
                <p className="chat-picker-empty">불러오는 중…</p>
              ) : picks.length === 0 ? (
                <p className="chat-picker-empty">{macroList.length ? "맞는 매크로가 없어요." : "오늘 등록된 매크로가 없어요."}</p>
              ) : picks.map((entry, index) => (
                <button
                  key={entry.id}
                  type="button"
                  role="option"
                  aria-selected={index === pickIndex}
                  className={`chat-picker-row${index === pickIndex ? " is-active" : ""}`}
                  onMouseEnter={() => setPickIndex(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => pickMacro(entry)}
                >
                  <CoinIcon symbol={entry.symbol} size={24} alt="" />
                  <span className="chat-picker-body">
                    <span className="chat-picker-title"><b className="num">{entry.symbol}</b><span>{entry.username || entry.nickname}</span></span>
                    <span className="chat-picker-summary">{entry.locked ? "잠긴 매크로" : entry.human_summary || "전략 설명 없음"}</span>
                  </span>
                </button>
              ))}
            </div>
          ) : null}
          {member && replyTo ? (
            <p className="chat-reply-bar">
              <span className="chat-reply-to"><b>{replyTo.username}</b>에게 답장</span>
              <span className="chat-reply-excerpt">{stripReplyToken(replyTo.text)}</span>
              <button type="button" onClick={() => setReplyTo(null)} aria-label="답장 취소">✕</button>
            </p>
          ) : null}
          {/* 붙여넣은 매크로 링크 — 보내면 카드로 바뀐다는 걸 미리 알려 준다. */}
          {member && macroIdsInText(text).length ? (
            <p className="chat-macro-hint">매크로 <b className="num">{macroIdsInText(text).length}</b>개를 언급했어요. 보내면 카드로 보여요.</p>
          ) : null}
          {member ? (
            <form onSubmit={send} className="chat-composer">
              <span className="chat-name-chip chat-member-name" aria-label={`로그인 회원 ${member.username}`}><UserAvatar src={member.avatar_url} name={member.username} size={24} /><span className="chat-member-label">{member.username}</span></span>
              <button type="button" className={`chat-sticker-btn${stickerOpen ? " is-on" : ""}`} onClick={() => setStickerOpen((tray) => !tray)} aria-expanded={stickerOpen} aria-label={stickerOpen ? "스티커 닫기" : "스티커 열기"} title="스티커" disabled={busy}><img src={STICKERS[0].src} alt="" width="22" height="22" draggable="false" decoding="async" /></button>
              <textarea
                ref={inputRef}
                value={text}
                rows={1}
                aria-label="채팅 메시지"
                aria-invalid={error ? true : undefined}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={(event) => {
                  if (picking && picks.length) {
                    if (event.key === "ArrowDown") { event.preventDefault(); setPickIndex((i) => (i + 1) % picks.length); return; }
                    if (event.key === "ArrowUp") { event.preventDefault(); setPickIndex((i) => (i - 1 + picks.length) % picks.length); return; }
                    if ((event.key === "Enter" || event.key === "Tab") && !event.nativeEvent.isComposing) {
                      event.preventDefault();
                      pickMacro(picks[pickIndex] || picks[0]);
                      return;
                    }
                  }
                  if (event.key === "Escape" && picking) { event.preventDefault(); setText(""); return; }
                  // 줄바꿈은 Shift+Enter. Enter 는 보내기(한글 조합 중에는 넘긴다).
                  if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                  event.preventDefault();
                  send(event);
                }}
                maxLength={300}
                placeholder="메시지"
                className="chat-field"
                disabled={busy}
              />
              <button type="submit" className="chat-send" disabled={busy || !text.trim()} aria-label={busy ? "보내는 중" : "전송"}>{busy ? <span className="chat-spinner" aria-hidden="true" /> : <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></svg>}</button>
            </form>
          ) : <div className="chat-login-prompt"><span>회원으로 로그인하고 대화에 참여해 보세요.</span><a href="/login?next=%2Fleaderboard">로그인</a></div>}
          {error ? <p className="chat-helper is-error" role="alert">{error}</p> : <span className="chat-composer-gap" aria-hidden="true" />}
          {/* 메뉴는 body 에 띄운다 — 채팅 시트 안에 두면 시트의 스크롤·변형에 잘린다. */}
          {menu ? createPortal(
            <div className="chat-menu" style={{ left: menu.x, top: menu.y }} role="menu" aria-label="메시지 메뉴" onPointerDown={(event) => event.stopPropagation()}>
              {member ? (
                <button type="button" role="menuitem" onClick={() => { setReplyTo(menu.message); setMenu(null); inputRef.current?.focus(); }}>답장</button>
              ) : null}
              <button type="button" role="menuitem" onClick={() => { copyMessage(menu.message); setMenu(null); }}>
                {copiedId === menu.message.id ? "복사했어요" : "복사"}
              </button>
              {member && menu.message.user_id !== member.id ? (
                <button type="button" role="menuitem" className="is-danger" onClick={() => { setReporting(menu.message); setMenu(null); }}>신고</button>
              ) : null}
            </div>,
            document.body,
          ) : null}
          {shortcutsOpen ? createPortal(
            <div className="scrim fixed inset-0 z-[95] grid place-items-center p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) setShortcutsOpen(false); }}>
              <div role="dialog" aria-modal="true" aria-labelledby="chat-shortcuts-title" className="dialog chat-shortcuts">
                <h2 id="chat-shortcuts-title" className="t-h4 text-slate-900">빠른 입력</h2>
                <p className="mt-3 t-small text-slate-700">채팅을 더 빠르게 쓰는 방법이에요.</p>
                <ul className="chat-shortcut-list">
                  <li>
                    <p className="chat-shortcut-title"><kbd>/</kbd><b>매크로 언급</b></p>
                    <p className="chat-shortcut-desc">오늘의 매크로를 골라 채팅에 카드로 보내요. <kbd>/BTC</kbd> 처럼 이어 치면 좁혀져요.</p>
                  </li>
                  <li>
                    <p className="chat-shortcut-title"><kbd>↑</kbd><kbd>↓</kbd><kbd>Enter</kbd><b>목록에서 고르기</b></p>
                    <p className="chat-shortcut-desc">매크로 목록이 떠 있을 때 써요. <kbd>Esc</kbd> 로 닫아요.</p>
                  </li>
                  <li>
                    <p className="chat-shortcut-title"><kbd>Enter</kbd><b>보내기</b></p>
                    <p className="chat-shortcut-desc"><kbd>Shift</kbd><kbd>Enter</kbd> 는 줄바꿈이에요. 입력칸은 줄이 늘면 위로 커져요.</p>
                  </li>
                  <li>
                    <p className="chat-shortcut-title"><kbd className="is-mouse">오른쪽 클릭</kbd><b>답장 · 복사 · 신고</b></p>
                    <p className="chat-shortcut-desc">메시지를 오른쪽 클릭하면 나와요. 답장하면 상대 말이 인용돼요.</p>
                  </li>
                  <li>
                    <p className="chat-shortcut-title"><kbd className="is-mouse">깃털 버튼</kbd><b>스티커</b></p>
                    <p className="chat-shortcut-desc">작성줄 왼쪽 깃털을 누르면 껄무새 표정 6종을 바로 보내요.</p>
                  </li>
                </ul>
                <div className="confirm-dialog-actions">
                  <button type="button" className="btn btn-l w-full btn-primary" onClick={() => setShortcutsOpen(false)}>닫기</button>
                </div>
              </div>
            </div>,
            document.body,
          ) : null}
          <ReportDialog open={Boolean(reporting)} targetType="chat" targetId={reporting?.id} label="메시지" onClose={() => setReporting(null)} />
        </section>
      ) : null}
      <button type="button" className={`chat-fab${open ? " is-open" : ""}${unseen ? " has-news" : ""}${dragging ? " is-dragging" : ""}`} onClick={onFabClick} onPointerDown={mobilePlacement ? undefined : onFabPointerDown} onPointerMove={mobilePlacement ? undefined : onFabPointerMove} onPointerUp={mobilePlacement ? undefined : onFabPointerEnd} onPointerCancel={mobilePlacement ? undefined : onFabPointerEnd} title={mobilePlacement ? undefined : "끌어서 옮길 수 있어요"} aria-expanded={open} aria-controls={open ? panelId : undefined} aria-label={open ? "채팅 닫기" : badge ? `채팅 열기, 새 메시지 ${unseen}개` : "채팅 열기"}>
        <img src={FAB_ICON} alt="" width="44" height="44" draggable="false" decoding="async" /><span className="chat-fab-label" aria-hidden="true">Chat</span>{badge ? <span className="chat-fab-badge num" aria-hidden="true">{badge}</span> : null}
      </button>
    </div>
  );
}
