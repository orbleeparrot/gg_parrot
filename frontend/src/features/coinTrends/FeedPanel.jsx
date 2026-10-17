import { useEffect, useRef } from "react";
import CoinIcon from "../../components/CoinIcon.jsx";
import InfiniteSentinel from "./InfiniteSentinel.jsx";
import { changeTone, coinName, formatPct, relativeTime } from "./trendsFormat.js";

const toneClass = (value) => {
  const tone = changeTone(value);
  return tone === "up" ? "text-green-600" : tone === "down" ? "text-red-600" : "text-slate-500";
};

function PostItem({ item, coinsByBase, onCoin, now }) {
  const chips = (item.asset_symbols || []).map((base) => coinsByBase.get(base)).filter(Boolean).slice(0, 3);
  const excerpt = item.community_summary || item.excerpt || "";
  return (
    <li className="ct-post">
      <a className="ct-post-link" href={item.url} target="_blank" rel="noopener noreferrer">
        <span className="ct-post-title">{item.title}</span>
        {excerpt ? <span className="ct-post-ex">{excerpt}</span> : null}
      </a>
      <div className="ct-post-foot">
        <span className="ct-post-meta t-caption">
          <span>{item.source}</span><span className="dot" aria-hidden="true">·</span>
          <time dateTime={item.published}>{relativeTime(item.published, { now, precision: item.published_precision })}</time>
        </span>
        {chips.length ? (
          <div className="ct-post-coins">
            {chips.map((coin) => (
              <button key={coin.base} type="button" className="ct-chip" onClick={() => onCoin(coin.base)} aria-label={`${coinName(coin)} 보기`}>
                <CoinIcon symbol={coin.symbol} size={22} alt="" />{coinName(coin)}
                <b className={"num " + toneClass(coin.change_pct)}>{formatPct(coin.change_pct)}</b>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </li>
  );
}

const CHECK_LINE = {
  kr: { community: "디시인사이드 · 토큰포스트 · 코인판을 1분마다 확인해요.", news: "블록미디어 · 블루밍비트 · 디지털애셋 · 토큰포스트를 5분마다 확인해요." },
  global: { community: "Binance Square 글을 모아 보여 줘요.", news: "CoinDesk · Decrypt · CryptoSlate를 5분마다 확인해요." },
};

// 소식 칸 — 커뮤니티/뉴스 탭, 출처 고르기, 안쪽 스크롤에서 다음 쪽을 서버에 이어 받는다.
export default function FeedPanel({ region, feeds, tab, onTab, source, onSource, coin, keyword, coinsByBase, onCoin, onClearCoin }) {
  const listRef = useRef(null);
  const feed = feeds[tab];
  const kr = region === "kr";
  const labels = { community: kr ? "국내 커뮤니티" : "Binance Square", news: kr ? "국내 뉴스" : "해외 뉴스" };
  const picked = coin ? coinsByBase.get(coin) : null;
  const sources = (feeds.news.extra?.sources || feeds.community.extra?.sources || [])
    .filter((row) => (tab === "community" ? row.content_type === "community" : row.content_type === "article"));
  const now = Date.now();
  const kind = tab === "community" ? "커뮤니티 글이" : "기사가";
  const emptyLine = coin ? `최근 7일 동안 이 코인을 ${tab === "community" ? "말한" : "다룬"} ${kind} 없어요.`
    : keyword ? `‘${keyword}’ 들어간 ${kind} 없어요.` : `최근 7일 동안 모은 ${kind} 없어요.`;
  // 탭 · 코인 · 검색어 · 출처가 바뀌면 새 목록의 맨 위부터 — 앞 목록에서 내려간 만큼 가운데서 시작하지 않게.
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = 0; }, [feed.key]);
  return (
    <aside className="ct-side" aria-labelledby="ct-side-title">
      <header className="ct-col-head">
        <h2 id="ct-side-title" className="ct-sec-title">
          {picked ? <><CoinIcon symbol={picked.symbol} size={28} alt="" /><span>{coinName(picked)} 소식</span></>
            : keyword ? <span>‘{keyword}’ 들어간 글</span> : <span>{kr ? "국내 소식" : "해외 소식"}</span>}
        </h2>
        {picked ? <button type="button" className="ct-link-btn" onClick={onClearCoin}>전체 소식 보기</button> : null}
      </header>
      <div className="seg ct-side-tabs" role="tablist" aria-label="글 종류">
        {["community", "news"].map((name) => (
          <button key={name} type="button" role="tab" aria-selected={tab === name}
            className={tab === name ? "seg-item seg-item-on" : "seg-item"} onClick={() => onTab(name)}>
            {labels[name]}<span className="num">{feeds[name].loading ? "" : feeds[name].total}</span>
          </button>
        ))}
      </div>
      <div className="ct-side-tools">
        <span className="t-caption">최근 7일 · <span className="num">{feed.total}</span>개</span>
        {sources.length > 1 ? (
          <label className="ct-select">
            <span className="sr-only">출처</span>
            <select value={source} onChange={(event) => onSource(event.target.value)}>
              <option value="">모든 출처</option>
              {sources.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
            </select>
          </label>
        ) : null}
      </div>
      {tab === "community" ? <p className="ct-note">커뮤니티 글은 작성자의 주장·매매 의견이며, 보도 기사나 검증된 투자 정보가 아니에요.</p> : null}
      <div className="ct-side-list" ref={listRef}>
        {feed.items.length ? (
          <ol key={feed.key}>
            {feed.items.map((item) => (
              <PostItem key={item.id || item.url} item={item} coinsByBase={coinsByBase} onCoin={onCoin} now={now} />
            ))}
            <InfiniteSentinel as="li" rootRef={listRef} active={feed.hasMore} loading={feed.more} count={feed.items.length} onReach={feed.loadMore} />
          </ol>
        ) : feed.loading ? (
          <div className="ct-side-empty" role="status">소식을 불러오는 중…</div>
        ) : (
          <div className="ct-side-empty">
            {feed.error ? "소식을 불러오지 못했어요. 잠시 뒤 다시 보여 줄게요." : emptyLine}
            {!feed.error ? <small>{CHECK_LINE[region][tab]}</small> : null}
          </div>
        )}
      </div>
    </aside>
  );
}
