import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/csr/ArrowLeft";
import { UserCircleIcon } from "@phosphor-icons/react/dist/csr/UserCircle";
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/csr/ShieldCheck";
import { api } from "../api.js";
import { clearAuth, getAuthUser, getToken, mergeFetchedAuthUser, setAuth, updateAuthUser, useAuth } from "../lib/auth.js";
import ProfileEditor, { DeleteAccountDialog, PasswordChangeDialog } from "../components/ProfileEditor.jsx";
import { RunnerKeyPanel } from "../components/RunnerSessions.jsx";
import "./ProfileSettings.css";

function hasProfile(user) {
  return user?.id != null && typeof user.username === "string" && typeof user.email === "string"
    && typeof user.bio === "string" && typeof user.can_change_password === "boolean"
    && Object.prototype.hasOwnProperty.call(user, "avatar_url");
}

function MemberKeySettings() {
  const [open, setOpen] = useState(false);
  return <div className="profile-settings-member-key">
    <div className="profile-settings-security-row">
      <h3>회원 키</h3>
      <button type="button" className="btn btn-m btn-secondary" aria-label="회원 키 관리" aria-expanded={open} aria-controls="profile-settings-member-key" onClick={() => setOpen((value) => !value)}>{open ? "닫기" : "관리"}</button>
    </div>
    {open ? <div id="profile-settings-member-key" className="profile-settings-key-panel"><RunnerKeyPanel menu /></div> : null}
  </div>;
}

export default function ProfileSettings() {
  const { token, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [dialog, setDialog] = useState(null);
  const [notice, setNotice] = useState("");
  const [loadError, setLoadError] = useState("");
  const [retry, setRetry] = useState(0);
  const leavingAccount = useRef(false);
  const security = searchParams.get("tab") === "security";
  const needsProfile = !hasProfile(user);

  useEffect(() => {
    setDialog(null);
    setLoadError("");
    if (!token && !leavingAccount.current) {
      navigate(`/login?next=${encodeURIComponent(`/mypage/settings${security ? "?tab=security" : ""}`)}`, { replace: true });
    }
  }, [token, navigate]);

  useEffect(() => { setNotice(""); }, [user?.id]);

  useEffect(() => {
    if (!token || !needsProfile) return undefined;
    let alive = true;
    const requested = getAuthUser();
    setLoadError("");
    api.me().then((response) => {
      if (!alive || getToken() !== token || (requested?.id != null && getAuthUser()?.id !== requested.id)) return;
      if (!hasProfile(response.user) || (requested?.id != null && response.user.id !== requested.id)) {
        setLoadError("계정 정보를 불러오지 못했어요.");
        return;
      }
      if (getAuthUser()?.id != null && getAuthUser().id !== response.user.id) return;
      updateAuthUser(mergeFetchedAuthUser(response.user, requested));
    }).catch((error) => {
      if (alive && getToken() === token) setLoadError(String(error?.message || "계정 정보를 불러오지 못했어요."));
    });
    return () => { alive = false; };
  }, [token, needsProfile, retry]);

  function saveProfile(updated) {
    updateAuthUser(updated);
    setNotice("프로필을 저장했어요.");
  }

  function savePassword(response) {
    setDialog(null);
    setAuth(response.token, response.user);
    setNotice("비밀번호를 변경했어요.");
  }

  function logout() {
    leavingAccount.current = true;
    clearAuth();
    navigate("/login", { replace: true });
  }

  function deleted() {
    leavingAccount.current = true;
    clearAuth();
    navigate(`/login?notice=${encodeURIComponent("회원 탈퇴가 완료됐어요.")}`, { replace: true });
  }

  if (!token) return null;
  const accountKey = `${token}:${user?.id}`;
  return (
    <div className="profile-settings-page">
      <header className="profile-settings-header">
        <Link to="/mypage" className="profile-settings-back"><ArrowLeftIcon size={18} aria-hidden="true" />프로필로 돌아가기</Link>
        <h1>설정</h1>
      </header>
      <div className="profile-settings-layout">
        <nav className="profile-settings-nav" aria-label="설정 메뉴">
          <Link to="/mypage/settings" aria-current={!security ? "page" : undefined}><UserCircleIcon size={22} aria-hidden="true" /><span>프로필</span></Link>
          <Link to="/mypage/settings?tab=security" aria-current={security ? "page" : undefined}><ShieldCheckIcon size={22} aria-hidden="true" /><span>계정 및 보안</span></Link>
        </nav>
        <div className="profile-settings-content">
          {loadError ? <div className="profile-settings-load-error" role="alert"><p>{loadError}</p><button type="button" className="btn btn-s btn-secondary" onClick={() => setRetry((value) => value + 1)}>다시 불러오기</button></div> : null}
          {notice ? <p className="profile-settings-status" role="status">{notice}</p> : null}
          {user?.id != null ? <>
            <section className="profile-settings-panel" hidden={security} aria-labelledby="profile-settings-profile-title">
              <h2 id="profile-settings-profile-title">프로필</h2>
              {needsProfile ? !loadError ? <p className="profile-settings-loading" role="status">계정 정보를 불러오는 중…</p> : null : <ProfileEditor key={accountKey} inline user={user} onClose={() => navigate("/mypage")} onSaved={saveProfile} />}
            </section>
            <section className="profile-settings-panel" hidden={!security} aria-labelledby="profile-settings-security-title">
              <h2 id="profile-settings-security-title">계정 및 보안</h2>
              <dl className="profile-settings-account">
                <div><dt>이메일</dt><dd>{user.email || "—"}</dd></div>
                <div><dt>로그인 방식</dt><dd>{typeof user.can_change_password === "boolean" ? (user.can_change_password ? "이메일 · 비밀번호" : "Google 로그인") : "불러오는 중…"}</dd></div>
              </dl>
              {user.can_change_password ? <div className="profile-settings-security-row"><h3>비밀번호</h3><button type="button" className="btn btn-m btn-secondary" aria-haspopup="dialog" onClick={() => setDialog("password")}>비밀번호 변경</button></div> : null}
              {security ? <MemberKeySettings key={accountKey} /> : null}
              <div className="profile-settings-session-actions">
                <button type="button" className="btn btn-m btn-secondary" onClick={logout}>로그아웃</button>
                <button type="button" className="btn btn-m btn-ghost profile-settings-withdrawal" aria-haspopup="dialog" disabled={typeof user.can_change_password !== "boolean"} onClick={() => setDialog("delete")}>회원 탈퇴</button>
              </div>
            </section>
          </> : !loadError ? <p className="profile-settings-loading" role="status">계정 정보를 불러오는 중…</p> : null}
        </div>
      </div>
      {dialog === "password" && user?.can_change_password ? <PasswordChangeDialog key={accountKey} user={user} onClose={() => setDialog(null)} onSaved={savePassword} /> : null}
      {dialog === "delete" && user?.id != null ? <DeleteAccountDialog key={accountKey} user={user} onClose={() => setDialog(null)} onDeleted={deleted} /> : null}
    </div>
  );
}
