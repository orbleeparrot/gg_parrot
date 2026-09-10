// Theme preference: "light" | "dark" | "system".
// The actual palette swap is CSS-only (see index.css); this just toggles the
// `dark` class on <html>. index.html applies the stored choice before first
// paint, so there's no white flash on load for dark-mode users.
const KEY = "ggp_theme";

export function getStoredTheme() {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "system";
  } catch (_) {
    return "system"; // private mode / storage disabled
  }
}

export function prefersDark() {
  return !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

export function resolveTheme(pref) {
  return pref === "system" ? (prefersDark() ? "dark" : "light") : pref;
}

export function applyTheme(pref) {
  document.documentElement.classList.toggle("dark", resolveTheme(pref) === "dark");
}

export function setStoredTheme(pref) {
  try {
    localStorage.setItem(KEY, pref);
  } catch (_) {
    /* preference just won't persist */
  }
  applyTheme(pref);
  window.dispatchEvent(new CustomEvent("ggp:theme-change", { detail: pref }));
}

/** Watch the OS setting; only repaints while the user is on "system". */
export function watchSystemTheme(onChange) {
  const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
  if (!mq) return () => {};
  const handler = () => onChange();
  mq.addEventListener("change", handler);
  return () => mq.removeEventListener("change", handler);
}
