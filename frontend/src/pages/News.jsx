import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import CoinIcon from "../components/CoinIcon.jsx";
import NewsBriefingReader from "../components/NewsBriefingReader.jsx";
import { AnnotatedText, TermChips } from "../components/NewsTerms.jsx";
import { Loading, ErrorNote } from "../components/Page.jsx";

const COIN_NEWS_CONCURRENCY = 3;
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

function requestCoinNews(symbol) {
  return api.newsCoin(symbol);
}

function useCoinNewsBriefings(coins) {
  const [newsBySymbol, setNewsBySymbol] = useState({});
  const generationRef = useRef(0);
  const symbolsKey = coins.map((coin) => coin.symbol).join("|");

  useEffect(() => {
    const symbols = symbolsKey ? symbolsKey.split("|") : [];
    const generation = generationRef.current + 1;
    generationRef.current = generation;

    if (symbols.length === 0) {
      setNewsBySymbol({});
      return undefined;
    }

    setNewsBySymbol((current) => {
      const next = {};
      for (const symbol of symbols) {
        const previous = current[symbol];
        next[symbol] = previous?.status === "success"
          ? previous
          : { status: "queued", data: null, error: "" };
      }
      return next;
    });

    let cursor = 0;
    async function runWorker() {
      while (cursor < symbols.length && generationRef.current === generation) {
        const symbol = symbols[cursor];
        cursor += 1;
        setNewsBySymbol((current) => ({
          ...current,
          [symbol]: { status: "loading", data: current[symbol]?.data || null, error: "" },
        }));

        try {
          const data = await requestCoinNews(symbol);
          if (generationRef.current !== generation) return;
          setNewsBySymbol((current) => ({
            ...current,
            [symbol]: { status: "success", data, error: "" },
          }));
        } catch (reason) {
          if (generationRef.current !== generation) return;
          setNewsBySymbol((current) => ({
            ...current,
            [symbol]: { status: "error", data: null, error: errorMessage(reason) },
          }));
        }
      }
    }

    const workerCount = Math.min(COIN_NEWS_CONCURRENCY, symbols.length);
    for (let index = 0; index < workerCount; index += 1) runWorker();

    return () => {
      if (generationRef.current === generation) generationRef.current += 1;
    };
  }, [symbolsKey]);

  const retry = useCallback(async (symbol) => {
    const generation = generationRef.current;
    setNewsBySymbol((current) => ({
      ...current,
      [symbol]: { status: "loading", data: null, error: "" },
    }));

    try {
      const data = await requestCoinNews(symbol);
      if (generationRef.current !== generation) return;
      setNewsBySymbol((current) => ({
        ...current,
        [symbol]: { status: "success", data, error: "" },
      }));
    } catch (reason) {
      if (generationRef.current !== generation) return;
      setNewsBySymbol((current) => ({
        ...current,
        [symbol]: { status: "error", data: null, error: errorMessage(reason) },
      }));
    }
  }, []);

  return { newsBySymbol, retry };
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
  const readerItems = useMemo(
    () => (market?.items || []).map((item) => ({
      id: item.url || item.title,
      title: item.title,
      source: item.source,
      time: item.published_display,
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
          <NewsBriefingReader
            key={market.updated_at || market.as_of || "market"}
            items={readerItems}
            ariaLabel="현재 읽는 시장·규제 헤드라인"
            empty="지금은 불러올 시장 헤드라인이 없어요."
            queueLabel="헤드라인 읽는 순서"
            rotateMs={5_000}
          />

          <div className="news-market-summary">
            <div className="news-market-summary-head">
              <span>{market.ai && market.overview ? "AI 요약" : "오늘의 맥락"}</span>
              {market.as_of ? <time className="num">{market.as_of} KST</time> : null}
            </div>
            {market.overview ? (
              <p><AnnotatedText text={market.overview} /></p>
            ) : (
              <p>요약 대신 위 실제 헤드라인을 순서대로 확인해 보세요.</p>
            )}
          </div>

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
  const change = Number(coin.change_pct) || 0;
  const changeTone = change > 0 ? "is-up" : change < 0 ? "is-down" : "is-flat";
  const changePrefix = change > 0 ? "+" : "";
  const headingId = `racer-${coin.symbol.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const readerItems = useMemo(
    () => (data?.items || []).map((item) => ({
      id: item.url || item.title,
      title: item.title,
      source: item.source,
      time: item.published_display,
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

      {status === "success" && readerItems.length === 0 ? (
        <div className="news-racer-reader-state">
          <strong>{base} 관련 최근 뉴스가 없어요.</strong>
          <span>새 원문이 수집되면 이 자리에 브리핑이 나타나요.</span>
        </div>
      ) : null}

      {status === "success" && readerItems.length > 0 ? (
        <NewsBriefingReader
          key={data.updated_at || data.as_of || coin.symbol}
          items={readerItems}
          ariaLabel={`${data.coin_name || base} 뉴스`}
          queueLabel={`${base} 뉴스`}
          rotateMs={RACER_NEWS_ROTATE_MS}
          syncTick={rotationTick}
          rowHeight={92}
          visibleRows={3}
          queueOnly
        />
      ) : null}

      <footer className="news-racer-footer">
        <Link to={`/builder?symbol=${encodeURIComponent(coin.symbol)}`} className="news-racer-builder-link">
          <span>매크로 만들기</span><span aria-hidden="true">→</span>
        </Link>
      </footer>
    </article>
  );
});

function RacerBriefing({ coins, loading, error }) {
  const { newsBySymbol, retry } = useCoinNewsBriefings(coins);
  const [rotationTick, setRotationTick] = useState(0);
  const termTexts = coins.flatMap((coin) => (
    newsBySymbol[coin.symbol]?.data?.items || []
  ).map((item) => item.title));

  useEffect(() => {
    const timer = window.setInterval(() => {
      setRotationTick((value) => value + 1);
    }, RACER_NEWS_ROTATE_MS);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <section className="news-briefing-section is-racers" aria-labelledby="racer-briefing-title">
      <BriefingSectionHeader
        id="racer-briefing-title"
        title="경주마 동향"
        description="거래가 활발한 종목을 두 개씩 비교하고, 모든 카드의 뉴스가 같은 리듬으로 올라와요."
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
          <div className="news-racer-briefing-stack">
            {coins.map((coin, index) => (
              <RacerNewsBriefing
                key={coin.symbol}
                coin={coin}
                rank={index + 1}
                newsState={newsBySymbol[coin.symbol]}
                onRetry={retry}
                rotationTick={rotationTick}
              />
            ))}
          </div>
          <TermChips texts={termTexts} />
        </>
      ) : null}
    </section>
  );
}

export default function News() {
  const [market, setMarket] = useState(null);
  const [marketLoading, setMarketLoading] = useState(true);
  const [marketError, setMarketError] = useState("");
  const [coins, setCoins] = useState([]);
  const [coinsLoading, setCoinsLoading] = useState(true);
  const [coinsError, setCoinsError] = useState("");

  useEffect(() => {
    let alive = true;

    api.newsMarket()
      .then((response) => {
        if (alive) setMarket(response);
      })
      .catch((reason) => {
        if (alive) setMarketError(errorMessage(reason));
      })
      .finally(() => {
        if (alive) setMarketLoading(false);
      });

    api.hotCoins(10)
      .then((response) => {
        if (alive) setCoins(response.coins || []);
      })
      .catch((reason) => {
        if (alive) setCoinsError(errorMessage(reason));
      })
      .finally(() => {
        if (alive) setCoinsLoading(false);
      });

    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="news-briefing-page">
      <header className="news-page-head">
        <span className="news-page-eyebrow">MARKET NEWSROOM</span>
        <div className="news-page-title-row">
          <h1>오늘의 코인동향</h1>
          {market?.as_of ? <time className="news-page-asof num">기준 {market.as_of} · KST</time> : null}
        </div>
        <p className="news-page-description">시장·규제와 활발히 움직이는 코인을 두 개의 브리핑으로 나눠 읽어요.</p>
        <p className="news-page-disclaimer">경주마 선정과 뉴스는 참고용이며 투자 권유가 아니에요.</p>
      </header>

      <div className="news-briefing-grid">
        <MarketBriefing market={market} loading={marketLoading} error={marketError} />
        <RacerBriefing coins={coins} loading={coinsLoading} error={coinsError} />
      </div>
    </div>
  );
}
