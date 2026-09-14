// Account auth state (token + user), kept in localStorage and exposed as a tiny
// reactive store via useSyncExternalStore so header/pages update on login/logout.
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { accountScope, transitionAccountStorage } from "./accountStorage.js";

const TOKEN_KEY = "ggp_token";
const USER_KEY = "ggp_user";
const listeners = new Set();

function read() {
  try {
    return {
      token: localStorage.getItem(TOKEN_KEY) || "",
      user: JSON.parse(localStorage.getItem(USER_KEY) || "null"),
    };
  } catch {
    return { token: "", user: null };
  }
}

let state = { ...read(), sessionVersion: 0, accountVersion: 0 };
let revision = 0;
let accountRevision = 0;

function emit(options) {
  const next = read();
  if (state.token !== next.token || state.user?.id !== next.user?.id) revision += 1;
  if (accountScope(state) !== accountScope(next)) accountRevision += 1;
  transitionAccountStorage(accountScope(state), accountScope(next), options);
  state = { ...next, sessionVersion: revision, accountVersion: accountRevision };
  listeners.forEach((l) => l());
}

// Account changes in another tab must also switch request and chat identity here.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === null || event.key === TOKEN_KEY || event.key === USER_KEY) emit();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) emit(); // A history-restored page may have missed logout.
  });
}

export function getToken() {
  return state.token;
}
export function getAuthUser() {
  return state.user;
}
export function getAuthScope() {
  return accountScope(state);
}
// Also rejects A -> B -> A transitions, even if a previously issued token is
// reused. Call before awaiting; check again before applying an account result.
export function captureAccountGuard({ accountOnly = false } = {}) {
  const started = accountOnly ? accountRevision : revision;
  return () => (accountOnly ? accountRevision : revision) === started;
}
// Use inside a component keyed by useAuth().accountVersion. A token renewal for
// the same account keeps its draft and UI; another account retires old results.
export function useAccountGuard() {
  const owner = useRef(accountRevision);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  return useCallback(() => mounted.current && owner.current === accountRevision, []);
}
export function isLoggedIn() {
  return !!state.token;
}
export function setAuth(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  emit();
}
export function updateAuthUser(user) {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  emit();
}

// A response started before an edit may refresh points, but cannot undo the
// profile fields that changed locally while that request was in flight.
export function mergeFetchedAuthUser(fetched, requested) {
  if (state.user?.id !== fetched?.id) return fetched;
  const merged = { ...fetched };
  for (const field of ["username", "bio", "avatar_url", "can_change_password"]) {
    if (state.user[field] !== undefined && state.user[field] !== requested?.[field]) merged[field] = state.user[field];
  }
  return merged;
}
export function clearAuth(options = {}) {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  emit(options);
}

function subscribe(l) {
  listeners.add(l);
  return () => listeners.delete(l);
}
function snapshot() {
  return state;
}

// { token, user } — re-renders on any auth change.
export function useAuth() {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
