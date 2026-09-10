import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { XIcon } from "@phosphor-icons/react/dist/csr/X";
import { api } from "../api.js";
import { getAuthUser, getToken } from "../lib/auth.js";
import UserAvatar from "./UserAvatar.jsx";
import GoogleSignInButton from "./GoogleSignInButton.jsx";
import "./ProfileEditor.css";

const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const USERNAME_PATTERN = /^[가-힣a-zA-Z0-9_]{2,20}$/;

function EditorDialog({ title, busy, onClose, initialFocus, children }) {
  const dialog = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    const previousFocus = document.activeElement;
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    element.showModal();
    initialFocus?.current?.focus({ preventScroll: true });
    return () => {
      element.close();
      document.documentElement.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, [initialFocus]);

  return createPortal(
    <dialog ref={dialog} className="profile-editor-dialog" aria-labelledby={titleId} onCancel={(event) => {
      event.preventDefault();
      if (!busy) onClose();
    }}>
      <div className="profile-editor-heading">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="profile-editor-close" aria-label="닫기" onClick={onClose} disabled={busy}><XIcon size={20} weight="regular" aria-hidden="true" /></button>
      </div>
      {children}
    </dialog>,
    document.body,
  );
}

// A mutation belongs to the account that started it. Leaving this dialog aborts
// its request; an old response can never update a newly signed-in account.
function useAccountSave(user, onSuccess) {
  const mounted = useRef(false);
  const controller = useRef(null);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);

  async function save(action) {
    if (pending.current) return;
    const requestToken = getToken();
    const userId = user.id;
    const current = () => mounted.current && getToken() === requestToken && getAuthUser()?.id === userId;
    pending.current = true;
    setBusy(true);
    setError("");
    controller.current = new AbortController();
    try {
      const response = await action(controller.current.signal);
      if (current()) onSuccess(response);
    } catch (reason) {
      if (current() && reason?.name !== "AbortError") setError(String(reason?.message || "저장하지 못했어요. 다시 시도해주세요."));
    } finally {
      pending.current = false;
      if (current()) setBusy(false);
    }
  }
  return { busy, error, setError, save };
}

export default function ProfileEditor({ user, onClose, onSaved }) {
  const id = useId();
  const nameInput = useRef(null);
  const fileInput = useRef(null);
  const [username, setUsername] = useState(user.username || "");
  const [bio, setBio] = useState(user.bio || "");
  const [image, setImage] = useState(null);
  const [preview, setPreview] = useState("");
  const [removeAvatar, setRemoveAvatar] = useState(false);
  const [invalidName, setInvalidName] = useState(false);
  const { busy, error, setError, save } = useAccountSave(user, (response) => {
    onSaved?.(response.user);
    onClose();
  });

  useEffect(() => {
    if (!image) {
      setPreview("");
      return undefined;
    }
    const url = URL.createObjectURL(image);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  function pickImage(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError("");
    if (!IMAGE_TYPES.has(file.type)) {
      setError("PNG, JPG, WebP 사진을 선택해주세요.");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setError("2MB 이하의 사진을 선택해주세요.");
      return;
    }
    setImage(file);
    setRemoveAvatar(false);
  }

  function submit(event) {
    event.preventDefault();
    const name = username.trim();
    if (!USERNAME_PATTERN.test(name)) {
      setInvalidName(true);
      setError("이름은 한글, 영문, 숫자, _를 사용해 2–20자로 입력해주세요.");
      nameInput.current?.focus();
      return;
    }
    setInvalidName(false);
    save((signal) => api.updateProfile({ username: name, bio, image, removeAvatar }, { signal }));
  }

  const source = removeAvatar ? null : preview || user.avatar_url;
  const dirty = username.trim() !== user.username || bio !== (user.bio || "") || !!image || (removeAvatar && !!user.avatar_url);
  return (
    <EditorDialog title="프로필 편집" busy={busy} onClose={onClose} initialFocus={nameInput}>
      <form className="profile-editor-form" onSubmit={submit}>
        <fieldset className="profile-editor-fields" disabled={busy}>
          <div className="profile-editor-photo">
            <UserAvatar src={source} name={username} size={72} />
            <div className="profile-editor-photo-options">
              <div className="profile-editor-photo-actions">
                <button type="button" className="btn btn-s btn-secondary" onClick={() => fileInput.current?.click()}>사진 변경</button>
                {source ? <button type="button" className="btn btn-s btn-ghost" onClick={() => { setImage(null); setPreview(""); setRemoveAvatar(true); setError(""); }}>삭제</button> : null}
              </div>
              <span id={`${id}-photo-hint`} className="profile-editor-hint">PNG, JPG, WebP · 최대 2MB</span>
              <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1} aria-label="프로필 사진 파일" aria-describedby={`${id}-photo-hint`} onChange={pickImage} />
            </div>
          </div>
          <label className="profile-editor-label" htmlFor={`${id}-name`}>프로필명
            <input ref={nameInput} id={`${id}-name`} aria-label="프로필명" className={`field${invalidName ? " field-err" : ""}`} value={username} onChange={(event) => { setUsername(event.target.value); setInvalidName(false); }} maxLength={20} autoComplete="nickname" aria-invalid={invalidName || undefined} aria-describedby={`${id}-name-hint${invalidName ? ` ${id}-error` : ""}`} />
            <span id={`${id}-name-hint`} className="profile-editor-hint">한글, 영문, 숫자, _ · 2–20자</span>
          </label>
          <label className="profile-editor-label" htmlFor={`${id}-bio`}>
            <span className="profile-editor-label-row"><span>소개</span><span className="profile-editor-counter num" aria-hidden="true">{bio.length}/160</span></span>
            <textarea id={`${id}-bio`} className="field" value={bio} onChange={(event) => setBio(event.target.value)} rows={3} maxLength={160} aria-describedby={`${id}-bio-hint`} />
            <span id={`${id}-bio-hint`} className="sr-only">최대 160자</span>
          </label>
          {error ? <p id={`${id}-error`} className="profile-editor-error" role="alert">{error}</p> : null}
        </fieldset>
        <div className="profile-editor-footer">
          <button type="button" className="btn btn-m btn-secondary" onClick={onClose} disabled={busy}>취소</button>
          <button type="submit" className="btn btn-m btn-primary" disabled={busy || !dirty}>{busy ? "저장 중…" : "저장"}</button>
        </div>
      </form>
    </EditorDialog>
  );
}

export function PasswordChangeDialog({ user, onClose, onSaved }) {
  const id = useId();
  const currentInput = useRef(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [invalid, setInvalid] = useState("");
  const { busy, error, setError, save } = useAccountSave(user, (response) => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    onSaved?.(response);
    onClose();
  });

  function submit(event) {
    event.preventDefault();
    if (!currentPassword) {
      setInvalid("current");
      setError("현재 비밀번호를 입력해주세요.");
      return;
    }
    if (newPassword.length < 8 || newPassword.length > 256) {
      setInvalid("new");
      setError("새 비밀번호는 8–256자로 입력해주세요.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setInvalid("confirm");
      setError("새 비밀번호가 일치하지 않아요.");
      return;
    }
    setInvalid("");
    save((signal) => api.changePassword({ currentPassword, newPassword }, { signal }));
  }

  return (
    <EditorDialog title="비밀번호 변경" busy={busy} onClose={onClose} initialFocus={currentInput}>
      <form className="profile-editor-form" onSubmit={submit}>
        <fieldset className="profile-editor-fields" disabled={busy}>
          <input type="text" name="username" autoComplete="username" value={user.email || user.username} readOnly hidden />
          <label className="profile-editor-label" htmlFor={`${id}-current`}>현재 비밀번호
            <input ref={currentInput} id={`${id}-current`} type="password" className="field" value={currentPassword} onChange={(event) => { setCurrentPassword(event.target.value); setInvalid(""); }} maxLength={256} autoComplete="current-password" aria-invalid={invalid === "current" || undefined} aria-describedby={invalid === "current" ? `${id}-error` : undefined} />
          </label>
          <label className="profile-editor-label" htmlFor={`${id}-new`}>새 비밀번호
            <input id={`${id}-new`} aria-label="새 비밀번호" type="password" className="field" value={newPassword} onChange={(event) => { setNewPassword(event.target.value); setInvalid(""); }} maxLength={256} autoComplete="new-password" aria-invalid={invalid === "new" || undefined} aria-describedby={`${id}-password-hint${invalid === "new" ? ` ${id}-error` : ""}`} />
            <span id={`${id}-password-hint`} className="profile-editor-hint">8–256자</span>
          </label>
          <label className="profile-editor-label" htmlFor={`${id}-confirm`}>새 비밀번호 확인
            <input id={`${id}-confirm`} type="password" className="field" value={confirmPassword} onChange={(event) => { setConfirmPassword(event.target.value); setInvalid(""); }} maxLength={256} autoComplete="new-password" aria-invalid={invalid === "confirm" || undefined} aria-describedby={invalid === "confirm" ? `${id}-error` : undefined} />
          </label>
          {error ? <p id={`${id}-error`} className="profile-editor-error" role="alert">{error}</p> : null}
        </fieldset>
        <div className="profile-editor-footer">
          <button type="button" className="btn btn-m btn-secondary" onClick={onClose} disabled={busy}>취소</button>
          <button type="submit" className="btn btn-m btn-primary" disabled={busy}>{busy ? "변경 중…" : "변경"}</button>
        </div>
      </form>
    </EditorDialog>
  );
}

export function DeleteAccountDialog({ user, onClose, onDeleted }) {
  const id = useId();
  const confirmationInput = useRef(null);
  const [confirmation, setConfirmation] = useState("");
  const [password, setPassword] = useState("");
  const [credential, setCredential] = useState("");
  const [googleConfig, setGoogleConfig] = useState(null);
  const [googleAttempt, setGoogleAttempt] = useState(0);
  const { busy, error, setError, save } = useAccountSave(user, () => onDeleted());
  useEffect(() => {
    if (user.can_change_password) return undefined;
    let alive = true;
    setGoogleConfig(null);
    api.googleConfig().then((value) => { if (alive) setGoogleConfig(value); })
      .catch(() => { if (alive) setGoogleConfig({ enabled: false }); });
    return () => { alive = false; };
  }, [user.can_change_password, googleAttempt]);

  function submit(event) {
    event.preventDefault();
    if (confirmation !== "탈퇴") { setError("확인란에 '탈퇴'를 입력해주세요."); return; }
    save((signal) => api.deleteAccount({ confirmation, password, credential }, { signal }));
  }
  return (
    <EditorDialog title="회원 탈퇴" busy={busy} onClose={onClose} initialFocus={confirmationInput}>
      <form className="profile-editor-form" onSubmit={submit}>
        <fieldset className="profile-editor-fields" disabled={busy}>
          <div className="profile-delete-notice">
            <p>프로필·사진·보유 포인트·내 매크로는 삭제되며 복구할 수 없어요.</p>
            <p>게시글·채팅·판매 매크로는 ‘탈퇴한 회원’으로 남고, 거래 기록은 계정 정보 없이 보관돼요. <Link to="/mypage?tab=posts" onClick={onClose}>내 게시글 확인</Link></p>
            <p>실행 중인 매크로는 먼저 종료해주세요. <Link to="/agents" onClick={onClose}>내 에이전트</Link></p>
          </div>
          <label className="profile-editor-label" htmlFor={`${id}-confirmation`}>확인란에 ‘탈퇴’를 입력해주세요
            <input ref={confirmationInput} id={`${id}-confirmation`} className="field" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" maxLength={2} />
          </label>
          {user.can_change_password ? <label className="profile-editor-label" htmlFor={`${id}-password`}>현재 비밀번호
            <input id={`${id}-password`} className="field" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" maxLength={256} required />
          </label> : <div className="profile-delete-google">
            <span className="profile-editor-label">Google 계정 본인 확인</span>
            {googleConfig?.enabled && googleConfig.client_id ? <GoogleSignInButton clientId={googleConfig.client_id} text="continue_with" onCredential={(value) => { setCredential(value); setError(""); }} /> : <button type="button" className="btn btn-m btn-secondary" disabled={googleConfig === null} onClick={() => setGoogleAttempt((value) => value + 1)}>{googleConfig === null ? "불러오는 중…" : "Google 본인 확인 다시 불러오기"}</button>}
            {credential ? <span className="profile-editor-hint" role="status">Google 계정을 선택했어요.</span> : null}
          </div>}
          {error ? <p className="profile-editor-error" role="alert">{error}</p> : null}
        </fieldset>
        <div className="profile-editor-footer">
          <button type="button" className="btn btn-m btn-secondary" onClick={onClose} disabled={busy}>취소</button>
          <button type="submit" className="btn btn-m btn-danger" disabled={busy || confirmation !== "탈퇴" || (user.can_change_password ? !password : !credential)}>{busy ? "처리 중…" : "회원 탈퇴"}</button>
        </div>
      </form>
    </EditorDialog>
  );
}
