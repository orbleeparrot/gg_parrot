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

export default function Leaderboard() {
  const uid = getUserId();
  const navigate = useNavigate();
  const location = useLocation();
  const quickRunMode = new URLSearchParams(location.search).get("from") === "quick-run";
  const registeredId = location.state?.registeredId || null;
  const justRegistered = !!location.state?.justRegistered;
  useAuth(); // re-render on login/logout so gating reflects the current account
  const [items, setItems] = useState([]);
  const [challenge, setChallenge] = useState(null); // 오늘의 AI 챌린지
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
  // Ensure + fetch today's AI challenge once (first call of the day generates it).
  useEffect(() => {
    api.challengeToday().then(setChallenge).catch(() => {});
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

  async function vote(id, value) {
    try {
      await api.leaderboardVote(id, uid, value);
      load();
    } catch (_) {}
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
    <div>
      <PageHeader
        eyebrow="매일 KST 00:00 초기화 · 상위 3등은 방어전"
        title="오늘의 리더보드"
        actions={<SimBadge className="lg:hidden" />}
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

      {/* 한 줄 도구막대: 오늘의 AI 챌린지 · (오른쪽) 매크로 만들기 · 초기화 카운트다운 */}
      <div className="lb-toolbar">
        <div className="lb-toolbar-left t-small text-slate-700">
          {challenge?.active && challenge.symbol ? (
            <><b className="text-slate-900">오늘의 AI 챌린지</b> · <b className="text-slate-900 num">{challenge.symbol.replace(/USDT$/, "")}</b></>
          ) : null}
        </div>
        <div className="lb-toolbar-right">
          <button onClick={() => navigate("/builder?guide=1")} className="btn btn-m btn-primary">
            매크로 만들기
          </button>
          <span className="lb-countdown t-small text-slate-500">
            리더보드 초기화 <span className="num text-slate-900">{fmtCountdown(remain)}</span>
          </span>
        </div>
      </div>

      {busy && <Loading />}
      {error && <ErrorNote>오류: {error}</ErrorNote>}
      {!busy && items.length === 0 && (
        <EmptyState title="아직 등록된 매크로가 없어요">
          위 <b className="text-slate-900">매크로 만들기</b>에서 조건을 정하고 결과를 확인한 뒤 등록할 수 있어요.
        </EmptyState>
      )}

      {/* board-row: 카드 대신 캔버스 위 괘선 리스트. 순위+종목 로고+이름/설명 스택.
          보드 폭은 1120px 로 묶어 이름→수익률→액션의 시선 이동을 짧게 하고, 넓은 화면에선
          액션 무리가 우하단 채팅 버튼과 겹치지 않게 한다. 1·2·3위 숫자는 금·은·동. */}
      <div className="lb-board">
        {items.map((e, idx) => {
          const r = ret(e);
          const first = idx === 0;
          // 아바타 자리를 종목 로고로 바꾼다 — 목록을 훑을 때 '누가 올렸나'보다
          // '무슨 코인인가'가 먼저 눈에 들어와야 고르기 쉽다.
          return (
            <div
              key={e.id}
              id={`leaderboard-entry-${e.id}`}
              tabIndex={registeredId === e.id ? -1 : undefined}
              className={
                "py-4 border-b border-slate-200 last:border-0 flex items-center gap-3 sm:gap-4 flex-wrap " +
                (registeredId === e.id ? "border-l-2 border-l-brand pl-3" : "")
              }
            >
              <div className={`lb-rank w-7 shrink-0 text-center t-h4 num is-${idx + 1}`}>{idx + 1}</div>
              <CoinIcon symbol={e.symbol} size={36} className={"shrink-0" + (first ? " is-first" : "")} alt="" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-x-2 gap-y-1 flex-wrap">
                  {e.crown && <span className="badge badge-flat" title="판매·좋아요 상위">인기 셀러</span>}
                  {/* AI bots carry their own numbered name (껄무새1호기봇 …) — use
                      the stored username rather than a hardcoded label. */}
                  <span className="t-title text-slate-900 truncate">{e.username || e.nickname}</span>
                  {e.is_ai && <span className="badge badge-ai">AI</span>}
                  {(e.is_owner || e.is_mine) && <span className="badge badge-mine">내 것</span>}
                  {e.macro?.leverage > 1 && (
                    <span className="badge badge-risk" title="고위험 레버리지 전략">
                      고위험 · {e.macro.leverage}배
                    </span>
                  )}
                  {/* 초기화를 넘기고 살아남은 매크로 — 며칠째 버티는지가 곧 실력이다. */}
                  {e.defending && (
                    <span className="badge badge-streak" title={`${e.first_created_kst} 등록 이후 초기화 없이 상위권을 지키는 중 · 수익률도 그때부터 이어져요`}>
                      {e.streak_days}일째 순위권 방어중
                    </span>
                  )}
                  <span className="t-caption text-slate-500">
                    {e.defending ? `· ${e.first_created_kst} 등록` : `· 오늘 ${e.created_kst} 등록`}
                  </span>
                </div>
                {e.locked ? (
                  <div className="mt-1 t-small text-slate-500 truncate">잠김 · 언락하면 전략과 설정이 공개돼요</div>
                ) : (
                  <div className="mt-1 t-small text-slate-700 truncate">{e.human_summary}</div>
                )}
              </div>

              <div className={"w-24 shrink-0 text-right t-h4 num " + r.cls}>{r.text}</div>

              {/* Five action buttons never fit beside the summary on a phone —
                  give them their own full-width row below it.
                  투표는 다중 선택이 아닌 토글이라 chip 규격을 쓰되, 상승/하락색으로
                  채우지 않는다(§2-1: 등락색은 글자 색으로만). */}
              <div className="lb-actions">
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
                {e.locked ? (
                  // 행마다 노란 버튼을 두면 화면에 노랑이 열 개가 된다 —
                  // 페이지의 primary 는 상단 '등록' 하나뿐이라 여기는 secondary.
                  <button
                    onClick={() => unlock(e)}
                    disabled={unlocking === e.id}
                    className="btn btn-s btn-secondary font-bold"
                    title="포인트를 써서 매크로 공개+복사 (창작자에게 70% 적립)"
                  >
                    {unlocking === e.id ? "여는 중…" : quickRunMode ? <>언락 후 사용 · <span className="num">{e.unlock_price}P</span></> : <>언락 <span className="num">{e.unlock_price}P</span></>}
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
          );
        })}
        {/* 목록의 실제 종결 요소 — 마지막 행이 화면 바닥·채팅 버튼에 붙지 않게 한다. */}
        {!busy && items.length > 0 ? (
          <div className="lb-end" aria-label="리더보드 끝">
            <span>오늘 <span className="num">{items.length}</span>개 · 자정에 초기화</span>
            <span>상위 3등은 수익률 그대로 방어전</span>
          </div>
        ) : null}
      </div>

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
