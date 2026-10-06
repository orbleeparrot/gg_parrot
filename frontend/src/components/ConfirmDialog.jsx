import { useId, useRef } from "react";
import { createPortal } from "react-dom";
import { useModalLayer } from "../hooks/useModalLayer.js";

// 브라우저 기본 confirm 대체. DESIGN §6 dialog — CTA 는 세로로 쌓고(48px) 주 동작이 위.
// 되돌릴 수 없는 동작(tone="danger")은 처음 포커스를 '취소'에 둔다 — Enter 한 번에 삭제·청산되지 않게.
export default function ConfirmDialog({
  open,
  title,
  description,
  warning = "",
  confirmLabel = "확인",
  cancelLabel = "취소",
  tone = "primary",
  busy = false,
  onConfirm,
  onCancel,
}) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef(null);
  const confirmRef = useRef(null);
  const cancelRef = useRef(null);
  const danger = tone === "danger";
  useModalLayer({ open, ref: dialogRef, onEscape: () => onCancel?.(), busy, trap: true, initialFocusRef: danger ? cancelRef : confirmRef });

  if (!open) return null;

  return createPortal(
    <div
      className="scrim fixed inset-0 z-90 grid place-items-center p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel?.();
      }}
    >
      <div
        ref={dialogRef}
        role={danger ? "alertdialog" : "dialog"}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="dialog confirm-dialog"
      >
        <h2 id={titleId} className="t-h4 text-slate-900">{title}</h2>
        <p id={descriptionId} className="mt-3 t-small text-slate-700">{description}</p>
        {warning ? <div className="alert alert-warn mt-4 t-small" role="note">{warning}</div> : null}
        <div className="confirm-dialog-actions">
          <button
            ref={confirmRef}
            type="button"
            className={`btn btn-l w-full ${danger ? "btn-danger" : "btn-primary"}`}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "처리 중…" : confirmLabel}
          </button>
          <button ref={cancelRef} type="button" className="btn btn-l w-full btn-ghost" disabled={busy} onClick={onCancel}>
            {cancelLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
