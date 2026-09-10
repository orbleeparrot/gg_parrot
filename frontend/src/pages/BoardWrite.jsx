import { useEffect, useRef, useState } from "react";
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

// 로그인 계정만 여는 글쓰기 폼(제목/본문 + 사진 jpg·png 여러 장).
const MAX_IMAGES = 10;

function Composer({ onCreated, onCancel }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [images, setImages] = useState([]); // [{ file, url }] — 첨부 순서 그대로
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const input = useRef(null);

  // 미리보기 URL 은 목록이 바뀔 때마다가 아니라 화면을 떠날 때 한꺼번에 거둔다.
  const imagesRef = useRef(images);
  imagesRef.current = images;
  useEffect(() => () => { for (const item of imagesRef.current) URL.revokeObjectURL(item.url); }, []);

  function pickImages(e) {
    setErr("");
    const picked = Array.from(e.target.files || []);
    e.target.value = ""; // 같은 파일을 다시 골라도 change 가 나게
    if (!picked.length) return;
    const room = MAX_IMAGES - images.length;
    if (room <= 0) return setErr(`사진은 ${MAX_IMAGES}장까지 붙일 수 있어요.`);
    const accepted = [];
    for (const f of picked) {
      if (!["image/jpeg", "image/png"].includes(f.type)) return setErr("JPG 또는 PNG 이미지만 올릴 수 있어요.");
      if (f.size > MAX_IMAGE_BYTES) return setErr(`${f.name} — 사진은 한 장에 2MB 이하만 올릴 수 있어요.`);
      accepted.push({ file: f, url: URL.createObjectURL(f) });
    }
    if (accepted.length > room) setErr(`사진은 ${MAX_IMAGES}장까지 붙일 수 있어요. 앞의 ${room}장만 붙였어요.`);
    setImages((current) => [...current, ...accepted.slice(0, room)]);
  }

  function removeImage(url) {
    setImages((current) => {
      const target = current.find((item) => item.url === url);
      if (target) URL.revokeObjectURL(target.url);
      return current.filter((item) => item.url !== url);
    });
  }

  async function submit() {
    setErr("");
    if (!title.trim()) return setErr("제목을 입력해 주세요.");
    setBusy(true);
    try {
      const post = await api.boardCreate({ title: title.trim(), body, images: images.map((item) => item.file) });
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
      {images.length > 0 && (
        <ul className="board-composer-previews" aria-label="첨부한 사진">
          {images.map((item, index) => (
            <li key={item.url} className="board-composer-preview">
              <img src={item.url} alt={`첨부 ${index + 1}: ${item.file.name}`} />
              <span className="board-composer-preview-no num" aria-hidden="true">{index + 1}</span>
              <button type="button" onClick={() => removeImage(item.url)} aria-label={`${item.file.name} 지우기`}>✕</button>
            </li>
          ))}
        </ul>
      )}
      <p className="board-form-error" role={err ? "alert" : undefined}>{err}</p>
      <div className="board-composer-foot">
        <div className="board-attach">
          <button type="button" className="btn btn-s btn-secondary" onClick={() => input.current?.click()} disabled={images.length >= MAX_IMAGES}>
            사진 첨부
          </button>
          <input ref={input} type="file" accept="image/png,image/jpeg" multiple onChange={pickImages} />
          <span className="board-hint">
            {images.length > 0 ? <><b className="num">{images.length}</b>/{MAX_IMAGES}장 · </> : null}JPG·PNG · 한 장에 2MB 이하 · {MAX_IMAGES}장까지
          </span>
        </div>
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
