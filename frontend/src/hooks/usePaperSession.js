import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import useAdaptivePolling from "./useAdaptivePolling.js";

const PAPER_POLL_ERROR = "페이퍼 세션 상태를 갱신하지 못했어요. 잠시 뒤 다시 확인할게요.";

export default function usePaperSession({ macro, valErr = "", onStarted, stopOnUnmount = false }) {
  const [session, setSession] = useState(null);
  const [status, setStatus] = useState(null);
  const [mode, setMode] = useState("live");
  const [phase, setPhase] = useState("idle");
  const [error, setError] = useState("");
  const [startedMacro, setStartedMacro] = useState(null);
  const [startedMode, setStartedMode] = useState("");
  const generationRef = useRef(0);
  const actionRef = useRef(false);
  const sessionRef = useRef(session);
  const runningRef = useRef(status?.status === "running");
  const phaseRef = useRef(phase);
  const onStartedRef = useRef(onStarted);
  const stopOnUnmountRef = useRef(stopOnUnmount);

  sessionRef.current = session;
  runningRef.current = status?.status === "running";
  phaseRef.current = phase;
  onStartedRef.current = onStarted;
  stopOnUnmountRef.current = stopOnUnmount;

  const changePhase = useCallback((nextPhase) => {
    phaseRef.current = nextPhase;
    setPhase(nextPhase);
  }, []);

  const pollStatus = useCallback(async (signal) => {
    const activeSession = sessionRef.current;
    if (!activeSession?.session_id) return;
    const generation = generationRef.current;
    try {
      const nextStatus = await api.paperStatus(activeSession.session_id, { signal });
      if (generation !== generationRef.current) return;
      runningRef.current = nextStatus.status === "running";
      setStatus(nextStatus);
      setError((current) => current === PAPER_POLL_ERROR ? "" : current);
      changePhase(nextStatus.status === "running" ? "running" : "stopped");
    } catch (reason) {
      if (reason?.name === "AbortError") throw reason;
      if (generation === generationRef.current) setError(PAPER_POLL_ERROR);
      throw reason;
    }
  }, [changePhase]);

  useAdaptivePolling(pollStatus, {
    intervalMs: 2_000,
    maxIntervalMs: 30_000,
    enabled: !!session?.session_id && status?.status === "running" && phase === "running",
    pollKey: session?.session_id,
  });

  useEffect(() => {
    const retirePreview = () => {
      if (stopOnUnmountRef.current && runningRef.current && sessionRef.current?.session_id) {
        void api.paperStop(sessionRef.current.session_id, { keepalive: true }).catch(() => {});
      }
    };
    window.addEventListener("pagehide", retirePreview);
    return () => {
      generationRef.current += 1;
      window.removeEventListener("pagehide", retirePreview);
      retirePreview();
    };
  }, []);

  const createSession = useCallback(async (generation, macroSnapshot, modeSnapshot) => {
    const nextSession = await api.paperStart(macroSnapshot, macroSnapshot.symbol, modeSnapshot);
    if (generation !== generationRef.current) {
      // Starting succeeded after the guide moved away or unmounted. The server
      // has already created a runner, so explicitly retire it instead of
      // leaving an invisible paper session behind.
      void api.paperStop(nextSession.session_id).catch(() => {});
      return false;
    }

    const initialStatus = {
      ...nextSession,
      current_equity: nextSession.virtual_balance,
      current_return: 0,
      last_price: 0,
      trades: [],
      liquidations: 0,
    };
    sessionRef.current = nextSession;
    runningRef.current = true;
    setSession(nextSession);
    setStartedMacro(macroSnapshot);
    setStartedMode(modeSnapshot);
    setStatus(initialStatus);
    changePhase("running");
    onStartedRef.current?.({ session: nextSession, macro: macroSnapshot, mode: modeSnapshot });
    return true;
  }, [changePhase]);

  const start = useCallback(async () => {
    if (actionRef.current) return false;
    if (valErr) {
      setError(valErr);
      return false;
    }

    actionRef.current = true;
    const generation = ++generationRef.current;
    const macroSnapshot = macro;
    const modeSnapshot = mode;
    setError("");
    setStartedMacro(null);
    setStartedMode("");
    changePhase("starting");
    try {
      return await createSession(generation, macroSnapshot, modeSnapshot);
    } catch (reason) {
      if (generation === generationRef.current) {
        setError(String(reason.message || reason));
        changePhase("error");
      }
      return false;
    } finally {
      actionRef.current = false;
    }
  }, [changePhase, createSession, macro, mode, valErr]);

  const stop = useCallback(async () => {
    if (phaseRef.current === "starting") {
      // Invalidate the in-flight start. createSession will stop the server
      // runner as soon as its response supplies the new session id.
      generationRef.current += 1;
      changePhase("idle");
      return true;
    }

    const activeSession = sessionRef.current;
    if (!activeSession?.session_id) return true;
    if (actionRef.current) return false;
    if (!runningRef.current) {
      changePhase("stopped");
      return true;
    }

    actionRef.current = true;
    const generation = ++generationRef.current;
    setError("");
    changePhase("stopping");
    try {
      await api.paperStop(activeSession.session_id);
      if (generation !== generationRef.current) return false;
      runningRef.current = false;
      setStatus((current) => current ? { ...current, status: "stopped" } : current);
      changePhase("stopped");
      try {
        const nextStatus = await api.paperStatus(activeSession.session_id);
        if (generation === generationRef.current) setStatus(nextStatus);
      } catch (_) {
        if (generation === generationRef.current) setError(PAPER_POLL_ERROR);
      }
      return true;
    } catch (reason) {
      if (generation === generationRef.current) {
        setError(String(reason.message || reason));
        changePhase("error");
      }
      return false;
    } finally {
      actionRef.current = false;
    }
  }, [changePhase]);

  const restart = useCallback(async () => {
    if (actionRef.current) return false;
    if (valErr) {
      setError(valErr);
      return false;
    }

    actionRef.current = true;
    const generation = ++generationRef.current;
    const activeSession = sessionRef.current;
    const macroSnapshot = macro;
    const modeSnapshot = mode;
    setError("");
    setStartedMacro(null);
    setStartedMode("");
    changePhase("starting");
    try {
      if (activeSession?.session_id && runningRef.current) {
        await api.paperStop(activeSession.session_id);
        runningRef.current = false;
        setStatus((current) => current ? { ...current, status: "stopped" } : current);
      }
      if (generation !== generationRef.current) return false;
      return await createSession(generation, macroSnapshot, modeSnapshot);
    } catch (reason) {
      if (generation === generationRef.current) {
        setError(String(reason.message || reason));
        changePhase("error");
      }
      return false;
    } finally {
      actionRef.current = false;
    }
  }, [changePhase, createSession, macro, mode, valErr]);

  return {
    session,
    status,
    mode,
    setMode,
    phase,
    busy: phase === "starting" || phase === "stopping",
    error,
    setError,
    startedMacro,
    startedMode,
    startedKey: startedMacro ? JSON.stringify(startedMacro) : "",
    hasStarted: !!session,
    running: status?.status === "running",
    start,
    stop,
    restart,
  };
}
