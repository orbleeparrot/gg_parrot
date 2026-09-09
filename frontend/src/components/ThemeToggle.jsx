import { useEffect, useState } from "react";
import { MoonIcon, SunIcon } from "./utilityIcons.jsx";
import {
  applyTheme,
  getStoredTheme,
  resolveTheme,
  setStoredTheme,
  watchSystemTheme,
} from "../lib/theme.js";

export default function ThemeToggle({ className = "" }) {
  const [pref, setPref] = useState(getStoredTheme);
  const [systemTheme, setSystemTheme] = useState(() => resolveTheme("system"));

  // Re-apply on mount so React state and the pre-paint class can't drift.
  useEffect(() => {
    applyTheme(pref);
  }, [pref]);

  // Follow the OS while on "system".
  useEffect(() => {
    if (pref !== "system") return;
    return watchSystemTheme(() => {
      applyTheme("system");
      setSystemTheme(resolveTheme("system"));
    });
  }, [pref]);

  const resolved = pref === "system" ? systemTheme : pref;

  function toggle() {
    const next = resolved === "dark" ? "light" : "dark";
    setPref(next);
    setStoredTheme(next);
  }

  const isDark = resolved === "dark";
  const nextLabel = isDark ? "라이트 모드" : "다크 모드";
  // 아이콘은 "누르면 되는 것"을 그린다 — 다크에서는 해, 라이트에서는 달.
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={isDark ? "다크 모드 켜짐, 라이트 모드로 전환" : "다크 모드 꺼짐, 다크 모드로 전환"}
      aria-checked={isDark}
      role="switch"
      className={`header-control header-theme ${className}`}
    >
      {isDark ? <SunIcon /> : <MoonIcon />}
      <span className="header-tooltip" aria-hidden="true">{nextLabel}</span>
    </button>
  );
}
