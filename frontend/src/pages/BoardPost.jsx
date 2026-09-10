import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { boardFullTime, boardTime, kstDateTime } from "../lib/boardText.js";
import { ErrorNote } from "../components/Page.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { ChevronLeftIcon, ImageIcon } from "../components/boardIcons.jsx";
import { writePath } from "./BoardWrite.jsx";
import { AuthorAvatar } from "../components/UserAvatar.jsx";
import "./Board.css";

// §6 text-field 규격. `bg-white` 를 쓰면 안 된다: Tailwind 의 리터럴 흰색이라
// `.dark` 에서 near-white 로 뒤집히는 text-slate-900 과 겹쳐 글자가 사라진다.
const commentInputCls = "field field-sm";

// 댓글 작성 — 리더보드 채팅처럼 계정 없이 '일회성 이름+비밀번호'를 매번 입력.
// 라벨은 입력 위에 보이게 둔다(자리표시자는 라벨이 아니다).
function CommentForm({ postId, onAdded }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e) {
    e.preventDefault();
    setErr("");
    if (!username.trim() || !password.trim() || !text.trim()) {
      setErr("이름·비밀번호·내용을 모두 입력해 주세요.");
      return;
    }
    setBusy(true);
    try {
      const { comment } = await api.boardAddComment(postId, { username, password, text });
      onAdded(comment);
      setText("");
      // 이름/비밀번호는 남겨둬 연속 작성 편하게 (계정 아님, 일회성 입력값)
    } catch (e2) {
      setErr(String(e2.message || e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="board-comment-form" aria-label="댓글 쓰기">
      <div className="board-comment-form-id">
        <label className="board-field-label">
          이름
          <input value={username} onChange={(e) => setUsername(e.target.value)} maxLength={24} autoComplete="nickname" className={commentInputCls} />
        </label>
        <label className="board-field-label">
          비밀번호
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" className={commentInputCls} />
        </label>
      </div>
      <label className="board-field-label">
        댓글
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={500} className="field w-full" />
      </label>
      <button type="submit" disabled={busy} className="btn btn-m btn-primary">
        {busy ? "등록 중…" : "댓글 등록"}
      </button>
      <p className="board-form-error" role={err ? "alert" : undefined}>{err}</p>
      <p className="board-comment-form-note">계정 없이 남길 수 있어요. 비밀번호는 댓글을 지울 때만 써요.</p>
    </form>
  );
}

function Comment({ c, onDeleted }) {
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const when = kstDateTime(c.created_kst);

  async function remove() {
    setErr("");
    setBusy(true);
    try {
      await api.boardDeleteComment(c.id, password);
      onDeleted(c.id);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="board-comment">
      <div className="board-comment-top">
        <b>{c.username}</b>
        <time className="num" dateTime={when || undefined}>{c.created_kst}</time>
        <button type="button" onClick={() => setConfirming((v) => !v)} className="board-text-btn" aria-expanded={confirming}>
          삭제
        </button>
      </div>
      <p className="board-comment-text">{c.text}</p>
      {confirming && (
        <div className="board-comment-confirm">
          <input
            type="password"
            value={password}
            aria-label="댓글을 쓸 때 정한 비밀번호"
            onChange={(e) => setPassword(e.target.value)}
            placeholder="작성 시 비밀번호"
            className="field field-sm min-w-0 flex-1 sm:flex-none sm:w-56"
          />
          {/* red-600 은 다크에서 밝은 분홍이라 흰 글자가 안 읽힌다 — danger 는 채움용 별도 토큰. */}
          <button type="button" onClick={remove} disabled={busy || !password} className="btn btn-m btn-danger">
            {busy ? "지우는 중…" : "삭제 확인"}
          </button>
          {err && <span className="t-caption text-red-600" role="alert">{err}</span>}
        </div>
      )}
    </li>
  );
}

// 글 아래 목록 — 읽고 나면 다음 글로 가는 게 게시판 관례(퀘이사존·디시·클리앙 모두 글 아래에 목록을 다시 둔다).
function ListBelow({ currentId, token, onWrite }) {
  const [data, setData] = useState(null);
  const [now] = useState(() => Date.now());
  useEffect(() => {
    let alive = true;
    api.boardList(1, 10).then((d) => { if (alive) setData(d); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  if (!data || !data.items?.length) return null;
  return (
    <section className="board-post-list" aria-labelledby="board-post-list-title">
      <h2 id="board-post-list-title" className="board-post-list-head">껄무새 게시판</h2>
      <ul className="board-table">
        <li className="board-head" role="row" aria-hidden="true">
          <span className="board-col-no">번호</span>
          <span className="board-col-title">제목</span>
          <span className="board-col-author">글쓴이</span>
          <span className="board-col-time">시각</span>
        </li>
        {data.items.map((post) => {
          const current = post.id === currentId;
          const time = boardTime(post.created_ms, now);
          return (
            <li key={post.id} className="board-item">
              <Link to={`/board/${post.id}`} className={`board-row${current ? " is-current" : ""}`} aria-current={current ? "page" : undefined}>
                <span className="board-no num" aria-hidden="true">{post.id}</span>
                <span className="board-title">
                  <span className="board-title-text">{post.title}</span>
                  {post.comment_count > 0 ? <span className="board-count num" aria-hidden="true">{post.comment_count}</span> : null}
                  {post.has_image ? <span className="board-mark" aria-hidden="true"><ImageIcon /></span> : null}
                </span>
                <span className="board-author" aria-hidden="true"><AuthorAvatar userId={post.author_user_id} src={post.author_avatar_url} name={post.author_name} size={20} /><span className="board-author-name">{post.author_name}</span></span>
                <time className={`board-time num${/:/.test(time) ? " is-today" : ""}`} dateTime={kstDateTime(post.created_kst) || undefined} title={boardFullTime(post.created_ms) || post.created_kst}>{time}</time>
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="board-post-list-foot">
        <Link to="/board" className="btn btn-m btn-secondary">목록 전체</Link>
        <button type="button" onClick={onWrite} className="btn btn-m btn-primary">글쓰기</button>
      </div>
    </section>
  );
}

function PostSkeleton() {
  return (
    <div className="board-post-skeleton" aria-hidden="true">
      <span className="board-skeleton" style={{ width: "58%", height: 22 }} />
      <span className="board-skeleton" style={{ width: 220 }} />
      <span className="board-skeleton" style={{ width: "92%", marginTop: 12 }} />
      <span className="board-skeleton" style={{ width: "84%" }} />
      <span className="board-skeleton" style={{ width: "48%" }} />
    </div>
  );
}

export default function BoardPost() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user, token } = useAuth();
  const [post, setPost] = useState(null);
  const [err, setErr] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    setPost(null);
    setErr("");
    api
      .boardGet(id)
      .then((d) => setPost(d))
      .catch((e) => setErr(String(e.message || e)));
  }, [id]);

  async function removePost() {
    setDeleting(true);
    try {
      await api.boardDelete(id);
      navigate("/board");
    } catch (e) {
      setErr(String(e.message || e));
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  const isMine = post && user && user.id === post.author_user_id;
  const when = post ? kstDateTime(post.created_kst) : "";
  const full = post ? (boardFullTime(post.created_ms) || post.created_kst) : "";

  const write = () => navigate(writePath(token));

  return (
    <div className="board-post-page">
    <div className="board-post">
      <Link to="/board" className="btn btn-s btn-ghost board-back">
        <ChevronLeftIcon />목록
      </Link>

      {err ? <div className="mt-4"><ErrorNote>글을 불러오지 못했어요: {err}</ErrorNote></div> : null}
      {!post && !err ? <PostSkeleton /> : null}

      {post ? (
        <>
          <article>
            <h1 className="board-post-title">{post.title}</h1>
            {/* 괘선 띠 — 글쓴이 | 시각. 댓글 수는 아래 댓글 구획 제목에만 둔다(같은 정보를 두 번 쓰지 않는다). */}
            <div className="board-post-strip">
              <span className="board-post-author"><AuthorAvatar userId={post.author_user_id} src={post.author_avatar_url} name={post.author_name} size={32} /><b>{post.author_name}</b></span>
              <time className="num board-post-strip-time" dateTime={when || undefined}>{full}</time>
              {isMine ? (
                <button type="button" onClick={() => setConfirmDelete(true)} disabled={deleting} className="board-text-btn">
                  {deleting ? "지우는 중…" : "글 삭제"}
                </button>
              ) : null}
            </div>

            {post.image_url ? (
              <figure className="board-post-figure">
                <img src={api.boardImageUrl(post.id)} alt="첨부 이미지" loading="lazy" />
              </figure>
            ) : null}

            {post.body ? <p className="board-post-body">{post.body}</p> : null}
          </article>

          <section className="board-comments" aria-labelledby="board-comments-title">
            <h2 id="board-comments-title" className="board-comments-head">
              댓글 <span className="num">{post.comments.length}</span>
            </h2>
            {post.comments.length === 0 ? (
              <p className="board-empty">아직 댓글이 없어요. 첫 댓글을 남겨봐요.</p>
            ) : (
              <ul className="board-comment-list">
                {post.comments.map((c) => (
                  <Comment
                    key={c.id}
                    c={c}
                    onDeleted={(cid) => setPost((p) => ({ ...p, comments: p.comments.filter((x) => x.id !== cid) }))}
                  />
                ))}
              </ul>
            )}
            <CommentForm
              postId={post.id}
              onAdded={(comment) => setPost((p) => ({ ...p, comments: [...p.comments, comment] }))}
            />
          </section>


          <ConfirmDialog
            open={confirmDelete}
            title="이 글을 삭제할까요?"
            description="글과 달린 댓글이 함께 지워지고 되돌릴 수 없어요."
            confirmLabel="삭제"
            tone="danger"
            busy={deleting}
            onConfirm={removePost}
            onCancel={() => { if (!deleting) setConfirmDelete(false); }}
          />
        </>
      ) : null}
    </div>
    <ListBelow currentId={Number(id)} token={token} onWrite={write} />
    </div>
  );
}
