import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { initialOf, kstDateTime } from "../lib/boardText.js";
import { ErrorNote } from "../components/Page.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { ChevronLeftIcon } from "../components/boardIcons.jsx";
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
      <div className="board-comment-form-row">
        <label className="board-field-label">
          이름
          <input value={username} onChange={(e) => setUsername(e.target.value)} maxLength={24} autoComplete="nickname" className={commentInputCls} />
        </label>
        <label className="board-field-label">
          비밀번호 · 지울 때만 써요
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" className={commentInputCls} />
        </label>
      </div>
      <label className="board-field-label">
        댓글
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={500} className={commentInputCls + " w-full"} />
      </label>
      <p className="board-form-error" role={err ? "alert" : undefined}>{err}</p>
      <div className="board-comment-form-foot">
        <span>계정 없이 남길 수 있어요.</span>
        <button type="submit" disabled={busy} className="btn btn-m btn-primary">
          {busy ? "등록 중…" : "댓글 등록"}
        </button>
      </div>
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
      <span className="board-avatar" aria-hidden="true">{initialOf(c.username)}</span>
      <div className="min-w-0">
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
      </div>
    </li>
  );
}

function PostSkeleton() {
  return (
    <div className="board-post-skeleton" aria-hidden="true">
      <span className="board-skeleton is-title" style={{ width: "60%", height: 26 }} />
      <span className="board-skeleton is-meta" style={{ width: 160 }} />
      <span className="board-skeleton" style={{ width: "92%", marginTop: 12 }} />
      <span className="board-skeleton" style={{ width: "84%" }} />
      <span className="board-skeleton" style={{ width: "48%" }} />
    </div>
  );
}

export default function BoardPost() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
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

  return (
    <div className="board-post">
      <Link to="/board" className="btn btn-s btn-ghost board-back">
        <ChevronLeftIcon />목록으로
      </Link>

      {err ? <div className="mt-4"><ErrorNote>글을 불러오지 못했어요: {err}</ErrorNote></div> : null}
      {!post && !err ? <PostSkeleton /> : null}

      {post ? (
        <>
          <article>
            <header className="board-post-head">
              <h1 className="board-post-title">{post.title}</h1>
              <div className="board-post-meta">
                <span className="board-avatar" aria-hidden="true">{initialOf(post.author_name)}</span>
                <b>{post.author_name}</b>
                <time className="num" dateTime={when || undefined}>{post.created_kst}</time>
                {isMine ? (
                  <button type="button" onClick={() => setConfirmDelete(true)} disabled={deleting} className="board-text-btn">
                    {deleting ? "지우는 중…" : "글 삭제"}
                  </button>
                ) : null}
              </div>
            </header>

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
              <p className="board-empty" style={{ borderTop: "1px solid rgb(var(--c-slate-200))" }}>아직 댓글이 없어요. 첫 댓글을 남겨봐요.</p>
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
  );
}
