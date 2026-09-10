import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { boardFullTime, boardTime, kstDateTime, pageWindow } from "../lib/boardText.js";
import { PageHeader, EmptyState, ErrorNote } from "../components/Page.jsx";
import { writePath } from "./BoardWrite.jsx";
import { ChevronLeftIcon, ChevronRightIcon, ImageIcon, SearchIcon } from "../components/boardIcons.jsx";
import { AuthorAvatar } from "../components/UserAvatar.jsx";
import "./Board.css";

// 한 쪽의 글 수는 화면 높이에 맞춘다 — 표가 스크롤 없이 페이지 이동 바로 위까지 차게.
const ROW_PX = 43; // .board-row 42 + 괘선 1
const ROW_PX_MOBILE = 63; // ≤639px 두 줄 행(패딩 10×2 + 두 줄 + 괘선 1)
const HEAD_PX = 32; // 열 머리글(모바일은 접힘)
const BELOW_PX = 20 + 34 + 20 + 18 + 56; // 쪽 이동(여백 20 + 34) + 고지문(여백 20 + 한 줄 18) + 본문 아래 여백(하단 띠 56)
const MIN_ROWS = 5;
const MAX_ROWS = 30; // 백엔드 상한과 같다

function fittedRows(tableTop, viewportHeight, mobile = false) {
  const room = viewportHeight - tableTop - (mobile ? 0 : HEAD_PX) - BELOW_PX;
  return Math.max(MIN_ROWS, Math.min(MAX_ROWS, Math.floor(room / (mobile ? ROW_PX_MOBILE : ROW_PX))));
}

// 표가 시작하는 자리(sentinel)와 창 높이로 한 쪽의 글 수를 정한다. 창 크기가 바뀌면 다시 잰다.
function useFittedPageSize() {
  const sentinel = useRef(null);
  const [size, setSize] = useState(null);
  useLayoutEffect(() => {
    let timer = null;
    const measure = () => {
      const top = sentinel.current ? sentinel.current.getBoundingClientRect().top + window.scrollY : 0;
      setSize(fittedRows(top, window.innerHeight, window.matchMedia("(max-width: 639px)").matches));
    };
    const onResize = () => { window.clearTimeout(timer); timer = window.setTimeout(measure, 120); };
    measure();
    window.addEventListener("resize", onResize);
    return () => { window.clearTimeout(timer); window.removeEventListener("resize", onResize); };
  }, []);
  return [sentinel, size];
}

// 쪽 이동 — 단일 선택이라 segmented 문법(§6). 현재 쪽만 면 배경, 앞뒤는 화살표.
function Pager({ page, pages, onGo }) {
  if (pages <= 1) return null;
  return (
    <nav className="board-pager" aria-label="쪽 이동">
      <button type="button" className="board-page-btn" disabled={page <= 1} onClick={() => onGo(page - 1)} aria-label="이전 쪽">
        <ChevronLeftIcon />
      </button>
      {pageWindow(page, pages).map((n) => (
        <button
          key={n}
          type="button"
          className={`board-page-btn num${n === page ? " is-on" : ""}`}
          aria-current={n === page ? "page" : undefined}
          onClick={() => onGo(n)}
        >
          {n}
        </button>
      ))}
      <button type="button" className="board-page-btn" disabled={page >= pages} onClick={() => onGo(page + 1)} aria-label="다음 쪽">
        <ChevronRightIcon />
      </button>
    </nav>
  );
}

export function TableHead() {
  return (
    <li className="board-head" role="row" aria-hidden="true">
      <span className="board-col-likes">추천</span>
      <span className="board-col-title">제목</span>
      <span className="board-col-author">글쓴이</span>
      <span className="board-col-time">시각</span>
      <span className="board-col-views">조회</span>
    </li>
  );
}

const SORTS = [["new", "최신순"], ["likes", "추천순"], ["views", "조회순"], ["comments", "댓글순"]];
const FIELDS = [["all", "제목+내용"], ["title", "제목"], ["author", "글쓴이"]];

// 검색 — 제목 줄 아래 왼쪽. 범위 드롭다운(우리 입력칸 모양 + 화살표)과 검색어 칸 안의 돋보기·지우기.
function SearchBar({ q, field, onSearch }) {
  const [draft, setDraft] = useState(q);
  const [where, setWhere] = useState(field);
  useEffect(() => { setDraft(q); setWhere(field); }, [q, field]);
  return (
    <form className="board-searchbar" role="search" onSubmit={(e) => { e.preventDefault(); onSearch({ q: draft.trim(), field: where }); }}>
      <span className="board-select">
        <select value={where} onChange={(e) => setWhere(e.target.value)} className="field field-sm" aria-label="검색 범위">
          {FIELDS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </span>
      <span className="board-search">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} className="field field-sm" placeholder="검색어" aria-label="검색어" maxLength={80} />
        {q ? <button type="button" className="board-search-clear" aria-label="검색 지우기" onClick={() => onSearch({ q: "", field: "all" })}>✕</button> : null}
        <button type="submit" className="board-search-go" aria-label="검색"><SearchIcon /></button>
      </span>
    </form>
  );
}

// 불러오는 동안의 뼈대 — 행 모양 그대로, 회전 대신.
function SkeletonRows({ count = MIN_ROWS }) {
  return (
    <ul className="board-table" aria-hidden="true">
      <TableHead />
      {Array.from({ length: count }, (_, index) => (
        <li key={index} className="board-row is-skeleton">
          <span className="board-likes"><span className="board-skeleton is-no" /></span>
          <span className="board-title"><span className="board-skeleton is-title" /></span>
          <span className="board-author"><span className="board-skeleton is-author" /></span>
          <span className="board-time"><span className="board-skeleton is-time" /></span>
          <span className="board-views"><span className="board-skeleton is-time" /></span>
        </li>
      ))}
    </ul>
  );
}

// 글 한 줄 — 추천수(퀘이사존식 왼쪽 숫자) | 제목 [댓글수] (사진) | 글쓴이 | 시각 | 조회. 행 전체가 링크.
export function PostRow({ post, now, current = false }) {
  const time = boardTime(post.created_ms, now);
  const isToday = /전$/.test(time);
  const full = boardFullTime(post.created_ms) || post.created_kst;
  const when = kstDateTime(post.created_kst);
  return (
    <li className="board-item">
      <Link to={`/board/${post.id}`} className={`board-row${current ? " is-current" : ""}`} aria-current={current ? "page" : undefined} aria-label={`${post.title}${post.comment_count > 0 ? `, 댓글 ${post.comment_count}개` : ""}${post.has_image ? ", 사진 첨부" : ""}, ${post.author_name}, ${full}, 조회 ${post.views || 0}, 추천 ${post.likes || 0}`}>
        <span className={`board-likes num${post.likes > 0 ? " is-hot" : ""}`} aria-hidden="true">{post.likes || 0}</span>
        <span className="board-title">
          <span className="board-title-text">{post.title}</span>
          {post.comment_count > 0 ? <span className="board-count num" aria-hidden="true">{post.comment_count}</span> : null}
          {post.has_image ? <span className="board-mark" aria-hidden="true"><ImageIcon /></span> : null}
        </span>
        <span className="board-author" aria-hidden="true"><AuthorAvatar userId={post.author_user_id} src={post.author_avatar_url} name={post.author_name} size={20} /><span className="board-author-name">{post.author_name}</span></span>
        <time className={`board-time${isToday ? " is-today" : " num"}`} dateTime={when || undefined} title={full} aria-hidden="true">{time}</time>
        <span className="board-views num" aria-hidden="true">{post.views || 0}</span>
      </Link>
    </li>
  );
}

export default function Board() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, parseInt(searchParams.get("page") || "1", 10) || 1);
  const sort = searchParams.get("sort") || "new";
  const field = searchParams.get("field") || "all";
  const q = searchParams.get("q") || "";
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [sentinel, pageSize] = useFittedPageSize();

  function load(p) {
    setBusy(true);
    setErr("");
    api
      .boardList(p, pageSize, { sort, q, field })
      .then((d) => {
        setData(d);
        setNow(Date.now()); // 시각 표기(오늘 HH:MM)의 기준을 목록을 받은 순간으로
      })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setBusy(false));
  }

  useEffect(() => {
    if (!pageSize) return; // 첫 렌더에서 자리를 재기 전
    load(page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, sort, field, q, pageSize]);

  // 주소가 곧 상태 — 기본값(1쪽·최신순·검색 없음)은 주소에서 뺀다.
  function update(next) {
    const merged = { page, sort, field, q, ...next };
    if (!("page" in next)) merged.page = 1;
    const params = {};
    if (merged.page > 1) params.page = String(merged.page);
    if (merged.sort !== "new") params.sort = merged.sort;
    if (merged.q) { params.q = merged.q; if (merged.field !== "all") params.field = merged.field; }
    setSearchParams(params);
  }
  function go(p) {
    update({ page: p });
    window.scrollTo({ top: 0 });
  }

  return (
    <div className="board-page">
      {/* 제목 줄은 다른 화면과 같은 공용 규격(§9 PageHeader). 아이브로·설명·글 수 없이 제목, 오른쪽에 글쓰기.
          글쓰기는 글 보기처럼 전용 페이지(/board/write)로 이동한다 — 목록 위에 끼워 넣지 않는다. */}
      <PageHeader
        title={<>껄무새 게시판{data ? <span className="board-title-count"><span className="num">{data.total.toLocaleString()}</span>건</span> : null}</>}
        actions={
          <>
            <div className="seg board-sort" role="group" aria-label="정렬">
              {SORTS.map(([key, label]) => (
                <button key={key} type="button" className={`seg-item${sort === key ? " seg-item-on" : ""}`} aria-pressed={sort === key} onClick={() => update({ sort: key })}>{label}</button>
              ))}
            </div>
            <Link to={writePath(token)} className="btn btn-m btn-primary" title={token ? undefined : "로그인하면 바로 글을 쓸 수 있어요"}>
              글쓰기
            </Link>
          </>
        }
      >
        <SearchBar q={q} field={field} onSearch={update} />
      </PageHeader>

      {err && <ErrorNote>글 목록을 불러오지 못했어요: {err}</ErrorNote>}
      <div ref={sentinel} aria-hidden="true" />
      {(busy || !pageSize) && !data && !err ? <SkeletonRows count={pageSize || MIN_ROWS} /> : null}

      {data && (
        <>
          {data.items.length === 0 ? (
            q
              ? <EmptyState title={`‘${q}’ 검색 결과가 없어요`}>다른 말로 검색해 보세요.</EmptyState>
              : <EmptyState title="아직 글이 없어요">첫 글을 남겨봐요.</EmptyState>
          ) : (
            <ul className={`board-table${busy ? " is-busy" : ""}`} aria-busy={busy || undefined} aria-label="글 목록">
              <TableHead />
              {data.items.map((post) => <PostRow key={post.id} post={post} now={now} />)}
            </ul>
          )}
          <Pager page={data.page} pages={data.pages} onGo={go} />
          {data.disclaimer ? <p className="board-foot">{data.disclaimer}</p> : null}
        </>
      )}
    </div>
  );
}
