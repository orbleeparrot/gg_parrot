import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import useNewsBriefings from "../hooks/useNewsBriefings.js";
import { communityPostIdentity, communitySummaryPresentation, hasPendingTranslation, historicalNewsLabel, newsPublishedLabel, newsSourceLabel } from "../lib/newsBriefings.js";
import CoinIcon from "../components/CoinIcon.jsx";
import NewsBriefingReader from "../components/NewsBriefingReader.jsx";
import { AnnotatedText, TermChips } from "../components/NewsTerms.jsx";
import { PageHeader, Loading, ErrorNote } from "../components/Page.jsx";
import { splitSummary } from "../lib/summaryText.js";
import { layoutTreemap, racerWeight } from "../lib/treemap.js";

const COIN_NEWS_CONCURRENCY = 2;
const RACER_NEWS_ROTATE_MS = 5_000;
const compactVolumeFormatter = new Intl.NumberFormat("ko-KR", {
  notation: "compact",
  maximumFractionDigits: 1,
});

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

function Disclaimer({ text }) {
  return (
    <div className="news-briefing-disclaimer t-caption text-slate-700">
      <b className="text-slate-900">주의 · </b>{text}
    </div>
  );
}

function BriefingSectionHeader({ id, title, description, count, countLabel, pendingLabel }) {
  const tooltipId = `${id}-description`;

  return (
    <header className="news-briefing-section-head">
      <div className="news-briefing-section-title">
        <h2 id={id}>{title}</h2>
        <span className="news-briefing-info">
          <button type="button" aria-label={`${title} 설명`} aria-describedby={tooltipId}>i</button>
          <span id={tooltipId} role="tooltip">{description}</span>
        </span>
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
      community: item.content_type === "community" ? { author: item.author, source: item.source, summary: communitySummaryPresentation(item) } : null,
      url: item.url,
    })),
    [market],
  );

  return (
    <section className="news-briefing-section is-market" aria-labelledby="market-briefing-title">
      <BriefingSectionHeader
        id="market-briefing-title"
        title="시장·규제 한눈에"
        description="오늘 시장을 움직이는 정책과 주요 이슈를 헤드라인 근거로 읽어요."
        count={market?.items?.length || 0}
        countLabel="헤드라인"
        pendingLabel="새 소식 확인 중"
      />

      {loading ? <Loading label="시장 브리핑을 준비하는 중…" /> : null}
      {error ? <ErrorNote>시장 뉴스를 불러오지 못했어요: {error}</ErrorNote> : null}

      {market ? (
        <>
          {readerItems.length > 0 || !translationPending ? <NewsBriefingReader
            key={market.updated_at || market.as_of || "market"}
            items={readerItems}
            ariaLabel="현재 읽는 시장·규제 헤드라인"
            empty="지금은 불러올 시장 헤드라인이 없어요."
            queueLabel="헤드라인 읽는 순서"
            rotateMs={5_000}
            rowHeight={readerItems.some((item) => item.community) ? 76 : 48}
          /> : null}
          {translationPending ? <TranslationPending data={market} /> : null}

          {/* AI 요약은 페이지 머리(제목·기준일 아래)로 올라갔다. 여기엔 용어 칩만 남긴다. */}
          <TermChips texts={[market.overview, ...(market.items || []).map((item) => item.title)]} />
          {market.disclaimer ? <Disclaimer text={market.disclaimer} /> : null}
        </>
      ) : null}
    </section>
  );
}

const RacerNewsBriefing = memo(function RacerNewsBriefing({ coin, rank, newsState, onRetry, rotationTick }) {
  const base = coinOf(coin.symbol);
  const data = newsState?.data || null;
  const status = newsState?.status || "queued";
  const translationPending = hasPendingTranslation(data);
  const change = Number(coin.change_pct) || 0;
  const changeTone = change > 0 ? "is-up" : change < 0 ? "is-down" : "is-flat";
  const changePrefix = change > 0 ? "+" : "";
  const headingId = `racer-${coin.symbol.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const readerItems = useMemo(
    () => (data?.items || []).map((item) => ({
      id: communityPostIdentity(item) || item.url || item.title,
      title: item.title,
      source: newsSourceLabel(item),
      rowLabel: item.is_historical
        ? `${historicalNewsLabel(item)} · ${newsSourceLabel(item)}` : undefined,
      time: historicalNewsLabel(item) || newsPublishedLabel(item),
      community: item.content_type === "community" ? { author: item.author, source: item.source, summary: communitySummaryPresentation(item) } : null,
      url: item.url,
    })),
    [data],
  );

  return (
    <article className="news-racer-briefing" aria-labelledby={headingId}>
      <header className="news-racer-briefing-head">
        <div className="news-racer-identity">
          <span className="news-racer-rank" aria-label={`${rank}위`}>
            <strong className="num">{String(rank).padStart(2, "0")}</strong><small>위</small>
          </span>
          <CoinIcon symbol={coin.symbol} size={32} className="news-racer-logo" alt="" />
          <div className="news-racer-ticker">
            <h3 id={headingId} className="num">{base}</h3>
          </div>
          <div className="news-racer-identity-actions">
            <strong className={`news-racer-change num ${changeTone}`}>
              {changePrefix}{change.toFixed(2)}%
            </strong>
          </div>
        </div>
        <div className="news-racer-metrics">
          <span>
            <small>현재가</small>
            <strong><b className="num">{formatPrice(coin.last_price)}</b><em>USDT</em></strong>
          </span>
          <span>
            <small>24시간 거래대금</small>
            <strong><b className="num">{formatVolume(coin.quote_volume)}</b><em>USDT</em></strong>
          </span>
        </div>
      </header>

      {status === "queued" || status === "loading" ? (
        <div className="news-racer-reader-state" role="status">
          <strong>{base} 뉴스 브리핑 준비 중</strong>
          <span>{status === "queued" ? "순위대로 뉴스를 불러오고 있어요." : "최신 원문을 확인하고 있어요."}</span>
        </div>
      ) : null}

      {status === "error" ? (
        <div className="news-racer-reader-state is-error" role="alert">
          <strong>{base} 뉴스를 불러오지 못했어요.</strong>
          <span>{newsState.error}</span>
          <button type="button" onClick={() => onRetry(coin.symbol)}>다시 시도</button>
        </div>
      ) : null}

      {status === "success" && readerItems.length === 0 && !translationPending ? (
        <div className="news-racer-reader-state">
          <strong>{base} 관련 최근 뉴스가 없어요.</strong>
          <span>새 원문이 수집되면 이 자리에 브리핑이 나타나요.</span>
        </div>
      ) : null}

      {readerItems.length > 0 ? (
        <NewsBriefingReader
          key={data.updated_at || data.as_of || coin.symbol}
          items={readerItems}
          ariaLabel={`${data.coin_name || base} 뉴스`}
          queueLabel={`${base} 뉴스`}
          rotateMs={RACER_NEWS_ROTATE_MS}
          syncTick={rotationTick}
          rowHeight={readerItems.some((item) => item.community) ? 108 : 92}
          visibleRows={3}
          queueOnly
        />
      ) : null}

      {translationPending ? <TranslationPending data={data} /> : null}

      {status === "success" && data?.stale ? (
        <div className="news-racer-reader-state is-notice" role="status">
          <span>최신 소식을 가져오지 못해 마지막으로 수집한 뉴스를 표시하고 있어요.</span>
        </div>
      ) : null}

      <footer className="news-racer-footer">
        <Link to={`/builder?symbol=${encodeURIComponent(coin.symbol)}`} className="news-racer-builder-link">
          <span>매크로 만들기</span><span aria-hidden="true">→</span>
        </Link>
      </footer>
    </article>
  );
});

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

// 타일 밀도 — 넓이에 따라 보여줄 정보량을 줄인다(xl: 로고·가격까지, xs: 티커만).
function tileDensity(width, height) {
  if (width >= 220 && height >= 150) return "is-xl";
  if (width >= 150 && height >= 96) return "is-lg";
  if (width >= 96 && height >= 64) return "is-md";
  if (width >= 64 && height >= 40) return "is-sm";
  return "is-xs";
}

// 색 농도 — 상승률의 로그 비율(최대 대비)을 5단계로. 순위 편차가 커도(4%·112%) 고르게 퍼진다.
function tileTier(change, maxChange) {
  if (!(change > 0)) return 0;
  const t = Math.log1p(change) / Math.log1p(Math.max(maxChange, change, 1));
  return Math.min(5, Math.max(1, Math.ceil(t * 5)));
}

// 경주마 트리맵 — 한 직사각형을 상승률 비율로 나눈 벤토. 타일을 누르면 아래 브리핑이 그 코인으로 바뀐다.
function RacerTreemap({ coins, selected, onSelect }) {
  const ref = useRef(null);
  const size = useElementSize(ref);
  const maxChange = coins.reduce((acc, coin) => Math.max(acc, Number(coin.change_pct) || 0), 0);
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
        const tier = tileTier(change, maxChange);
        const density = tileDensity(width * size.width, height * size.height);
        const isSelected = selected === coin.symbol;
        return (
          <button
            key={coin.symbol}
            type="button"
            role="listitem"
            className={`news-map-tile ${density} tier-${tier} ${isSelected ? "is-selected" : ""}`}
            style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }}
            aria-pressed={isSelected}
            aria-label={`${rank}위 ${base} ${change > 0 ? "+" : ""}${change.toFixed(2)}%`}
            title={`${rank}위 ${base} · ${change > 0 ? "+" : ""}${change.toFixed(2)}%`}
            onClick={() => onSelect(coin.symbol)}
          >
            <span className="news-map-rank num" aria-hidden="true">{String(rank).padStart(2, "0")}</span>
            <CoinIcon symbol={coin.symbol} size={density === "is-xl" ? 36 : 24} className="news-map-logo" alt="" />
            <span className="news-map-ticker num">{base}</span>
            <span className="news-map-change num">{change > 0 ? "+" : ""}{change.toFixed(2)}%</span>
            <span className="news-map-price num">{formatPrice(coin.last_price)} <small>USDT</small></span>
          </button>
        );
      }) : null}
    </div>
  );
}

function RacerBriefing({ coins, loading, error }) {
  const { newsBySymbol, retry } = useCoinNewsBriefings(coins);
  const [selected, setSelected] = useState("");
  const termTexts = coins.flatMap((coin) => (
    newsBySymbol[coin.symbol]?.data?.items || []
  ).map((item) => item.title));

  // 선택이 없거나 목록에서 사라지면 1위로 되돌린다.
  const selectedIndex = coins.findIndex((coin) => coin.symbol === selected);
  const focusIndex = selectedIndex >= 0 ? selectedIndex : 0;
  const focusCoin = coins[focusIndex] || null;

  return (
    <section className="news-briefing-section is-racers" aria-labelledby="racer-briefing-title">
      <BriefingSectionHeader
        id="racer-briefing-title"
        title="경주마 동향"
        description="오늘 많이 오른 종목일수록 넓은 자리를 차지해요. 타일을 누르면 그 종목의 뉴스가 아래에 펼쳐져요."
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
          <RacerTreemap coins={coins} selected={focusCoin?.symbol} onSelect={setSelected} />
          {focusCoin ? (
            <div className="news-racer-focus">
              <RacerNewsBriefing
                key={focusCoin.symbol}
                coin={focusCoin}
                rank={focusIndex + 1}
                newsState={newsBySymbol[focusCoin.symbol]}
                onRetry={retry}
              />
            </div>
          ) : null}
          <TermChips texts={termTexts} />
        </>
      ) : null}
    </section>
  );
}

export default function News() {
  const { states: marketStates } = useNewsBriefings(
    ["market"], (_key, signal) => api.newsMarket({ signal }), 1,
  );
  const marketState = marketStates.market;
  const market = marketState?.data || null;
  const marketLoading = !marketState || ["queued", "loading"].includes(marketState.status);
  const marketError = marketState?.error || "";
  const [coins, setCoins] = useState([]);
  const [coinsLoading, setCoinsLoading] = useState(true);
  const [coinsError, setCoinsError] = useState("");

  useEffect(() => {
    let alive = true;

    const controller = new AbortController();

    api.hotCoins(10, { signal: controller.signal })
      .then((response) => {
        if (alive) setCoins(response.coins || []);
      })
      .catch((reason) => {
        if (alive && reason?.name !== "AbortError") setCoinsError(errorMessage(reason));
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
        eyebrow="MARKET NEWSROOM"
        title="오늘의 코인동향"
        meta={market?.as_of ? <>기준 <span className="num">{market.as_of}</span> · KST</> : null}
        description={summary ? undefined : "시장·규제와 활발히 움직이는 코인을 두 개의 브리핑으로 나눠 읽어요."}
        note={summary
          ? "AI가 오늘 헤드라인만 근거로 쓴 요약이에요. 경주마 선정과 뉴스는 참고용이며 투자 권유가 아니에요."
          : "경주마 선정과 뉴스는 참고용이며 투자 권유가 아니에요."}
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
      </div>
      <MarketBriefing market={market} loading={marketLoading} error={marketError} />
      </div>

      <div className="news-briefing-grid">
        <RacerBriefing coins={coins} loading={coinsLoading} error={coinsError} />
      </div>
    </div>
  );
}
