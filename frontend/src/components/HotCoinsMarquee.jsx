import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fmtPrice } from "../lib/format.js";
import useHotCoins from "../hooks/useHotCoins.js";
import SelectMenu from "./SelectMenu.jsx";
import { EXCHANGE_META, formatPrice } from "../features/coinTrends/trendsFormat.js";
import {
  MARQUEE_EXCHANGES, MARQUEE_HOLD_MS, MARQUEE_STILL_MS, marqueeChoices, nextMarqueeExchange, shownMarqueeExchange,
} from "../lib/marqueeRotation.js";

// 하단 '오늘의 경주마' 띠 — 업비트 → 빗썸 → 바이낸스를 차례로 돌린다. 한 거래소의 TOP10 이 한 바퀴 다 지나가면
// 5초 뒤 다음 거래소로 넘어간다(마우스를 올려 멈춘 동안은 넘기지 않는다). 국내는 코인동향 경주마와 같은 목록(원화).
// 왼쪽 칸의 거래소를 누르면 위로 목록이 열리고, 고르면 그 거래소부터 다시 차례로 돈다(기억하지 않음, 2026-10-19 시안 B).
const LOGO_SRC = { upbit: "/exchanges/upbit.png", bithumb: "/exchanges/bithumb.png", binance: "/exchanges/binance.svg" };

const priceText = (coin, krw) => (krw ? `${formatPrice(coin.last_price, "KRW")}원` : `$${fmtPrice(coin.last_price)}`);
const reducedMotion = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

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
  const lists = {
    upbit: useHotCoins("upbit").coins,
    bithumb: useHotCoins("bithumb").coins,
    binance: useHotCoins("binance").coins,
  };
  const navigate = useNavigate();
  const [current, setCurrent] = useState(MARQUEE_EXCHANGES[0]);
  const exchange = shownMarqueeExchange(current, lists);
  const coins = exchange ? lists[exchange] : [];
  const krw = EXCHANGE_META[exchange]?.quote === "KRW";

  const listsRef = useRef(lists);
  listsRef.current = lists;
  const shownRef = useRef(exchange);
  shownRef.current = exchange;
  const holdRef = useRef(0);
  const pausedRef = useRef(false);
  const waitingRef = useRef(false);

  const advance = useCallback(() => {
    waitingRef.current = false;
    const next = nextMarqueeExchange(shownRef.current, listsRef.current);
    if (next) setCurrent(next);
  }, []);
  // 한 바퀴가 끝났다 → 5초 더 보여 주고 넘긴다. 그때 마우스를 올려 보고 있으면 내려놓을 때 넘긴다.
  // 5초를 기다리는 동안 다음 바퀴가 끝나도 다시 세지 않는다.
  const finishPass = useCallback(() => {
    if (holdRef.current) return;
    holdRef.current = setTimeout(() => {
      holdRef.current = 0;
      if (pausedRef.current) waitingRef.current = true;
      else advance();
    }, MARQUEE_HOLD_MS);
  }, [advance]);
  const pause = () => { pausedRef.current = true; };
  const resume = () => {
    pausedRef.current = false;
    if (waitingRef.current) advance();
  };
  // 고른 거래소로 바로 넘어가 처음부터 한 바퀴 — 기다리던 '다음 거래소'는 버린다.
  const choose = (name) => {
    clearTimeout(holdRef.current);
    holdRef.current = 0;
    waitingRef.current = false;
    setCurrent(name);
  };

  // 목록은 받은 거래소가 바뀔 때만 새로 만든다 — 시세를 다시 받을 때마다 바뀌면 열린 목록의 키보드 위치가 처음으로 돌아간다.
  const choiceKey = marqueeChoices(lists).join(",");
  const choices = useMemo(() => (choiceKey ? choiceKey.split(",") : []).map((name) => [name, (
    <span key={name} className="marquee-ex-option"><img src={LOGO_SRC[name]} alt="" width="16" height="16" />{EXCHANGE_META[name].name}</span>
  )]), [choiceKey]);

  // 띠가 흐르지 않는(움직임 줄이기) 환경은 시간으로 넘긴다.
  useEffect(() => {
    if (!exchange || !reducedMotion()) return undefined;
    const id = setTimeout(finishPass, MARQUEE_STILL_MS - MARQUEE_HOLD_MS);
    return () => clearTimeout(id);
  }, [exchange, finishPass]);
  useEffect(() => () => { clearTimeout(holdRef.current); holdRef.current = 0; }, []);

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
        {/* 거래소를 고르는 동안(목록 위 포함) 마우스가 올라가 있으면 다음 거래소로 넘기지 않는다. */}
        <div className="shrink-0 pl-2 pr-4 h-11 t-label font-bold text-slate-900 border-r border-slate-200 flex items-center gap-1.5"
          onMouseEnter={pause} onMouseLeave={resume}>
          <SelectMenu className="marquee-ex" value={exchange} onChange={choose} label="오늘의 경주마 거래소"
            options={choices}
            trigger={(
              <span className="marquee-ex-option">
                <img src={LOGO_SRC[exchange]} alt={EXCHANGE_META[exchange]?.name || ""} width="16" height="16" />
                <span className="hidden sm:inline">{EXCHANGE_META[exchange]?.name}</span>
              </span>
            )} />
          <span className="hidden sm:inline">오늘의 경주마</span>
          <span className="sm:hidden">급등</span>
        </div>

        <div className="ggp-marquee overflow-hidden flex-1"
          onMouseEnter={pause} onMouseLeave={resume} onFocus={pause} onBlur={resume}>
          {/* 거래소가 바뀌면 처음부터 다시 흐른다(key). 한 바퀴 = 목록 하나가 다 지나감. */}
          <div key={exchange} className="ggp-marquee-track" style={{ "--ggp-marquee-duration": duration }}
            onAnimationIteration={(event) => { if (event.target === event.currentTarget) finishPass(); }}>
            {group(false)}
            {group(true)}
          </div>
        </div>
      </div>
    </div>
  );
}
