import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { boardFullTime, boardTime, kstDateTime, pageWindow } from "../lib/boardText.js";
import { PageHeader, EmptyState, ErrorNote } from "../components/Page.jsx";
import { ChevronLeftIcon, ChevronRightIcon, ImageIcon } from "../components/boardIcons.jsx";
import { AuthorAvatar } from "../components/UserAvatar.jsx";
import "./Board.css";

const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
const PAGE_SIZE = 10;

// 로그인 계정만 여는 글쓰기 폼(제목/본문 + 이미지 jpg·png 1장).
function Composer({ onCreated, onCancel }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [image, setImage] = useState(null);
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview]
  );

  function pickImage(e) {
    setErr("");
    const f = e.target.files?.[0];
    if (!f) {
      setImage(null);
      setPreview("");
      return;
    }
    if (!["image/jpeg", "image/png"].includes(f.type)) {
      setImage(null);
      setPreview("");
      setErr("JPG 또는 PNG 이미지만 올릴 수 있어요.");
      e.target.value = "";
      return;
    }
    if (f.size > MAX_IMAGE_BYTES) {
      setImage(null);
      setPreview("");
      setErr("이미지는 2MB 이하만 올릴 수 있어요.");
      e.target.value = "";
      return;
    }
    setImage(f);
    setPreview(URL.createObjectURL(f));
  }

  async function submit() {
    setErr("");
    if (!title.trim()) return setErr("제목을 입력해 주세요.");
    setBusy(true);
    try {
      const post = await api.boardCreate({ title: title.trim(), body, image });
      onCreated(post);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  // 카드로 띄우지 않는다 — 목록 위에 같은 폭으로 끼어드는 괘선 구획(라벨·입력·버튼은 댓글창과 같은 규격).
  return (
    <section className="board-composer" aria-labelledby="board-composer-title">
      <h2 id="board-composer-title" className="board-composer-head">새 글 쓰기</h2>
      <label className="board-field-label">
        제목
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={120}
          className={"field field-sm" + (err && !title.trim() ? " field-err" : "")}
          aria-invalid={err && !title.trim() ? true : undefined}
        />
      </label>
      <label className="board-field-label">
        내용
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={8} maxLength={5000} className="field" />
      </label>
      {preview && (
        <div className="board-composer-preview">
          <img src={preview} alt="미리보기" />
          <button
            type="button"
            onClick={() => {
              setImage(null);
              setPreview("");
            }}
            aria-label="첨부 이미지 지우기"
          >
            ✕
          </button>
        </div>
      )}
      <p className="board-form-error" role={err ? "alert" : undefined}>{err}</p>
      <div className="board-composer-foot">
        <label className="board-attach">
          <span className="btn btn-s btn-secondary">사진 첨부</span>
          <input type="file" accept="image/png,image/jpeg" onChange={pickImage} />
          <span className="board-hint">{image ? image.name : "JPG·PNG · 2MB 이하"}</span>
        </label>
        <div className="board-composer-actions">
          <button type="button" onClick={onCancel} className="btn btn-m btn-secondary">취소</button>
          <button type="button" onClick={submit} disabled={busy} className="btn btn-m btn-primary">
            {busy ? "등록 중…" : "등록"}
          </button>
        </div>
      </div>
    </section>
  );
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

function TableHead() {
  return (
    <li className="board-head" role="row" aria-hidden="true">
      <span className="board-col-no">번호</span>
      <span className="board-col-title">제목</span>
      <span className="board-col-author">글쓴이</span>
      <span className="board-col-time">시각</span>
    </li>
  );
}

// 불러오는 동안의 뼈대 — 행 모양 그대로, 회전 대신.
function SkeletonRows({ count = PAGE_SIZE }) {
  return (
    <ul className="board-table" aria-hidden="true">
      <TableHead />
      {Array.from({ length: count }, (_, index) => (
        <li key={index} className="board-row is-skeleton">
          <span className="board-no"><span className="board-skeleton is-no" /></span>
          <span className="board-title"><span className="board-skeleton is-title" /></span>
          <span className="board-author"><span className="board-skeleton is-author" /></span>
          <span className="board-time"><span className="board-skeleton is-time" /></span>
        </li>
      ))}
    </ul>
  );
}

// 글 한 줄 — 번호 | 제목 [댓글수] (사진) | 글쓴이 | 시각. 행 전체가 링크.
function PostRow({ post, now }) {
  const time = boardTime(post.created_ms, now);
  const isToday = /:/.test(time);
  const full = boardFullTime(post.created_ms) || post.created_kst;
  const when = kstDateTime(post.created_kst);
  return (
    <li className="board-item">
      <Link to={`/board/${post.id}`} className="board-row" aria-label={`${post.title}${post.comment_count > 0 ? `, 댓글 ${post.comment_count}개` : ""}${post.has_image ? ", 사진 첨부" : ""}, ${post.author_name}, ${full}`}>
        <span className="board-no num" aria-hidden="true">{post.id}</span>
        <span className="board-title">
          <span className="board-title-text">{post.title}</span>
          {post.comment_count > 0 ? <span className="board-count num" aria-hidden="true">{post.comment_count}</span> : null}
          {post.has_image ? <span className="board-mark" aria-hidden="true"><ImageIcon /></span> : null}
        </span>
        <span className="board-author" aria-hidden="true"><AuthorAvatar userId={post.author_user_id} src={post.author_avatar_url} name={post.author_name} size={20} /><span className="board-author-name">{post.author_name}</span></span>
        <time className={`board-time num${isToday ? " is-today" : ""}`} dateTime={when || undefined} title={full} aria-hidden="true">{time}</time>
      </Link>
    </li>
  );
}

export default function Board() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, parseInt(searchParams.get("page") || "1", 10) || 1);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState("");
  const [composing, setComposing] = useState(() => searchParams.get("write") === "1");
  const [now, setNow] = useState(() => Date.now());

  function load(p) {
    setBusy(true);
    setErr("");
    api
      .boardList(p, PAGE_SIZE)
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
  }, [page]);

  function go(p) {
    setSearchParams({ page: String(p) });
    window.scrollTo({ top: 0 });
  }

  return (
    <div className="board-page">
      {/* 제목 줄은 다른 화면과 같은 공용 규격(§9 PageHeader). 아이브로·설명·글 수 없이 제목, 오른쪽에 글쓰기. */}
      <PageHeader
        title="껄무새 게시판"
        actions={
          token ? (
            <button
              onClick={() => setComposing((v) => !v)}
              className={"btn btn-m " + (composing ? "btn-secondary" : "btn-primary")}
              aria-expanded={composing}
            >
              {composing ? "닫기" : "글쓰기"}
            </button>
          ) : (
            <button
              onClick={() => navigate("/login?next=%2Fboard")}
              className="btn btn-m btn-primary"
              title="로그인하면 바로 글을 쓸 수 있어요"
            >
              글쓰기
            </button>
          )
        }
      />

      {composing && token && (
        <Composer
          onCreated={(post) => {
            setComposing(false);
            navigate(`/board/${post.id}`);
          }}
          onCancel={() => setComposing(false)}
        />
      )}

      {err && <ErrorNote>글 목록을 불러오지 못했어요: {err}</ErrorNote>}
      {busy && !data && !err ? <SkeletonRows /> : null}

      {data && (
        <>
          {data.items.length === 0 ? (
            <EmptyState title="아직 글이 없어요">첫 글을 남겨봐요.</EmptyState>
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
