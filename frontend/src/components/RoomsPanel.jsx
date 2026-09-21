import { useState } from "react";
import { api } from "../api.js";
import { getAuthUser, updateAuthUser } from "../lib/auth.js";
import { canJoin, joinConfirmText, remainingLabel, validateCreate } from "../lib/roomFlow.js";
import { CONSENT_LABEL, CREATE_HINT, CREATE_TITLE, FEE_HINT, JOIN_FREE, LOGIN_TO_FIND, NO_ROOMS } from "../lib/roomsCopy.js";
import ConfirmDialog from "./ConfirmDialog.jsx";
import UserAvatar from "./UserAvatar.jsx";
import "./RoomsPanel.css";

// 전략방 찾기·만들기 — ChatBox 의 '방 찾기' 탭. 목록은 부모(ChatBox)가 들고 있고 바뀌면 onChanged 로 다시 받는다.
export default function RoomsPanel({ member, data, onChanged, onEnter }) {
  const [joining, setJoining] = useState(null);   // 확인 중인 방
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ title: "", capacity: 5, entryFee: 0, consent: false });
  if (!member) return <div className="rooms-panel"><p className="rooms-empty">{LOGIN_TO_FIND}</p></div>;
  if (!data) return <div className="rooms-panel"><div className="chat-skeleton" aria-hidden="true"><i /><i /><i /></div></div>;
  const me = getAuthUser();
  const consented = data.consented;

  async function join(room) {
    setBusy(true); setError("");
    try {
      const result = await api.roomJoin(room.id);
      if (result.points_balance != null) updateAuthUser({ ...getAuthUser(), points_balance: result.points_balance });
      setJoining(null);
      onEnter?.(room.id);
    } catch (reason) {
      setError(reason?.message || "들어가지 못했어요.");
      setJoining(null);
      onChanged?.();   // 정원·잔액이 바뀌었을 수 있다 — 목록을 다시 받는다
    } finally { setBusy(false); }
  }

  async function create(event) {
    event.preventDefault();
    const check = validateCreate(form);
    if (!check.ok) { setError(Object.values(check.errors)[0]); return; }
    if (!consented && !form.consent) { setError("안내에 동의해 주세요."); return; }
    setBusy(true); setError("");
    try {
      const result = await api.roomCreate({ title: form.title.trim(), capacity: Number(form.capacity), entry_fee: Number(form.entryFee), consent: form.consent || consented });
      if (result.points_balance != null) updateAuthUser({ ...getAuthUser(), points_balance: result.points_balance });
      setCreating(false);
      onEnter?.(result.room.id);
    } catch (reason) { setError(reason?.message || "만들지 못했어요."); }
    finally { setBusy(false); }
  }

  return (
    <div className="rooms-panel">
      {creating ? (
        <form className="rooms-create" onSubmit={create}>
          <h4>{CREATE_TITLE}</h4>
          <p className="rooms-hint">{CREATE_HINT(data.create_cost)}</p>
          <label>방 제목<input value={form.title} maxLength={30} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="예: 비트 4시간봉 토론" /></label>
          <label>정원 <span className="num">{form.capacity}</span>명<input type="range" min="2" max={data.max_capacity} value={form.capacity} onChange={(e) => setForm({ ...form, capacity: Number(e.target.value) })} /></label>
          <label>입장료 <span className="num">{form.entryFee}</span>P<input type="range" min="0" max={data.max_entry_fee} step="10" value={form.entryFee} onChange={(e) => setForm({ ...form, entryFee: Number(e.target.value) })} /></label>
          <p className="rooms-hint">{FEE_HINT}</p>
          {!consented ? <label className="rooms-consent"><input type="checkbox" checked={form.consent} onChange={(e) => setForm({ ...form, consent: e.target.checked })} /> {CONSENT_LABEL}</label> : null}
          {error ? <p className="chat-helper is-error" role="alert">{error}</p> : null}
          <div className="rooms-actions">
            <button type="button" className="btn btn-s btn-secondary" onClick={() => { setCreating(false); setError(""); }} disabled={busy}>취소</button>
            <button type="submit" className="btn btn-s btn-primary" disabled={busy}>{busy ? "만드는 중…" : `${data.create_cost}P로 만들기`}</button>
          </div>
        </form>
      ) : (
        <>
          <div className="rooms-head">
            <span className="rooms-count">열린 전략방 <b className="num">{data.items.length}</b></span>
            <button type="button" className="btn btn-s btn-primary rooms-new" onClick={() => { setCreating(true); setError(""); }}>+ 방 만들기</button>
          </div>
          {error ? <p className="chat-helper is-error" role="alert">{error}</p> : null}
          {data.items.length === 0 ? <p className="rooms-empty">{NO_ROOMS}</p> : (
            <ul className="rooms-list">
              {data.items.map((room) => {
                const gate = canJoin(room, me);
                return (
                  <li key={room.id} className="rooms-card">
                    <UserAvatar src={room.owner_avatar_url} name={room.owner_username} size={28} />
                    <div className="rooms-card-body">
                      <strong>{room.title}</strong>
                      <small>{room.owner_username} · 👥 <span className="num">{room.member_count}/{room.capacity}</span> · ⏳ {remainingLabel(room.expires_ms)} · {room.entry_fee ? <><span className="num">{room.entry_fee}</span>P</> : "무료"}</small>
                      {/* 못 들어가는 이유는 글자로 — 비활성 버튼은 툴팁이 안 뜬다. */}
                      {!room.is_member && !gate.ok ? <small className="rooms-gate">{gate.reason}</small> : null}
                    </div>
                    {room.is_member
                      ? <button type="button" className="btn btn-s btn-secondary" onClick={() => onEnter?.(room.id)}>들어가기</button>
                      : <button type="button" className="btn btn-s btn-primary" disabled={!gate.ok || busy} title={gate.reason || undefined} onClick={() => setJoining(room)}>{room.entry_fee ? `${room.entry_fee}P 입장` : JOIN_FREE}</button>}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      <ConfirmDialog open={Boolean(joining)} title="전략방 입장" description={joining ? joinConfirmText(joining) : ""} confirmLabel="들어가기" busy={busy} onConfirm={() => join(joining)} onCancel={() => setJoining(null)} />
    </div>
  );
}
