import { useEffect, useMemo, useRef, useState } from "react";
import { NewspaperIcon } from "@phosphor-icons/react/dist/csr/Newspaper";
import useFitLines from "../../hooks/useFitLines.js";
import { AnnotatedText } from "../../components/NewsTerms.jsx";
import { historicalNewsLabel, newsSourceLabel, prepareNewsResponse } from "../../lib/newsBriefings.js";
import { newsContentLabel } from "../../lib/newsContent.js";
import { splitSummary } from "../../lib/summaryText.js";
import InfiniteSentinel, { useChunks } from "./InfiniteSentinel.jsx";
import ScrollThumb from "./ScrollThumb.jsx";
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

function MarketRow({ item, now, index, on }) {
  return (
    <a className={on ? "mk-row is-on" : "mk-row"} data-index={index} href={item.url} target="_blank" rel="noopener noreferrer">
      <span className="mk-thumb">{item.image ? <img src={item.image} alt="" loading="lazy" /> : <NewspaperIcon size={22} aria-hidden="true" />}</span>
      <span><span className="mk-row-title">{item.title}</span><span className="mk-meta">{item.label} · {item.historical || relativeTime(item.published, { now })}</span></span>
    </a>
  );
}

// 줄 단위 휠(deltaMode 1)·쪽 단위(2)를 픽셀로.
const wheelPixels = (event, page) => event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? page : 1);

// 시장·규제 한눈에 — 위 큰 박스는 아래 목록 맨 위에 올라온 기사(스크롤하면 하나씩 바뀐다, 2026-10-17 사용자 요청).
// 목록은 고정 높이 안에서 스크롤로 이어 그리고, 큰 박스 위에서 휠을 굴려도 목록이 움직인다.
export function MarketSection({ state, onRetry }) {
  const scrollRef = useRef(null);
  const leadRef = useRef(null);
  const spacerRef = useRef(null);
  const [active, setActive] = useState(0);
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
  const { shown, more, hasMore } = useChunks(items.length, { first: 8, step: 6, resetKey: items.length });
  const lead = items[Math.min(active, items.length - 1)];

  // 목록 맨 위(절반 넘게 보이는 첫 줄)의 기사를 큰 박스에 올린다.
  useEffect(() => {
    const box = scrollRef.current;
    if (!box) return undefined;
    let frame = 0;
    const pick = () => {
      frame = 0;
      const rows = box.querySelectorAll(".mk-row");
      let next = 0;
      for (const row of rows) {
        if (row.offsetTop + row.offsetHeight / 2 > box.scrollTop) { next = Number(row.dataset.index) || 0; break; }
      }
      setActive(next);
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(pick); };
    box.addEventListener("scroll", onScroll, { passive: true });
    return () => { cancelAnimationFrame(frame); box.removeEventListener("scroll", onScroll); };
  }, [items.length]);
  // 같은 기사 목록을 다시 받았을 때(주기 갱신)는 읽던 자리를 지킨다 — 기사가 실제로 바뀔 때만 맨 위로.
  const itemsKey = items.map((item) => item.id).join("\n");
  useEffect(() => { setActive(0); if (scrollRef.current) scrollRef.current.scrollTop = 0; }, [itemsKey]);

  // 끝까지 다 그렸으면 마지막 기사도 맨 위로 올라와 큰 박스에 오를 수 있게 아래를 비워 둔다.
  useEffect(() => {
    const box = scrollRef.current, spacer = spacerRef.current;
    if (!box || !spacer) return;
    const rows = box.querySelectorAll(".mk-row");
    const last = rows[rows.length - 1];
    spacer.style.height = !hasMore && last ? `${Math.max(0, box.clientHeight - last.offsetHeight - 8)}px` : "0px";
  }, [hasMore, shown]);

  // 큰 박스 위에서 굴린 휠은 목록으로 넘긴다. 목록이 그 방향 끝이면 페이지가 스크롤된다.
  useEffect(() => {
    const node = leadRef.current, box = scrollRef.current;
    if (!node || !box) return undefined;
    const onWheel = (event) => {
      const dy = wheelPixels(event, box.clientHeight);
      const room = dy > 0 ? box.scrollHeight - box.clientHeight - box.scrollTop : box.scrollTop;
      if (!dy || Math.abs(event.deltaX) > Math.abs(event.deltaY) || room <= 0) return;
      event.preventDefault();
      box.scrollTop += dy;
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [items.length]);

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
          <a className="mk-lead" ref={leadRef} href={lead.url} target="_blank" rel="noopener noreferrer">
            <div className="mk-lead-img mk-swap" key={`img-${lead.id}`}>
              {lead.image ? <img src={lead.image} alt="" />
                : <span className="mk-lead-fallback"><NewspaperIcon size={30} aria-hidden="true" /><span>{lead.source}</span></span>}
            </div>
            <div className="mk-swap" key={`text-${lead.id}`}>
              <p className="mk-lead-title">{lead.title}</p>
              <span className="mk-meta">{lead.label} · {lead.historical || relativeTime(lead.published, { now })}</span>
            </div>
          </a>
          <div className="mk-scroll ct-scroll" ref={scrollRef}>
            <ScrollThumb boxRef={scrollRef} />
            {items.slice(0, shown).map((item, index) => (
              <MarketRow key={item.id} item={item} now={now} index={index} on={index === active} />
            ))}
            <InfiniteSentinel rootRef={scrollRef} active={hasMore} count={shown} onReach={more} />
            <div className="mk-end-spacer" ref={spacerRef} aria-hidden="true" />
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
