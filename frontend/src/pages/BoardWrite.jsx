import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import BoardBodyEditor from "../components/BoardBodyEditor.jsx";
import { ErrorNote } from "../components/Page.jsx";
import "./Board.css";

const MAX_IMAGES = 10;
export const WRITE_PATH = "/board/write";

/** 글쓰기 주소 — 로그인 전이면 로그인 뒤 여기로 돌아온다. */
export function writePath(token) {
  return token ? WRITE_PATH : `/login?next=${encodeURIComponent(WRITE_PATH)}`;
}

export function editPath(postId) {
  return `/board/${postId}/edit`;
}

// 글쓰기·수정 폼(제목 + 본문 편집기). 사진은 본문 커서 자리에 그대로 들어간다(BoardBodyEditor);
// 저장은 글자 + `[사진n]` 자리 표시로 하고, 글 보기가 그 자리에 사진을 끼운다.
function Composer({ initial, onSaved, onCancel }) {
  const [title, setTitle] = useState(initial?.title || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const editor = useRef(null);

  async function submit() {
    setErr("");
    if (!title.trim()) return setErr("제목을 입력해 주세요.");
    const { html, files } = editor.current?.collect() || { html: "", files: [] };
    setBusy(true);
    try {
      const post = initial?.id
        ? await api.boardUpdate(initial.id, { title: title.trim(), body: html, bodyFormat: "html", images: files })
        : await api.boardCreate({ title: title.trim(), body: html, bodyFormat: "html", images: files });
      onSaved(post);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="board-composer" aria-label={initial?.id ? "글 수정" : "새 글 쓰기"}>
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
      <div className="board-field-label">
        내용
        <BoardBodyEditor
          ref={editor}
          initialHtml={initial?.bodyHtml || ""}
          maxImages={MAX_IMAGES}
          onError={setErr}
        />
      </div>
      <p className="board-form-error" role={err ? "alert" : undefined}>{err}</p>
      <div className="board-composer-foot">
        <div className="board-composer-actions">
          <button type="button" onClick={onCancel} className="btn btn-m btn-secondary">취소</button>
          <button type="button" onClick={submit} disabled={busy} className="btn btn-m btn-primary">
            {busy ? (initial?.id ? "고치는 중…" : "등록 중…") : (initial?.id ? "고치기" : "등록")}
          </button>
        </div>
      </div>
    </section>
  );
}

// 글쓰기·수정 전용 페이지 — 글 보기와 같은 골격(← 목록 · 제목 · 본문 자리).
// 새 글: 등록하면 새 글로, 취소하면 목록으로. 수정(/board/:id/edit): 본인 글만, 저장·취소 모두 그 글로.
export default function BoardWrite() {
  const { id } = useParams();
  const { token, user } = useAuth();
  const navigate = useNavigate();
  const editing = Boolean(id);
  const [initial, setInitial] = useState(null);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    if (!token) navigate(editing ? `/login?next=${encodeURIComponent(editPath(id))}` : writePath(null), { replace: true });
  }, [editing, id, navigate, token]);

  useEffect(() => {
    if (!editing || !token) return undefined;
    let alive = true;
    setInitial(null);
    setLoadError("");
    api.boardGet(id).then((post) => {
      if (!alive) return;
      if (!user || post.author_user_id !== user.id) {
        navigate(`/board/${id}`, { replace: true }); // 남의 글은 보기로
        return;
      }
      setInitial({ id: post.id, title: post.title, bodyHtml: post.body_html || "" });
    }).catch((e) => { if (alive) setLoadError(String(e.message || e)); });
    return () => { alive = false; };
  }, [editing, id, navigate, token, user]);

  if (!token) return null;
  const backTo = editing ? `/board/${id}` : "/board";
  return (
    <div className="board-post-page board-write">
      <div className="board-post">
        <h1 className="board-post-title">{editing ? "글 수정" : "새 글 쓰기"}</h1>
        {loadError ? <ErrorNote>글을 불러오지 못했어요: {loadError}</ErrorNote> : null}
        {!editing || initial ? (
          <Composer
            key={initial?.id || "new"}
            initial={initial}
            onSaved={(post) => navigate(`/board/${post.id}`, { replace: true })}
            onCancel={() => navigate(backTo)}
          />
        ) : null}
      </div>
    </div>
  );
}
