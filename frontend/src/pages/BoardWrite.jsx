import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { ChevronLeftIcon } from "../components/boardIcons.jsx";
import "./Board.css";

const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
export const WRITE_PATH = "/board/write";

/** 글쓰기 주소 — 로그인 전이면 로그인 뒤 여기로 돌아온다. */
export function writePath(token) {
  return token ? WRITE_PATH : `/login?next=${encodeURIComponent(WRITE_PATH)}`;
}

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

  // 카드로 띄우지 않는다 — 제목 아래 전체 폭 괘선 구획(라벨·입력·버튼은 댓글창과 같은 규격).
  return (
    <section className="board-composer" aria-label="새 글 쓰기">
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


// 글쓰기 전용 페이지 — 글 보기와 같은 골격(← 목록 · 제목 · 본문 자리). 등록하면 새 글로, 취소하면 목록으로 간다.
export default function BoardWrite() {
  const { token } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!token) navigate(writePath(null), { replace: true });
  }, [navigate, token]);

  if (!token) return null;
  return (
    <div className="board-post-page board-write">
      <div className="board-post">
        <Link to="/board" className="btn btn-s btn-ghost board-back">
          <ChevronLeftIcon />목록
        </Link>
        <h1 className="board-post-title">새 글 쓰기</h1>
        <Composer onCreated={(post) => navigate(`/board/${post.id}`, { replace: true })} onCancel={() => navigate("/board")} />
      </div>
    </div>
  );
}
