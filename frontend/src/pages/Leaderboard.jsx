import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import SimBadge from "../components/SimBadge.jsx";
import RegisterMacroModal from "../components/RegisterMacroModal.jsx";
import ChatBox from "../components/ChatBox.jsx";
import { PageHeader, EmptyState, Loading, ErrorNote } from "../components/Page.jsx";
import { api } from "../api.js";
import CoinIcon from "../components/CoinIcon.jsx";
import { getUserId } from "../lib/user.js";
import { useAuth, isLoggedIn, getAuthUser, updateAuthUser } from "../lib/auth.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { applyVote, settleVote } from "../lib/leaderboardVotes.js";
import { baseOf, quoteOf } from "../lib/format.js";
import { leaderboardStrategy } from "../lib/leaderboardStrategy.js";
import "./LeaderboardMobile.css";

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

function ret(e) {
  if (e.return_pct == null) return { text: "집계중…", cls: "text-slate-500" };
  const up = e.return_pct >= 0;
  return { text: `${up ? "+" : ""}${e.return_pct.toFixed(2)}%`, cls: up ? "text-green-600" : "text-red-600" };
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

function StrategyDetails({ entry }) {
  const details = leaderboardStrategy(entry);
  return (
    <dl className="lb-strategy-facts">
      <div className="lb-fact lb-fact-ticker">
        <dt className="sr-only">티커</dt>
        <dd className="lb-strategy-ticker num"><strong>{baseOf(entry.symbol)}</strong><small>{quoteOf(entry.symbol)}</small></dd>
      </div>
      {details ? <>
        <div className="lb-fact lb-fact-position">
          <dt className="sr-only">포지션</dt>
          <dd className={`lb-position is-${details.side || "unknown"}`}>
            {{ long: "롱", short: "숏", switch: "롱 → 숏" }[details.side] || "—"}
          </dd>
        </div>
        <div className="lb-fact lb-fact-capital">
          <dt>{details.capital?.label || "자금"}</dt>
          <dd><span className="num">{details.capital?.value || "—"}</span>{details.capital ? <small>{details.capital.unit}</small> : null}</dd>
        </div>
        <div className="lb-fact lb-fact-strategy">
          <dt className="sr-only">전략</dt>
          <dd className="lb-summary-text">{details.description}</dd>
        </div>
      </> : <div className="lb-fact lb-fact-locked"><dt className="sr-only">전략</dt><dd>잠긴 전략</dd></div>}
    </dl>
  );
}

export default function Leaderboard() {
  const uid = getUserId();
  const navigate = useNavigate();
  const location = useLocation();
  const quickRunMode = new URLSearchParams(location.search).get("from") === "quick-run";
  const registeredId = location.state?.registeredId || null;
  const justRegistered = !!location.state?.justRegistered;
  useAuth(); // re-render on login/logout so gating reflects the current account
  const [items, setItems] = useState([]);
  const [unlocking, setUnlocking] = useState(0); // entry id being unlocked
  const [deleting, setDeleting] = useState(0); // entry id being deleted
  const [remain, setRemain] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false); // false | {edit?: entry}
  const focusedRegistrationRef = useRef(false);

  const load = useCallback(async (signal) => {
    try {
      const d = await api.leaderboard(uid, { signal });
      setItems(d.items || []);
      setRemain(d.seconds_to_reset || 0);
      setError("");
    } catch (e) {
      if (e?.name !== "AbortError") setError(String(e.message || e));
      if (signal) throw e;
    } finally {
      setBusy(false);
    }
  }, [uid]);

  // Poll live returns every 5s; tick the countdown every 1s locally.
  useAdaptivePolling(load, { intervalMs: 5_000, maxIntervalMs: 60_000 });
  // 오늘의 AI 챌린지(매일 한 종목으로 AI 매크로 3개 자동 등록)는 첫 조회가 생성을 겸한다.
  // 화면엔 라벨을 두지 않고 AI 배지로만 드러낸다.
  useEffect(() => {
    api.challengeToday().catch(() => {});
  }, []);
  useEffect(() => {
    const t = setInterval(() => setRemain((r) => (r > 0 ? r - 1 : 0)), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!registeredId || focusedRegistrationRef.current) return;
    if (!items.some((entry) => entry.id === registeredId)) return;
    focusedRegistrationRef.current = true;
    const row = document.getElementById(`leaderboard-entry-${registeredId}`);
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    row?.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
    row?.focus({ preventScroll: true });
  }, [items, registeredId]);

  // 누르는 즉시 화면에 반영하고(낙관적 갱신), 서버 응답의 수치로 확정한다. 목록 전체를
  // 다시 받지 않는다 — 그 재조회(0.8초)가 체감 지연의 대부분이었다. 실패하면 되돌린다.
  async function vote(id, value) {
    let snapshot = null;
    setItems((current) => {
      snapshot = current;
      return current.map((entry) => (entry.id === id ? applyVote(entry, value) : entry));
    });
    try {
      const result = await api.leaderboardVote(id, uid, value);
      setItems((current) => settleVote(current, result));
    } catch (e) {
      if (snapshot) setItems(snapshot);
      setError(String(e.message || e));
    }
  }

  function copyToBuilder(entry) {
    // Reuse the clone/prefill path: pass the full macro to the builder via state.
    navigate("/builder", { state: { macro: entry.macro } });
  }

  async function remove(entry) {
    if (deleting) return;
    if (!window.confirm("이 매크로를 리더보드에서 삭제할까요? 되돌릴 수 없어요.")) return;
    setError("");
    setDeleting(entry.id);
    try {
      await api.leaderboardDelete(entry.id);
      await load();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setDeleting(0);
    }
  }

  async function unlock(entry) {
    if (!isLoggedIn()) {
      const next = quickRunMode ? "%2Fleaderboard%3Ffrom%3Dquick-run" : "%2Fleaderboard";
      navigate(`/login?mode=signup&next=${next}`);
      return;
    }
    setError("");
    setUnlocking(entry.id);
    try {
      const d = await api.leaderboardUnlock(entry.id);
      if (d.points_balance != null) {
        updateAuthUser({ ...getAuthUser(), points_balance: d.points_balance });
      }
      if (quickRunMode && d.user_macro?.id) {
        navigate("/?run=1&step=1", { state: { selectedMacroId: d.user_macro.id } });
        return;
      }
      await load(); // reveal the now-unlocked macro
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setUnlocking(0);
    }
  }

  async function useForQuickRun(entry) {
    if (!isLoggedIn()) {
      navigate("/login?next=%2Fleaderboard%3Ffrom%3Dquick-run");
      return;
    }
    setError("");
    setUnlocking(entry.id);
    try {
      if (!entry.for_sale) {
        const saved = await api.saveMyMacro(entry.macro, `리더보드 · ${entry.symbol}`);
        navigate("/?run=1&step=1", { state: { selectedMacroId: saved.item.id } });
        return;
      }
      navigate("/?run=1&step=1", { state: { selectedSourceRef: entry.id } });
    } catch (e) {
      setError(String(e.message || e));
      setUnlocking(0);
    }
  }

  return (
    <div className="leaderboard-page">
      <PageHeader
        title="오늘의 리더보드"
        meta={<><span className="lb-reset-prefix">리더보드 </span>초기화 <span className="num">{fmtCountdown(remain)}</span></>}
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
      {!busy && items.length === 0 && (
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
            <span role="columnheader" className="lb-col-return">수익률</span>
            <span role="columnheader" className="lb-col-actions">반응</span>
          </div>
          {items.map((e, idx) => {
            const r = ret(e);
            const top3 = idx < 3;
            return (
              <div
                key={e.id}
                id={`leaderboard-entry-${e.id}`}
                tabIndex={registeredId === e.id ? -1 : undefined}
                className={`lb-row${registeredId === e.id ? " is-registered" : ""}`}
                role="row"
              >
                <div className={`lb-rank num is-${idx + 1}`} role="cell" aria-label={`${idx + 1}위`}>{idx + 1}</div>
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
                <div className={"lb-return num " + r.cls} role="cell" aria-label={`수익률 ${r.text}`}>
                  <span className="lb-return-value">{r.text}</span>
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
                      onClick={() => unlock(e)}
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
                      onClick={() => useForQuickRun(e)}
                      disabled={unlocking === e.id}
                      className="btn btn-s btn-secondary"
                      title="이 매크로를 빠른 실행에 연결"
                    >
                      {unlocking === e.id ? "저장 중…" : "이 매크로 사용"}
                    </button>
                  ) : (
                    <button
                      onClick={() => copyToBuilder(e)}
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
