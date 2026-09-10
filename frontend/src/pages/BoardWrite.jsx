import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { imageMark, renumberImageMarks } from "../lib/boardText.js";
import { ErrorNote } from "../components/Page.jsx";
import { ChevronLeftIcon } from "../components/boardIcons.jsx";
import "./Board.css";

const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
const MAX_IMAGES = 10;
export const WRITE_PATH = "/board/write";

/** 글쓰기 주소 — 로그인 전이면 로그인 뒤 여기로 돌아온다. */
export function writePath(token) {
  return token ? WRITE_PATH : `/login?next=${encodeURIComponent(WRITE_PATH)}`;
}

export function editPath(postId) {
  return `/board/${postId}/edit`;
}

// 텍스트 영역의 커서 자리에 글자를 끼워 넣고, 새 본문과 커서 위치를 돌려준다.
function insertAt(textarea, body, snippet) {
  const start = textarea?.selectionStart ?? body.length;
  const end = textarea?.selectionEnd ?? body.length;
  const before = body.slice(0, start);
  const after = body.slice(end);
  const lead = before && !before.endsWith("\n") ? "\n" : "";
  const tail = after && !after.startsWith("\n") ? "\n" : "";
  const inserted = `${lead}${snippet}${tail}`;
  return { body: before + inserted + after, cursor: start + inserted.length };
}

// 글쓰기·수정 폼(제목/본문 + 사진 jpg·png 여러 장). 사진은 본문 커서 자리에 `[사진n]` 로 들어가고,
// 글 보기가 그 자리에 사진을 끼운다(한국 게시판의 본문 삽입 방식). 자리 없는 사진은 본문 뒤에 붙는다.
function Composer({ initial, onSaved, onCancel }) {
  const [title, setTitle] = useState(initial?.title || "");
  const [body, setBody] = useState(initial?.body || "");
  // 첨부 순서 그대로 — 남긴 기존 사진({kind:"existing", id, url}) 뒤에 새 사진({kind:"new", file, url})
  const [items, setItems] = useState(() => (initial?.images || []).map((img) => ({ kind: "existing", id: img.id, url: img.url })));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const input = useRef(null);
  const textarea = useRef(null);
  const pendingCursor = useRef(null);

  const itemsRef = useRef(items);
  itemsRef.current = items;
  useEffect(() => () => { for (const item of itemsRef.current) if (item.kind === "new") URL.revokeObjectURL(item.url); }, []);
  useEffect(() => {
    if (pendingCursor.current == null || !textarea.current) return;
    const at = pendingCursor.current; pendingCursor.current = null;
    textarea.current.focus(); textarea.current.setSelectionRange(at, at);
  }, [body]);

  function placeMark(index1) {
    const next = insertAt(textarea.current, body, imageMark(index1));
    pendingCursor.current = next.cursor;
    setBody(next.body);
  }

  function pickImages(e) {
    setErr("");
    const picked = Array.from(e.target.files || []);
    e.target.value = ""; // 같은 파일을 다시 골라도 change 가 나게
    if (!picked.length) return;
    const room = MAX_IMAGES - items.length;
    if (room <= 0) return setErr(`사진은 ${MAX_IMAGES}장까지 붙일 수 있어요.`);
    const accepted = [];
    for (const f of picked) {
      if (!["image/jpeg", "image/png"].includes(f.type)) return setErr("JPG 또는 PNG 이미지만 올릴 수 있어요.");
      if (f.size > MAX_IMAGE_BYTES) return setErr(`${f.name} — 사진은 한 장에 2MB 이하만 올릴 수 있어요.`);
      accepted.push({ kind: "new", file: f, url: URL.createObjectURL(f) });
    }
    if (accepted.length > room) setErr(`사진은 ${MAX_IMAGES}장까지 붙일 수 있어요. 앞의 ${room}장만 붙였어요.`);
    const added = accepted.slice(0, room);
    setItems((current) => [...current, ...added]);
    // 고른 사진은 커서 자리에 순서대로 들어간다.
    const marks = added.map((_, i) => imageMark(items.length + i + 1)).join("\n");
    const next = insertAt(textarea.current, body, marks);
    pendingCursor.current = next.cursor;
    setBody(next.body);
  }

  function removeItem(index) {
    const target = items[index];
    if (target?.kind === "new") URL.revokeObjectURL(target.url);
    setItems((current) => current.filter((_, i) => i !== index));
    setBody((current) => renumberImageMarks(current, index + 1));
  }

  async function submit() {
    setErr("");
    if (!title.trim()) return setErr("제목을 입력해 주세요.");
    setBusy(true);
    try {
      const files = items.filter((item) => item.kind === "new").map((item) => item.file);
      const post = initial?.id
        ? await api.boardUpdate(initial.id, {
            title: title.trim(), body, images: files,
            keepImageIds: items.filter((item) => item.kind === "existing").map((item) => item.id),
          })
        : await api.boardCreate({ title: title.trim(), body, images: files });
      onSaved(post);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  const labelOf = (item) => (item.kind === "new" ? item.file.name : "올린 사진");

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
      <label className="board-field-label">
        내용
        <textarea ref={textarea} value={body} onChange={(e) => setBody(e.target.value)} rows={8} maxLength={5000} className="field" />
      </label>
      {items.length > 0 && (
        <ul className="board-composer-previews" aria-label="첨부한 사진 — 누르면 커서 자리에 넣어요">
          {items.map((item, index) => (
            <li key={item.url} className="board-composer-preview">
              <button type="button" className="board-composer-preview-pick" onClick={() => placeMark(index + 1)} title={`${labelOf(item)} — 본문 커서 자리에 넣기`}>
                <img src={item.url} alt={`첨부 ${index + 1}: ${labelOf(item)}`} />
                <span className="board-composer-preview-no num" aria-hidden="true">{index + 1}</span>
              </button>
              <button type="button" className="board-composer-preview-remove" onClick={() => removeItem(index)} aria-label={`${labelOf(item)} 지우기`}>✕</button>
            </li>
          ))}
        </ul>
      )}
      <p className="board-form-error" role={err ? "alert" : undefined}>{err}</p>
      <div className="board-composer-foot">
        <div className="board-attach">
          <button type="button" className="btn btn-s btn-secondary" onClick={() => input.current?.click()} disabled={items.length >= MAX_IMAGES}>
            사진 첨부
          </button>
          <input ref={input} type="file" accept="image/png,image/jpeg" multiple onChange={pickImages} />
          <span className="board-hint">
            {items.length > 0 ? <><b className="num">{items.length}</b>/{MAX_IMAGES}장 · 본문의 <span className="num">[사진1]</span> 자리에 들어가요 · </> : "커서 자리에 들어가요 · "}
            JPG·PNG · 한 장에 2MB · {MAX_IMAGES}장까지
          </span>
        </div>
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
      setInitial({ id: post.id, title: post.title, body: post.body || "", images: post.images?.length ? post.images : post.image_url ? [{ id: 0, url: post.image_url }] : [] });
    }).catch((e) => { if (alive) setLoadError(String(e.message || e)); });
    return () => { alive = false; };
  }, [editing, id, navigate, token, user]);

  if (!token) return null;
  const backTo = editing ? `/board/${id}` : "/board";
  return (
    <div className="board-post-page board-write">
      <div className="board-post">
        <Link to={backTo} className="btn btn-s btn-ghost board-back">
          <ChevronLeftIcon />{editing ? "글로" : "목록"}
        </Link>
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
