import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import SimBadge from "../components/SimBadge.jsx";
import RegisterMacroModal from "../components/RegisterMacroModal.jsx";
import ChatBox from "../components/ChatBox.jsx";
import { PageHeader, EmptyState, Loading, ErrorNote } from "../components/Page.jsx";
import { api } from "../api.js";
import { getUserId } from "../lib/user.js";
import { useAuth, isLoggedIn, getAuthUser, updateAuthUser } from "../lib/auth.js";

const pad = (n) => String(n).padStart(2, "0");
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
  const loadRef = useRef(null);
  const focusedRegistrationRef = useRef(false);

  async function load() {
    try {
      const d = await api.leaderboard(uid);
      setItems(d.items || []);
      setRemain(d.seconds_to_reset || 0);
      setError("");
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }
  loadRef.current = load;

  // Poll live returns every 5s; tick the countdown every 1s locally.
  useEffect(() => {
    load();
    const poll = setInterval(() => loadRef.current(), 5000);
    return () => clearInterval(poll);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
        eyebrow="매일 KST 00:00 초기화"
        title="오늘의 리더보드"
        description="실시간 모의(페이퍼) 수익률과 좋아요로 겨루는 오늘의 보드예요. 좋아요·수익률은 참고용이고 매수 추천이 아니에요."
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

      {/* countdown + register */}
      <div className="flex items-center justify-between flex-wrap gap-3 mb-6 pb-4 border-b border-slate-200">
        <div className="t-small text-slate-700">
          리더보드 초기화까지{" "}
          <span className="t-title num text-slate-900">{fmtCountdown(remain)}</span>{" "}
          <span className="text-slate-500">남음 (매일 KST 00:00 초기화)</span>
        </div>
        <button onClick={() => navigate("/builder?guide=1")} className="btn btn-m btn-primary">
          매크로 만들기
        </button>
      </div>

      {challenge?.active && challenge.symbol && (
        <div className="notice mb-4">
          <div className="t-small text-slate-700">
            <b className="text-slate-900">오늘의 AI 챌린지</b> — AI가 <b className="text-slate-900">{challenge.symbol.replace(/USDT$/, "")}</b>로 짠 매크로 3개가 리더보드에 있어요. 나만의 매크로를 등록해 수익률을 겨뤄봐요.
          </div>
        </div>
      )}

      {busy && <Loading />}
      {error && <ErrorNote>오류: {error}</ErrorNote>}
      {!busy && items.length === 0 && (
        <EmptyState title="아직 등록된 매크로가 없어요">
          위 <b className="text-slate-900">매크로 만들기</b>에서 조건을 정하고 결과를 확인한 뒤 등록할 수 있어요.
        </EmptyState>
      )}

      {/* board-row: 카드 대신 캔버스 위 괘선 리스트. 순위+아바타+이름/설명 스택,
          1위만 아바타를 브랜드색으로 채우고 순위 숫자를 강조색으로 뒤집는다. */}
      <div>
        {items.map((e, idx) => {
          const r = ret(e);
          const first = idx === 0;
          const initial = (e.username || e.nickname || "?").charAt(0);
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
              <div className={"w-6 shrink-0 text-center t-h4 num " + (first ? "text-slate-900" : "text-slate-600")}>{idx + 1}</div>
              <div className={"w-9 h-9 shrink-0 rounded-full grid place-items-center t-label font-bold " + (first ? "bg-brand text-brand-ink" : "bg-slate-100 text-slate-700")}>{initial}</div>
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
                  <span className="t-caption text-slate-500">· 오늘 {e.created_kst} 등록</span>
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
              <div className="flex items-center gap-2 flex-wrap w-full sm:w-auto justify-end">
                <button
                  onClick={() => vote(e.id, 1)}
                  className={"chip num " + (e.my_vote === 1 ? "border-slate-300 bg-slate-100 text-slate-900" : "")}
                  title="좋아요"
                  aria-pressed={e.my_vote === 1}
                >
                  좋아요 {e.likes}
                </button>
                <button
                  onClick={() => vote(e.id, -1)}
                  className={"chip num " + (e.my_vote === -1 ? "border-slate-300 bg-slate-100 text-slate-900" : "")}
                  title="싫어요"
                  aria-pressed={e.my_vote === -1}
                >
                  싫어요 {e.dislikes}
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
                ) : (
                  <button
                    onClick={() => quickRunMode ? useForQuickRun(e) : copyToBuilder(e)}
                    disabled={unlocking === e.id}
                    className="btn btn-s btn-secondary"
                    title={quickRunMode ? "이 매크로를 빠른 실행에 연결" : "이 매크로를 빌더로 복사"}
                  >
                    {quickRunMode ? (unlocking === e.id ? "저장 중…" : "이 매크로 사용") : "빌더로 복사"}
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
