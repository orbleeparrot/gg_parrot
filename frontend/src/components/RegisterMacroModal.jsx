import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import Builder from "./Builder.jsx";
import { api } from "../api.js";
import { RULE_TYPES, PERIOD_PRESETS, buildMacro, defaultForm, macroToForm, validate } from "../lib/macro.js";
import { getUserId } from "../lib/user.js";
import { clearAuth, useAuth } from "../lib/auth.js";
import { lockBodyScroll } from "../lib/bodyScrollLock.js";
import { saveRegistrationDraft } from "../lib/journey.js";

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function MacroReview({ macro }) {
  const strategy = RULE_TYPES[macro.rule_type]?.label || macro.rule_type;
  const period = macro.period?.preset === "custom" ? (
    <span className="num">{macro.period.start || "?"} ~ {macro.period.end || "?"}</span>
  ) : (
    PERIOD_PRESETS.find((item) => item.value === macro.period?.preset)?.label || macro.period?.preset
  );
  const rows = [
    ["종목", <span className="num">{macro.symbols?.length ? macro.symbols.join(", ") : macro.symbol}</span>],
    ["매매 방식", strategy],
    ["포지션", <>{macro.position_side === "short" ? "숏" : "롱"} · <span className="num">{macro.leverage || 1}배</span></>],
    ["테스트 기간", period],
    ["봉 간격", <span className="num">{macro.candle_interval}</span>],
    ["손절 기준", macro.risk?.stop_loss_pct == null ? "사용 안 함" : <span className="num">{macro.risk.stop_loss_pct}%</span>],
  ];

  return (
    <div className="border-t border-slate-200">
      {rows.map(([label, value]) => (
        <div key={label} className="table-row">
          <span className="row-label">{label}</span>
          <span className="t-label font-bold text-slate-900 text-right">{value}</span>
        </div>
      ))}
    </div>
  );
}

// New Studio registrations can use reviewOnly: the tested snapshot is shown as
// immutable facts instead of opening a second, divergent Builder. Existing
// entry edits retain the complete Builder and every A~K field.
export default function RegisterMacroModal({
  open,
  onClose,
  onDone,
  editEntry = null,
  initialMacro = null,
  reviewOnly = false,
  loginReturnPath = "",
  initialMode = "live",
  draftOrigin = "builder",
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const { token, user } = useAuth();
  const isEdit = !!editEntry;
  const [form, setForm] = useState(() => {
    if (isEdit && editEntry.macro) return macroToForm(editEntry.macro);
    if (initialMacro) return macroToForm(initialMacro);
    return defaultForm();
  });
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState(() => editEntry?.mode || initialMode || "live");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);
  const previousFocusRef = useRef(null);
  const submittingRef = useRef(false);
  const onCloseRef = useRef(onClose);
  const busyRef = useRef(busy);
  const titleId = useId();

  onCloseRef.current = onClose;
  busyRef.current = busy;

  const authenticated = !!token && !!user;
  const accountOwnedEdit = isEdit && !!editEntry.is_owner;
  const needsLogin = (!isEdit && !authenticated) || (accountOwnedEdit && !authenticated);
  const accountEdit = accountOwnedEdit && authenticated;
  const needsPassword = isEdit && !accountOwnedEdit;
  const asAccount = !isEdit && authenticated;
  const valErr = validate(form);
  const macro = useMemo(() => buildMacro(form), [form]);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement;
    const previousFocus = previousFocusRef.current;
    const parentDialog = previousFocus?.closest?.('[role="dialog"]');
    const background = parentDialog || document.getElementById("root");
    const previousInert = background?.inert ?? false;
    const previousAriaHidden = background?.getAttribute("aria-hidden") ?? null;
    if (background) {
      background.inert = true;
      background.setAttribute("aria-hidden", "true");
    }
    const unlockBodyScroll = lockBodyScroll();
    window.setTimeout(() => closeButtonRef.current?.focus(), 0);

    const onKeyDown = (event) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll(FOCUSABLE) || []);
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!dialogRef.current?.contains(active) || active === dialogRef.current) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (background) {
        background.inert = previousInert;
        if (previousAriaHidden === null) background.removeAttribute("aria-hidden");
        else background.setAttribute("aria-hidden", previousAriaHidden);
      }
      unlockBodyScroll();
      window.requestAnimationFrame(() => previousFocus?.focus?.({ preventScroll: true }));
    };
  }, [open]);

  if (!open) return null;

  async function save() {
    if (submittingRef.current) return;
    setError("");
    if (needsLogin) return setError(isEdit ? "로그인 후 수정할 수 있어요." : "로그인 후 등록할 수 있어요.");
    if (needsPassword && !password) return setError("비밀번호를 입력하세요.");
    if (valErr) return setError(valErr);
    submittingRef.current = true;
    setBusy(true);
    try {
      if (isEdit) {
        const data = await api.leaderboardEdit(editEntry.id, macro, accountEdit ? "" : password, mode);
        await onDone?.(data.entry);
      } else {
        const data = await api.leaderboardRegister(macro, user.username, "", getUserId(), mode);
        await onDone?.(data.entry);
      }
      onClose();
    } catch (reason) {
      if (reason.status === 401 && !isEdit && initialMacro) {
        saveRegistrationDraft(initialMacro, {
          origin: draftOrigin,
          mode,
          returnTo: loginReturnPath,
        });
        clearAuth();
        const next = loginReturnPath || "/builder?guide=1&register=1";
        navigate(`/login?next=${encodeURIComponent(next)}`, { replace: true });
        return;
      }
      setError(String(reason.message || reason));
    } finally {
      submittingRef.current = false;
      setBusy(false);
    }
  }

  function continueToLogin() {
    if (initialMacro) {
      saveRegistrationDraft(initialMacro, {
        origin: draftOrigin,
        mode,
        returnTo: loginReturnPath,
      });
    }
    const next = initialMacro
      ? loginReturnPath || "/builder?guide=1&register=1"
      : location.pathname + location.search;
    const modeQuery = isEdit ? "" : "mode=signup&";
    navigate(`/login?${modeQuery}next=${encodeURIComponent(next)}`);
  }

  const modeSelect = (
    <label className="block">
      <span className="t-small font-semibold text-slate-700 mb-2 block">모의매매 방식</span>
      <select className="field" value={mode} onChange={(event) => setMode(event.target.value)}>
        <option value="live">지금 시세로 집계</option>
        <option value="replay">최근 시세 빠르게 재생</option>
      </select>
    </label>
  );

  const title = isEdit ? "매크로 수정" : reviewOnly ? "이 설정으로 등록" : "리더보드에 등록";

  return createPortal(
    <div className="scrim fixed inset-x-0 bottom-0 top-16 z-[80] flex items-start justify-center p-2 sm:p-4 overflow-y-auto">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="dialog w-full max-w-2xl my-4 sm:my-8"
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 sticky top-0 bg-surface rounded-t-[20px] z-10">
          <h2 id={titleId} className="t-h4 text-slate-900">{title}</h2>
          <button ref={closeButtonRef} onClick={onClose} disabled={busy}
            className="btn btn-s btn-ghost text-xl leading-none" aria-label="닫기">×</button>
        </div>

        <div className="px-6 py-6 space-y-5">
          {needsLogin ? (
            <div className="py-5">
              <div className="t-title text-slate-900">{isEdit ? "수정하려면 다시 로그인해요" : "등록할 때 계정이 필요해요"}</div>
              <p className="mt-2 t-small text-slate-700 measure">
                {isEdit
                  ? "계정 소유 매크로는 로그인 상태에서만 수정할 수 있어요. 로그인한 뒤 리더보드에서 다시 열어 주세요."
                  : "테스트한 설정은 이 브라우저에 잠시 보관해요. 가입하거나 로그인한 뒤 이 등록 화면으로 돌아와요."}
              </p>
              <button onClick={continueToLogin} className="mt-5 btn btn-l btn-primary">
                {isEdit ? "로그인하기" : "로그인하거나 가입하기"}
              </button>
            </div>
          ) : (
            <>
              {accountEdit || asAccount ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 items-end">
                  <div className="notice t-small text-slate-700">
                    <b className="text-slate-900">{user.username}</b> 계정으로 {accountEdit ? "수정해요." : "등록해요. 남이 언락하면 포인트 70%가 적립돼요."}
                  </div>
                  {modeSelect}
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <label className="block">
                      <span className="t-small font-semibold text-slate-700 mb-2 block">아이디</span>
                      <input className="field opacity-40" value={editEntry.username || ""} disabled />
                    </label>
                    <label className="block">
                      <span className="t-small font-semibold text-slate-700 mb-2 block">비밀번호</span>
                      <input className="field" type="password" value={password}
                        placeholder="수정 비밀번호" onChange={(event) => setPassword(event.target.value)} />
                    </label>
                    {modeSelect}
                  </div>
                  <p className="t-caption text-amber-700">이 엔트리는 예전 비밀번호 방식으로 등록됐어요.</p>
                </>
              )}

              {reviewOnly ? <MacroReview macro={macro} /> : <Builder form={form} setForm={setForm} />}

              {valErr ? <div className="t-small text-amber-700" role="alert">{valErr}</div> : null}
              <p className="t-caption text-slate-500">
                등록하면 이 설정으로 모의매매가 시작되고 오늘 자정까지 수익률이 집계돼요. 실제 주문은 보내지 않아요.
              </p>
            </>
          )}

          {error ? <div className="t-small text-red-600" role="alert">오류: {error}</div> : null}
        </div>

        <div className="flex flex-col gap-2 px-6 py-4 border-t border-slate-200 sticky bottom-0 bg-surface rounded-b-[20px]">
          {!needsLogin ? (
            <button onClick={save} disabled={busy || !!valErr} className="btn btn-l btn-primary w-full">
              {busy ? "처리 중…" : isEdit ? "수정 저장" : "이 설정으로 등록"}
            </button>
          ) : null}
          <button onClick={onClose} disabled={busy} className="btn btn-l btn-secondary w-full">
            {needsLogin ? "나중에 등록" : isEdit ? "수정 취소" : "설정으로 돌아가기"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
