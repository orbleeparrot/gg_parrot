import { COOLDOWN, ENTRY_AT, HALTED, HOLDING, KIND, RECOVERING, REENTRY, STOPPED, TRADES, WAITING } from "./leaderboardCopy.js";

const fmtPct = (v) => `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
const fmtPrice = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 4 });
const base = (symbol) => String(symbol || "").replace(/USDT$/, "");

export function isLive(entry, prices, now = Date.now()) {
  if (entry?.state !== "holding" || !(entry.virtual_balance > 0)) return false;
  if (!Number.isFinite(entry.equity)) return false;
  // 러너가 죽었는데 마지막 체크포인트만 남은 고아 세션은 "실시간"이 아니다 — 90초 넘으면 서버값으로 폴백.
  if (!Number.isFinite(entry.checkpoint_ms) || now - entry.checkpoint_ms > 90_000) return false;
  const held = (entry.legs || []).filter((l) => l.in_position);
  return held.length > 0 && held.every((l) => Number.isFinite(prices?.[l.symbol]));
}

export function liveReturn(entry, prices, now = Date.now()) {
  if (!isLive(entry, prices, now)) return entry?.return_pct ?? null;
  const delta = (entry.legs || []).filter((l) => l.in_position)
    .reduce((sum, l) => sum + l.qty * (prices[l.symbol] - l.last_price) * (l.dir || 1), 0);
  const equity = entry.equity + delta;
  return Math.round(((equity - entry.virtual_balance) / entry.virtual_balance) * 100 * 100) / 100;
}

export function cooldownLabel(untilMs, now = Date.now()) {
  if (!untilMs || untilMs <= now) return REENTRY;
  return COOLDOWN(Math.ceil((untilMs - now) / 60_000));
}

// 서버는 running 이라는데 체크포인트가 없거나 오래됐다 — 재배포로 끊긴 세션이 되살아나는 중(또는 아직 못 살아남).
export const STALE_AFTER_MS = 90_000;
export function isRecovering(entry, now = Date.now()) {
  if (!entry || !["waiting", "holding", "exited"].includes(entry.state)) return false;
  return !Number.isFinite(entry.checkpoint_ms) || now - entry.checkpoint_ms > STALE_AFTER_MS;
}

export function stateLine(entry, now = Date.now()) {
  if (!entry || !entry.state || entry.state === "none") return null;
  if (isRecovering(entry, now)) return { text: RECOVERING, tone: "muted" };
  const trades = entry.trade_count > 0 ? [TRADES(entry.trade_count)] : [];
  switch (entry.state) {
    case "waiting": {
      // 시세가 아직 없는 행(러너 체크포인트 전·구 행)은 "ONE 0" 대신 종목·가격을 뺀다.
      const quote = Number.isFinite(entry.last_price) && entry.last_price > 0 ? `${base(entry.symbol)} ${fmtPrice(entry.last_price)}` : null;
      return { text: [WAITING, quote, ...trades].filter(Boolean).join(" · "), tone: "muted" };
    }
    case "holding": {
      const leg = (entry.legs || []).find((l) => l.in_position);
      // 잠긴 행은 서버가 entry_price 를 0으로 가린다 — 그런 값으로 "진입가 0"을 보여주지 않는다.
      const entryAt = leg && leg.entry_price > 0 ? ENTRY_AT(fmtPrice(leg.entry_price)) : null;
      return { text: [HOLDING, entryAt, ...trades].filter(Boolean).join(" · "), tone: "live" };
    }
    case "exited": {
      const kind = KIND[entry.last_fill_kind] || KIND.exit;
      const head = `${entry.last_fill_kst || ""} ${kind} ${fmtPct(entry.last_fill_return ?? 0)}`.trim();
      return { text: [head, cooldownLabel(entry.cooldown_until_ms, now), ...trades].join(" · "), tone: entry.last_fill_kind === "sl" ? "bad" : "good" };
    }
    case "halted":
      return { text: [HALTED, ...trades].join(" · "), tone: "warn" };
    case "stopped":
      return { text: [STOPPED(fmtPct(entry.return_pct ?? 0)), ...trades].join(" · "), tone: "muted" };
    default:
      return null;
  }
}

export function symbolsOf(items) {
  const out = [];
  for (const e of items || []) {
    if (e?.state !== "holding") continue;
    for (const l of e.legs || []) if (l.in_position && !out.includes(l.symbol)) out.push(l.symbol);
  }
  return out.slice(0, 30);
}
