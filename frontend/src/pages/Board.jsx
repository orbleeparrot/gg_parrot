import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { initialOf, kstDateTime, pageWindow } from "../lib/boardText.js";
import { PageHeader, EmptyState, ErrorNote } from "../components/Page.jsx";
import { ChevronLeftIcon, ChevronRightIcon, CommentIcon, ImageIcon } from "../components/boardIcons.jsx";
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

  // 폼은 §1-3 이 상자를 허용하는 예외 — 목록 위로 끼어들어 오는 것이라
  // 끼어든 것처럼 보여야 한다.
  return (
    <div className="form-surface border border-slate-200 p-5 space-y-4">
      <h2 className="t-title text-slate-900">새 글 쓰기</h2>
      <label className="block">
        <span className="block t-small font-semibold text-slate-700 mb-2">제목</span>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={120}
          className={"field" + (err && !title.trim() ? " field-err" : "")}
          aria-invalid={err && !title.trim() ? true : undefined}
        />
      </label>
      <label className="block">
        <span className="block t-small font-semibold text-slate-700 mb-2">내용</span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={6}
          maxLength={5000}
          className="field"
        />
      </label>
      <div className="flex items-center gap-3 flex-wrap">
        <label className="inline-flex items-center gap-2 cursor-pointer">
          <span className="btn btn-s btn-secondary">사진 첨부</span>
          <input type="file" accept="image/png,image/jpeg" onChange={pickImage} className="hidden" />
          <span className="t-caption text-slate-500">JPG·PNG · 2MB 이하</span>
        </label>
      </div>
      {preview && (
        <div className="relative inline-block">
          <img src={preview} alt="미리보기" className="max-h-48 rounded-xl border border-slate-200" />
          {/* slate-900/50 은 두 테마에서 서로 뒤집히는 짝 — 다크에서도 대비가 유지된다. */}
          <button
            onClick={() => {
              setImage(null);
              setPreview("");
            }}
            className="absolute -top-2 -right-2 w-6 h-6 rounded-full bg-slate-900 text-slate-50 t-caption font-bold"
            aria-label="첨부 이미지 지우기"
          >
            ✕
          </button>
        </div>
      )}
      <p className="board-form-error" role={err ? "alert" : undefined}>{err}</p>
      <div className="flex items-center gap-2">
        <button onClick={submit} disabled={busy} className="btn btn-l btn-primary">
          {busy ? "등록 중…" : "등록"}
        </button>
        <button onClick={onCancel} className="btn btn-l btn-secondary">
          취소
        </button>
      </div>
    </div>
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

// 불러오는 동안의 뼈대 — 행 모양 그대로, 회전 대신.
function SkeletonRows({ count = 6 }) {
  return (
    <ul className="board-list" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <li key={index} className="board-row is-skeleton">
          <span className="board-row-link">
            <span className="board-avatar" />
            <span className="board-row-body">
              <span className="board-skeleton is-title" />
              <span className="board-skeleton is-snippet" />
            </span>
            <span className="board-row-meta">
              <span className="board-skeleton is-meta" />
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

// 글 한 줄 — 아바타 | 제목(+사진·댓글 표식)·요약 | 작성자·시각. 행 전체가 링크.
function PostRow({ post }) {
  const marks = post.has_image || post.comment_count > 0;
  const when = kstDateTime(post.created_kst);
  return (
    <li className="board-row">
      <Link to={`/board/${post.id}`} className="board-row-link">
        <span className="board-avatar" aria-hidden="true">{initialOf(post.author_name)}</span>
        <span className="board-row-body">
          <span className="board-row-title">
            <span>{post.title}</span>
            {marks ? (
              <span className="board-row-marks">
                {post.has_image ? (
                  <span title="사진 첨부"><ImageIcon /><span className="sr-only">사진 첨부</span></span>
                ) : null}
                {post.comment_count > 0 ? (
                  <span title={`댓글 ${post.comment_count}개`}>
                    <CommentIcon /><b className="num">{post.comment_count}</b><span className="sr-only">개 댓글</span>
                  </span>
                ) : null}
              </span>
            ) : null}
          </span>
          {post.snippet ? <span className="board-row-snippet">{post.snippet}</span> : null}
        </span>
        <span className="board-row-meta">
          <span className="board-row-author">{post.author_name}</span>
          <time className="board-row-time num" dateTime={when || undefined}>{post.created_kst}</time>
        </span>
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
  const [composing, setComposing] = useState(false);

  function load(p) {
    setBusy(true);
    setErr("");
    api
      .boardList(p, PAGE_SIZE)
      .then((d) => setData(d))
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
      {/* 제목 줄은 다른 화면과 같은 공용 규격(§9 PageHeader). 아이브로·설명 없이 제목 + 글 수, 오른쪽에 글쓰기. */}
      <PageHeader
        title="껄무새 게시판"
        meta={data ? (
          <>
            글 <span className="num">{data.total.toLocaleString()}</span>개
            {data.pages > 1 ? <> · <span className="num">{data.page}</span>/<span className="num">{data.pages}</span> 쪽</> : null}
          </>
        ) : null}
        actions={
          token ? (
            <button
              onClick={() => setComposing((v) => !v)}
              className={"btn btn-m " + (composing ? "btn-secondary" : "btn-primary")}
              aria-expanded={composing}
            >
              {composing ? "닫기" : "새 글 쓰기"}
            </button>
          ) : (
            <button
              onClick={() => navigate("/login?next=%2Fboard")}
              className="btn btn-m btn-secondary"
              title="글쓰기는 로그인 후 이용할 수 있어요"
            >
              로그인하고 글쓰기
            </button>
          )
        }
      />

      {composing && token && (
        <div className="board-composer">
          <Composer
            onCreated={(post) => {
              setComposing(false);
              navigate(`/board/${post.id}`);
            }}
            onCancel={() => setComposing(false)}
          />
        </div>
      )}

      {err && <ErrorNote>글 목록을 불러오지 못했어요: {err}</ErrorNote>}
      {busy && !data && !err ? <SkeletonRows /> : null}

      {data && (
        <>
          {data.items.length === 0 ? (
            <EmptyState title="아직 글이 없어요">첫 글을 남겨봐요.</EmptyState>
          ) : (
            <ul className={`board-list${busy ? " is-busy" : ""}`} aria-busy={busy || undefined}>
              {data.items.map((post) => <PostRow key={post.id} post={post} />)}
            </ul>
          )}
          <Pager page={data.page} pages={data.pages} onGo={go} />
          {data.disclaimer ? <p className="board-foot">{data.disclaimer}</p> : null}
        </>
      )}
    </div>
  );
}
