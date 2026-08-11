import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { PageHeader, EmptyState, Loading, ErrorNote } from "../components/Page.jsx";

const MAX_IMAGE_BYTES = 2 * 1024 * 1024;

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
          className="field"
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
      {err && <div className="t-small text-red-600" role="alert">{err}</div>}
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

function Pager({ page, pages, onGo }) {
  if (pages <= 1) return null;
  const nums = [];
  const from = Math.max(1, page - 2);
  const to = Math.min(pages, from + 4);
  for (let i = from; i <= to; i++) nums.push(i);
  // 페이지 번호는 단일 선택이지만 개수가 유동적이라 chip 규격을 쓴다(§6 chip).
  const btn = "chip justify-center min-w-[34px] num disabled:opacity-30 ";
  return (
    <div className="flex items-center justify-center gap-2 mt-6 flex-wrap">
      <button disabled={page <= 1} onClick={() => onGo(page - 1)} className={btn}>
        ‹
      </button>
      {nums.map((n) => (
        <button key={n} onClick={() => onGo(n)} className={btn + (n === page ? "chip-on" : "")}>
          {n}
        </button>
      ))}
      <button disabled={page >= pages} onClick={() => onGo(page + 1)} className={btn}>
        ›
      </button>
    </div>
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
    api
      .boardList(p, 10)
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
    <div className="max-w-3xl">
      <PageHeader
        eyebrow="전략·질문·정보"
        title="껄무새 게시판"
        description="코린이끼리 전략·질문·정보를 나눠요. (투자 조언 아님)"
        actions={
          token ? (
            <button
              onClick={() => setComposing((v) => !v)}
              className={"btn btn-m " + (composing ? "btn-secondary" : "btn-primary")}
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
        <div className="mb-5">
          <Composer
            onCreated={(post) => {
              setComposing(false);
              navigate(`/board/${post.id}`);
            }}
            onCancel={() => setComposing(false)}
          />
        </div>
      )}

      {busy && <Loading />}
      {err && <ErrorNote>오류: {err}</ErrorNote>}

      {data && (
        <>
          {data.items.length === 0 ? (
            <EmptyState title="아직 글이 없어요">첫 글을 남겨봐요.</EmptyState>
          ) : (
            <ul className="divide-y divide-slate-200 border-t border-slate-200">
              {data.items.map((p) => (
                <li key={p.id}>
                  <Link to={`/board/${p.id}`} className="block py-4 px-2 -mx-2 rounded-lg hover:bg-slate-100">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="t-title text-slate-900 truncate">
                          {p.has_image && <span className="badge badge-flat mr-2">사진</span>}
                          {p.title}
                          {p.comment_count > 0 && (
                            <span className="ml-2 t-caption font-bold num text-indigo-800">[{p.comment_count}]</span>
                          )}
                        </div>
                        {p.snippet && <div className="t-small text-slate-500 truncate mt-1">{p.snippet}</div>}
                      </div>
                      <div className="text-right shrink-0">
                        <div className="t-caption text-slate-700">{p.author_name}</div>
                        <div className="t-caption text-slate-500 num">{p.created_kst}</div>
                      </div>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <Pager page={data.page} pages={data.pages} onGo={go} />
          <p className="mt-4 t-caption text-slate-500 text-center">{data.disclaimer}</p>
        </>
      )}
    </div>
  );
}
