// 껄무새 회원 키 — 실행기가 내 계정으로 세션을 올릴 때 쓰는 키. 조회·복사·재발급.
// 상단바 회원 키 팝오버·설정·실행기 설치가 함께 쓴다. RunnerSessions(차트 포함)에서 떼어 둔 이유: 상단바가
// 이 패널 하나 때문에 차트 라이브러리(lightweight-charts)와 지표 계산까지 첫 화면 번들로 끌고 왔다.
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api } from "../api.js";
import { captureAccountGuard, useAuth } from "../lib/auth.js";
import { createRunnerKeyStore } from "../lib/runnerKeyStore.js";
import ConfirmDialog from "./ConfirmDialog.jsx";
import "./RunnerKeyPanel.css";

const runnerKeys = createRunnerKeyStore({
  read: (generation) => api.runnerKey({ requestKey: `runner-key-${generation}` }),
  regenerate: () => api.runnerKeyRegenerate(),
});

export function RunnerKeyPanel({ enabled = true, compact = false, menu = false }) {
  const { accountVersion } = useAuth();
  const isCurrent = useMemo(() => captureAccountGuard({ accountOnly: true }), [accountVersion]);
  const getSnapshot = useCallback(() => runnerKeys.getSnapshot(accountVersion), [accountVersion]);
  const { data, error: err, regenerating } = useSyncExternalStore(runnerKeys.subscribe, getSnapshot, getSnapshot);
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [confirmRegen, setConfirmRegen] = useState(false);

  useEffect(() => {
    setRevealed(false);
    setCopied(false);
    setCopyFailed(false);
    if (enabled) void runnerKeys.load(accountVersion, isCurrent);
  }, [enabled, accountVersion, isCurrent]);

  if (!enabled) return null;

  // 재발급은 돌고 있는 실행기의 연결을 바로 끊는다 — 브라우저 기본 confirm 대신 위험 확인창(취소에 첫 포커스).
  async function regen() {
    if (!isCurrent() || regenerating) return;
    setConfirmRegen(false);
    const updated = await runnerKeys.rotate(accountVersion, isCurrent);
    if (updated && isCurrent()) {
      setRevealed(true);
      setCopied(false);
      setCopyFailed(false);
    }
  }

  async function copy() {
    if (!isCurrent() || !data?.key || regenerating) return;
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(data.key);
      if (!isCurrent()) return;
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (_) { if (isCurrent()) setCopyFailed(true); }
  }

  const regenDialog = (
    <ConfirmDialog
      open={confirmRegen}
      tone="danger"
      title="회원 키를 다시 발급할까요?"
      description="새 키를 발급하면 기존 키는 즉시 무효화돼요. 실행기에 새 키를 다시 입력해야 해요."
      confirmLabel="새 키 발급"
      busy={regenerating}
      onConfirm={regen}
      onCancel={() => setConfirmRegen(false)}
    />
  );

  if (menu) {
    if (err && !data) return <p className="t-small text-red-600" role="alert">회원 키를 불러오지 못했어요. 닫았다가 다시 열어 주세요.</p>;
    if (!data) return <p className="t-small text-slate-700" role="status">회원 키 불러오는 중…</p>;
    return (
      <div className="runner-key-menu">
        {regenDialog}
        <div className="runner-key-menu-field">
          <input className="num" aria-label="회원 키" type={revealed || copyFailed ? "text" : "password"} readOnly value={data.key} onFocus={(event) => event.target.select()} />
          <button type="button" className="t-small" onClick={() => { setRevealed(!(revealed || copyFailed)); setCopyFailed(false); }}>{revealed || copyFailed ? "숨기기" : "보기"}</button>
        </div>
        <div className="runner-key-menu-actions">
          <button type="button" className="t-small" onClick={copy} disabled={regenerating}>{copied ? "복사됨" : "복사"}</button>
          <button type="button" className="t-small" onClick={() => setConfirmRegen(true)} disabled={regenerating}>재발급</button>
        </div>
        <span className="sr-only" role="status">{copied ? "회원 키를 복사했어요." : ""}</span>
        {err ? <p className="t-small" role="alert">재발급하지 못했어요. 다시 시도해 주세요.</p> : null}
        {copyFailed ? <p className="t-small" role="alert">복사하지 못했어요. 키를 직접 복사해 주세요.</p> : null}
      </div>
    );
  }

  if (compact) {
    return (
      <div className="space-y-2">
        <button type="button" onClick={copy} disabled={!data?.key || !!err || regenerating} className="btn btn-l btn-secondary w-full">
          {err ? "회원 키를 불러오지 못했어요" : !data ? "회원 키 불러오는 중…" : copied ? "복사했어요" : "회원 키 복사"}
        </button>
        <span className="sr-only" role="status">{copied ? "회원 키를 복사했어요." : ""}</span>
        {err ? <p className="t-small text-red-600" role="alert">키 조회 오류: {err}</p> : null}
        {copyFailed ? (
          <div className="space-y-2">
            <p className="t-small text-red-600" role="alert">자동 복사를 하지 못했어요. 아래 키를 직접 복사해 주세요.</p>
            <input aria-label="직접 복사할 회원 키" readOnly value={data.key} onFocus={(event) => event.target.select()} className="field w-full min-w-0 num" />
          </div>
        ) : null}
      </div>
    );
  }

  if (err) return <div className="t-small text-red-600">키 조회 오류: {err}</div>;
  if (!data) return <div className="t-small text-slate-500">키 불러오는 중…</div>;

  const masked = data.key.slice(0, 8) + "•".repeat(Math.max(0, data.key.length - 12)) + data.key.slice(-4);
  return (
    <div className="notice space-y-2">
      {regenDialog}
      <div className="t-small text-slate-700">
        아래 <b className="text-slate-900">껄무새 회원 키</b>를 매크로 실행기의 ④번 칸에 입력하세요. 계정당 1개예요.
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <code className="num text-slate-900 bg-slate-100 px-2 py-1 rounded-sm break-all">
          {revealed ? data.key : masked}
        </code>
        <button onClick={() => setRevealed((v) => !v)} className="btn btn-s btn-secondary">
          {revealed ? "숨기기" : "보기"}
        </button>
        <button onClick={copy} disabled={regenerating} className="btn btn-s btn-secondary">{copied ? "복사됨!" : "복사"}</button>
        <button onClick={() => setConfirmRegen(true)} disabled={regenerating} className="btn btn-s btn-secondary">키 재발급</button>
      </div>
      <div className="t-caption text-slate-500">
        이 키는 서버 상태 확인·원격 종료에만 쓰여요. 거래소 API 키는 실행기에서 로컬로만 쓰고 서버로 보내지 않아요.
      </div>
    </div>
  );
}
