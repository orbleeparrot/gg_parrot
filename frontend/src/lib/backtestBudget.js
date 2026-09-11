const DAY = 86_400_000;

export function validBacktestLimits(value) {
  return !!(Number.isSafeInteger(value?.max_bars) && value.max_bars > 0
    && value.interval_ms && Object.values(value.interval_ms).every((ms) => Number.isFinite(ms) && ms > 0)
    && value.preset_days && Object.values(value.preset_days).every((days) => Number.isFinite(days) && days > 0));
}

// The server supplies both the cap and the exact preset durations (e.g. 6m is
// 182 days), so the UI never substitutes a different interval or simulation.
export function backtestBudget(form, limits) {
  if (!validBacktestLimits(limits)) return null;
  const interval = limits.interval_ms[form.candle_interval];
  const duration = form.preset === "custom"
    ? Date.parse(form.end) - Date.parse(form.start)
    : limits.preset_days[form.preset] * DAY;
  if (!Number.isFinite(interval) || interval <= 0) return { error: "봉 간격을 선택해 주세요.", allowed: false, suggestions: [] };
  if (!Number.isFinite(duration) || duration <= 0) return { error: "시작일보다 뒤의 종료일을 선택해 주세요.", allowed: false, suggestions: [] };
  const bars = Math.max(1, Math.ceil(duration / interval));
  const allowed = bars <= limits.max_bars;
  const suggestions = [];
  if (!allowed) {
    const larger = Object.entries(limits.interval_ms).sort((a, b) => a[1] - b[1])
      .find(([, milliseconds]) => milliseconds > interval && Math.ceil(duration / milliseconds) <= limits.max_bars);
    if (larger) suggestions.push({ kind: "interval", value: larger[0], patch: { candle_interval: larger[0] } });
    const shorter = Object.entries(limits.preset_days).sort((a, b) => b[1] - a[1])
      .find(([, days]) => days * DAY < duration && Math.ceil(days * DAY / interval) <= limits.max_bars);
    if (shorter) suggestions.push({ kind: "period", value: shorter[0], patch: { preset: shorter[0], start: "", end: "" } });
  }
  return { bars, maxBars: limits.max_bars, allowed, suggestions };
}
