// 회원 관리 탭의 조치 — 행의 버튼 세 개(메시지 · 차단/해제 · 탈퇴)와 그 버튼들이 여는 창.
// 차단·탈퇴는 프로젝트 규칙대로 브라우저 기본 confirm 이 아니라 ConfirmDialog 를 쓰고, 메시지만 작은 폼 모달이다.
// 조치의 뜻(무엇을 못 하게 되는지)은 창 본문에도 그대로 적는다 — 목록의 용어 표를 다시 보러 가지 않게.
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import ConfirmDialog from "../ConfirmDialog.jsx";
import { MEMBER_BODY_MAX, MEMBER_LINK_MAX, MEMBER_REASON_MAX, MEMBER_TITLE_MAX, memberActions } from "../../lib/memberList.js";

const EMPTY_FORM = { title: "", body: "", link: "", reason: "" };

// 탈퇴 행·자기 계정은 버튼을 아예 숨기고, 관리자 행은 메시지만 남긴다(차단·탈퇴는 서버가 막는다).
export function MemberRowActions({ member, selfId = null, disabled = false, onAction }) {
  const allowed = memberActions(member, selfId);
  if (!allowed.message && !allowed.block && !allowed.remove) return <span className="adm-muted">—</span>;
  return (
    <span className="adm-mem-acts">
      {allowed.message ? (
        <button type="button" className="adm-act" disabled={disabled} onClick={() => onAction({ kind: "message", member })}>메시지</button>
      ) : null}
      {allowed.block ? (
        <button type="button" className="adm-act" disabled={disabled} onClick={() => onAction({ kind: "block", member })}>
          {member.is_blocked ? "차단 해제" : "차단"}
        </button>
      ) : null}
      {allowed.reset ? (
        <button type="button" className="adm-act" disabled={disabled} onClick={() => onAction({ kind: "reset", member })}>재설정 링크</button>
      ) : null}
      {allowed.remove ? (
        <button type="button" className="adm-act is-danger" disabled={disabled} onClick={() => onAction({ kind: "delete", member })}>탈퇴</button>
      ) : null}
    </span>
  );
}

// 사유 한 줄 — ConfirmDialog 의 설명 자리에 넣는다(라벨·입력은 문장 요소라 <p> 안에서도 유효하다).
function ReasonField({ value, onChange, hint }) {
  return (
    <label className="adm-dlg-reason">
      <span>사유 (선택 · 최대 {MEMBER_REASON_MAX}자)</span>
      <input
        type="text" className="field field-sm" value={value} maxLength={MEMBER_REASON_MAX}
        placeholder={hint} onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

export function MemberActionDialogs({ action, busy = false, error = "", onSubmit, onCancel }) {
  const titleId = useId();
  const [form, setForm] = useState(EMPTY_FORM);
  const kind = action?.kind || "";
  const member = action?.member || null;
  const memberId = member?.id ?? "";

  // 창을 새로 열 때마다 비운다 — 앞 회원에게 쓴 사유·메시지가 다음 회원에게 남으면 안 된다.
  useEffect(() => { setForm(EMPTY_FORM); }, [kind, memberId]);

  useEffect(() => {
    if (kind !== "message") return undefined;
    const onKey = (e) => { if (e.key === "Escape" && !busy) { e.preventDefault(); onCancel?.(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, kind, onCancel]);

  if (!member) return null;
  const name = member.username || "회원";
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  if (kind === "block") {
    const unblocking = Boolean(member.is_blocked);
    return (
      <ConfirmDialog
        open
        title={unblocking ? `${name} 님 차단을 해제할까요?` : `${name} 님을 차단할까요?`}
        description={(
          <>
            <span className="adm-dlg-text">
              {unblocking
                ? "다시 채팅·게시글·댓글을 쓸 수 있게 됩니다."
                : "채팅·게시글·댓글을 쓸 수 없게 됩니다. 로그인·열람·백테스트는 그대로이고, 언제든 되돌릴 수 있어요."}
            </span>
            <ReasonField value={form.reason} onChange={(reason) => set({ reason })} hint={unblocking ? "해제 사유" : "차단 사유"} />
          </>
        )}
        confirmLabel={unblocking ? "차단 해제" : "차단하기"}
        tone={unblocking ? "primary" : "danger"}
        busy={busy}
        onConfirm={() => onSubmit({ reason: form.reason })}
        onCancel={onCancel}
      />
    );
  }

  if (kind === "delete") {
    return (
      <ConfirmDialog
        open
        title={`${name} 님을 탈퇴 처리할까요?`}
        description={(
          <>
            <span className="adm-dlg-text">
              계정을 지우고 남긴 글·댓글은 ‘탈퇴한 회원’ 으로 익명화하며 포인트를 회수합니다.
            </span>
            <ReasonField value={form.reason} onChange={(reason) => set({ reason })} hint="탈퇴 처리 사유" />
          </>
        )}
        warning="되돌릴 수 없어요. 같은 이메일로는 다시 가입할 수 없습니다(본인 탈퇴와 달라요)."
        confirmLabel="탈퇴 처리"
        tone="danger"
        busy={busy}
        onConfirm={() => onSubmit({ reason: form.reason })}
        onCancel={onCancel}
      />
    );
  }

  if (kind === "reset") {
    return <ResetLinkDialog name={name} link={action.link} expires={action.expires_min} busy={busy} error={error} onSubmit={onSubmit} onCancel={onCancel} />;
  }

  if (kind !== "message") return null;
  const canSend = form.title.trim().length > 0 && !busy;
  return createPortal(
    <div
      className="scrim fixed inset-0 z-[90] grid place-items-center p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onCancel?.(); }}
    >
      <form
        role="dialog" aria-modal="true" aria-labelledby={titleId} className="dialog confirm-dialog adm-msg-dialog"
        onSubmit={(e) => { e.preventDefault(); if (canSend) onSubmit({ title: form.title.trim(), body: form.body, link: form.link.trim() }); }}
      >
        <h2 id={titleId} className="t-h4 text-slate-900">{name} 님에게 메시지</h2>
        <p className="mt-3 t-small text-slate-700">회원의 알림창으로 관리자 메시지를 보냅니다. 접속 중이면 실시간 알림까지 그대로 떠요.</p>
        <label className="adm-msg-field">
          <span>제목 (필수 · 최대 {MEMBER_TITLE_MAX}자)</span>
          <input
            type="text" className="field field-sm" value={form.title} maxLength={MEMBER_TITLE_MAX} required
            placeholder="예) 게시판 이용 안내" onChange={(e) => set({ title: e.target.value })}
          />
        </label>
        <label className="adm-msg-field">
          <span>내용 (최대 {MEMBER_BODY_MAX}자)</span>
          <textarea
            className="field adm-msg-body" value={form.body} maxLength={MEMBER_BODY_MAX} rows={4}
            placeholder="알림창에 그대로 보입니다." onChange={(e) => set({ body: e.target.value })}
          />
        </label>
        <label className="adm-msg-field">
          <span>링크 (선택 · 최대 {MEMBER_LINK_MAX}자)</span>
          <input
            type="text" className="field field-sm" value={form.link} maxLength={MEMBER_LINK_MAX}
            placeholder="/board/12" onChange={(e) => set({ link: e.target.value })}
          />
        </label>
        {error ? <p className="adm-msg-error" role="alert">{error}</p> : null}
        <div className="confirm-dialog-actions">
          <button type="submit" className="btn btn-l w-full btn-primary" disabled={!canSend}>{busy ? "보내는 중…" : "메시지 보내기"}</button>
          <button type="button" className="btn btn-l w-full btn-ghost" disabled={busy} onClick={onCancel}>취소</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

// 비밀번호 재설정 링크 — 비밀번호는 해시라 알려줄 수 없다. 만들기 전엔 확인 창, 만든 뒤엔 링크와 복사 버튼.
// 링크는 30분·1회성이라 관리자가 본인에게 카톡 등으로 바로 전해 주는 용도다.
function ResetLinkDialog({ name, link, expires, busy, error, onSubmit, onCancel }) {
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  useEffect(() => { setCopied(false); }, [link]);
  useEffect(() => {
    if (!link) return undefined;
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); onCancel?.(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [link, onCancel]);
  const absolute = link && !/^https?:/i.test(link) && typeof window !== "undefined" ? `${window.location.origin}${link}` : link;
  const minutes = expires || 30;

  if (!link) {
    return (
      <ConfirmDialog
        open
        title={`${name} 님의 비밀번호 재설정 링크를 만들까요?`}
        description={(
          <span className="adm-dlg-text">
            비밀번호는 서버에도 없어 알려줄 수 없어요. 대신 새 비밀번호를 정할 수 있는 링크를 만들어 드리니 본인에게 직접 전해 주세요.
            링크는 {minutes}분 동안만 유효하고 한 번 쓰면 무효가 돼요.
          </span>
        )}
        warning={error || undefined}
        confirmLabel="링크 만들기"
        busy={busy}
        onConfirm={() => onSubmit({})}
        onCancel={onCancel}
      />
    );
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(absolute);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      window.prompt("링크를 복사해 주세요.", absolute);
    }
  }

  return createPortal(
    <div className="scrim fixed inset-0 z-[90] grid place-items-center p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel?.(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="dialog confirm-dialog adm-msg-dialog">
        <h2 id={titleId} className="t-h4 text-slate-900">{name} 님 재설정 링크</h2>
        <p className="mt-3 t-small text-slate-700">본인에게 이 링크를 전해 주세요. {minutes}분 안에 열어 새 비밀번호를 정하면 돼요. 한 번 쓰면 무효가 됩니다.</p>
        <label className="adm-msg-field">
          <span>링크</span>
          <input type="text" className="field field-sm num" value={absolute} readOnly onFocus={(e) => e.target.select()} />
        </label>
        <div className="confirm-dialog-actions">
          <button type="button" className="btn btn-l w-full btn-primary" onClick={copy}>{copied ? "복사했어요" : "링크 복사"}</button>
          <button type="button" className="btn btn-l w-full btn-ghost" onClick={onCancel}>닫기</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default MemberActionDialogs;
