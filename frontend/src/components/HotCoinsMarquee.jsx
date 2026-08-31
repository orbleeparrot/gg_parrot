import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { fmtPrice } from "../lib/format.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";

// Poll interval (ms). Server caches the aggregate, so this is cheap. Default 45s.
const POLL_MS = Number(import.meta.env?.VITE_HOTCOINS_POLL_MS) || 45000;

function Item({ coin, onPick, ariaHidden }) {
  const up = coin.change_pct >= 0;
  const color = up ? "text-green-600" : "text-red-600";
  return (
    <button
      type="button"
      aria-hidden={ariaHidden || undefined}
      tabIndex={ariaHidden ? -1 : 0}
      onClick={() => onPick(coin.symbol)}
      title={`${coin.symbol} 로 매크로 만들기`}
      className="inline-flex items-center gap-2 h-11 px-4 hover:bg-slate-100 rounded-md transition-colors"
    >
      <span className="t-label font-bold text-slate-900">{coin.base}</span>
      <span className={`t-label font-bold num ${color}`}>
        {up ? "▲" : "▼"}{Math.abs(coin.change_pct).toFixed(2)}%
      </span>
      <span className="t-caption text-slate-500 num">${fmtPrice(coin.last_price)}</span>
    </button>
  );
}

export default function HotCoinsMarquee() {
  const [coins, setCoins] = useState([]);
  const navigate = useNavigate();

  const load = useCallback(async (signal) => {
    const d = await api.hotCoins(10, { signal });
    setCoins(Array.isArray(d.coins) ? d.coins : []);
  }, []);
  useAdaptivePolling(load, { intervalMs: POLL_MS, maxIntervalMs: 10 * 60_000 });

  // Nothing to show yet -> hide the strip entirely (spec §1.4).
  if (!coins.length) return null;

  const pick = (symbol) => {
    // Prefill the builder with the full pair (e.g. XRPUSDT) via query param.
    navigate(`/builder?symbol=${encodeURIComponent(symbol)}`);
  };

  // Speed scales with count so the flow feels consistent regardless of list size.
  const duration = `${Math.max(24, coins.length * 4)}s`;
  const group = (hidden) =>
    coins.map((c) => <Item key={(hidden ? "b-" : "a-") + c.symbol} coin={c} onPick={pick} ariaHidden={hidden} />);

  return (
    <div className="site-marquee fixed bottom-0 inset-x-0 z-20 border-t border-slate-200 glass pb-[env(safe-area-inset-bottom)]">
      <div className="flex items-center">
        <div className="shrink-0 px-4 h-11 t-label font-bold text-slate-900 border-r border-slate-200 flex items-center gap-1">
          <span className="hidden sm:inline">오늘의 경주마</span>
          <span className="sm:hidden">급등</span>
        </div>

        <div className="ggp-marquee overflow-hidden flex-1">
          <div className="ggp-marquee-track" style={{ "--ggp-marquee-duration": duration }}>
            {group(false)}
            {group(true)}
          </div>
        </div>
      </div>
    </div>
  );
}
