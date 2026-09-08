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
import { STICKERS, stickerFromText, stickerText } from "../lib/chatStickers.js";

// 리더보드 채팅 — 우하단 원형 껄무새 버튼으로 여는 대화록. 목록이 길어도 항상 손에 닿고,
// 닫혀 있는 동안 도착한 메시지는 'N new' 배지와 놀란 표정으로 알린다. 매일 KST 00:00 초기화.
// React 가 메시지 텍스트를 이스케이프하므로 저장된 원문이 HTML 로 실행되지 않는다.
const POLL_MS = 3000;
const FAB_ICON = "/brand/ggparrot-feather-terminal.svg"; // 정사각 깃털 — 원판 안에 잘림 없이 들어간다
const EMPTY_FACE = "/brand/agent/ggparrot-agent-curious-v1.svg";

// 헤더의 KST 시계(시:분).
function kstClock(now = Date.now()) {
  const date = new Date(now + 9 * 60 * 60 * 1000);
  const two = (n) => String(n).padStart(2, "0");
  return `${two(date.getUTCHours())}:${two(date.getUTCMinutes())}`;
}

export default function ChatBox({ defaultOpen = false, defaultStickerTray = false }) {
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
  const [stickerOpen, setStickerOpen] = useState(defaultStickerTray);

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
      if (event.key !== "Escape") return;
      // 스티커 트레이가 열려 있으면 그것부터 닫는다
      setStickerOpen((tray) => {
        if (!tray) setOpen(false);
        return false;
      });
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

  // 글과 스티커가 같은 길로 나간다 — 아이디 확인·전송·429 처리 한 곳.
  async function post(body) {
    setError("");
    if (!body.trim()) return false;
    if (!name.trim()) {
      setError("먼저 아이디를 정해 주세요.");
      nameRef.current?.focus();
      return false;
    }
    setBusy(true);
    try {
      setNickname(name.trim());
      await api.chatPost(name.trim(), body.trim());
      stickToBottomRef.current = true;
      refresh();
      return true;
    } catch (err) {
      setError(String(err.message || err)); // 429 rate limit surfaces here
      return false;
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  async function send(e) {
    e.preventDefault();
    if (await post(text)) setText("");
  }

  // 카카오톡처럼 스티커는 누르는 즉시 보낸다.
  async function sendSticker(id) {
    setStickerOpen(false);
    await post(stickerText(id));
  }

  const nameNeeded = !name.trim() || editingName;
  // 별도 타이머 없이 렌더마다 계산 — 폴링이 3초마다 새 목록으로 재렌더하므로 충분하다.
  const clock = kstClock();

  return (
    <div className="chat-float">
      {open ? (
        <section id={panelId} className="chat-sheet" role="dialog" aria-label="리더보드 채팅">
          <header className="chat-head">
            <div className="chat-head-title">
              <h3>리더보드 채팅</h3>
              <p>KST <span className="num">{clock}</span></p>
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
                          {stickerFromText(m.text) ? (
                            <p className="chat-bubble is-sticker">
                              <img src={stickerFromText(m.text).src} alt={`${stickerFromText(m.text).label} 스티커`} width="120" height="120" draggable="false" decoding="async" />
                            </p>
                          ) : (
                            <p className="chat-bubble">{m.text}</p>
                          )}
                          <time className="num">{m.created_kst}</time>
                        </div>
                      </div>
                    </article>
                  </Fragment>
                );
              })
            )}
          </div>

          {stickerOpen ? (
            <div className="chat-sticker-tray" role="group" aria-label="스티커 고르기">
              {STICKERS.map((sticker) => (
                <button
                  key={sticker.id}
                  type="button"
                  className="chat-sticker-tile"
                  onClick={() => sendSticker(sticker.id)}
                  disabled={busy}
                  aria-label={`${sticker.label} 스티커 보내기`}
                >
                  <img src={sticker.src} alt="" width="56" height="56" draggable="false" decoding="async" />
                  <span>{sticker.label}</span>
                </button>
              ))}
            </div>
          ) : null}

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
            <button
              type="button"
              className={`chat-sticker-btn${stickerOpen ? " is-on" : ""}`}
              onClick={() => setStickerOpen((tray) => !tray)}
              aria-expanded={stickerOpen}
              aria-label={stickerOpen ? "스티커 닫기" : "스티커 열기"}
              title="스티커"
            >
              <img src={STICKERS[0].src} alt="" width="22" height="22" draggable="false" decoding="async" />
            </button>
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
          {error ? <p className="chat-helper is-error" role="alert">{error}</p> : <span className="chat-composer-gap" aria-hidden="true" />}
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
        <img src={FAB_ICON} alt="" width="44" height="44" draggable="false" decoding="async" />
        <span className="chat-fab-label" aria-hidden="true">Chat</span>
        {badge ? <span className="chat-fab-badge num" aria-hidden="true">{badge}</span> : null}
      </button>
    </div>
  );
}
