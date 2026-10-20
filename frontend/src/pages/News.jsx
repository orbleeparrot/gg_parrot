import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import useNewsBriefings from "../hooks/useNewsBriefings.js";
import CoinBoard from "../features/coinTrends/CoinBoard.jsx";
import FeedPanel from "../features/coinTrends/FeedPanel.jsx";
import { MarketSection, SummarySection } from "../features/coinTrends/TopSections.jsx";
import { useFeedList, useRankings, useUsdKrw } from "../features/coinTrends/useTrendsData.js";
import { EXCHANGE_META, regionOf } from "../features/coinTrends/trendsFormat.js";
import { getTrendsExchange, setTrendsExchange } from "../lib/trendsExchange.js";
import "../features/coinTrends/CoinTrends.css";

// 코인동향 — 국내(업비트·빗썸 원화) / 해외(바이낸스 USDT) 판으로 페이지 전체가 바뀐다.
// 위: 오늘의 요약 · 시장·규제 한눈에. 아래: 코인 순위(경주마·거래대금·상승률·하락률·언급, 검색) · 소식(커뮤니티·뉴스).
// 코인을 고르면 순위 칸은 그 코인 정보로, 소식 칸은 그 코인 글로 바뀐다. 검색창의 X 가 메인으로 돌려보낸다.
const REGIONS = [
  { key: "kr", label: "국내", logos: ["upbit", "bithumb"] },
  { key: "global", label: "해외", logos: ["binance"] },
];
const LOGO_SRC = { upbit: "/exchanges/upbit.png", bithumb: "/exchanges/bithumb.png", binance: "/exchanges/binance.svg" };

function ModeSwitch({ region, onRegion }) {
  return (
    <div className="ct-mode" role="tablist" aria-label="시장">
      {REGIONS.map((item) => (
        <button key={item.key} type="button" role="tab" aria-selected={region === item.key}
          className={region === item.key ? "ct-mode-item is-on" : "ct-mode-item"} onClick={() => onRegion(item.key)}>
          <span className="ct-mode-logos" aria-hidden="true">
            {item.logos.map((name) => <img key={name} src={LOGO_SRC[name]} alt="" width="22" height="22" />)}
          </span>
          {item.label}
        </button>
      ))}
    </div>
  );
}

const symbolOf = (exchange, base) => (EXCHANGE_META[exchange].quote === "KRW" ? `KRW-${base}` : `${base}USDT`);
const reducedMotion = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function News() {
  // 마지막으로 고른 거래소에서 시작한다 — 하단 '오늘의 경주마' 띠도 같은 값을 따라간다.
  const [exchange, setExchange] = useState(getTrendsExchange);
  const [lastDomestic, setLastDomestic] = useState(() => (regionOf(getTrendsExchange()) === "kr" ? getTrendsExchange() : "upbit"));
  const [sort, setSort] = useState("race");
  const [coin, setCoin] = useState(null);
  const [keyword, setKeyword] = useState("");
  const [tab, setTab] = useState("community");
  const [source, setSource] = useState("");
  const [searching, setSearching] = useState(false);
  const boardRef = useRef(null);
  const region = regionOf(exchange);

  // 시장·규제는 하루 단위 자료라 10분 안에 돌아오면 다시 받지 않는다(기존 공유 캐시).
  const { states: marketStates, retry: retryMarket } = useNewsBriefings(
    ["market"], (_key, signal) => api.newsMarket({ signal }), 1, { freshMs: 10 * 60 * 1000 },
  );

  // 다른 거래소 시세는 검색하거나 코인을 고를 때만 받는다(다른 거래소에만 있는 코인 · 거래소별 가격).
  const needOthers = searching || !!coin;
  const rankings = useRankings(exchange);
  const upbit = useRankings("upbit", exchange === "upbit" || needOthers);
  const bithumb = useRankings("bithumb", exchange === "bithumb" || needOthers);
  const binance = useRankings("binance", exchange === "binance" || needOthers);
  const lists = useMemo(() => ({
    upbit: upbit.data?.coins || [], bithumb: bithumb.data?.coins || [], binance: binance.data?.coins || [],
  }), [upbit.data, bithumb.data, binance.data]);
  const usdkrw = useUsdKrw(!!coin);
  const coins = rankings.data?.coins;
  const coinsByBase = useMemo(() => new Map((coins || []).map((row) => [row.base, row])), [coins]);

  const symbol = coin ? symbolOf(exchange, coin) : "";
  const community = useFeedList("community", { region, exchange, symbol, q: keyword, source: tab === "community" ? source : "" });
  const news = useFeedList("news", { region, exchange, symbol, q: keyword, source: tab === "news" ? source : "" });
  // 거래소 공식 공지 — 지금은 빗썸만(공식 REST). 업비트는 공지 키를 연결하기 전까지 탭을 두지 않는다.
  const noticesOn = exchange === "bithumb";
  const notices = useFeedList("notices", { region, exchange, symbol, q: keyword, enabled: noticesOn });
  useEffect(() => { if (tab === "notices" && !noticesOn) setTab("community"); }, [tab, noticesOn]);

  // 언급 수 · 오늘의 요약은 코인을 골라 목록을 다시 받는 동안에도 마지막 값을 쓴다.
  const mentionsRef = useRef({});
  if (community.extra?.mentions) mentionsRef.current[exchange] = community.extra.mentions;
  const mentions = mentionsRef.current[exchange] || {};
  const briefRef = useRef({});
  if (news.extra?.collection) briefRef.current[region] = news;
  const briefNews = briefRef.current[region] || news;

  // 고른 코인이 다른 거래소에 없으면 메인으로 돌아간다.
  useEffect(() => {
    if (coin && coins?.length && !coinsByBase.has(coin)) setCoin(null);
  }, [coin, coins, coinsByBase]);

  // 고른 코인 · 검색어에 글이 있는 탭을 먼저 연다(커뮤니티 우선).
  const autoTabRef = useRef("");
  useEffect(() => {
    const key = coin || keyword ? `${exchange}|${coin || ""}|${keyword}` : "";
    if (!key || autoTabRef.current === key || community.loading || news.loading || (noticesOn && notices.loading)) return;
    autoTabRef.current = key;
    setTab(community.total ? "community" : news.total ? "news" : noticesOn && notices.total ? "notices" : "community");
  }, [coin, keyword, exchange, community.loading, community.total, news.loading, news.total, noticesOn, notices.loading, notices.total]);

  const revealBoard = useCallback(() => {
    const node = boardRef.current;
    if (!node) return;
    const top = node.getBoundingClientRect().top;
    if (top > window.innerHeight * 0.4 || top < -120) node.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  }, []);

  const changeExchange = useCallback((next) => {
    setExchange(next);
    setTrendsExchange(next);
    if (regionOf(next) === "kr") setLastDomestic(next);
    setSource("");
  }, []);
  const changeRegion = useCallback((next) => changeExchange(next === "kr" ? lastDomestic : "binance"), [changeExchange, lastDomestic]);
  const pickCoin = useCallback((base, onExchange) => {
    if (onExchange && onExchange !== exchange) changeExchange(onExchange);
    setCoin(base);
    setKeyword("");
    setSource("");
    setSearching(false);
    revealBoard();
  }, [changeExchange, exchange, revealBoard]);
  const pickKeyword = useCallback((query) => {
    setKeyword(query);
    setCoin(null);
    setSource("");
    setSearching(false);
    revealBoard();
  }, [revealBoard]);
  const clearSelection = useCallback(() => { setCoin(null); setKeyword(""); setSource(""); }, []);

  return (
    <div className="ct-page">
      <div className="ct-head-row">
        <h1 className="ct-page-title">오늘의 코인동향</h1>
        <ModeSwitch region={region} onRegion={changeRegion} />
      </div>
      <div className="ct-top">
        <SummarySection region={region} news={briefNews} />
        <MarketSection state={marketStates.market} onRetry={() => retryMarket("market")} />
      </div>
      <div className="ct-board" ref={boardRef}>
        <CoinBoard region={region} exchange={exchange} onExchange={changeExchange} rankings={rankings} lists={lists}
          sort={sort} onSort={setSort} mentions={mentions} coin={coin} onCoin={pickCoin} keyword={keyword}
          onKeyword={pickKeyword} onClearSelection={clearSelection} usdkrw={usdkrw}
          onSearchActive={(text) => setSearching(!!String(text).trim())} />
        <FeedPanel region={region} feeds={{ community, news, notices: noticesOn ? notices : null }} tab={tab} onTab={(name) => { setTab(name); setSource(""); }}
          source={source} onSource={setSource} coin={coin} keyword={keyword} coinsByBase={coinsByBase}
          onCoin={pickCoin} onClearCoin={clearSelection} />
      </div>
    </div>
  );
}
