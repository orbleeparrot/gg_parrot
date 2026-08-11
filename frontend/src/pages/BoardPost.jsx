import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";

// §6 text-field 규격. `bg-white` 를 쓰면 안 된다: Tailwind 의 리터럴 흰색이라
// `.dark` 에서 near-white 로 뒤집히는 text-slate-900 과 겹쳐 글자가 사라진다.
const commentInputCls = "field field-sm";

// 댓글 작성 — 리더보드 채팅처럼 계정 없이 '일회성 이름+비밀번호'를 매번 입력.
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
    <form onSubmit={submit} className="pt-4 border-t border-slate-200 space-y-2">
      <div className="flex gap-2">
        <input
          value={username}
          aria-label="댓글 작성자 이름"
          onChange={(e) => setUsername(e.target.value)}
          placeholder="이름"
          maxLength={24}
          className={"w-1/2 " + commentInputCls}
        />
        <input
          type="password"
          value={password}
          aria-label="댓글 삭제용 비밀번호"
          onChange={(e) => setPassword(e.target.value)}
          placeholder="비밀번호(삭제용)"
          className={"w-1/2 " + commentInputCls}
        />
      </div>
      <textarea
        value={text}
        aria-label="댓글 내용"
        onChange={(e) => setText(e.target.value)}
        placeholder="댓글을 입력해요"
        rows={2}
        maxLength={500}
        className={"w-full " + commentInputCls}
      />
      {err && <div className="t-caption text-red-600" role="alert">{err}</div>}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span className="t-caption text-slate-500">계정 없이 남길 수 있어요 · 비밀번호는 지울 때만 필요해요</span>
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
    <div className="py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="t-label font-bold text-slate-900">{c.username}</span>
        <span className="t-caption text-slate-500 num">
          {c.created_kst}
          <button onClick={() => setConfirming((v) => !v)} className="ml-2 font-semibold hover:text-red-600">
            삭제
          </button>
        </span>
      </div>
      <div className="t-label font-medium text-slate-700 whitespace-pre-line mt-1">{c.text}</div>
      {confirming && (
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          <input
            type="password"
            value={password}
            aria-label="댓글 삭제 비밀번호"
            onChange={(e) => setPassword(e.target.value)}
            placeholder="작성 시 비밀번호"
            className="field field-sm min-w-0 flex-1 sm:flex-none sm:w-56"
          />
          {/* red-600 은 다크에서 밝은 분홍이라 흰 글자가 안 읽힌다 — danger 는 채움용 별도 토큰. */}
          <button onClick={remove} disabled={busy} className="btn btn-s btn-danger">
            삭제 확인
          </button>
          {err && <span className="t-caption text-red-600" role="alert">{err}</span>}
        </div>
      )}
    </div>
  );
}

export default function BoardPost() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [post, setPost] = useState(null);
  const [err, setErr] = useState("");
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
    if (!window.confirm("이 글을 삭제할까요? 되돌릴 수 없어요.")) return;
    setDeleting(true);
    try {
      await api.boardDelete(id);
      navigate("/board");
    } catch (e) {
      setErr(String(e.message || e));
      setDeleting(false);
    }
  }

  if (err) return <div className="max-w-3xl t-small text-red-600">오류: {err}</div>;
  if (!post) return <div className="max-w-3xl t-small text-slate-500">불러오는 중…</div>;

  const isMine = user && user.id === post.author_user_id;

  return (
    <div className="max-w-3xl">
      <Link to="/board" className="t-small font-semibold text-slate-700 hover:text-slate-900">← 목록으로</Link>

      <article className="mt-4 pb-5 border-b border-slate-200">
        <h1 className="t-h2 text-slate-900">{post.title}</h1>
        <div className="mt-2 flex items-center justify-between gap-2 flex-wrap">
          <div className="t-small text-slate-500">
            {post.author_name} · <span className="num">{post.created_kst}</span>
          </div>
          {isMine && (
            <button
              onClick={removePost}
              disabled={deleting}
              className="t-caption text-slate-500 hover:text-red-600 disabled:opacity-40"
            >
              {deleting ? "삭제 중…" : "글 삭제"}
            </button>
          )}
        </div>

        {post.image_url && (
          <img
            src={api.boardImageUrl(post.id)}
            alt="첨부 이미지"
            className="mt-4 max-w-full rounded-xl border border-slate-200"
          />
        )}

        {post.body && <div className="mt-4 t-body text-slate-600 whitespace-pre-line">{post.body}</div>}
      </article>

      <section className="mt-6">
        <h2 className="t-title text-slate-900 mb-2">
          댓글 <span className="text-slate-500 num">({post.comments.length})</span>
        </h2>
        <div className="divide-y divide-slate-200 border-t border-slate-200">
          {post.comments.length === 0 ? (
            <div className="py-6 t-small text-slate-500">첫 댓글을 남겨봐요.</div>
          ) : (
            post.comments.map((c) => (
              <Comment
                key={c.id}
                c={c}
                onDeleted={(cid) =>
                  setPost((p) => ({ ...p, comments: p.comments.filter((x) => x.id !== cid) }))
                }
              />
            ))
          )}
        </div>
        <div className="mt-3">
          <CommentForm
            postId={post.id}
            onAdded={(comment) => setPost((p) => ({ ...p, comments: [...p.comments, comment] }))}
          />
        </div>
      </section>
    </div>
  );
}
