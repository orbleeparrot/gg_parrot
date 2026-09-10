import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import DOMPurify from "dompurify";
import { boardFullTime, boardTime, kstDateTime } from "../lib/boardText.js";
import { ErrorNote } from "../components/Page.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import ReportDialog from "../components/ReportDialog.jsx";
import { ThumbDownIcon, ThumbUpIcon } from "../components/boardIcons.jsx";
import { PostRow, TableHead } from "./Board.jsx";
import { editPath, writePath } from "./BoardWrite.jsx";
import { AuthorAvatar } from "../components/UserAvatar.jsx";
import "./Board.css";

// 댓글 쓰기 — 로그인 계정만, 닉네임은 계정 이름(왼쪽에 사진과 함께). 로그인 전에는 안내 한 줄.
function CommentForm({ postId, user, token, onAdded, parentId = null, onCancel = null, autoFocus = false }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const location = useLocation();
  if (!token || !user) {
    if (parentId) return null;
    return (
      <p className="board-comment-login">
        댓글은 로그인한 회원만 남길 수 있어요.
        <Link to={`/login?next=${encodeURIComponent(location.pathname)}`} className="btn btn-s btn-secondary">로그인</Link>
      </p>
    );
  }

  async function submit(e) {
    e.preventDefault();
    setErr("");
    if (!text.trim()) return setErr("댓글 내용을 입력해 주세요.");
    setBusy(true);
    try {
      const { comment } = await api.boardAddComment(postId, text, parentId);
      onAdded(comment);
      setText("");
      onCancel?.();
    } catch (e2) {
      setErr(String(e2.message || e2));
    } finally {
      setBusy(false);
    }
  }

  // 입력칸 → 오른쪽 아래 등록. 라벨 대신 자리표시자(댓글은 한 칸뿐이라 무엇인지 분명하다). 작성자 표시는 등록된 댓글 행이 한다.
  return (
    <form onSubmit={submit} className={`board-comment-form${parentId ? " is-reply" : ""}`} aria-label={parentId ? "답글 쓰기" : "댓글 쓰기"}>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={500} className="field board-comment-form-input" placeholder={parentId ? "답글을 남겨보세요" : "댓글을 남겨보세요"} aria-label={parentId ? "답글" : "댓글"} autoFocus={autoFocus} />
      <div className="board-comment-form-foot">
        <span className="board-form-error" role={err ? "alert" : undefined}>{err}</span>
        {onCancel ? <button type="button" className="btn btn-s btn-ghost" onClick={onCancel}>취소</button> : null}
        <button type="submit" disabled={busy} className={`btn ${parentId ? "btn-s btn-secondary" : "btn-m btn-primary"}`}>
          {busy ? "등록 중…" : parentId ? "답글 등록" : "댓글 등록"}
        </button>
      </div>
    </form>
  );
}

// 댓글 한 개 — 사진 · 닉네임 · 시각(고쳤으면 '수정됨') | 답글 · 편집 · 삭제 · 신고. 답글은 아래에 들여 쓴다.
function Comment({ c, post, user, token, onChange, onDeleted, isReply = false }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [replying, setReplying] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(c.text);
  const [reporting, setReporting] = useState(false);
  const when = kstDateTime(c.created_kst);
  const rel = Number.isFinite(c.created_ms) ? boardTime(c.created_ms) : c.created_kst;
  const mine = Boolean(user) && c.author_user_id === user.id;
  const canDelete = mine || (Boolean(user) && c.author_user_id == null && post.author_user_id === user.id);

  async function remove() {
    setErr(""); setBusy(true);
    try {
      await api.boardDeleteComment(c.id);
      onDeleted(c.id);
    } catch (e) {
      setErr(String(e.message || e)); setBusy(false); setConfirming(false);
    }
  }

  async function saveEdit(e) {
    e.preventDefault();
    if (!draft.trim()) return setErr("댓글 내용을 입력해 주세요.");
    setErr(""); setBusy(true);
    try {
      const { comment } = await api.boardEditComment(c.id, draft);
      onChange(comment);
      setEditing(false);
    } catch (e2) {
      setErr(String(e2.message || e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={`board-comment${isReply ? " is-reply" : ""}`}>
      <div className="board-comment-top">
        <AuthorAvatar userId={c.author_user_id} src={c.author_avatar_url} name={c.username} size={24} />
        <b>{c.username}</b>
        <time className={/전$/.test(rel) ? undefined : "num"} dateTime={when || undefined} title={c.created_kst}>{rel}</time>
        {c.edited ? <span className="board-comment-edited">수정됨</span> : null}
        <span className="board-comment-actions">
          {token && !isReply ? <button type="button" onClick={() => setReplying((v) => !v)} className="board-text-btn" aria-expanded={replying}>답글</button> : null}
          {mine ? <button type="button" onClick={() => { setEditing((v) => !v); setDraft(c.text); }} className="board-text-btn" aria-expanded={editing}>편집</button> : null}
          {canDelete ? <button type="button" onClick={() => setConfirming(true)} disabled={busy} className="board-text-btn">삭제</button> : null}
          {token && !mine ? <button type="button" onClick={() => setReporting(true)} className="board-text-btn">신고</button> : null}
        </span>
      </div>
      {editing ? (
        <form className="board-comment-edit" onSubmit={saveEdit}>
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={2} maxLength={500} className="field board-comment-form-input" aria-label="댓글 고치기" autoFocus />
          <div className="board-comment-form-foot">
            <span className="board-form-error" role={err ? "alert" : undefined}>{err}</span>
            <button type="button" className="btn btn-s btn-ghost" onClick={() => { setEditing(false); setErr(""); }}>취소</button>
            <button type="submit" className="btn btn-s btn-secondary" disabled={busy}>{busy ? "저장 중…" : "저장"}</button>
          </div>
        </form>
      ) : (
        <p className="board-comment-text">{c.text}</p>
      )}
      {!editing && err ? <p className="board-form-error" role="alert">{err}</p> : null}
      {replying ? (
        <CommentForm postId={post.id} user={user} token={token} parentId={c.id} autoFocus onCancel={() => setReplying(false)} onAdded={(reply) => onChange(null, reply)} />
      ) : null}
      {c.replies?.length ? (
        <ul className="board-replies">
          {c.replies.map((r) => (
            <Comment key={r.id} c={r} post={post} user={user} token={token} isReply onChange={onChange} onDeleted={onDeleted} />
          ))}
        </ul>
      ) : null}
      <ConfirmDialog
        open={confirming}
        title={isReply ? "이 답글을 지울까요?" : c.replies?.length ? "이 댓글과 답글을 지울까요?" : "이 댓글을 지울까요?"}
        description="지운 댓글은 되돌릴 수 없어요."
        confirmLabel="삭제"
        tone="danger"
        busy={busy}
        onConfirm={remove}
        onCancel={() => { if (!busy) setConfirming(false); }}
      />
      <ReportDialog open={reporting} targetType="comment" targetId={c.id} label="댓글" onClose={() => setReporting(false)} />
    </li>
  );
}

// 댓글 트리(원댓글 + replies) 갱신 — 화면 상태만 만진다.
function replaceComment(list, edited) {
  return list.map((c) => (c.id === edited.id ? { ...c, ...edited, replies: c.replies } : { ...c, replies: (c.replies || []).map((r) => (r.id === edited.id ? { ...r, ...edited } : r)) }));
}
function addReply(list, reply) {
  return list.map((c) => (c.id === reply.parent_id ? { ...c, replies: [...(c.replies || []), reply] } : c));
}
function removeComment(list, id) {
  return list.filter((c) => c.id !== id).map((c) => ({ ...c, replies: (c.replies || []).filter((r) => r.id !== id) }));
}
function countComments(list) {
  return list.reduce((n, c) => n + 1 + (c.replies?.length || 0), 0);
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
        <TableHead />
        {data.items.map((post) => <PostRow key={post.id} post={post} now={now} current={post.id === currentId} />)}
      </ul>
      <div className="board-post-list-foot">
        <Link to="/board" className="btn btn-m btn-secondary">목록 전체</Link>
        <button type="button" onClick={onWrite} className="btn btn-m btn-primary">글쓰기</button>
      </div>
    </section>
  );
}

// 본문 — 서버가 정제해 준 HTML(편집기 서식·본문 안 사진). 화면에서 한 번 더 걸러 그린다.
const PURIFY = { ALLOWED_TAGS: ["p", "br", "strong", "b", "em", "i", "u", "s", "strike", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "a", "img", "span", "mark", "code", "pre", "hr"],
  ALLOWED_ATTR: ["href", "target", "rel", "src", "alt", "width", "height", "style", "data-align"],
  // DOMPurify 는 이 정규식으로 href·src 뿐 아니라 width 같은 일반 속성값도 거른다 — 스킴 없는 값(상대 주소·숫자)은 통과, `javascript:` 류는 차단.
  ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|[^a-z]|[a-z+.-]+(?:[^a-z+.:-]|$))/i };
function PostBody({ html }) {
  if (!html) return null;
  return <div className="board-post-body board-rich" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html, PURIFY) }} />;
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
  const rel = post ? boardTime(post.created_ms) : "";
  const [voting, setVoting] = useState(false);
  const [voteErr, setVoteErr] = useState("");
  const [avatarOpen, setAvatarOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const avatarSrc = post ? (isMine && user?.avatar_url !== undefined ? user.avatar_url : post.author_avatar_url) : null;

  async function vote(value) {
    if (!token || voting) return;
    setVoting(true); setVoteErr("");
    try {
      const r = await api.boardVote(post.id, value);
      setPost((p) => ({ ...p, likes: r.likes, dislikes: r.dislikes, my_vote: r.my_vote }));
    } catch (e) {
      setVoteErr(String(e.message || e));
    } finally {
      setVoting(false);
    }
  }
  useEffect(() => {
    if (!avatarOpen) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setAvatarOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [avatarOpen]);

  const write = () => navigate(writePath(token));

  return (
    <div className="board-post-page">
    <div className="board-post">
      {err ? <div className="mt-4"><ErrorNote>글을 불러오지 못했어요: {err}</ErrorNote></div> : null}
      {!post && !err ? <PostSkeleton /> : null}

      {post ? (
        <>
          <article className="board-post-main">
            <h1 className="board-post-title">{post.title}</h1>
            {/* 괘선 띠 — 글쓴이(사진은 눌러 크게) | N분 전 · 조회 N | 편집·삭제. 댓글 수는 아래 댓글 구획 제목에만. */}
            <div className="board-post-strip">
              <span className="board-post-author">
                {avatarSrc ? (
                  <button type="button" className="board-avatar-btn" onClick={() => setAvatarOpen(true)} aria-label={`${post.author_name} 프로필 사진 크게 보기`}>
                    <AuthorAvatar userId={post.author_user_id} src={post.author_avatar_url} name={post.author_name} size={32} />
                  </button>
                ) : <AuthorAvatar userId={post.author_user_id} src={post.author_avatar_url} name={post.author_name} size={32} />}
                <b>{post.author_name}</b>
              </span>
              <span className="board-post-strip-time">
                <time className={/전$/.test(rel) ? undefined : "num"} dateTime={when || undefined} title={full}>{rel}</time>
                <span className="board-post-strip-dot" aria-hidden="true">·</span>
                <span>조회 <span className="num">{(post.views || 0).toLocaleString()}</span></span>
              </span>
              {isMine ? (
                <span className="board-post-actions">
                  <Link to={editPath(post.id)} className="btn btn-s btn-ghost">편집</Link>
                  <button type="button" onClick={() => setConfirmDelete(true)} disabled={deleting} className="btn btn-s btn-ghost is-danger">
                    {deleting ? "지우는 중…" : "삭제"}
                  </button>
                </span>
              ) : token ? (
                <span className="board-post-actions">
                  <button type="button" onClick={() => setReportOpen(true)} className="btn btn-s btn-ghost">신고</button>
                </span>
              ) : null}
            </div>

            <PostBody html={post.body_html} />

            {/* 반응 — 본문 아래 가운데. 추천은 로그인 계정당 한 표, 같은 표를 다시 누르면 취소. */}
            <div className="board-react" role="group" aria-label="이 글에 반응">
              <button type="button" onClick={() => vote(1)} disabled={voting || !token} className={`board-react-btn${post.my_vote === 1 ? " is-on" : ""}`} aria-pressed={post.my_vote === 1} title={token ? "추천" : "로그인한 회원만 추천할 수 있어요"}>
                <ThumbUpIcon /><span>추천</span><span className="num">{post.likes || 0}</span>
              </button>
              <button type="button" onClick={() => vote(-1)} disabled={voting || !token} className={`board-react-btn${post.my_vote === -1 ? " is-on is-down" : ""}`} aria-pressed={post.my_vote === -1} title={token ? "비추천" : "로그인한 회원만 비추천할 수 있어요"}>
                <ThumbDownIcon /><span>비추천</span><span className="num">{post.dislikes || 0}</span>
              </button>
            </div>
            {!token ? <p className="board-react-hint">추천과 댓글은 로그인한 회원만 할 수 있어요.</p> : null}
            {voteErr ? <p className="board-form-error" role="alert">{voteErr}</p> : null}
          </article>

          {avatarOpen && avatarSrc ? (
            <div className="board-lightbox" onClick={() => setAvatarOpen(false)} role="dialog" aria-label={`${post.author_name} 프로필 사진`}>
              <img src={avatarSrc} alt={`${post.author_name} 프로필 사진`} onClick={(e) => e.stopPropagation()} />
            </div>
          ) : null}

          <section className="board-comments" aria-labelledby="board-comments-title">
            <h2 id="board-comments-title" className="board-comments-head">
              댓글 <span className="num">{countComments(post.comments)}</span>
            </h2>
            {post.comments.length === 0 ? (
              <p className="board-empty">아직 댓글이 없어요. 첫 댓글을 남겨봐요.</p>
            ) : (
              <ul className="board-comment-list">
                {post.comments.map((c) => (
                  <Comment
                    key={c.id}
                    c={c}
                    post={post}
                    user={user}
                    token={token}
                    onChange={(edited, reply) => setPost((p) => ({ ...p, comments: reply ? addReply(p.comments, reply) : replaceComment(p.comments, edited) }))}
                    onDeleted={(cid) => setPost((p) => ({ ...p, comments: removeComment(p.comments, cid) }))}
                  />
                ))}
              </ul>
            )}
            <CommentForm
              postId={post.id}
              user={user}
              token={token}
              onAdded={(comment) => setPost((p) => ({ ...p, comments: [...p.comments, comment] }))}
            />
          </section>


          <ReportDialog open={reportOpen} targetType="post" targetId={post.id} label="글" onClose={() => setReportOpen(false)} />
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
