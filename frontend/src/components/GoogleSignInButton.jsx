import { useEffect, useRef, useState } from "react";

// Google Identity Services(GIS) 공식 버튼. 스크립트를 한 번만 불러오고, 로그인
// 성공 시 credential(ID 토큰)을 onCredential 로 넘긴다. 서버가 그 토큰을 검증한다.
const GSI_SRC = "https://accounts.google.com/gsi/client";
let gsiPromise = null;

function loadGsi() {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (gsiPromise) return gsiPromise;
  gsiPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${GSI_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("gsi load failed")));
      return;
    }
    const s = document.createElement("script");
    s.src = GSI_SRC;
    s.async = true;
    s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("gsi load failed"));
    document.head.appendChild(s);
  });
  return gsiPromise;
}

export default function GoogleSignInButton({ clientId, onCredential, text = "signin_with" }) {
  const holder = useRef(null);
  // 최신 콜백을 ref 로 들고 있어, 콜백 재생성 때마다 버튼을 다시 그리지 않게 한다.
  const cbRef = useRef(onCredential);
  cbRef.current = onCredential;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!clientId || !holder.current) return;
    let cancelled = false;
    loadGsi()
      .then(() => {
        if (cancelled || !holder.current) return;
        const id = window.google?.accounts?.id;
        if (!id) throw new Error("gsi unavailable");
        id.initialize({
          client_id: clientId,
          callback: (resp) => resp?.credential && cbRef.current?.(resp.credential),
        });
        const dark = document.documentElement.classList.contains("dark");
        const width = Math.min(400, Math.max(240, holder.current.offsetWidth || 320));
        holder.current.innerHTML = "";
        id.renderButton(holder.current, {
          type: "standard",
          theme: dark ? "filled_black" : "outline",
          size: "large",
          text, // signin_with | signup_with | continue_with
          shape: "rectangular",
          logo_alignment: "left",
          width,
        });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId, text]);

  if (failed) {
    return (
      <div className="t-caption text-slate-500 text-center">
        구글 로그인을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.
      </div>
    );
  }
  return <div ref={holder} className="flex justify-center min-h-[44px]" />;
}
