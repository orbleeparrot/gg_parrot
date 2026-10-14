import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { buildMacro, validate } from "../lib/macro.js";

export function macroFingerprint(macro) {
  return JSON.stringify(macro);
}

const EMPTY_BACKTEST = {
  busyKey: "",
  testedKey: "",
  testedMacro: null,
  result: null,
  perSymbol: [],
  explanation: null,
  summary: "",
  dataSource: "",
  periodLabel: "",
  error: "",
};

// The guide owns its successful snapshot so browser back/forward can keep the
// real result on screen. A generation id prevents a slower, older request from
// replacing a newer result after the user edits a condition.
export default function useHeroBacktest(currentKey) {
  const [state, setState] = useState(EMPTY_BACKTEST);
  const requestIdRef = useRef(0);
  const mountedRef = useRef(false);
  const pendingKeyRef = useRef("");
  const pendingRequestIdRef = useRef(0);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const run = useCallback(async (snapshot) => {
    const validationError = validate(snapshot);
    if (validationError) {
      setState((current) => ({ ...current, error: validationError }));
      return false;
    }

    const macro = buildMacro(snapshot);
    const key = macroFingerprint(macro);
    if (pendingKeyRef.current === key) return false;

    const requestId = ++requestIdRef.current;
    pendingKeyRef.current = key;
    pendingRequestIdRef.current = requestId;
    setState((current) => ({
      ...current,
      busyKey: key,
      error: "",
    }));

    try {
      const data = await api.backtest(macro);
      if (!mountedRef.current || requestId !== requestIdRef.current) return false;
      setState((current) => ({
        ...current,
        busyKey: "",
        testedKey: key,
        testedMacro: macro,
        result: data.result,
        perSymbol: data.per_symbol || [],
        explanation: data.explanation || null,
        summary: data.human_summary || "",
        dataSource: data.data_source || "",
        periodLabel: data.period_label || "",
        error: "",
      }));
      return true;
    } catch (reason) {
      if (mountedRef.current && requestId === requestIdRef.current) {
        setState((current) => ({
          ...current,
          busyKey: "",
          error: String(reason.message || reason),
        }));
      }
      return false;
    } finally {
      if (pendingRequestIdRef.current === requestId) {
        pendingKeyRef.current = "";
        pendingRequestIdRef.current = 0;
      }
    }
  }, []);


  return {
    ...state,
    run,
    busy: state.busyKey !== "",
    currentBusy: state.busyKey === currentKey,
    resultIsFresh: !!state.result && state.testedKey === currentKey,
  };
}
