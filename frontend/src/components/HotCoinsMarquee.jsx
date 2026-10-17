import { useNavigate } from "react-router-dom";
import { fmtPrice } from "../lib/format.js";
import useHotCoins from "../hooks/useHotCoins.js";
import { useTrendsExchange } from "../lib/trendsExchange.js";
import { EXCHANGE_META, formatPrice } from "../features/coinTrends/trendsFormat.js";

// 코인동향에서 고른 거래소를 따라간다 — 국내는 코인 순위의 경주마 탭과 같은 목록(원화), 바이낸스는 USDT.
const LOGO_SRC = { upbit: "/exchanges/upbit.png", bithumb: "/exchanges/bithumb.png", binance: "/exchanges/binance.svg" };

const priceText = (coin, krw) => (krw ? `${formatPrice(coin.last_price, "KRW")}원` : `$${fmtPrice(coin.last_price)}`);

function Item({ coin, krw, onPick, ariaHidden }) {
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
      <span className="t-caption text-slate-500 num">{priceText(coin, krw)}</span>
    </button>
  );
}

export default function HotCoinsMarquee() {
  const exchange = useTrendsExchange();
  const { coins } = useHotCoins(exchange);
  const navigate = useNavigate();
  const krw = EXCHANGE_META[exchange]?.quote === "KRW";

  // Nothing to show yet -> hide the strip entirely (spec §1.4).
  if (!coins.length) return null;

  const pick = (symbol) => {
    // Prefill the builder with the full pair (e.g. XRPUSDT · KRW-XRP); 국내는 거래소도 넘겨 기본값을 맞춘다.
    const onExchange = exchange === "binance" ? "" : `exchange=${exchange}&`;
    navigate(`/builder?${onExchange}symbol=${encodeURIComponent(symbol)}`);
  };

  // Speed scales with count so the flow feels consistent regardless of list size.
  const duration = `${Math.max(24, coins.length * 4)}s`;
  const group = (hidden) =>
    coins.map((c) => <Item key={(hidden ? "b-" : "a-") + c.symbol} coin={c} krw={krw} onPick={pick} ariaHidden={hidden} />);

  return (
    <div className="site-marquee fixed bottom-0 inset-x-0 z-20 border-t border-slate-200 glass pb-[env(safe-area-inset-bottom)]">
      <div className="flex items-center">
        <div className="shrink-0 px-4 h-11 t-label font-bold text-slate-900 border-r border-slate-200 flex items-center gap-1">
          <img src={LOGO_SRC[exchange]} alt={EXCHANGE_META[exchange]?.name || ""} title={`${EXCHANGE_META[exchange]?.name || ""} 기준`}
            width="16" height="16" className="w-4 h-4 rounded-full shrink-0 mr-1" />
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
