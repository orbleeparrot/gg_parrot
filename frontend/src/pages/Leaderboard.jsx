import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import SimBadge from "../components/SimBadge.jsx";
import RegisterMacroModal from "../components/RegisterMacroModal.jsx";
import ChatBox from "../components/ChatBox.jsx";
import { PageHeader, EmptyState, Loading, ErrorNote } from "../components/Page.jsx";
import { api } from "../api.js";
import CoinIcon from "../components/CoinIcon.jsx";
import { getUserId } from "../lib/user.js";
import { useAuth, useAccountGuard, isLoggedIn, getAuthUser, updateAuthUser } from "../lib/auth.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { applyVote, settleVote } from "../lib/leaderboardVotes.js";
import StrategyDetails from "../components/StrategyDetails.jsx";
import { impressionKey } from "../lib/visit.js";
import { isLive, liveReturn, stateHelp, stateLine, symbolsOf } from "../lib/leaderboardState.js";
import { LIVE_TITLE, STATE_LEGEND } from "../lib/leaderboardCopy.js";
import "./LeaderboardMobile.css";

// 매크로 지표 비콘 — 노출(목록에 보임)·열람(빌더로 가져오기 · 빠른 실행 · 언락 중 하나를 누름).
// 세션당 매크로 1회만 — 목록은 5초마다 다시 오므로 렌더마다 세면 노출이 9배로 부푼다.
// 중복 판정의 기준은 모듈 메모리 Set 이고, sessionStorage 는 새로고침을 넘기기 위한 씨앗·보관용일 뿐이다.
// (sessionStorage 가 막힌 브라우저에서 저장소만 믿으면 5초 폴링마다 최대 100개 id 를 다시 보내
// IP 제한 60회/분까지 서버에 upsert 를 때린다.)
// 키는 "KST 날짜:id" — 서버가 노출·열람을 day_kst 로 접으므로, 자정을 넘긴 세션은 다음 날 다시 1회 세야
// 그날 클릭률의 분모가 비지 않는다(열람도 같은 키를 써야 분자·분모 규칙이 같다).
// 실패는 삼킨다: 지표 하나 잃는 것이지 화면 흐름을 막을 일이 아니다.
const LB_SEEN_KEY = "ggp:lb_seen";
const LB_OPEN_KEY = "ggp:lb_open";
const IMPRESSION_BATCH = 100;
const KEY_RE = /^\d{4}-\d{2}-\d{2}:\d+$/;
function readKeySet(key) {
  try {
    const raw = JSON.parse(sessionStorage.getItem(key) || "[]");
    // 옛 형식(숫자 id 배열)은 버린다 — 날짜가 없으면 오늘 것인지 알 수 없다.
    return new Set(Array.isArray(raw) ? raw.filter((k) => typeof k === "string" && KEY_RE.test(k)) : []);
  } catch {
    return new Set();
  }
}
function writeKeySet(key, set) {
  try { sessionStorage.setItem(key, JSON.stringify([...set].slice(-5000))); } catch { /* 저장 못 해도 메모리 Set 이 이번 세션의 중복은 막는다 */ }
}
// 모듈 로드 때 한 번만 저장소에서 씨앗을 읽는다. 이후엔 메모리가 진실.
const seenMem = readKeySet(LB_SEEN_KEY);
const openMem = readKeySet(LB_OPEN_KEY);
function sendImpressions(ids) {
  const now = Date.now();
  const fresh = [];
  const picked = new Set();
  for (const raw of ids) {
    const id = Number(raw);
    if (!Number.isSafeInteger(id) || id < 1 || picked.has(id) || seenMem.has(impressionKey(id, now))) continue;
    picked.add(id);
    fresh.push(id);
    if (fresh.length >= IMPRESSION_BATCH) break;
  }
  if (!fresh.length) return;
  fresh.forEach((id) => seenMem.add(impressionKey(id, now)));
  writeKeySet(LB_SEEN_KEY, seenMem);
  try { api.leaderboardImpressions(fresh).catch(() => {}); } catch { /* 비콘 */ }
}
function sendOpen(id) {
  const entryId = Number(id);
  if (!Number.isSafeInteger(entryId) || entryId < 1) return;
  const key = impressionKey(entryId, Date.now());
  if (openMem.has(key)) return;
  openMem.add(key);
  writeKeySet(LB_OPEN_KEY, openMem);
  try { api.leaderboardOpen(entryId).catch(() => {}); } catch { /* 비콘 */ }
}

const pad = (n) => String(n).padStart(2, "0");

// 행 액션 아이콘 — 글자 버튼 셋이 오른쪽 끝에 몰리지 않게 아이콘으로 줄인다.
function ThumbUpIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.3a2 2 0 0 0 2-1.7l1.4-9a2 2 0 0 0-2-2.3H14zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
    </svg>
  );
}
function ThumbDownIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.7a2 2 0 0 0-2 1.7l-1.4 9a2 2 0 0 0 2 2.3H10zM17 2h2.7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H17" />
    </svg>
  );
}
// 잠긴 매크로 — 자물쇠. 언락 버튼은 좋아요·싫어요와 같은 알약이고 글자는 가격(100P)만.
function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="4" y="10.5" width="16" height="11" rx="2.4" />
      <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
      <circle cx="12" cy="15.5" r="1.3" />
      <path d="M12 16.8v1.7" />
    </svg>
  );
}
function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}
const fmtCountdown = (s) => `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;

// 보유 중 행은 현재가(3초 시세)로 미실현 수익률을 다시 계산하고, 나머지는 서버 값 그대로.
function ret(e, prices, now) {
  const value = liveReturn(e, prices, now);
  if (value == null) return { text: "집계중…", cls: "text-slate-500", live: false };
  const up = value >= 0;
  return { text: `${up ? "+" : ""}${value.toFixed(2)}%`, cls: up ? "text-green-600" : "text-red-600", live: isLive(e, prices, now) };
}

function registrationLabel(entry) {
  return entry.defending ? `${entry.first_created_kst} 등록` : `오늘 ${entry.created_kst} 등록`;
}

function EntryBadges({ entry, top3 }) {
  return (
    <>
      {entry.is_ai && <span className="badge badge-ai">AI</span>}
      {(entry.is_owner || entry.is_mine) && <span className="badge badge-mine">내 것</span>}
      {top3 && (
        <span
          className="badge badge-streak"
          title={entry.defending
            ? `${entry.first_created_kst} 등록 이후 초기화 없이 상위권을 지키는 중 · 수익률도 그때부터 이어져요`
            : "자정 초기화 뒤에도 수익률을 그대로 들고 다음 날 방어전을 치러요"}
        >
          {entry.defending ? `방어전 · ${entry.streak_days}일째` : "방어전"}
        </span>
      )}
      {entry.macro?.leverage > 1 && (
        <span className="badge badge-risk" title="고위험 레버리지 전략">고위험 · {entry.macro.leverage}배</span>
      )}
      {entry.crown && <span className="badge badge-flat" title="판매·좋아요 상위">인기 셀러</span>}
    </>
  );
}

function ResetCountdown({ resetAt }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <span className="num">{fmtCountdown(Math.max(0, Math.ceil((resetAt - now) / 1000)))}</span>;
}

export default function Leaderboard() {
  const { accountVersion } = useAuth();
  return <AccountLeaderboard key={accountVersion} />;
}

function AccountLeaderboard() {
  const isCurrentAccount = useAccountGuard();
  const uid = getUserId();
  const navigate = useNavigate();
  const location = useLocation();
  const quickRunMode = new URLSearchParams(location.search).get("from") === "quick-run";
  const registeredId = Number(location.state?.registeredId) || null;
  const justRegistered = !!location.state?.justRegistered;
  const auth = useAuth();
  const [items, setItems] = useState([]);
  const [unlocking, setUnlocking] = useState(0); // entry id being unlocked
  const [deleting, setDeleting] = useState(0); // entry id being deleted
  const [resetAt, setResetAt] = useState(Date.now());
  const [page, setPage] = useState(1);
  const [board, setBoard] = useState({ total: 0, has_more: false, preparing: false, stale: false });
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false); // false | {edit?: entry}
  const focusRequestRef = useRef(null);
  const [pendingFocus, setPendingFocus] = useState(null);

  const load = useCallback(async (signal) => {
    if (!isCurrentAccount()) return;
    const focusRequest = focusRequestRef.current;
    try {
      const d = await api.leaderboard(uid, {
        signal, page, pageSize: 50, entryId: focusRequest?.entryId,
      });
      if (!isCurrentAccount() || signal?.aborted || focusRequest !== focusRequestRef.current) return;
      if (d.snapshot_expired) {
        setPage(1);
        return;
      }
      // The server returns the effective page after a daily reset or entry lookup.
      setPage(d.page || 1);
      setBoard(d);
      setItems(d.items || []);
      setResetAt(Date.now() + (d.seconds_to_reset || 0) * 1000);
      setError("");
      if (focusRequest && d.entry_location) {
        focusRequestRef.current = null;
        setPendingFocus(focusRequest.entryId);
      } else if (focusRequest && Date.now() >= focusRequest.retryUntil) {
        focusRequestRef.current = null;
        setError("현재 리더보드에서 이 매크로를 찾을 수 없어요. 잠시 후 다시 확인해 주세요.");
      }
    } catch (e) {
      if (isCurrentAccount() && e?.name !== "AbortError") setError(String(e.message || e));
      if (signal) throw e;
    } finally {
      if (isCurrentAccount() && !signal?.aborted) setBusy(false);
    }
  }, [uid, page, auth.token, isCurrentAccount]);

  useEffect(() => {
    setItems([]);
    setBusy(true);
    setPage(1);
  }, [auth.token]);

  // 노출 비콘 — 목록이 그려진 뒤, 이번 세션에서 아직 안 보낸 id 만. 화면을 기다리게 하지 않는다.
  useEffect(() => {
    if (!items.length) return;
    sendImpressions(items.map((e) => e.id));
  }, [items]);

  // Only the visible bounded page is refreshed. The countdown owns its timer.
  const refreshBoard = useAdaptivePolling(load, { intervalMs: 5_000, maxIntervalMs: 60_000, pollKey: `${auth.token}:${page}` });

  // 보유 중 행의 현재가 — 들고 있는 종목만 3초마다 일괄 조회(없으면 폴링 자체를 안 돈다).
  // 실패는 폴러에 던져 백오프(최대 30초)를 타게 하고, 그때까지는 마지막 값·서버 수익률로 그린다.
  const [prices, setPrices] = useState({});
  const symbols = useMemo(() => symbolsOf(items), [items]);
  const loadPrices = useCallback(async (signal) => {
    if (!symbols.length) return;
    try {
      const d = await api.prices(symbols, { signal });
      setPrices((p) => ({ ...p, ...(d?.prices || {}) }));
    } catch (e) {
      if (e?.name !== "AbortError") throw e;
    }
  }, [symbols]);
  useAdaptivePolling(loadPrices, {
    intervalMs: 3_000,
    maxIntervalMs: 30_000,
    enabled: symbols.length > 0,
    pollKey: `prices:${symbols.join(",")}`,
  });
  // 쿨다운 "n분" 표기용 — 30초면 충분하다.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    if (!registeredId) return;
    // A newly registered entry can need one background refresh before it has a rank.
    focusRequestRef.current = { entryId: registeredId, retryUntil: Date.now() + 90_000 };
    refreshBoard();
  }, [registeredId, refreshBoard]);
  useEffect(() => {
    const focusEntry = (event) => {
      const entryId = Number(event.detail?.entryId);
      if (!Number.isSafeInteger(entryId) || entryId < 1) return;
      focusRequestRef.current = { entryId, retryUntil: 0 };
      setBusy(true);
      refreshBoard();
    };
    window.addEventListener("ggp:leaderboard-focus-entry", focusEntry);
    return () => window.removeEventListener("ggp:leaderboard-focus-entry", focusEntry);
  }, [refreshBoard]);
  useEffect(() => {
    if (!pendingFocus || !items.some((entry) => entry.id === pendingFocus)) return;
    const row = document.getElementById(`leaderboard-entry-${pendingFocus}`);
    if (!row) return;
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    row.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
    row.focus({ preventScroll: true });
    row.classList.add("is-flash");
    window.setTimeout(() => row.classList.remove("is-flash"), 1600);
    setPendingFocus(null);
  }, [items, pendingFocus]);

  // 누르는 즉시 화면에 반영하고(낙관적 갱신), 서버 응답의 수치로 확정한다. 목록 전체를
  // 다시 받지 않는다 — 그 재조회(0.8초)가 체감 지연의 대부분이었다. 실패하면 되돌린다.
  async function vote(id, value) {
    if (!isCurrentAccount()) return;
    let snapshot = null;
    setItems((current) => {
      snapshot = current;
      return current.map((entry) => (entry.id === id ? applyVote(entry, value) : entry));
    });
    try {
      const result = await api.leaderboardVote(id, uid, value);
      if (!isCurrentAccount()) return;
      setItems((current) => settleVote(current, result));
    } catch (e) {
      if (!isCurrentAccount()) return;
      if (snapshot) setItems(snapshot);
      setError(String(e.message || e));
    }
  }

  function copyToBuilder(entry) {
    // Reuse the clone/prefill path: pass the full macro to the builder via state.
    navigate("/builder", { state: { macro: entry.macro } });
  }

  async function remove(entry) {
    if (deleting || !isCurrentAccount()) return;
    if (!window.confirm("이 매크로를 리더보드에서 삭제할까요? 되돌릴 수 없어요.")) return;
    setError("");
    setDeleting(entry.id);
    try {
      await api.leaderboardDelete(entry.id);
      if (!isCurrentAccount()) return;
      await load();
    } catch (e) {
      if (!isCurrentAccount()) return;
      setError(String(e.message || e));
    } finally {
      if (isCurrentAccount()) setDeleting(0);
    }
  }

  async function unlock(entry) {
    if (!isCurrentAccount()) return;
    if (!isLoggedIn()) {
      const next = quickRunMode ? "%2Fleaderboard%3Ffrom%3Dquick-run" : "%2Fleaderboard";
      navigate(`/login?mode=signup&next=${next}`);
      return;
    }
    setError("");
    setUnlocking(entry.id);
    try {
      const d = await api.leaderboardUnlock(entry.id);
      if (!isCurrentAccount()) return;
      if (d.points_balance != null) {
        updateAuthUser({ ...getAuthUser(), points_balance: d.points_balance });
      }
      if (quickRunMode && d.user_macro?.id) {
        navigate("/?run=1&step=1", { state: { selectedMacroId: d.user_macro.id } });
        return;
      }
      await load(); // reveal the now-unlocked macro
    } catch (e) {
      if (!isCurrentAccount()) return;
      setError(String(e.message || e));
    } finally {
      if (isCurrentAccount()) setUnlocking(0);
    }
  }

  async function useForQuickRun(entry) {
    if (!isCurrentAccount()) return;
    if (!isLoggedIn()) {
      navigate("/login?next=%2Fleaderboard%3Ffrom%3Dquick-run");
      return;
    }
    setError("");
    setUnlocking(entry.id);
    try {
      if (!entry.for_sale) {
        const saved = await api.saveMyMacro(entry.macro, `리더보드 · ${entry.symbol}`);
        if (!isCurrentAccount()) return;
        navigate("/?run=1&step=1", { state: { selectedMacroId: saved.item.id } });
        return;
      }
      navigate("/?run=1&step=1", { state: { selectedSourceRef: entry.id } });
    } catch (e) {
      if (!isCurrentAccount()) return;
      setError(String(e.message || e));
      setUnlocking(0);
    }
  }

  return (
    <div className="leaderboard-page">
      <PageHeader
        title="오늘의 리더보드"
        meta={<><span className="lb-reset-prefix">리더보드 </span>초기화 <ResetCountdown resetAt={resetAt} /></>}
        actions={(
          <>
            <SimBadge className="lg:hidden" />
            <button onClick={() => navigate("/builder?guide=1")} className="btn btn-m btn-primary">
              매크로 만들기
            </button>
          </>
        )}
      />

      {quickRunMode ? (
        <div className="leaderboard-quick-run-callout" role="status">
          <div>
            <span className="num">QUICK RUN / 01</span>
            <strong>빠른 실행에 연결할 매크로를 골라요.</strong>
            <p>내 것 또는 이미 언락한 전략은 바로 선택할 수 있어요.</p>
          </div>
          <button type="button" onClick={() => navigate("/?run=1&step=1")} className="btn btn-m btn-secondary">매크로 선택으로 돌아가기</button>
        </div>
      ) : null}

      {justRegistered ? (
        <div className="notice-good mb-5 t-small text-slate-700" role="status">
          등록을 완료했어요. 같은 설정으로 모의 수익률 집계를 시작했어요.
        </div>
      ) : null}

      {busy && <Loading />}
      {error && <ErrorNote>오류: {error}</ErrorNote>}
      {!busy && board.preparing && (
        <p className="notice-good mb-5" role="status">
          {board.stale ? "오늘의 순위를 준비하고 있어요. 마지막으로 완료된 순위를 보여드려요." : "오늘의 순위를 준비하고 있어요. 잠시 후 자동으로 표시돼요."}
        </p>
      )}
      {!busy && !board.preparing && items.length === 0 && (
        <EmptyState title="아직 등록된 매크로가 없어요">
          위 <b className="text-slate-900">매크로 만들기</b>에서 조건을 정하고 결과를 확인한 뒤 등록할 수 있어요.
        </EmptyState>
      )}

      {/* board — 전체 폭을 쓰는 괘선 표(§1-3 카드 없음, §5 폭 제한 없음).
          순위 | 로고 | 매크로(이름·배지·등록) | 전략 | 수익률 | 반응. 넓은 화면에선 전략이
          자기 열을 갖고, 좁아지면 이름 아래로 내려온다. 1·2·3위는 금·은·동 + '방어전' 배지. */}
      {!busy && items.length > 0 ? (
        <div className="lb-board" role="table" aria-label="오늘의 리더보드">
          <div className="lb-row lb-row-head" role="row">
            <span role="columnheader" className="lb-col-rank">순위</span>
            <span aria-hidden="true" className="lb-col-coin" />
            <span role="columnheader" className="lb-col-name">매크로</span>
            <span role="columnheader" className="lb-col-summary">전략</span>
            <span role="columnheader" className="lb-col-return">
              수익률
              <button type="button" className="lb-state-help" aria-label="수익률 아래 상태 설명" aria-describedby="lb-state-legend">i</button>
              <dl id="lb-state-legend" className="lb-state-legend" role="tooltip">
                {STATE_LEGEND.map(([name, desc]) => <Fragment key={name}><dt>{name}</dt><dd>{desc}</dd></Fragment>)}
              </dl>
            </span>
            <span role="columnheader" className="lb-col-actions">반응</span>
          </div>
          {items.map((e, idx) => {
            const r = ret(e, prices, nowMs);
            const line = stateLine(e, nowMs);
            const rank = e.rank || ((page - 1) * 50 + idx + 1);
            const top3 = rank <= 3;
            return (
              <div
                key={e.id}
                id={`leaderboard-entry-${e.id}`}
                tabIndex={-1}
                className={`lb-row${registeredId === e.id ? " is-registered" : ""}`}
                role="row"
              >
                <div className={`lb-rank num is-${rank}`} role="cell" aria-label={`${rank}위`}>{rank}</div>
                <CoinIcon symbol={e.symbol} size={36} className="lb-coin" alt="" />
                <div className="lb-name" role="cell">
                  <div className="lb-name-line">
                    <span className="lb-mobile-symbol num">
                      <strong>{e.symbol.replace(/USDT$/, "")}</strong>
                      {e.symbol.endsWith("USDT") ? <small>USDT</small> : null}
                    </span>
                    <span className="lb-title">{e.username || e.nickname}</span>
                    <EntryBadges entry={e} top3={top3} />
                  </div>
                  <div className="lb-meta t-caption text-slate-500">
                    {registrationLabel(e)}
                  </div>
                </div>
                <div className="lb-entry-details" role="presentation">
                  <div className={`lb-summary${e.locked ? " is-locked" : ""}`} role="cell">
                    <StrategyDetails entry={e} />
                  </div>
                  <div className="lb-mobile-meta" role="cell">
                    <span className="sr-only">작성자: </span>
                    <span className="lb-mobile-author-name">{e.username || e.nickname}</span>
                    <span className="lb-mobile-time"><span className="sr-only">등록: </span>{e.defending ? e.first_created_kst : e.created_kst}</span>
                    <span className="lb-mobile-badges"><EntryBadges entry={e} top3={top3} /></span>
                  </div>
                </div>
                <div
                  className={"lb-return num " + r.cls}
                  role="cell"
                  aria-label={`수익률 ${r.text}${r.live ? " (실시간)" : ""}${line ? ` · ${line.text}` : ""}`}
                >
                  <span className="lb-return-value">
                    {r.text}
                    {r.live ? <i className="lb-live-dot" aria-hidden="true" title={LIVE_TITLE} /> : null}
                  </span>
                  {line ? <span className={`lb-state is-${line.tone}`} title={stateHelp(e, nowMs)}>{line.text}</span> : null}
                </div>
                <div className="lb-actions" role="cell">
                  <div className="lb-reactions" role="group" aria-label="매크로 반응">
                  <button
                    onClick={() => vote(e.id, 1)}
                    className={"lb-vote" + (e.my_vote === 1 ? " is-on" : "")}
                    title="좋아요"
                    aria-label={`좋아요 ${e.likes}`}
                    aria-pressed={e.my_vote === 1}
                  >
                    <ThumbUpIcon /><span className="num">{e.likes}</span>
                  </button>
                  <button
                    onClick={() => vote(e.id, -1)}
                    className={"lb-vote" + (e.my_vote === -1 ? " is-on" : "")}
                    title="싫어요"
                    aria-label={`싫어요 ${e.dislikes}`}
                    aria-pressed={e.my_vote === -1}
                  >
                    <ThumbDownIcon /><span className="num">{e.dislikes}</span>
                  </button>
                  </div>
                  <div className="lb-command-actions" role="group" aria-label="매크로 이용">
                  {e.locked ? (
                    <button
                      onClick={() => { sendOpen(e.id); unlock(e); }}
                      disabled={unlocking === e.id}
                      className="lb-vote lb-unlock"
                      title={quickRunMode ? "포인트를 써서 언락하고 빠른 실행에 연결 (창작자에게 70% 적립)" : "포인트를 써서 매크로 공개+복사 (창작자에게 70% 적립)"}
                      aria-label={`언락 ${e.unlock_price}P${quickRunMode ? " 후 사용" : ""}`}
                    >
                      <LockIcon />
                      <span className="num">{unlocking === e.id ? "여는 중…" : `${e.unlock_price}P`}</span>
                    </button>
                  ) : quickRunMode ? (
                    <button
                      onClick={() => { sendOpen(e.id); useForQuickRun(e); }}
                      disabled={unlocking === e.id}
                      className="btn btn-s btn-secondary"
                      title="이 매크로를 빠른 실행에 연결"
                    >
                      {unlocking === e.id ? "저장 중…" : "이 매크로 사용"}
                    </button>
                  ) : (
                    <button
                      onClick={() => { sendOpen(e.id); copyToBuilder(e); }}
                      disabled={unlocking === e.id}
                      className="lb-icon-btn"
                      title="빌더로 복사"
                      aria-label="빌더로 복사"
                    >
                      <CopyIcon />
                    </button>
                  )}
                  {(e.is_owner || (e.is_mine && !e.for_sale)) && (
                    <button
                      onClick={() => setModal({ edit: e })}
                      className="btn btn-s btn-secondary"
                      title={e.is_owner ? "내 매크로 수정" : "비밀번호 확인 후 수정"}
                    >
                      수정
                    </button>
                  )}
                  {e.is_owner && (
                    <button
                      onClick={() => remove(e)}
                      disabled={deleting === e.id}
                      className="btn btn-s btn-secondary text-red-600 hover:text-red-700"
                      title="내 매크로 삭제"
                    >
                      {deleting === e.id ? "삭제 중…" : "삭제"}
                    </button>
                  )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {!busy && board.total > 50 && (
        <nav className="flex items-center justify-center gap-4 my-5" aria-label="리더보드 페이지">
          <button className="btn btn-s btn-secondary" disabled={page === 1} onClick={() => { setBusy(true); setPage((value) => value - 1); }}>이전</button>
          <span>{page} / {Math.ceil(board.total / 50)}</span>
          <button className="btn btn-s btn-secondary" disabled={!board.has_more} onClick={() => { setBusy(true); setPage((value) => value + 1); }}>다음</button>
        </nav>
      )}

      {modal && (
        <RegisterMacroModal
          key={modal.edit ? `edit-${modal.edit.id}` : "new"}
          open={true}
          editEntry={modal.edit || null}
          onClose={() => setModal(false)}
          onDone={() => load()}
        />
      )}

      <ChatBox />
    </div>
  );
}
