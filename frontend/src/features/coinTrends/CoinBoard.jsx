import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChatsCircleIcon } from "@phosphor-icons/react/dist/csr/ChatsCircle";
import { CurrencyDollarIcon } from "@phosphor-icons/react/dist/csr/CurrencyDollar";
import { CurrencyKrwIcon } from "@phosphor-icons/react/dist/csr/CurrencyKrw";
import { HorseIcon } from "@phosphor-icons/react/dist/csr/Horse";
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/csr/MagnifyingGlass";
import { TrendDownIcon } from "@phosphor-icons/react/dist/csr/TrendDown";
import { TrendUpIcon } from "@phosphor-icons/react/dist/csr/TrendUp";
import { XIcon } from "@phosphor-icons/react/dist/csr/X";
import CoinIcon from "../../components/CoinIcon.jsx";
import InfoTooltip from "../../components/InfoTooltip.jsx";
import InfiniteSentinel, { useChunks } from "./InfiniteSentinel.jsx";
import {
  EXCHANGE_META, changeTone, coinName, formatPct, formatPrice, formatTurnover, kstClock, neighborsByTurnover,
  premiumPct, raceRows, rangePosition, searchCoins, sortCoins, turnoverRank,
} from "./trendsFormat.js";

const SORTS = [
  { key: "race", label: "경주마" },
  { key: "turnover", label: "거래대금" },
  { key: "gainers", label: "상승률" },
  { key: "losers", label: "하락률" },
  { key: "mentions", label: "언급" },
];

// 탭 아이콘 — 경주마=말, 거래대금=원화(해외는 달러), 상승·하락=추세선, 언급=말풍선 (Phosphor regular).
function SortIcon({ sort, region }) {
  if (sort === "race") return <span className="ct-sort-icon is-race" aria-hidden="true"><HorseIcon size={18} /></span>;
  if (sort === "turnover") {
    return <span className="ct-sort-icon" aria-hidden="true">{region === "kr" ? <CurrencyKrwIcon size={18} /> : <CurrencyDollarIcon size={18} />}</span>;
  }
  if (sort === "gainers") return <span className="ct-sort-icon is-up" aria-hidden="true"><TrendUpIcon size={18} /></span>;
  if (sort === "losers") return <span className="ct-sort-icon is-down" aria-hidden="true"><TrendDownIcon size={18} /></span>;
  return <span className="ct-sort-icon" aria-hidden="true"><ChatsCircleIcon size={18} /></span>;
}

const toneClass = (value) => {
  const tone = changeTone(value);
  return tone === "up" ? "text-green-600" : tone === "down" ? "text-red-600" : "text-slate-500";
};
const rankClass = (rank) => (rank === 1 ? "ct-rank-num num is-1" : rank === 2 ? "ct-rank-num num is-2" : rank === 3 ? "ct-rank-num num is-3" : "ct-rank-num num");
const pricePrefix = (quote) => (quote === "USDT" ? "$" : "");

function Highlight({ text, query }) {
  const index = String(text).toLowerCase().indexOf(String(query).trim().toLowerCase());
  if (!query.trim() || index < 0) return text;
  const end = index + query.trim().length;
  return <>{text.slice(0, index)}<mark>{text.slice(index, end)}</mark>{text.slice(end)}</>;
}

// 검색 — 코인 이름·심볼·별칭. 고르면 칸에 코인 이름이 남고 오른쪽 X 가 메인 순위로 돌려보낸다.
function CoinSearch({ exchange, lists, selectedLabel, keyword, onPickCoin, onPickKeyword, onClear, onQueryChange }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef(null);
  const listId = useId();
  const showing = selectedLabel || keyword || "";
  useEffect(() => { setQuery(""); setOpen(false); }, [showing]);
  useEffect(() => {
    const close = (event) => { if (!boxRef.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  const value = query || (open ? "" : showing);
  const trimmed = query.trim();
  const result = useMemo(() => (trimmed ? searchCoins(trimmed, { exchange, lists }) : { here: [], other: [] }), [trimmed, exchange, lists]);
  const items = trimmed ? [
    ...result.here.map((coin) => ({ type: "coin", exchange, coin })),
    ...result.other.map((hit) => ({ type: "coin", exchange: hit.exchange, coin: hit.coin })),
    { type: "keyword", query: trimmed },
  ] : [];
  const choose = (item) => {
    setOpen(false);
    setQuery("");
    if (!item) return;
    if (item.type === "coin") onPickCoin(item.coin.base, item.exchange);
    else onPickKeyword(item.query);
  };
  const meta = EXCHANGE_META[exchange];
  return (
    <div className="ct-search" ref={boxRef}>
      <label className="ct-search-field">
        <MagnifyingGlassIcon size={20} aria-hidden="true" />
        <input type="search" value={value} autoComplete="off" spellCheck={false} role="combobox"
          aria-expanded={open && !!trimmed} aria-controls={listId} aria-autocomplete="list" aria-label="코인 검색"
          placeholder="코인 이름이나 심볼 검색 (예: 비트코인, XRP)"
          onFocus={(event) => { if (showing && !query) event.target.select(); }}
          onChange={(event) => { setQuery(event.target.value); setOpen(true); setActive(0); onQueryChange?.(event.target.value); }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" && items.length) { event.preventDefault(); setActive((i) => (i + 1) % items.length); }
            else if (event.key === "ArrowUp" && items.length) { event.preventDefault(); setActive((i) => (i - 1 + items.length) % items.length); }
            else if (event.key === "Enter" && items.length) { event.preventDefault(); choose(items[active]); }
            else if (event.key === "Escape") { setOpen(false); setQuery(""); }
          }} />
        {value ? (
          <button type="button" className="ct-clear" onClick={() => { setQuery(""); setOpen(false); onClear(); }}
            aria-label={selectedLabel ? "선택한 코인 닫고 코인 순위로 돌아가기" : "검색어 지우기"}>
            <XIcon size={16} aria-hidden="true" />
          </button>
        ) : null}
      </label>
      {open && trimmed ? (
        <div className="ct-pop" id={listId} role="listbox" aria-label="검색 결과">
          {result.here.length ? <p className="ct-pop-label">{meta.name} {meta.market}</p> : null}
          {result.here.map((coin, index) => (
            <button key={coin.symbol} type="button" role="option" aria-selected={active === index}
              className={active === index ? "ct-pop-item is-active" : "ct-pop-item"} onClick={() => choose(items[index])}>
              <CoinIcon symbol={coin.symbol} size={28} alt="" />
              <span className="ct-pop-name"><b><Highlight text={coinName(coin)} query={trimmed} /></b><small className="num"><Highlight text={coin.base} query={trimmed} /></small></span>
              <span className="ct-pop-val num">{pricePrefix(meta.quote)}{formatPrice(coin.last_price, meta.quote)}<small className={toneClass(coin.change_pct)}>{formatPct(coin.change_pct)}</small></span>
            </button>
          ))}
          {result.other.length ? <p className="ct-pop-label">{meta.name}에는 없어요</p> : null}
          {result.other.map((hit, offset) => {
            const index = result.here.length + offset;
            return (
              <button key={hit.exchange + hit.coin.symbol} type="button" role="option" aria-selected={active === index}
                className={active === index ? "ct-pop-item is-active" : "ct-pop-item"} onClick={() => choose(items[index])}>
                <CoinIcon symbol={hit.coin.symbol} size={28} alt="" />
                <span className="ct-pop-name"><b><Highlight text={coinName(hit.coin)} query={trimmed} /></b><small className="num">{hit.coin.base}</small></span>
                <span className="ct-pop-go">{EXCHANGE_META[hit.exchange].name}에서 보기</span>
              </button>
            );
          })}
          <button type="button" role="option" aria-selected={active === items.length - 1}
            className={active === items.length - 1 ? "ct-pop-kw is-active" : "ct-pop-kw"} onClick={() => choose(items[items.length - 1])}>
            <MagnifyingGlassIcon size={18} aria-hidden="true" />
            <span>‘{trimmed}’ 들어간 커뮤니티 글 · 기사 찾기</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

// 페이지를 처음 열 때만 멈춘 채로 그린다. 그 뒤 경주마 탭을 다시 열거나 거래소를 바꾸면 출발선에서 달린다.
let raceShownOnce = false;

function RaceLanes({ rows, exchange, rule, selected, onSelect, rootRef, animateKey }) {
  const meta = EXCHANGE_META[exchange];
  const { shown, more, hasMore } = useChunks(rows.length, { first: 10, step: 10, resetKey: animateKey });
  // 첫 화면은 멈춘 채로 완성해 두고, 거래소를 바꾸거나 경주마 탭을 다시 열 때만 출발선에서 달린다.
  const [armed, setArmed] = useState(() => !raceShownOnce);
  const firstRef = useRef(!raceShownOnce);
  useEffect(() => {
    raceShownOnce = true;
    if (firstRef.current) { firstRef.current = false; return undefined; }
    if (typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches) return undefined;
    setArmed(false);
    let frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => setArmed(true)); });
    return () => cancelAnimationFrame(frame);
  }, [animateKey]);
  const basis = meta.region === "kr" ? "전일 종가보다" : "24시간 동안";
  const tip = `${meta.name} 거래대금 상위 ${rule?.pool || (meta.region === "kr" ? 50 : 100)}개 중 ${basis} 많이 오른 코인이에요. 돈이 몰리면서 오르는 코인을 보여 줘요.`;
  if (!rows.length) {
    return <div className="ct-list-empty">오늘은 거래대금 상위 코인 중 오른 코인이 없어요.</div>;
  }
  return (
    <div role="table" aria-label="경주마">
      <div className="ct-lanes-head" role="row">
        <span>순위</span><span>코인</span>
        <span className="ct-head-tip">{meta.region === "kr" ? "상승률" : "24시간 상승률"}<InfoTooltip text={tip} label="경주마 기준" placement="bottom" /></span>
        <span>현재가</span><span>거래대금</span>
      </div>
      <ol>
        {rows.slice(0, shown).map(({ coin, pos }, index) => (
          <li key={coin.symbol} role="row">
            <button type="button" className={(index === 0 ? "ct-lane is-lead" : "ct-lane") + (selected === coin.base ? " is-on" : "")}
              style={{ "--i": index % 10, "--pos": armed ? pos : 0 }} onClick={() => onSelect(coin.base)}
              aria-label={`${index + 1}위 ${coinName(coin)} ${coin.base}, ${formatPct(coin.change_pct)}, 자세히 보기`}>
              <span className={rankClass(index + 1)}>{index + 1}</span>
              <span className="ct-lane-name"><b>{coinName(coin)}</b><small className="num">{coin.base}</small></span>
              <span className="ct-track" aria-hidden="true">
                <span className="ct-bar" />
                <span className="ct-runner"><CoinIcon symbol={coin.symbol} size={30} alt="" /><b className="num text-green-600">{formatPct(coin.change_pct)}</b></span>
              </span>
              <span className="ct-lane-price num">{pricePrefix(meta.quote)}{formatPrice(coin.last_price, meta.quote)}</span>
              <span className="ct-lane-turn num">{formatTurnover(coin.quote_volume, meta.quote)}</span>
            </button>
          </li>
        ))}
        <InfiniteSentinel as="li" rootRef={rootRef} active={hasMore} count={shown} onReach={more} />
      </ol>
    </div>
  );
}

function RankRow({ coin, rank, quote, maxTurnover, mention, selected, onSelect }) {
  return (
    <li role="row">
      <button type="button" className={selected ? "ct-tr ct-row is-on" : "ct-tr ct-row"} onClick={() => onSelect(coin.base)}
        aria-label={`${rank}위 ${coinName(coin)} ${coin.base}, ${formatPrice(coin.last_price, quote)}, ${formatPct(coin.change_pct)}`}>
        <span className={rankClass(rank)}>{rank}</span>
        <span className="ct-row-coin">
          <CoinIcon symbol={coin.symbol} size={28} alt="" />
          <span className="ct-row-name"><b>{coinName(coin)}</b><small className="num">{coin.base}{coin.warning ? <span className="ct-risk-inline"> 유의</span> : null}</small></span>
          {coin.warning ? <span className="ct-risk">유의</span> : null}
        </span>
        <span className="ct-row-price num">{pricePrefix(quote)}{formatPrice(coin.last_price, quote)}</span>
        <span className={"ct-row-chg num " + toneClass(coin.change_pct)}>{formatPct(coin.change_pct)}</span>
        <span className="ct-row-turn"><span className="num">{formatTurnover(coin.quote_volume, quote)}</span>
          <i style={{ "--w": Math.max(0.02, maxTurnover ? coin.quote_volume / maxTurnover : 0) }} aria-hidden="true" /></span>
        <span className={mention ? "ct-row-ment num" : "ct-row-ment num is-zero"}>{mention || "–"}</span>
      </button>
    </li>
  );
}

function RankHead({ region, changeLabel, sort }) {
  const tip = `${region === "kr" ? "국내 커뮤니티 4곳" : "Binance Square"}의 최근 7일 글에서 이 코인을 말한 글 수예요.`;
  return (
    <div className="ct-tr ct-thead" role="row">
      <span>순위</span><span>코인</span><span>현재가</span>
      <span className={sort === "gainers" || sort === "losers" ? "is-sorted" : ""}>{changeLabel}</span>
      <span className={sort === "turnover" ? "ct-h-turn is-sorted" : "ct-h-turn"}>거래대금</span>
      <span className={sort === "mentions" ? "ct-h-ment ct-head-tip is-sorted" : "ct-h-ment ct-head-tip"}>언급<InfoTooltip text={tip} label="언급 설명" placement="bottom" /></span>
    </div>
  );
}

function RankTable({ coins, sort, exchange, mentions, selected, onSelect, rootRef }) {
  const meta = EXCHANGE_META[exchange];
  const rows = useMemo(() => sortCoins(coins, sort, mentions), [coins, sort, mentions]);
  const { shown, more, hasMore } = useChunks(rows.length, { first: 20, step: 20, resetKey: `${exchange}:${sort}` });
  const maxTurnover = useMemo(() => coins.reduce((max, coin) => Math.max(max, coin.quote_volume || 0), 0), [coins]);
  return (
    <div className={sort === "mentions" ? "ct-table is-ment" : "ct-table"} role="table" aria-label="코인 순위">
      <RankHead region={meta.region} changeLabel={meta.changeLabel} sort={sort} />
      {rows.length ? (
        <ol>
          {rows.slice(0, shown).map((coin, index) => (
            <RankRow key={coin.symbol} coin={coin} rank={index + 1} quote={meta.quote} maxTurnover={maxTurnover}
              mention={mentions[coin.base] || 0} selected={selected === coin.base} onSelect={onSelect} />
          ))}
          <InfiniteSentinel as="li" rootRef={rootRef} active={hasMore} count={shown} onReach={more} />
        </ol>
      ) : (
        <div className="ct-list-empty">{sort === "mentions" ? "아직 커뮤니티에서 말한 코인이 없어요." : "보여 줄 코인이 없어요."}</div>
      )}
    </div>
  );
}

function OtherExchange({ exchange, coin, row, current, binance, usdkrw }) {
  const meta = EXCHANGE_META[exchange];
  if (!row) return <div><dt>{meta.name}</dt><dd><span className="ct-kv-none">상장 안 됨</span></dd></div>;
  let note;
  if (current.quote === "KRW" && meta.quote === "KRW") {
    const gap = (row.last_price / coin.last_price - 1) * 100;
    note = <small>{current.name}보다 <b className={"num " + toneClass(gap)}>{formatPct(gap)}</b></small>;
  } else if (meta.quote === "KRW") {
    const gap = premiumPct(row.last_price, binance?.last_price, usdkrw);
    note = gap == null ? null : <small>해외 대비 <b className={"num " + toneClass(gap)}>{formatPct(gap)}</b></small>;
  } else {
    note = <small>{meta.changeLabel} <b className={"num " + toneClass(row.change_pct)}>{formatPct(row.change_pct)}</b></small>;
  }
  return (
    <div>
      <dt>{meta.name}</dt>
      <dd><span className="num">{pricePrefix(meta.quote)}{formatPrice(row.last_price, meta.quote)}{meta.quote === "KRW" ? "원" : ""}</span>{note}</dd>
    </div>
  );
}

// 고른 코인 — 순위 칸 전체를 코인 정보로 쓴다. 아래는 거래대금 순위에서 앞뒤 코인.
function CoinView({ base, exchange, coins, lists, usdkrw, mentions, onSelect }) {
  const meta = EXCHANGE_META[exchange];
  const coin = coins.find((row) => row.base === base);
  if (!coin) return null;
  const rank = turnoverRank(coins, base);
  const binance = lists.binance?.find((row) => row.base === base);
  const premium = meta.quote === "KRW" ? premiumPct(coin.last_price, binance?.last_price, usdkrw) : null;
  const at = rangePosition(coin.last_price, coin.low, coin.high);
  const near = neighborsByTurnover(coins, base, 5);
  const topTurnover = sortCoins(coins, "turnover")[0]?.quote_volume || 0;
  return (
    <section className="ct-coin" aria-label={`${coinName(coin)} 정보`}>
      <div className="ct-coin-top">
        <CoinIcon symbol={coin.symbol} size={44} alt="" />
        <div>
          <h3><span>{coinName(coin)}</span>{coin.warning ? <span className="ct-risk">유의</span> : null}</h3>
          <p className="t-caption"><span className="num">{coin.base}</span> · {meta.name} {meta.market}</p>
        </div>
      </div>
      <p className="ct-coin-price">
        <span className="num">{pricePrefix(meta.quote)}{formatPrice(coin.last_price, meta.quote)}</span>
        <small>{meta.quote}</small>
        <b className={"num " + toneClass(coin.change_pct)}>{formatPct(coin.change_pct)}</b>
      </p>
      {at != null ? (
        <>
          <div className="ct-range"><span className="num">{formatPrice(coin.low, meta.quote)}</span>
            <span className="ct-range-track" aria-hidden="true"><i style={{ "--p": at }} /></span>
            <span className="num">{formatPrice(coin.high, meta.quote)}</span></div>
          <p className="ct-range-cap t-caption"><span>{meta.rangeLabel} 저가</span><span>{meta.rangeLabel} 고가</span></p>
        </>
      ) : null}
      <dl className="ct-kv">
        <div><dt>24시간 거래대금</dt><dd><span className="num">{formatTurnover(coin.quote_volume, meta.quote)}</span>{rank ? <small>{meta.name} <span className="num">{rank}</span>위</small> : null}</dd></div>
        {premium != null ? (
          <div><dt>해외 대비</dt><dd><span className={"num " + toneClass(premium)}>{formatPct(premium)}</span><small>바이낸스 × <span className="num">{usdkrw.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}</span>원</small></dd></div>
        ) : null}
        {Object.keys(EXCHANGE_META).filter((name) => name !== exchange).map((name) => (
          <OtherExchange key={name} exchange={name} coin={coin} current={meta} binance={binance} usdkrw={usdkrw}
            row={lists[name]?.find((row) => row.base === base)} />
        ))}
        <div><dt>커뮤니티 언급</dt><dd><span className="num">{mentions[base] || 0}</span><small>최근 7일 글</small></dd></div>
      </dl>
      <div className="ct-coin-actions">
        <Link className="btn btn-s btn-secondary" to={`/builder?exchange=${exchange}&symbol=${encodeURIComponent(coin.symbol)}`}>이 코인으로 매크로 만들기</Link>
      </div>
      <section className="ct-near" aria-label="거래대금 순위에서 앞뒤 코인">
        <h4>{meta.name} 거래대금 순위에서</h4>
        <div className="ct-table" role="table">
          <RankHead region={meta.region} changeLabel={meta.changeLabel} sort="turnover" />
          <ol>
            {near.map(({ coin: row, rank: place }) => (
              <RankRow key={row.symbol} coin={row} rank={place} quote={meta.quote}
                maxTurnover={topTurnover}
                mention={mentions[row.base] || 0} selected={row.base === base} onSelect={onSelect} />
            ))}
          </ol>
        </div>
      </section>
    </section>
  );
}

// 코인 순위 칸 — 거래소 스위치 · 검색 · 탭 · 안쪽 스크롤(경주마 / 순위표 / 고른 코인).
export default function CoinBoard({
  region, exchange, onExchange, rankings, lists, sort, onSort, mentions, coin, onCoin, keyword, onKeyword,
  onClearSelection, usdkrw, onSearchActive,
}) {
  const scrollRef = useRef(null);
  const meta = EXCHANGE_META[exchange];
  const coins = rankings.data?.coins || [];
  const racers = useMemo(() => raceRows(coins, rankings.data?.racers), [coins, rankings.data?.racers]);
  const selected = coin ? coins.find((row) => row.base === coin) : null;
  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0; }, [coin, sort, exchange]);
  const updated = rankings.data?.updated_at;
  return (
    <section className="ct-rank" aria-labelledby="ct-rank-title">
      <header className="ct-col-head">
        <div className="ct-rank-title">
          <h2 id="ct-rank-title" className="ct-sec-title">코인 순위</h2>
          {region === "kr" ? (
            <div className="seg ct-mini" role="tablist" aria-label="거래소">
              {["upbit", "bithumb"].map((name) => (
                <button key={name} type="button" role="tab" aria-selected={exchange === name}
                  className={exchange === name ? "seg-item seg-item-on" : "seg-item"} onClick={() => onExchange(name)}>
                  <img src={`/exchanges/${name}.png`} alt="" width="16" height="16" />{EXCHANGE_META[name].name}
                </button>
              ))}
            </div>
          ) : (
            <span className="ct-ex-static"><img src="/exchanges/binance.svg" alt="" width="20" height="20" />바이낸스</span>
          )}
        </div>
        <p className="ct-asof t-caption">
          {meta.market} <span className="num">{coins.length}</span>개{updated ? <> · <span className="num">{kstClock(updated)}</span> 기준</> : null}
        </p>
      </header>
      <CoinSearch exchange={exchange} lists={lists} keyword={keyword}
        selectedLabel={selected ? `${coinName(selected)} ${selected.base}` : ""}
        onPickCoin={onCoin} onPickKeyword={onKeyword} onClear={onClearSelection} onQueryChange={onSearchActive} />
      {!coin ? (
        <div className="seg ct-sort" role="tablist" aria-label="순위 기준">
          {SORTS.map((item) => (
            <button key={item.key} type="button" role="tab" aria-selected={sort === item.key}
              className={sort === item.key ? "seg-item seg-item-on" : "seg-item"} onClick={() => onSort(item.key)}>
              <SortIcon sort={item.key} region={region} />{item.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className="ct-rank-scroll" ref={scrollRef}>
        {rankings.error && !coins.length ? (
          <div className="ct-list-empty">거래소 시세를 불러오지 못했어요. 잠시 뒤 다시 보여 줄게요.</div>
        ) : !coins.length ? (
          <div className="ct-list-empty" role="status">시세를 불러오는 중…</div>
        ) : coin ? (
          <CoinView base={coin} exchange={exchange} coins={coins} lists={lists} usdkrw={usdkrw} mentions={mentions} onSelect={onCoin} />
        ) : sort === "race" ? (
          <RaceLanes rows={racers} exchange={exchange} rule={rankings.data?.racer_rule} selected={coin}
            onSelect={onCoin} rootRef={scrollRef} animateKey={`${exchange}:${sort}`} />
        ) : (
          <RankTable coins={coins} sort={sort} exchange={exchange} mentions={mentions} selected={coin} onSelect={onCoin} rootRef={scrollRef} />
        )}
      </div>
    </section>
  );
}
