import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api.js";
import "./ReportDialog.css";

// 글·댓글 신고 — 사유 하나 고르고(선택) 한 줄 덧붙여 보낸다. ConfirmDialog 와 같은 dialog 표면.
export const REPORT_REASONS = [
  ["spam", "스팸·광고"],
  ["abuse", "욕설·비방·혐오"],
  ["scam", "사기·투자 권유"],
  ["privacy", "개인정보 노출"],
  ["other", "기타"],
];

export default function ReportDialog({ open, targetType, targetId, label, onClose }) {
  const titleId = useId();
  const [reason, setReason] = useState("spam");
  const [detail, setDetail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    setReason("spam"); setDetail(""); setErr(""); setDone(false);
    const onKey = (e) => { if (e.key === "Escape" && !busy) { e.preventDefault(); onClose?.(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await api.boardReport({ targetType, targetId, reason, detail });
      setDone(true);
    } catch (e2) {
      setErr(String(e2.message || e2));
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div className="scrim fixed inset-0 z-[90] grid place-items-center p-4" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose?.(); }}>
      <form role="dialog" aria-modal="true" aria-labelledby={titleId} className="dialog confirm-dialog report-dialog" onSubmit={submit}>
        <h2 id={titleId} className="t-h4 text-slate-900">{label} 신고</h2>
        {done ? (
          <>
            <p className="mt-3 t-small text-slate-700">신고를 받았어요. 운영진이 확인할게요.</p>
            <div className="confirm-dialog-actions">
              <button type="button" className="btn btn-l w-full btn-primary" onClick={onClose}>닫기</button>
            </div>
          </>
        ) : (
          <>
            <p className="mt-3 t-small text-slate-700">어떤 문제인지 골라 주세요. 같은 글은 한 번만 신고할 수 있어요.</p>
            <div className="report-reasons" role="radiogroup" aria-label="신고 사유">
              {REPORT_REASONS.map(([key, text]) => (
                <label key={key} className={`report-reason${reason === key ? " is-on" : ""}`}>
                  <input type="radio" name="reason" value={key} checked={reason === key} onChange={() => setReason(key)} />
                  <span>{text}</span>
                </label>
              ))}
            </div>
            <textarea value={detail} onChange={(e) => setDetail(e.target.value)} rows={2} maxLength={500} className="field report-detail" placeholder="덧붙일 말이 있으면 적어 주세요 (선택)" aria-label="덧붙일 말" />
            {err ? <p className="report-error" role="alert">{err}</p> : null}
            <div className="confirm-dialog-actions">
              <button type="submit" className="btn btn-l w-full btn-danger" disabled={busy}>{busy ? "보내는 중…" : "신고 보내기"}</button>
              <button type="button" className="btn btn-l w-full btn-ghost" disabled={busy} onClick={onClose}>취소</button>
            </div>
          </>
        )}
      </form>
    </div>,
    document.body,
  );
}
