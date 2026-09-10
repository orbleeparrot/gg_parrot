import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { boardFullTime, boardTime, kstDateTime, pageWindow } from "../lib/boardText.js";
import { PageHeader, EmptyState, ErrorNote } from "../components/Page.jsx";
import { writePath } from "./BoardWrite.jsx";
import { ChevronLeftIcon, ChevronRightIcon, ImageIcon } from "../components/boardIcons.jsx";
import { AuthorAvatar } from "../components/UserAvatar.jsx";
import "./Board.css";

const PAGE_SIZE = 10;

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

// 검색 — 그누보드식 `[제목+내용 ▾][검색어][검색]`. 제목 줄 아래 왼쪽.
function SearchBar({ q, field, onSearch }) {
  const [draft, setDraft] = useState(q);
  const [where, setWhere] = useState(field);
  useEffect(() => { setDraft(q); setWhere(field); }, [q, field]);
  return (
    <form className="board-searchbar" role="search" onSubmit={(e) => { e.preventDefault(); onSearch({ q: draft.trim(), field: where }); }}>
      <select value={where} onChange={(e) => setWhere(e.target.value)} className="field field-sm board-searchbar-field" aria-label="검색 범위">
        {FIELDS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
      <input value={draft} onChange={(e) => setDraft(e.target.value)} className="field field-sm board-searchbar-input" placeholder="검색어" aria-label="검색어" maxLength={80} />
      <button type="submit" className="btn btn-s btn-secondary">검색</button>
      {q ? <Link to="/board" className="btn btn-s btn-ghost">지우기</Link> : null}
    </form>
  );
}

// 불러오는 동안의 뼈대 — 행 모양 그대로, 회전 대신.
function SkeletonRows({ count = PAGE_SIZE }) {
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

  function load(p) {
    setBusy(true);
    setErr("");
    api
      .boardList(p, PAGE_SIZE, { sort, q, field })
      .then((d) => {
        setData(d);
        setNow(Date.now()); // 시각 표기(오늘 HH:MM)의 기준을 목록을 받은 순간으로
      })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setBusy(false));
  }

  useEffect(() => {
    load(page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, sort, field, q]);

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
      {busy && !data && !err ? <SkeletonRows /> : null}

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
