import { useMemo, useRef } from "react";
import { NewspaperIcon } from "@phosphor-icons/react/dist/csr/Newspaper";
import useFitLines from "../../hooks/useFitLines.js";
import { AnnotatedText } from "../../components/NewsTerms.jsx";
import { historicalNewsLabel, newsSourceLabel, prepareNewsResponse } from "../../lib/newsBriefings.js";
import { newsContentLabel } from "../../lib/newsContent.js";
import { splitSummary } from "../../lib/summaryText.js";
import InfiniteSentinel, { useChunks } from "./InfiniteSentinel.jsx";
import { kstDate, relativeTime } from "./trendsFormat.js";

function FitParagraph({ className, text, maxLines, minPx, children }) {
  const ref = useRef(null);
  useFitLines(ref, text, { maxLines, minPx });
  return <p ref={ref} className={className}>{children}</p>;
}

// 오늘의 요약 — 서버(regional_trends)가 국내/해외 매체 헤드라인으로 하루 한 번 만든 요약.
// 기존 '오늘의 코인동향' 머리 규격: 첫 문장은 노란 리드, 나머지는 18px 본문, 용어엔 ⓘ.
export function SummarySection({ region, news }) {
  const overview = news.extra?.overview || "";
  const summary = overview ? splitSummary(overview) : null;
  const updated = news.extra?.summary?.updated_at;
  const sourceCount = (news.extra?.collection?.sources || []).length;
  const regionName = region === "kr" ? "국내" : "해외";
  return (
    <section className="ct-brief" aria-labelledby="ct-brief-title">
      <header className="ct-col-head"><h2 id="ct-brief-title" className="ct-sec-title">오늘의 요약</h2></header>
      <p className="ct-brief-meta">
        기준 <span className="num">{kstDate(updated || Date.now())}</span> · KST
        {sourceCount ? <> · {regionName} 뉴스 <span className="num">{sourceCount}</span>곳 헤드라인 요약</> : null}
      </p>
      {summary ? (
        <div className="ct-lead">
          <FitParagraph className="ct-lead-first" text={summary.lead} maxLines={4} minPx={21}><AnnotatedText text={summary.lead} /></FitParagraph>
          {summary.body.map((line, index) => (
            <FitParagraph key={index} text={line} maxLines={7} minPx={15}><AnnotatedText text={line} /></FitParagraph>
          ))}
        </div>
      ) : (
        <p className="ct-brief-empty">{news.loading ? "오늘의 요약을 불러오는 중…" : `아직 오늘의 ${regionName} 요약이 없어요. 아래 소식에서 오늘 나온 글과 기사를 볼 수 있어요.`}</p>
      )}
      <p className="ct-brief-note">
        {summary ? "AI가 오늘 헤드라인만 근거로 쓴 요약이에요. 경주마 선정과 뉴스는 참고용이며 투자 권유가 아니에요."
          : "경주마 선정과 뉴스는 참고용이며 투자 권유가 아니에요."}
      </p>
    </section>
  );
}

function MarketRow({ item, now }) {
  return (
    <a className="mk-row" href={item.url} target="_blank" rel="noopener noreferrer">
      <span className="mk-thumb">{item.image ? <img src={item.image} alt="" loading="lazy" /> : <NewspaperIcon size={22} aria-hidden="true" />}</span>
      <span><span className="mk-row-title">{item.title}</span><span className="mk-meta">{item.label} · {item.historical || relativeTime(item.published, { now })}</span></span>
    </a>
  );
}

// 시장·규제 한눈에 — 사진 있는 대표 기사 1건 + 나머지는 고정 높이 목록 안에서 스크롤로 이어 그린다.
export function MarketSection({ state, onRetry }) {
  const scrollRef = useRef(null);
  const market = useMemo(() => (state?.data ? prepareNewsResponse(state.data) : null), [state?.data]);
  const items = useMemo(() => (market?.items || []).map((item) => ({
    id: item.id || item.url || item.title,
    title: item.title,
    source: newsSourceLabel(item),
    // 시장·규제 목록에도 커뮤니티 글이 섞일 수 있다 — 출처 앞에 '보도 기사 / 커뮤니티 의견 · 사실 확인 안 됨'.
    label: `${newsContentLabel(item)} · ${newsSourceLabel(item)}`,
    published: item.published,
    historical: historicalNewsLabel(item),
    image: item.image || "",
    url: item.article_url || item.url,
  })), [market]);
  const lead = items.find((item) => item.image) || items[0];
  const rest = items.filter((item) => item !== lead);
  const { shown, more, hasMore } = useChunks(rest.length, { first: 4, step: 3, resetKey: rest.length });
  const loading = !state || ["queued", "loading"].includes(state.status);
  const now = Date.now();
  return (
    <section className="ct-market" aria-labelledby="ct-market-title">
      <header className="ct-col-head">
        <h2 id="ct-market-title" className="ct-sec-title">시장·규제 한눈에</h2>
        {items.length ? <span className="ct-asof t-caption">헤드라인 <span className="num">{items.length}</span></span> : null}
      </header>
      <p className="ct-market-sub t-small">오늘 시장을 움직이는 정책과 주요 이슈예요.</p>
      {lead ? (
        <>
          <a className="mk-lead" href={lead.url} target="_blank" rel="noopener noreferrer">
            <div className="mk-lead-img">{lead.image ? <img src={lead.image} alt="" /> : <span className="mk-lead-fallback">{lead.source}</span>}</div>
            <div><p className="mk-lead-title">{lead.title}</p><span className="mk-meta">{lead.label} · {lead.historical || relativeTime(lead.published, { now })}</span></div>
          </a>
          <div className={hasMore ? "mk-scroll" : "mk-scroll is-end"} ref={scrollRef}>
            {rest.slice(0, shown).map((item) => <MarketRow key={item.id} item={item} now={now} />)}
            <InfiniteSentinel rootRef={scrollRef} active={hasMore} count={shown} onReach={more} />
          </div>
        </>
      ) : loading ? (
        <p className="ct-brief-empty" role="status">시장 헤드라인을 불러오는 중…</p>
      ) : state?.error ? (
        <div className="ct-side-empty">시장 뉴스를 불러오지 못했어요.
          <button type="button" className="btn btn-s btn-secondary" onClick={onRetry}>다시 불러오기</button></div>
      ) : (
        <p className="ct-brief-empty">지금은 불러올 시장 헤드라인이 없어요.</p>
      )}
    </section>
  );
}
