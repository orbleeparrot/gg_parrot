// 관리자 대시보드 — 자리 잡기 버전. 레이아웃은 시안 확정 뒤 채운다.
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";

export default function AdminDashboard() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!user?.is_admin) return undefined;
    const controller = new AbortController();
    Promise.all([api.adminOverview(30, { signal: controller.signal }), api.adminNews({ signal: controller.signal })])
      .then(([overview, news]) => setData({ overview, news }))
      .catch((err) => { if (err?.name !== "AbortError") setError(err?.message || "불러오지 못했어요"); });
    return () => controller.abort();
  }, [user?.is_admin]);
  if (!user) return <Navigate to="/login" replace />;
  if (!user.is_admin) return <Navigate to="/mypage" replace />;
  return (
    <div className="admin-page">
      <h1>관리자 대시보드</h1>
      {error ? <p role="alert">{error}</p> : !data ? <p role="status">불러오는 중…</p> : <pre className="num">{JSON.stringify(data, null, 2)}</pre>}
    </div>
  );
}
