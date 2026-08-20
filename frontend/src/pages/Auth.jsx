import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { setAuth } from "../lib/auth.js";
import GoogleSignInButton from "../components/GoogleSignInButton.jsx";
import {
  clearAuthReturn,
  recallAuthReturn,
  rememberAuthReturn,
  safeLocalPath,
} from "../lib/returnPath.js";

const inputCls = "field";

export default function Auth() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const requestedNext = safeLocalPath(params.get("next") || "");
  const next = requestedNext || recallAuthReturn("/leaderboard");
  const notice = (params.get("notice") || "").slice(0, 120);
  const [mode, setMode] = useState(params.get("mode") === "signup" ? "signup" : "login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [googleClientId, setGoogleClientId] = useState("");
  const [googleConfigState, setGoogleConfigState] = useState("loading");
  const [googleConfigAttempt, setGoogleConfigAttempt] = useState(0);

  const isSignup = mode === "signup";

  useEffect(() => {
    rememberAuthReturn(next);
  }, [next]);

  // 구글 로그인 사용 가능 여부·client_id 를 서버에서 런타임으로 받아온다(빌드 환경변수 불필요).
  useEffect(() => {
    let alive = true;
    setGoogleConfigState("loading");
    api.googleConfig()
      .then((cfg) => {
        if (!alive) return;
        if (cfg?.enabled && cfg.client_id) {
          setGoogleClientId(cfg.client_id);
          setGoogleConfigState("ready");
          return;
        }
        setGoogleClientId("");
        setGoogleConfigState("disabled");
      })
      .catch(() => {
        if (!alive) return;
        setGoogleClientId("");
        setGoogleConfigState("error");
      });
    return () => {
      alive = false;
    };
  }, [googleConfigAttempt]);

  const onGoogleCredential = useCallback(
    async (credential) => {
      setBusy(true);
      setError("");
      try {
        const data = await api.googleAuth(credential);
        setAuth(data.token, data.user);
        clearAuthReturn();
        navigate(next, { replace: true });
      } catch (err) {
        setError(String(err.message || err));
      } finally {
        setBusy(false);
      }
    },
    [navigate, next]
  );

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = isSignup
        ? await api.signup(email.trim(), username.trim(), password)
        : await api.login(email.trim(), password);
      setAuth(data.token, data.user);
      clearAuthReturn();
      navigate(next, { replace: true });
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card form-surface border border-slate-200">
        <h1 className="t-h2 text-slate-900 text-left mb-5">{isSignup ? "회원가입" : "로그인"}</h1>
        {notice ? (
          <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 t-small text-amber-800" role="status">
            {notice}
          </div>
        ) : null}

        <form onSubmit={submit} className="space-y-3">
          <label className="block">
            <span className="sr-only">{isSignup ? "이메일" : "아이디"}</span>
            <input className={inputCls} type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder={isSignup ? "이메일" : "아이디"} autoComplete="email" required />
          </label>
          {isSignup && (
            <label className="block">
              <span className="sr-only">아이디 (공개 표시용)</span>
              <input className={inputCls} value={username} onChange={(e) => setUsername(e.target.value)}
                placeholder="아이디 (공개 표시용)" autoComplete="username" required />
            </label>
          )}
          <label className="block">
            <span className="sr-only">비밀번호</span>
            <input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder={isSignup ? "비밀번호 (8자 이상)" : "비밀번호"}
              autoComplete={isSignup ? "new-password" : "current-password"} required />
          </label>

          {error && <div className="t-small text-red-600" role="alert">{error}</div>}

          <button type="submit" disabled={busy} className="btn btn-l btn-primary w-full">
            {busy ? "처리 중…" : isSignup ? "가입하기" : "로그인"}
          </button>
        </form>

        <div className="flex items-center gap-3 my-3" aria-hidden="true">
          <span className="h-px flex-1 bg-slate-200" />
          <span className="t-caption text-slate-400">또는</span>
          <span className="h-px flex-1 bg-slate-200" />
        </div>
        {googleClientId ? (
          <GoogleSignInButton
            clientId={googleClientId}
            text={isSignup ? "signup_with" : "signin_with"}
            onCredential={onGoogleCredential}
          />
        ) : (
          <button
            type="button"
            className="auth-google-fallback"
            disabled={googleConfigState === "loading"}
            aria-busy={googleConfigState === "loading"}
            onClick={() => setGoogleConfigAttempt((attempt) => attempt + 1)}
          >
            {googleConfigState === "loading"
              ? "Google 로그인 불러오는 중"
              : `Google 계정으로 ${isSignup ? "가입" : "로그인"}`}
          </button>
        )}

        {!isSignup && (
          <div className="mt-3 text-center">
            <button className="t-caption text-slate-500 hover:text-slate-900 underline underline-offset-4"
              onClick={() => navigate(`/forgot?next=${encodeURIComponent(next)}`)}>
              비밀번호를 잊으셨나요?
            </button>
          </div>
        )}

        <div className="mt-3 t-small text-slate-700 text-center">
          {isSignup ? "이미 계정이 있나요?" : "계정이 없나요?"}{" "}
          <button
            className="font-bold text-slate-900 underline underline-offset-4"
            onClick={() => { setError(""); setMode(isSignup ? "login" : "signup"); }}
          >
            {isSignup ? "로그인" : "회원가입"}
          </button>
        </div>
      </div>
    </div>
  );
}
