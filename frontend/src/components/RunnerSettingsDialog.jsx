import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useModalLayer } from "../hooks/useModalLayer.js";
import {
  badFields, buildChanges, changeSummary, fieldText, hasChanges, touchesPosition,
} from "../features/agents/settingsForm.js";

// 구동 중 매크로의 금액·조건을 바꾸는 창 (2026-10-30).
//
// **칸 목록은 서버가 준다**(session.settings.fields). 여기서 목록을 따로 들면 두 벌이 되어
// 어긋난다 — 화면에는 보이는데 서버가 거절하는 칸이 생긴다.
//
// 포지션을 들고 있으면 적용 시점을 묻는다. 바꾼 조건이 **지금 들고 있는 포지션의 청산
// 조건까지** 바꾼다는 사실을 사용자가 모르고 누르면 안 된다.
//
// 칸마다 한 줄 설명을 붙이고, 고른 쪽이 지금 포지션에 무슨 일을 하는지도 적는다. 설명이
// 없으면 "지금 적용" 을 고를 때 **팔고 다시 사는 건가** 하고 멈칫하게 된다 — 안 판다.
// 들고 있는 수량과 평단은 그대로 두고 청산 조건만 갈아 끼운다.
const APPLY_NOW = "immediate";
const APPLY_LATER = "next_entry";

export default function RunnerSettingsDialog({ open, session, busy = false, error = "", onSubmit, onClose }) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const firstRef = useRef(null);
  const [edits, setEdits] = useState({});
  const [applyMode, setApplyMode] = useState(APPLY_NOW);

  const settings = session?.settings || {};
  const fields = useMemo(() => settings.fields || [], [settings.fields]);
  const inPosition = !!session?.in_position;

  // ``edits`` 는 **사람이 손댄 칸만** 담는다. 안 담긴 칸은 그릴 때 지금 값으로 채운다.
  //
  // 열 때 모든 칸을 미리 채워 두면 그러려고 effect 가 필요한데, 그 effect 는 ``fields`` 가
  // 바뀔 때마다 돈다. 세션 목록은 계속 새로 받아 오므로 ``fields`` 는 매번 새 배열이고,
  // 그러면 **사용자가 치고 있는 중에 입력이 초기화된다.** 손댄 칸만 담으면 effect 가 없다.
  // 창이 닫힐 때 비워, 다음에 열면 그때의 현재 값으로 시작한다.
  useEffect(() => {
    if (open) return;
    setEdits({});
    setApplyMode(APPLY_NOW);
  }, [open]);

  useModalLayer({ open, ref: dialogRef, onEscape: () => onClose?.(), busy, trap: true, initialFocusRef: firstRef });

  if (!open || !session) return null;

  const bad = badFields(fields, edits);
  const summary = changeSummary(fields, edits);
  // 금액·비율만 바꿨으면 어느 쪽을 골라도 같다 — 묻기는 하되 같다는 사실을 말해 준다.
  const hitsPosition = touchesPosition(fields, edits);
  const ready = hasChanges(fields, edits) && bad.length === 0;

  const submit = (event) => {
    event.preventDefault();
    if (!ready || busy) return;
    onSubmit?.({
      changes: buildChanges(fields, edits),
      apply_mode: inPosition ? applyMode : APPLY_NOW,
      summary,
    });
  };

  return createPortal(
    <div
      className="scrim fixed inset-0 z-90 grid place-items-center p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose?.();
      }}
    >
      <form
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="dialog runner-settings-dialog"
        onSubmit={submit}
      >
        <h2 id={titleId} className="t-h4 text-slate-900">설정 바꾸기</h2>
        <p className="mt-3 t-small text-slate-700">
          {session.symbol} · 돌고 있는 그대로 금액과 조건만 바꿔요. 매매 방식·종목·방향은 바꿀 수 없어요.
        </p>
        {/* 실행기가 직접 판단하는 세션은 조건 값을 실행기 엔진이 들고 있다 — 왜 칸이 적은지 말해 준다. */}
        {settings.note ? <div className="alert alert-warn mt-4 t-small" role="note">{settings.note}</div> : null}

        <div className="runner-settings-fields">
          {fields.map((field, index) => (
            <label key={field.path} className="runner-settings-field">
              <span className="runner-settings-label">
                {field.label}
                {field.help ? <small className="runner-settings-help">{field.help}</small> : null}
              </span>
              {/* 단위를 칸 옆에 붙인다 — 숫자만 있으면 2 가 2%인지 2시간인지 2봉인지 모른다. */}
              <span className="runner-settings-input">
                <input
                  ref={index === 0 ? firstRef : null}
                  type="text"
                  inputMode="decimal"
                  className="input"
                  value={edits[field.path] ?? fieldText(field)}
                  disabled={busy}
                  aria-invalid={bad.includes(field) || undefined}
                  aria-describedby={field.unit_text ? `${titleId}-${field.path}` : undefined}
                  placeholder={field.clearable ? "비우면 끔" : ""}
                  onChange={(event) => setEdits((prev) => ({ ...prev, [field.path]: event.target.value }))}
                />
                {field.unit_text ? (
                  <span className="runner-settings-unit" id={`${titleId}-${field.path}`}>
                    {field.unit_text}
                  </span>
                ) : null}
              </span>
            </label>
          ))}
        </div>

        {bad.length ? (
          <div className="alert alert-warn mt-4 t-small" role="alert">
            {bad.map((field) => field.label).join(" · ")} — 숫자로 입력해 주세요.
          </div>
        ) : null}

        {inPosition ? (
          <fieldset className="runner-settings-apply">
            <legend className="t-small text-slate-700">지금 포지션을 들고 있어요. 언제 적용할까요?</legend>
            <p className="runner-settings-help">
              어느 쪽을 골라도 지금 들고 있는 건 팔지 않아요. 수량과 평단은 그대로예요.
              {hitsPosition ? "" : " 이번에 바꾼 값은 다음에 살 때 쓰는 것이라 두 쪽이 같아요."}
            </p>
            <label>
              <input type="radio" name="apply_mode" value={APPLY_NOW} disabled={busy}
                     checked={applyMode === APPLY_NOW}
                     onChange={() => setApplyMode(APPLY_NOW)} />
              <span>
                지금 적용: 들고 있는 포지션의 청산 조건도 바뀌어요
                <small className="runner-settings-help">
                  새 손절·익절이 지금 포지션에 바로 걸려요. 이미 그 선을 넘었으면 곧 정리될 수 있어요.
                </small>
              </span>
            </label>
            <label>
              <input type="radio" name="apply_mode" value={APPLY_LATER} disabled={busy}
                     checked={applyMode === APPLY_LATER}
                     onChange={() => setApplyMode(APPLY_LATER)} />
              <span>
                이 포지션 정리된 뒤부터: 지금 포지션은 살 때 조건 그대로 끝나요
                <small className="runner-settings-help">
                  새 설정은 예약해 두고, 지금 포지션이 정리되면 그때부터 써요.
                </small>
              </span>
            </label>
          </fieldset>
        ) : null}

        {summary ? <p className="runner-settings-summary t-small" role="status">{summary}</p> : null}
        {/* 기록이 남는다는 사실을 숨기지 않는다 — 나중에 통보하면 그게 더 나쁘다. */}
        <p className="runner-settings-note t-small text-slate-500">
          바꾼 내용은 기록으로 남아요{settings.changed_count ? ` · 지금까지 ${settings.changed_count}번 바꿨어요` : ""}.
        </p>
        {error ? <div className="alert alert-risk mt-4 t-small" role="alert">{error}</div> : null}

        <div className="confirm-dialog-actions">
          <button type="submit" className="btn btn-l w-full btn-primary" disabled={!ready || busy}>
            {busy ? "바꾸는 중…" : "이대로 바꾸기"}
          </button>
          <button type="button" className="btn btn-l w-full btn-ghost" disabled={busy} onClick={onClose}>취소</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
