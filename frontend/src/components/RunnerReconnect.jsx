import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api.js";
import { useAccountGuard } from "../lib/auth.js";
import { getRunnerDevice } from "../lib/runnerDevice.js";
import { reconnectTicketState, requireReconnectTicket, showRunnerReconnect } from "../lib/runnerReconnect.js";
import { useModalLayer } from "../hooks/useModalLayer.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import "./RunnerReconnect.css";

export default function RunnerReconnect({ session, busy = false, small = false }) {
  const [open, setOpen] = useState(false);
  const [ticket, setTicket] = useState(null);
  const [phase, setPhase] = useState("idle");
  const [error, setError] = useState("");
  const request = useRef(0);
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const titleId = useId();
  const descriptionId = useId();
  const isCurrentAccount = useAccountGuard();
  const canRun = getRunnerDevice().canRunRunner;
  const visible = showRunnerReconnect(session);
  const available = session?.recovery?.available === true;

  function close() {
    request.current += 1;
    setOpen(false);
    setTicket(null);
    setPhase("idle");
  }

  useEffect(() => {
    if (visible) return;
    request.current += 1;
    setOpen(false);
    setTicket(null);
    setPhase("idle");
  }, [visible]);

  useModalLayer({ open: open && visible, ref: dialogRef, onEscape: close, trap: true, initialFocusRef: closeRef });
  useAdaptivePolling(async (signal) => {
    const current = request.current;
    const data = await api.runnerLaunchTicketStatus(ticket.launch_id);
    if (!signal.aborted && isCurrentAccount() && current === request.current) {
      setPhase(Date.parse(ticket.expires_at) <= Date.now() ? "expired" : reconnectTicketState(data));
    }
  }, { intervalMs: 2000, maxIntervalMs: 10000, pollKey: ticket?.launch_id,
    enabled: open && visible && !!ticket && ["ready", "waiting"].includes(phase), immediate: false });

  async function prepare() {
    if (!isCurrentAccount() || busy || phase === "preparing") return;
    setOpen(true);
    setError("");
    if (!available || !canRun) return;
    const current = ++request.current;
    setTicket(null);
    setPhase("preparing");
    try {
      const data = await api.runnerReconnect(session.session_id);
      if (!isCurrentAccount() || current !== request.current) return;
      setTicket(requireReconnectTicket(data));
      setPhase("ready");
    } catch (reason) {
      if (!isCurrentAccount() || current !== request.current) return;
      setError(String(reason.message || reason));
      setPhase("error");
    }
  }

  if (!visible) return null;
  return <>
    <button type="button" disabled={busy} className={`btn btn-${small ? "s" : "m"} btn-primary runner-reconnect-button`}
      onClick={() => void prepare()}>실행기 다시 연결</button>
    {open ? createPortal(
      <div className="scrim fixed inset-0 z-90 grid place-items-center p-4"
        onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
        <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId}
          aria-describedby={descriptionId} className="dialog runner-reconnect-dialog">
          <h2 id={titleId} className="t-h4 text-slate-900">중단된 실행 다시 연결</h2>
          <p id={descriptionId}>{session.symbol} · 이 매크로를 실행하던 PC에서 실행기를 열어 주세요.
            실행기가 저장된 상태와 거래소 보유 수량·주문 결과를 확인한 뒤, ‘이어서 실행’을 누르면 같은 실행 기록으로 이어가요.</p>
          {!canRun ? <p role="status">Windows 또는 Mac의 원래 PC에서 같은 계정으로 로그인해 이 실행을 열어 주세요.</p>
            : !available ? <p role="status">{session.recovery?.reason || "이 실행에는 복구 정보가 없어요. 실행기를 업데이트한 뒤 시작한 실행부터 이어서 실행할 수 있어요."}</p>
              : <>
                <p className="runner-reconnect-note">PC가 꺼져 있던 동안에는 매크로와 손절이 작동하지 않았어요. 확인 중에는 새 주문을 보내지 않아요.</p>
                <div role="status" aria-live="polite">
                  {phase === "preparing" ? "연결을 준비하고 있어요…"
                    : phase === "claimed" ? "실행기가 연결 요청을 받았어요. 실행기에서 중단된 실행을 확인해 주세요."
                      : phase === "outdated" ? "실행기를 업데이트한 뒤 다시 연결해 주세요."
                        : phase === "expired" ? "연결 요청이 만료됐어요. 다시 준비해 주세요."
                          : phase === "waiting" ? "실행기에서 확인을 기다리고 있어요."
                            : "실행기를 열어 복구할 상태를 확인해요."}
                </div>
                {error ? <p role="alert">{error}</p> : null}
                {["ready", "waiting"].includes(phase) && ticket ? <a className="btn btn-l btn-primary"
                  href={ticket.launch_url} onClick={(event) => {
                    if (Date.parse(ticket.expires_at) <= Date.now()) { event.preventDefault(); setPhase("expired"); }
                    else setPhase("waiting");
                  }}>실행기 열기</a> : null}
                {["error", "expired", "outdated", "claimed"].includes(phase)
                  ? <button type="button" className="btn btn-l btn-secondary" onClick={() => void prepare()}>연결 다시 준비</button> : null}
                <a href="/runner/install" className="btn btn-m btn-ghost">실행기 설치·업데이트 안내</a>
              </>}
          <button ref={closeRef} type="button" className="btn btn-l btn-ghost" onClick={close}>닫기</button>
        </section>
      </div>, document.body) : null}
  </>;
}
