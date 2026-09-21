import { COOLDOWN, ENTRY_AT, HALTED, HOLDING, KIND, REENTRY, STOPPED, TRADES, WAITING } from "./leaderboardCopy.js";

const fmtPct = (v) => `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
const fmtPrice = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 4 });
const base = (symbol) => String(symbol || "").replace(/USDT$/, "");

export function isLive(entry, prices) {
  if (entry?.state !== "holding" || !(entry.virtual_balance > 0)) return false;
  const held = (entry.legs || []).filter((l) => l.in_position);
  return held.length > 0 && held.every((l) => Number.isFinite(prices?.[l.symbol]));
}

export function liveReturn(entry, prices) {
  if (!isLive(entry, prices)) return entry?.return_pct ?? null;
  const delta = (entry.legs || []).filter((l) => l.in_position)
    .reduce((sum, l) => sum + l.qty * (prices[l.symbol] - l.last_price) * (l.dir || 1), 0);
  const equity = entry.equity + delta;
  return Math.round(((equity - entry.virtual_balance) / entry.virtual_balance) * 100 * 100) / 100;
}

export function cooldownLabel(untilMs, now = Date.now()) {
  if (!untilMs || untilMs <= now) return REENTRY;
  return COOLDOWN(Math.ceil((untilMs - now) / 60_000));
}

export function stateLine(entry, now = Date.now()) {
  if (!entry || !entry.state || entry.state === "none") return null;
  const trades = entry.trade_count > 0 ? [TRADES(entry.trade_count)] : [];
  switch (entry.state) {
    case "waiting":
      return { text: [`${WAITING} · ${base(entry.symbol)} ${fmtPrice(entry.last_price ?? 0)}`, ...trades].join(" · "), tone: "muted" };
    case "holding": {
      const leg = (entry.legs || []).find((l) => l.in_position);
      return { text: [HOLDING, leg ? ENTRY_AT(fmtPrice(leg.entry_price)) : null, ...trades].filter(Boolean).join(" · "), tone: "live" };
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
