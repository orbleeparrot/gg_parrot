import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { getAuthUser, getToken, useAuth, updateAuthUser } from "../lib/auth.js";
import { CameraIcon } from "@phosphor-icons/react/dist/csr/Camera";
import { CaretDownIcon } from "@phosphor-icons/react/dist/csr/CaretDown";
import { PlantIcon } from "@phosphor-icons/react/dist/csr/Plant";
import { MedalIcon } from "@phosphor-icons/react/dist/csr/Medal";
import { DiamondIcon } from "@phosphor-icons/react/dist/csr/Diamond";
import {
  formatPoints, fullKst, joinedLabel, ledgerLabel, signedPoints, stampKst, tierNextLabel, tierStepAt, tierSteps,
} from "../lib/profileText.js";
import { EmptyState, ErrorNote } from "../components/Page.jsx";
import CoinIcon from "../components/CoinIcon.jsx";
import UserAvatar from "../components/UserAvatar.jsx";
import { ImageIcon } from "../components/boardIcons.jsx";
import "./Board.css"; // 게시글 탭은 게시판 목록 문법(.board-table)을 그대로 쓴다
import "./MyPage.css";

// 탭 = 내 것들의 목록. 개수는 탭 이름 뒤에 붙는다(깃허브·브런치 관례).
const TABS = [
  { key: "created", label: "만든 매크로" },
  { key: "purchased", label: "구매한 매크로" },
  { key: "sales", label: "판매 내역" },
  { key: "ledger", label: "포인트 내역" },
  { key: "posts", label: "게시글" },
];
const TAB_KEYS = new Set(TABS.map((t) => t.key));

function Stamp({ value, now, className = "" }) {
  const full = fullKst(value);
  return (
    <time className={`me-time num ${className}`} dateTime={full ? new Date(value).toISOString() : undefined} title={full || undefined}>
      {stampKst(value, now)}
    </time>
  );
}

function TierIcon({ name }) {
  const Icon = name === "새싹" ? PlantIcon : name === "다이아" ? DiamondIcon : MedalIcon;
  const tone = { "새싹": "seed", "브론즈": "bronze", "실버": "silver", "골드": "gold", "다이아": "diamond" }[name] || "silver";
  return <Icon className={`me-tier-icon is-${tone}`} size={24} weight="duotone" aria-hidden="true" />;
}

function TierGuide({ tier }) {
  return (
    <details className="me-tier">
      <summary aria-label={`등급 안내 · ${tier.name} · ${tierNextLabel(tier)}`}>
        <span className="me-tier-current"><TierIcon name={tier.name} /><b>{tier.name}</b></span>
        <span className="me-tier-next">{tierNextLabel(tier)}</span>
        <CaretDownIcon className="me-tier-caret" size={16} weight="bold" aria-hidden="true" />
      </summary>
      <ol className="me-tier-list" aria-label="판매 등급별 조건">
        {tierSteps(tier).map((step) => (
          <li key={step.name} className={step.state === "current" ? "is-current" : undefined} aria-current={step.state === "current" ? "step" : undefined}>
            <TierIcon name={step.name} />
            <b>{step.name}</b>
            <span className="num">{tierStepAt(step.at)}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function AvatarEditor({ user, onUpdate, onError }) {
  const input = useRef(null);
  const mounted = useRef(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  async function save(file) {
    if (busy) return;
    onError("");
    if (file && file.size > 2 * 1024 * 1024) {
      onError("2MB 이하의 사진을 선택해주세요.");
      return;
    }
    const requestToken = getToken();
    const isCurrent = () => mounted.current && getToken() === requestToken && getAuthUser()?.id === user.id;
    setBusy(true);
    try {
      const response = await (file ? api.uploadAvatar(file) : api.deleteAvatar());
      if (isCurrent()) onUpdate(response.user);
    } catch (e) {
      if (isCurrent()) onError(String(e.message || "사진을 저장하지 못했어요."));
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  return (
    <div className="me-photo" aria-busy={busy}>
      <button type="button" className="me-avatar-edit" aria-label="프로필 사진 변경" aria-describedby="me-photo-hint" disabled={busy} onClick={() => input.current?.click()}>
        <UserAvatar src={user.avatar_url} name={user.username} size={80} className="me-avatar" />
        <span className="me-avatar-camera"><CameraIcon size={16} weight="bold" aria-hidden="true" /></span>
      </button>
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1} aria-label="프로필 사진 파일" disabled={busy} onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) save(file);
      }} />
      <span id="me-photo-hint" className="sr-only">PNG, JPG, WebP · 최대 2MB</span>
      {busy ? <span className="me-photo-status" role="status">저장 중…</span> : user.avatar_url ? <button type="button" className="me-photo-reset" onClick={() => save(null)}>사진 삭제</button> : null}
    </div>
  );
}

function HeadRow({ cols }) {
  return (
    <li className="me-row me-table-head" role="row" aria-hidden="true">
      {cols.map((c, i) => <span key={i} className={c.right ? "is-right" : ""}>{c.label}</span>)}
    </li>
  );
}

function CreatedTab({ rows, now, onOpen }) {
  if (rows.length === 0) {
    return (
      <EmptyState title="아직 등록한 매크로가 없어요" action={<Link to="/builder" className="btn btn-m btn-secondary">매크로 만들기</Link>}>
        빌더에서 만들어 리더보드에 올리면 여기 쌓여요.
      </EmptyState>
    );
  }
  return (
    <ul className="me-table is-created" aria-label="만든 매크로">
      <HeadRow cols={[{ label: "" }, { label: "매크로" }, { label: "판매", right: true }, { label: "수익", right: true }, { label: "등록", right: true }, { label: "" }]} />
      {rows.map((m) => (
        <li key={m.entry_id} className="me-row">
          <CoinIcon symbol={m.symbol} size={36} alt="" />
          <div className="me-main">
            <span className="me-title num">{m.symbol}</span>
            <span className="me-sub">{m.human_summary}</span>
          </div>
          <span className="me-cell num is-right">{m.sales}건</span>
          <span className={`me-cell num is-right${m.earned > 0 ? " me-credit" : ""}`}>{m.earned > 0 ? signedPoints(m.earned) : formatPoints(0)}</span>
          <Stamp value={m.created_ms ?? m.created_kst} now={now} className="is-right" />
          <button type="button" onClick={() => onOpen(m.macro)} disabled={!m.macro} className="btn btn-s btn-secondary">빌더에서 열기</button>
        </li>
      ))}
    </ul>
  );
}

function PurchasedTab({ rows, now, onOpen }) {
  if (rows.length === 0) {
    return (
      <EmptyState title="언락한 매크로가 없어요" action={<Link to="/leaderboard" className="btn btn-m btn-secondary">리더보드 보기</Link>}>
        리더보드에서 전략을 열면 여기서 다시 볼 수 있어요.
      </EmptyState>
    );
  }
  return (
    <ul className="me-table is-purchased" aria-label="구매한 매크로">
      <HeadRow cols={[{ label: "" }, { label: "매크로" }, { label: "지불", right: true }, { label: "언락", right: true }, { label: "" }]} />
      {rows.map((m, i) => (
        <li key={`${m.entry_id}-${i}`} className="me-row">
          <CoinIcon symbol={m.symbol} size={36} alt="" />
          <div className="me-main">
            <span className="me-title"><span className="num">{m.symbol}</span> <span className="me-seller">@{m.seller}</span></span>
            <span className="me-sub">{m.human_summary}</span>
          </div>
          <span className="me-cell num is-right">{signedPoints(-m.price)}</span>
          <Stamp value={m.unlocked_at} now={now} className="is-right" />
          <button type="button" onClick={() => onOpen(m.macro)} disabled={!m.macro} className="btn btn-s btn-secondary">빌더로 복사</button>
        </li>
      ))}
    </ul>
  );
}

function SalesTab({ rows, now }) {
  if (rows.length === 0) {
    return <EmptyState title="아직 판매가 없어요">누군가 내 매크로를 언락하면 언락 금액의 70%가 들어와요.</EmptyState>;
  }
  return (
    <ul className="me-table is-sales" aria-label="판매 내역">
      <HeadRow cols={[{ label: "시각" }, { label: "내용" }, { label: "수익", right: true }]} />
      {rows.map((s, i) => (
        <li key={`${s.entry_id}-${i}`} className="me-row">
          <Stamp value={s.at} now={now} />
          <span className="me-cell me-text">
            <b>@{s.buyer}</b> 님이 <b className="num">{s.symbol}</b> 매크로를 언락
          </span>
          <span className="me-cell num is-right me-credit">{signedPoints(s.earned)}</span>
        </li>
      ))}
    </ul>
  );
}

function LedgerTab({ rows, now, symbolByEntry }) {
  if (rows.length === 0) {
    return <EmptyState title="포인트 변동이 없어요" />;
  }
  return (
    <ul className="me-table is-ledger" aria-label="포인트 내역">
      <HeadRow cols={[{ label: "시각" }, { label: "내용" }, { label: "변동", right: true }, { label: "잔액", right: true }]} />
      {rows.map((l, i) => (
        <li key={i} className="me-row">
          <Stamp value={l.created_at} now={now} />
          <span className="me-cell me-text">{ledgerLabel(l, symbolByEntry)}</span>
          <span className={`me-cell num is-right ${l.delta >= 0 ? "me-credit" : "me-debit"}`}>{signedPoints(l.delta)}</span>
          <span className="me-cell num is-right me-balance">{formatPoints(l.balance_after)}</span>
        </li>
      ))}
    </ul>
  );
}

function PostsTab({ rows, now }) {
  if (rows.length === 0) {
    return (
      <EmptyState title="아직 쓴 글이 없어요" action={<Link to="/board/write" className="btn btn-m btn-secondary">글쓰기</Link>}>
        게시판에 남긴 글이 여기 모여요.
      </EmptyState>
    );
  }
  return (
    <ul className="board-table me-posts" aria-label="게시글">
      <li className="board-head" role="row" aria-hidden="true">
        <span className="board-col-no">번호</span>
        <span className="board-col-title">제목</span>
        <span className="board-col-time">시각</span>
      </li>
      {rows.map((p) => {
        const full = fullKst(p.created_ms);
        return (
          <li key={p.id} className="board-item">
            <Link to={`/board/${p.id}`} className="board-row" aria-label={`${p.title}${p.comment_count > 0 ? `, 댓글 ${p.comment_count}개` : ""}${p.has_image ? ", 사진 첨부" : ""}, ${full}`}>
              <span className="board-no num" aria-hidden="true">{p.id}</span>
              <span className="board-title">
                <span className="board-title-text">{p.title}</span>
                {p.comment_count > 0 ? <span className="board-count num" aria-hidden="true">{p.comment_count}</span> : null}
                {p.has_image ? <span className="board-mark" aria-hidden="true"><ImageIcon /></span> : null}
              </span>
              <time className="board-time num" dateTime={full ? new Date(p.created_ms).toISOString() : undefined} title={full || undefined} aria-hidden="true">{stampKst(p.created_ms, now)}</time>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

// 불러오는 동안의 뼈대 — 머리(아바타·이름·수치)와 행 다섯 줄. 회전 대신 자리를 잡아 둔다.
function Skeleton() {
  return (
    <div className="me-page" aria-hidden="true">
      <div className="me-head">
        <span className="me-skeleton is-avatar" />
        <div className="me-id"><span className="me-skeleton" style={{ width: 200, height: 36 }} /><span className="me-skeleton" style={{ width: 280, height: 16 }} /></div>
        <span className="me-skeleton" style={{ width: 160, height: 44 }} />
      </div>
      <ul className="me-table" style={{ "--me-cols": "36px minmax(0,1fr) 120px" }}>
        {Array.from({ length: 5 }, (_, i) => (
          <li key={i} className="me-row"><span className="me-skeleton is-round" /><span className="me-skeleton" style={{ width: "48%", height: 14 }} /><span className="me-skeleton" style={{ width: 80, height: 14, justifySelf: "end" }} /></li>
        ))}
      </ul>
    </div>
  );
}

export default function MyPage() {
  const { token, user: authUser } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [photoError, setPhotoError] = useState("");
  const [now, setNow] = useState(() => Date.now());

  const paramTab = searchParams.get("tab");
  const tab = TAB_KEYS.has(paramTab) ? paramTab : "created";

  useEffect(() => {
    setData(null);
    setError("");
    setPhotoError("");
    if (!token) {
      navigate("/login?next=%2Fmypage");
      return;
    }
    let alive = true;
    const controller = new AbortController();
    const userAtStart = getAuthUser();
    api.myDashboard({ signal: controller.signal })
      .then((d) => {
        if (!alive || getToken() !== token) return;
        const currentUser = getAuthUser();
        const user = currentUser?.id === d.user.id && currentUser.avatar_url !== userAtStart?.avatar_url && currentUser.avatar_url !== undefined
          ? { ...d.user, avatar_url: currentUser.avatar_url }
          : d.user;
        setData({ ...d, user });
        setNow(Date.now());
        updateAuthUser(user);
      })
      .catch((e) => {
        if (alive) setError(String(e.message || e));
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [navigate, token]);

  // 포인트 내역의 `entry:123` 을 종목명으로 — 내가 만든 것과 산 것 양쪽에서 찾는다.
  const symbolByEntry = useMemo(() => {
    const map = {};
    for (const m of data?.created || []) map[m.entry_id] = m.symbol;
    for (const m of data?.purchased || []) map[m.entry_id] = m.symbol;
    return map;
  }, [data]);

  if (!token) return null;
  if (error) return <ErrorNote>내 활동을 불러오지 못했어요: {error}</ErrorNote>;
  if (!data) return <Skeleton />;

  const { tier, totals, created, purchased, sales, ledger, my_posts = [] } = data;
  const user = authUser?.id === data.user.id && authUser.avatar_url !== undefined
    ? { ...data.user, avatar_url: authUser.avatar_url }
    : data.user;
  const updateAvatar = (updatedUser) => {
    setData((previous) => ({ ...previous, user: { ...previous.user, avatar_url: updatedUser.avatar_url } }));
    updateAuthUser({ ...getAuthUser(), avatar_url: updatedUser.avatar_url });
  };
  const counts = { created: created.length, purchased: purchased.length, sales: sales.length, ledger: ledger.length, posts: my_posts.length };
  const openInBuilder = (macro) => navigate("/builder", { state: { macro } });
  const pickTab = (key) => setSearchParams(key === "created" ? {} : { tab: key }, { replace: true });

  return (
    <div className="me-page">
      {/* 머리 — 이름이 곧 제목(다른 화면의 PageHeader 제목과 같은 크기). 오른쪽은 포인트·수익·판매. */}
      <header className="me-head">
        <AvatarEditor key={token} user={user} onUpdate={updateAvatar} onError={setPhotoError} />
        <div className="me-id">
          <h1 className="me-name">
            {user.username}
          </h1>
          <p className="me-meta">
            <span>{user.email}</span>
            {joinedLabel(user.created_at) ? <span className="num">{joinedLabel(user.created_at)}</span> : null}
          </p>
          <TierGuide tier={tier} />
        </div>
        <dl className="me-stats">
          <div className="me-stat is-points"><dt>보유 포인트</dt><dd className="num">{formatPoints(user.points_balance)}</dd></div>
          <div className="me-stat"><dt>판매 수익</dt><dd className="num">{formatPoints(totals.earned)}</dd></div>
          <div className="me-stat"><dt>누적 판매</dt><dd className="num">{totals.sales}건</dd></div>
        </dl>
      </header>
      {photoError ? <p className="me-photo-error" role="alert">{photoError}</p> : null}

      <div className="me-tabs">
        <div className="seg" role="tablist" aria-label="내 활동 종류">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={`me-tab-${t.key}`}
              aria-selected={tab === t.key}
              aria-controls="me-panel"
              className={`seg-item${tab === t.key ? " seg-item-on" : ""}`}
              onClick={() => pickTab(t.key)}
            >
              {t.label}<span className="me-tab-count num">{counts[t.key]}</span>
            </button>
          ))}
        </div>
      </div>

      <section id="me-panel" role="tabpanel" aria-labelledby={`me-tab-${tab}`} className="me-panel">
        {tab === "created" && <CreatedTab rows={created} now={now} onOpen={openInBuilder} />}
        {tab === "purchased" && <PurchasedTab rows={purchased} now={now} onOpen={openInBuilder} />}
        {tab === "sales" && <SalesTab rows={sales} now={now} />}
        {tab === "ledger" && <LedgerTab rows={ledger} now={now} symbolByEntry={symbolByEntry} />}
        {tab === "posts" && <PostsTab rows={my_posts} now={now} />}
      </section>
    </div>
  );
}
