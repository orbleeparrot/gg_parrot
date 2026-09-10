import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import useNewsBriefings from "../hooks/useNewsBriefings.js";
import { newsCache } from "../lib/newsBriefings.js";
import { communityPostIdentity, communitySummaryPresentation, hasPendingTranslation, historicalNewsLabel, newsPublishedLabel, newsSourceLabel } from "../lib/newsBriefings.js";
import CoinIcon from "../components/CoinIcon.jsx";
import MarketCarousel from "../components/MarketCarousel.jsx";
import { AnnotatedText, TermChips } from "../components/NewsTerms.jsx";
import { PageHeader, Loading, ErrorNote } from "../components/Page.jsx";
import { splitSummary } from "../lib/summaryText.js";
import { layoutTreemap, racerWeight } from "../lib/treemap.js";
import "./NewsMobile.css";
import InfoTooltip from "../components/InfoTooltip.jsx";

const COIN_NEWS_CONCURRENCY = 2;
const HOT_COINS_CACHE_KEY = "hot-coins";
const RACER_NEWS_ROTATE_MS = 5_000;

function coinOf(symbol) {
  return (symbol || "").replace(/USDT$|BUSD$|USDC$/, "");
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toLocaleString("en-US", {
    maximumFractionDigits: number >= 1 ? 2 : 6,
  });
}

const compactVolumeFormatter = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });
function formatVolume(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return compactVolumeFormatter.format(number);
}

function errorMessage(reason) {
  return reason instanceof Error ? reason.message : String(reason);
}

function useCoinNewsBriefings(coins) {
  const { states: newsBySymbol, retry } = useNewsBriefings(
    coins.map((coin) => coin.symbol),
    (symbol, signal) => api.newsCoin(symbol, { signal }),
    COIN_NEWS_CONCURRENCY,
  );
  return { newsBySymbol, retry };
}

function TranslationPending({ data }) {
  const count = Number(data?.translation?.pending_count) || 0;
  return (
    <div className="news-racer-reader-state is-notice" role="status">
      <span>{count > 0 ? `${count}개 기사 제목을` : "기사 제목을"} 한국어로 번역하고 있어요. 완료되면 자동으로 표시해요.</span>
    </div>
  );
}

function BriefingSectionHeader({ id, title, description, count, countLabel, pendingLabel }) {
  return (
    <header className="news-briefing-section-head">
      <div className="news-briefing-section-title">
        <h2 id={id}>{title}</h2>
        <InfoTooltip text={description} label={`${title} 설명`} placement="bottom" />
      </div>
      <span className="news-briefing-section-status" aria-live="polite">
        {Number.isFinite(count) && count > 0 ? (
          <><strong className="num">{count}</strong><span>개 {countLabel}</span></>
        ) : (
          <span>{pendingLabel}</span>
        )}
      </span>
    </header>
  );
}

function MarketBriefing({ market, loading, error }) {
  const translationPending = hasPendingTranslation(market);
  const readerItems = useMemo(
    () => (market?.items || []).map((item) => ({
      id: communityPostIdentity(item) || item.url || item.title,
      title: item.title,
      source: newsSourceLabel(item),
      time: historicalNewsLabel(item) || newsPublishedLabel(item),
      image: item.image || "",
      url: item.article_url || item.url,
    })),
    [market],
  );

  return (
    <section className="news-briefing-section is-market" aria-labelledby="market-briefing-title">
      <BriefingSectionHeader
        id="market-briefing-title"
        title="시장·규제 한눈에"
        description="오늘 시장을 움직이는 정책과 주요 이슈. 화살표로 넘기면 가운데 기사가 선명해져요."
        count={market?.items?.length || 0}
        countLabel="헤드라인"
        pendingLabel="새 소식 확인 중"
      />

      {loading ? <Loading label="시장 브리핑을 준비하는 중…" /> : null}
      {error ? <ErrorNote>시장 뉴스를 불러오지 못했어요: {error}</ErrorNote> : null}

      {market ? (
        <>
          {readerItems.length > 0 ? (
            <MarketCarousel items={readerItems} />
          ) : !translationPending ? (
            <div className="news-reader-empty t-small text-slate-500">지금은 불러올 시장 헤드라인이 없어요.</div>
          ) : null}
          {translationPending ? <TranslationPending data={market} /> : null}

          {/* AI 요약은 페이지 머리(제목·기준일 아래)로 올라갔다. 여기엔 용어 칩만 남긴다. */}
          <TermChips texts={[market.overview, ...(market.items || []).map((item) => item.title)]} />
        </>
      ) : null}
    </section>
  );
}

// 컨테이너 크기 — 트리맵은 실제 종횡비로 나눠야 타일이 정사각형에 가깝다.
function useElementSize(ref) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) setSize({ width: Math.round(box.width), height: Math.round(box.height) });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

// 타일 맞춤 — 픽셀 크기로 글자 크기·순위 숫자·기사 제목 줄 수를 정한다.
// 기사는 타일당 한 건. 큰 타일은 글자를 키우고 줄 수를 늘려 채우고, 좁고 긴 타일은 줄 수를 늘려 제목이 잘리지 않게 한다.
const clampNum = (value, min, max) => Math.min(max, Math.max(min, value));
function tileFit(width, height) {
  const area = width * height;
  const scale = clampNum(Math.sqrt(area / 48_000), 0.6, 2); // 220×220 ≈ 1
  const ticker = Math.round(clampNum(22 * scale, 15, 40));
  const change = Math.round(clampNum(13 * scale, 10, 20));
  const icon = Math.round(clampNum(ticker * 1.15, 18, 44));
  const title = Math.round(clampNum(14 * scale, 11, 24)); // 작은 타일은 기사 글자도 11px 까지 줄인다
  const padX = width >= 160 ? 14 : 8;
  const padY = width >= 160 ? 12 : 8;
  const innerW = width - padX * 2;
  const tiny = width < 64 || height < 40; // 티커만
  const stackedHead = !tiny && innerW < 130; // 순위·로고 한 줄, 그 아래 티커·상승률 — 접지 않고 쌓는다
  const headH = tiny ? 16 : stackedHead ? icon + 4 + ticker + change * 1.2 + 2 : Math.max(icon, ticker + change * 1.2 + 2);
  const lineH = title * 1.4;
  const base = 6 + 14; // 제목 아래 간격 + 메타 한 줄
  // 기사가 먼저다 — 두 줄이 들어갈 자리를 확보한 뒤, 남으면 지표(한 줄, 줄바꿈 없음)를 넣는다.
  const metricsH = 30; // 위 선 10 + 글자 20
  const roomWithoutMetrics = height - padY * 2 - headH - 10 - 11 - 4;
  const newsPossible = !tiny && innerW >= 80 && Math.floor((roomWithoutMetrics - base) / lineH) >= 2;
  const showMetrics = !tiny && innerW >= 120 && (newsPossible
    ? roomWithoutMetrics - (2 * lineH + base) >= metricsH + 10
    : height >= headH + padY * 2 + 40);
  const metricsFull = innerW >= 260; // "현재가 n USDT · 거래대금 n만" 이 한 줄에 들어가는 폭
  const available = roomWithoutMetrics - (showMetrics ? metricsH + 10 : 0);
  const linesFit = Math.floor((available - base) / lineH);
  const showNews = newsPossible && linesFit >= 2;
  const lines = showNews ? clampNum(linesFit, 2, 6) : 0;
  return { ticker, change, icon, title, lines, showNews, showMetrics, metricsFull, padX, padY, tiny, stackedHead };
}

// 타일의 기사 한 건 — 모든 타일이 같은 tick 으로 다음 기사로 넘어가므로 화면 전체가 한 번에 바뀐다.
// key 가 바뀌면 새로 마운트되어 진입 애니메이션이 돌고, 그 시점이 모든 타일에서 같다.
function TileNews({ base, newsState, tick, onRetry, symbol, lines }) {
  const status = newsState?.status || "queued";
  const items = newsState?.data?.items || [];
  if (status === "queued" || status === "loading") {
    return <div className="news-map-news is-state">{base} 뉴스 준비 중</div>;
  }
  if (status === "error") {
    return (
      <div className="news-map-news is-state">
        뉴스를 불러오지 못했어요.
        <button type="button" onClick={() => onRetry(symbol)}>다시 시도</button>
      </div>
    );
  }
  if (!items.length) {
    return <div className="news-map-news is-state">최근 {base} 뉴스가 없어요.</div>;
  }
  const index = tick % items.length;
  const item = items[index];
  const time = historicalNewsLabel(item) || newsPublishedLabel(item);
  // 제목이 짧아 줄이 남으면 커뮤니티 글의 본문 요약으로 채운다(뉴스 기사는 요약이 없어 제목만).
  const summary = communitySummaryPresentation(item);
  const excerptLines = summary?.status === "ready" && lines >= 4 ? lines - 2 : 0;
  return (
    <div className="news-map-news" style={{ "--tile-excerpt-lines": excerptLines, "--tile-title-lines": excerptLines > 0 ? 2 : lines }}>
      <a
        key={`${index}-${item.url || item.title}`}
        className="news-map-news-item"
        href={item.url || undefined}
        target="_blank"
        rel="noreferrer noopener"
        aria-label={`${base} 뉴스 ${index + 1}/${items.length}: ${item.title}`}
      >
        <span className="news-map-news-title">{item.title}</span>
        {excerptLines > 0 ? <span className="news-map-news-excerpt">{summary.text}</span> : null}
        <span className="news-map-news-meta">
          <span className="news-map-news-source">{newsSourceLabel(item)}</span>
          {time ? <span className="news-map-news-time">{time}</span> : null}
          <span className="news-map-news-count num" aria-hidden="true">{index + 1}/{items.length}</span>
        </span>
      </a>
    </div>
  );
}

// 경주마 트리맵 — 한 직사각형을 상승률 비율로 나눈 벤토. 타일은 유리(§7 상단바와 같은 값), 색면 없음.
function RacerTreemap({ coins, newsBySymbol, onRetry, tick }) {
  const ref = useRef(null);
  const size = useElementSize(ref);
  const rects = useMemo(() => layoutTreemap(
    coins.map((coin, index) => ({ weight: racerWeight(coin.change_pct), coin, rank: index + 1 })),
    size.width || 16,
    size.height || 9,
  ), [coins, size.width, size.height]);

  return (
    <div ref={ref} className="news-racer-map" role="list" aria-label="경주마 상승률 지도 — 넓을수록 오늘 많이 오른 종목">
      {size.width > 0 ? rects.map(({ x, y, width, height, item }) => {
        const { coin, rank } = item;
        const base = coinOf(coin.symbol);
        const change = Number(coin.change_pct) || 0;
        const changeTone = change > 0 ? "is-up" : change < 0 ? "is-down" : "is-flat";
        const fit = tileFit(width * size.width - 8, height * size.height - 8);
        return (
          <article
            key={coin.symbol}
            role="listitem"
            className={`news-map-tile ${fit.tiny ? "is-tiny" : ""} ${fit.stackedHead ? "is-stacked" : ""}`}
            style={{
              left: `calc(${x * 100}% + 4px)`,
              top: `calc(${y * 100}% + 4px)`,
              width: `calc(${width * 100}% - 8px)`,
              height: `calc(${height * 100}% - 8px)`,
              padding: `${fit.padY}px ${fit.padX}px`,
              "--tile-ticker": `${fit.ticker}px`,
              "--tile-change": `${fit.change}px`,
              "--tile-icon": `${fit.icon}px`,
              "--tile-title": `${fit.title}px`,
              "--tile-lines": fit.lines,
            }}
            aria-label={`${rank}위 ${base} ${change > 0 ? "+" : ""}${change.toFixed(2)}%`}
          >
            <header className="news-map-head">
              <span className="news-map-rank num" aria-hidden="true">{String(rank).padStart(2, "0")}</span>
              <CoinIcon symbol={coin.symbol} size={fit.icon} className="news-map-logo" alt="" />
              <Link to={`/builder?symbol=${encodeURIComponent(coin.symbol)}`} className="news-map-ticker num" title={`${base} 매크로 만들기`}>{base}</Link>
              <span className={`news-map-change num ${changeTone}`}>{change > 0 ? "+" : ""}{change.toFixed(2)}%</span>
            </header>
            {fit.showMetrics ? (
              <p className="news-map-metrics">
                <span><small>현재가</small><b className="num">{formatPrice(coin.last_price)}</b><em>USDT</em></span>
                {fit.metricsFull ? (
                  <span><small>거래대금</small><b className="num">{formatVolume(coin.quote_volume)}</b></span>
                ) : null}
              </p>
            ) : null}
            {fit.showNews ? (
              <TileNews base={base} symbol={coin.symbol} newsState={newsBySymbol[coin.symbol]} tick={tick} onRetry={onRetry} lines={fit.lines} />
            ) : null}
          </article>
        );
      }) : null}
    </div>
  );
}

// Narrow screens use readable market rows; choosing a coin opens its full
// headlines below the list without changing the desktop treemap's layout.
function RacerMobileList({ coins, newsBySymbol, onRetry }) {
  const [selectedSymbol, setSelectedSymbol] = useState(coins[0]?.symbol);
  const reader = useRef(null);
  const selected = coins.find((coin) => coin.symbol === selectedSymbol) || coins[0];
  if (!selected) return null;
  const base = coinOf(selected.symbol);
  const newsState = newsBySymbol[selected.symbol];
  const status = newsState?.status || "queued";
  const items = newsState?.data?.items || [];
  const pending = hasPendingTranslation(newsState?.data);

  function choose(symbol) {
    setSelectedSymbol(symbol);
    window.requestAnimationFrame(() => reader.current?.scrollIntoView({
      block: "start",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    }));
  }

  return (
    <div className="news-racer-mobile">
      <div className="news-racer-mobile-columns" aria-hidden="true"><span>순위</span><span>코인</span><span>가격 · USDT</span><span>24시간</span></div>
      <ol className="news-racer-mobile-list" aria-label="경주마 상승률 순위">
        {coins.map((coin, index) => {
          const symbol = coinOf(coin.symbol);
          const prefixed = symbol.match(/^(\d+)([A-Z].*)$/);
          const change = Number(coin.change_pct) || 0;
          const tone = change > 0 ? "is-up" : change < 0 ? "is-down" : "is-flat";
          const changeText = `${change > 0 ? "+" : ""}${change.toFixed(2)}%`;
          return (
            <li key={coin.symbol}>
              <button type="button" className="news-racer-mobile-row" aria-pressed={selected.symbol === coin.symbol} aria-controls="news-racer-mobile-reader" aria-label={`${index + 1}위 ${symbol}, ${formatPrice(coin.last_price)} USDT, ${changeText}, 뉴스 보기`} onClick={() => choose(coin.symbol)}>
                <span className="news-racer-mobile-rank num">{index + 1}</span>
                <span className="news-racer-mobile-coin"><CoinIcon symbol={coin.symbol} size={24} alt="" /><b className="num">{prefixed ? <>{prefixed[1]}<wbr />{prefixed[2]}</> : symbol}</b></span>
                <span className="news-racer-mobile-price num">{formatPrice(coin.last_price)}</span>
                <span className={`news-racer-mobile-change num ${tone}`}>{changeText}</span>
              </button>
            </li>
          );
        })}
      </ol>
      <section ref={reader} id="news-racer-mobile-reader" className="news-racer-mobile-reader" aria-labelledby="news-racer-mobile-title">
        <header className="news-racer-mobile-reader-head">
          <h3 id="news-racer-mobile-title"><span className="num">{base}</span> 뉴스</h3>
          <Link to={`/builder?symbol=${encodeURIComponent(selected.symbol)}`}>매크로 만들기</Link>
        </header>
        {status === "queued" || status === "loading" ? <p className="news-racer-mobile-state" role="status">{base} 뉴스를 불러오는 중…</p> : null}
        {status === "error" ? <div className="news-racer-mobile-state is-error" role="alert"><p>뉴스를 불러오지 못했어요.</p><button type="button" className="btn btn-s btn-secondary" onClick={() => onRetry(selected.symbol)}>다시 시도</button></div> : null}
        {status === "success" && !items.length && !pending ? <p className="news-racer-mobile-state">최근 {base} 뉴스가 없어요.</p> : null}
        {items.length > 0 ? <MobileArticleList key={selected.symbol} base={base} items={items} /> : null}
        {pending ? <TranslationPending data={newsState.data} /> : null}
      </section>
    </div>
  );
}

function MobileArticleList({ base, items }) {
  const listRef = useRef(null);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return undefined;
    list.scrollTop = 0;

    const measure = () => {
      const visibleRows = [...list.children].slice(0, 3);
      const height = visibleRows.reduce((sum, row) => sum + row.getBoundingClientRect().height, 0);
      list.style.setProperty("--news-racer-mobile-articles-height", `${Math.ceil(height)}px`);
    };

    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    [...list.children].slice(0, 3).forEach((row) => observer.observe(row));
    return () => observer.disconnect();
  }, [items]);

  return (
    <ul
      ref={listRef}
      className="news-racer-mobile-articles"
      aria-label={`${base} 뉴스 목록`}
      tabIndex={items.length > 3 ? 0 : undefined}
    >
          {items.map((item, index) => {
            const summary = communitySummaryPresentation(item);
            return (
              <li key={communityPostIdentity(item) || item.url || `${item.title}-${index}`}>
                <a href={item.article_url || item.url || undefined} target="_blank" rel="noreferrer noopener">
                  <strong>{item.title}</strong>
                  <span className="news-racer-mobile-article-meta"><span>{newsSourceLabel(item)}</span><span>{historicalNewsLabel(item) || newsPublishedLabel(item)}</span></span>
                </a>
                {summary ? <div className="news-racer-mobile-summary"><span>{summary.label}</span>{summary.text ? <p>{summary.text}</p> : null}</div> : null}
              </li>
            );
          })}
    </ul>
  );
}

function RacerBriefing({ coins, loading, error }) {
  const { newsBySymbol, retry } = useCoinNewsBriefings(coins);
  const [tick, setTick] = useState(0);
  const termTexts = coins.flatMap((coin) => (
    newsBySymbol[coin.symbol]?.data?.items || []
  ).map((item) => item.title));

  // 한 박자 — 모든 타일의 헤드라인이 같은 순간에 다음 기사로 넘어간다. 탭이 숨겨지면 멈춘다.
  useEffect(() => {
    if (!coins.length) return undefined;
    let timer = 0;
    const start = () => {
      window.clearInterval(timer);
      timer = window.setInterval(() => setTick((value) => value + 1), RACER_NEWS_ROTATE_MS);
    };
    const onVisibility = () => {
      if (document.hidden) window.clearInterval(timer);
      else start();
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [coins.length]);

  return (
    <section className="news-briefing-section is-racers" aria-labelledby="racer-briefing-title">
      <BriefingSectionHeader
        id="racer-briefing-title"
        title="경주마 동향"
        description="24시간 상승률 순위와 종목별 뉴스를 확인해요."
        count={coins.length}
        countLabel="종목"
        pendingLabel="시장 확인 중"
      />

      {loading ? <Loading label="오늘의 경주마를 확인하는 중…" /> : null}
      {error ? <ErrorNote>경주마 정보를 불러오지 못했어요: {error}</ErrorNote> : null}

      {!loading && !error && coins.length === 0 ? (
        <div className="news-reader-empty t-small text-slate-500">지금은 보여줄 경주마가 없어요.</div>
      ) : null}

      {coins.length > 0 ? (
        <>
          <RacerTreemap coins={coins} newsBySymbol={newsBySymbol} onRetry={retry} tick={tick} />
          <RacerMobileList coins={coins} newsBySymbol={newsBySymbol} onRetry={retry} />
          <TermChips texts={termTexts} />
        </>
      ) : null}
    </section>
  );
}

export default function News() {
  // 시장 뉴스는 하루 단위 자료라 10분 안에 돌아오면 다시 받지 않는다.
  const { states: marketStates } = useNewsBriefings(
    ["market"], (_key, signal) => api.newsMarket({ signal }), 1, { freshMs: 10 * 60 * 1000 },
  );
  const marketState = marketStates.market;
  // 기사 사진(og:image)은 서버가 배경에서 채운다 — 아직이면 몇 번 더 조용히 받아 온다.
  const [marketRefresh, setMarketRefresh] = useState({ data: null, attempts: 0 });
  const market = marketRefresh.data || marketState?.data || null;
  const imagesPending = market?.image_status === "pending" && marketRefresh.attempts < 3;
  useEffect(() => {
    if (!imagesPending) return undefined;
    let alive = true;
    const timer = window.setTimeout(() => {
      api.newsMarket().then((next) => {
        if (alive) setMarketRefresh((current) => ({ data: next, attempts: current.attempts + 1 }));
      }).catch(() => {
        if (alive) setMarketRefresh((current) => ({ ...current, attempts: current.attempts + 1 }));
      });
    }, 7_000);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [imagesPending, marketRefresh.attempts]);
  const marketLoading = !marketState || ["queued", "loading"].includes(marketState.status);
  const marketError = marketState?.error || "";
  // 경주마 목록도 캐시로 먼저 그린다 — 돌아온 순간 트리맵 자리가 잡히고, 최신 순위는 조용히 갱신된다.
  const [coins, setCoins] = useState(() => newsCache.get(HOT_COINS_CACHE_KEY)?.data?.coins || []);
  const [coinsLoading, setCoinsLoading] = useState(() => !newsCache.get(HOT_COINS_CACHE_KEY));
  const [coinsError, setCoinsError] = useState("");

  useEffect(() => {
    let alive = true;

    const controller = new AbortController();

    api.hotCoins(10, { signal: controller.signal })
      .then((response) => {
        const next = response.coins || [];
        newsCache.set(HOT_COINS_CACHE_KEY, { coins: next });
        if (alive) setCoins(next);
      })
      .catch((reason) => {
        // 캐시로 이미 그려져 있으면 갱신 실패를 굳이 알리지 않는다.
        if (alive && reason?.name !== "AbortError" && !newsCache.get(HOT_COINS_CACHE_KEY)) setCoinsError(errorMessage(reason));
      })
      .finally(() => {
        if (alive) setCoinsLoading(false);
      });

    return () => {
      alive = false;
      controller.abort();
    };
  }, []);

  const summary = market?.overview ? splitSummary(market.overview) : null;

  return (
    <div className="news-briefing-page">
      {/* 위 줄: 왼쪽은 제목·기준일·AI 요약, 오른쪽은 시장·규제 헤드라인. 아래 줄: 경주마 트리맵. */}
      <div className="news-top">
      <div className="news-top-copy">
      {/* AI 요약이 있으면 그날의 내용이 머리 본문이 된다 — 첫 줄은 굵은 리드, 나머지는 본문.
          없으면 예전 설명문으로 돌아간다. */}
      <PageHeader
        title="오늘의 코인동향"
        meta={market?.as_of ? <>기준 <span className="num">{market.as_of}</span> · KST</> : null}
        description={summary ? undefined : "시장·규제와 활발히 움직이는 코인을 두 개의 브리핑으로 나눠 읽어요."}
      >
        {summary ? (
          <div className="page-head-lead">
            <p className="page-head-lead-first"><AnnotatedText text={summary.lead} /></p>
            {summary.body.map((line, index) => (
              <p key={index}><AnnotatedText text={line} /></p>
            ))}
          </div>
        ) : null}
      </PageHeader>
      {/* 고지는 요약 바로 뒤가 아니라 왼쪽 열의 맨 아래 — 오른쪽 열 바닥과 줄을 맞춘다. */}
      <p className="news-top-note">
        {summary
          ? "AI가 오늘 헤드라인만 근거로 쓴 요약이에요. 경주마 선정과 뉴스는 참고용이며 투자 권유가 아니에요."
          : "경주마 선정과 뉴스는 참고용이며 투자 권유가 아니에요."}
      </p>
      </div>
      <MarketBriefing market={market} loading={marketLoading} error={marketError} />
      </div>

      <div className="news-briefing-grid">
        <RacerBriefing coins={coins} loading={coinsLoading} error={coinsError} />
      </div>
    </div>
  );
}
