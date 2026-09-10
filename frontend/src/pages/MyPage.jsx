import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { clearAuth, getAuthUser, getToken, mergeFetchedAuthUser, updateAuthUser, useAuth } from "../lib/auth.js";
import { KeyIcon } from "@phosphor-icons/react/dist/csr/Key";
import { SignOutIcon } from "@phosphor-icons/react/dist/csr/SignOut";
import { RunnerKeyPanel } from "../components/RunnerSessions.jsx";
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/csr/PencilSimple";
import { GearSixIcon } from "@phosphor-icons/react/dist/csr/GearSix";
import { SquaresFourIcon } from "@phosphor-icons/react/dist/csr/SquaresFour";
import { ArrowsLeftRightIcon } from "@phosphor-icons/react/dist/csr/ArrowsLeftRight";
import { ArticleIcon } from "@phosphor-icons/react/dist/csr/Article";
import { CoinsIcon } from "@phosphor-icons/react/dist/csr/Coins";
import { TrendUpIcon } from "@phosphor-icons/react/dist/csr/TrendUp";
import { ReceiptIcon } from "@phosphor-icons/react/dist/csr/Receipt";
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/csr/ArrowUpRight";
import { PlusIcon } from "@phosphor-icons/react/dist/csr/Plus";
import { CaretRightIcon } from "@phosphor-icons/react/dist/csr/CaretRight";
import { XIcon } from "@phosphor-icons/react/dist/csr/X";
import { PlantIcon } from "@phosphor-icons/react/dist/csr/Plant";
import { MedalIcon } from "@phosphor-icons/react/dist/csr/Medal";
import { DiamondIcon } from "@phosphor-icons/react/dist/csr/Diamond";
import { ChatCircleIcon } from "@phosphor-icons/react/dist/csr/ChatCircle";
import { ImageIcon } from "@phosphor-icons/react/dist/csr/Image";
import { formatPoints, fullKst, joinedLabel, ledgerLabel, signedPoints, stampKst, tierNextLabel, tierStepAt, tierSteps, toMs } from "../lib/profileText.js";
import { ErrorNote } from "../components/Page.jsx";
import CoinIcon from "../components/CoinIcon.jsx";
import UserAvatar from "../components/UserAvatar.jsx";
import "./MyPage.css";
import "./MyPageMobile.css";

const FILTERS = {
  macros: [{ key: "created", label: "만든 매크로" }, { key: "purchased", label: "구매한 매크로" }],
  points: [{ key: "sales", label: "판매 내역" }, { key: "ledger", label: "포인트 내역" }],
};
const SECTIONS = [
  { key: "macros", label: "매크로", first: "created", Icon: SquaresFourIcon },
  { key: "points", label: "포인트·판매", first: "sales", Icon: ArrowsLeftRightIcon },
  { key: "posts", label: "게시글", first: "posts", Icon: ArticleIcon },
];
const TAB_KEYS = new Set(["created", "purchased", "sales", "ledger", "posts"]);

function Stamp({ value, now, className = "" }) {
  const ms = toMs(value);
  return <time className={"me-time num " + className} dateTime={ms == null ? undefined : new Date(ms).toISOString()} title={fullKst(value) || undefined}>{stampKst(value, now)}</time>;
}

function TierIcon({ name, size = 24 }) {
  const Icon = name === "새싹" ? PlantIcon : name === "다이아" ? DiamondIcon : MedalIcon;
  const tone = { 새싹: "seed", 브론즈: "bronze", 실버: "silver", 골드: "gold", 다이아: "diamond" }[name] || "silver";
  return <Icon className={"me-tier-icon is-" + tone} size={size} weight="duotone" aria-hidden="true" />;
}

function TierDialog({ tier, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement;
    const overflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    dialog.showModal();
    return () => {
      dialog.close();
      document.documentElement.style.overflow = overflow;
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  return createPortal(
    <dialog className="me-tier-dialog" ref={ref} aria-labelledby="me-tier-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <header><h2 id="me-tier-title">판매 등급</h2><button type="button" className="me-icon-button" aria-label="닫기" onClick={onClose}><XIcon size={22} aria-hidden="true" /></button></header>
      <p className="me-tier-next">{tierNextLabel(tier)}</p>
      <ol className="me-tier-list" aria-label="판매 등급별 조건">
        {tierSteps(tier).map((step) => (
          <li key={step.name} className={step.state === "current" ? "is-current" : ""} aria-current={step.state === "current" ? "step" : undefined}>
            <TierIcon name={step.name} size={32} /><b>{step.name}</b><span>{tierStepAt(step.at)}</span>
          </li>
        ))}
      </ol>
    </dialog>, document.body,
  );
}

function EmptyActivity({ kind }) {
  const data = {
    created: [SquaresFourIcon, "아직 만든 매크로가 없어요"],
    purchased: [SquaresFourIcon, "구매한 매크로가 없어요"],
    sales: [ReceiptIcon, "아직 판매 내역이 없어요"],
    ledger: [CoinsIcon, "포인트 변동이 없어요"],
    posts: [ArticleIcon, "아직 쓴 글이 없어요"],
  };
  const [Icon, title] = data[kind];
  return <div className="me-empty"><Icon size={36} weight="light" aria-hidden="true" /><p>{title}</p>{kind === "purchased" ? <Link to="/leaderboard">리더보드 보기<ArrowUpRightIcon size={16} aria-hidden="true" /></Link> : null}</div>;
}

function MacroCards({ rows, purchased, now, onOpen }) {
  if (!rows.length) return <EmptyActivity kind={purchased ? "purchased" : "created"} />;
  return (
    <ul className="me-macro-grid" aria-label={purchased ? "구매한 매크로" : "만든 매크로"}>
      {rows.map((macro, index) => (
        <li className="me-macro-card" key={String(macro.entry_id) + "-" + index}>
          <div className="me-macro-heading">
            <CoinIcon symbol={macro.symbol} size={36} alt="" />
            <div><h3 className="num">{macro.symbol}</h3>{purchased ? <span className="me-seller">@{macro.seller}</span> : <Stamp value={macro.created_ms ?? macro.created_kst} now={now} />}</div>
          </div>
          <p className="me-macro-description">{macro.human_summary}</p>
          <dl className="me-macro-metrics">
            {purchased ? <><div><dt>구매 금액</dt><dd className="num">{formatPoints(macro.price)}</dd></div><div><dt>구매일</dt><dd><Stamp value={macro.unlocked_at} now={now} /></dd></div></> : <><div><dt>판매</dt><dd className="num">{macro.sales}건</dd></div><div><dt>판매 수익</dt><dd className={"num " + (macro.earned > 0 ? "me-credit" : "")}>{macro.earned > 0 ? signedPoints(macro.earned) : formatPoints(0)}</dd></div></>}
          </dl>
          <footer><button type="button" className="me-card-action" onClick={() => onOpen(macro.macro)} disabled={!macro.macro}>{macro.macro ? (purchased ? "빌더로 복사" : "빌더에서 열기") : "불러오기 불가"}<ArrowUpRightIcon size={18} aria-hidden="true" /></button></footer>
        </li>
      ))}
    </ul>
  );
}

function Transactions({ rows, ledger, now, symbolByEntry }) {
  if (!rows.length) return <EmptyActivity kind={ledger ? "ledger" : "sales"} />;
  return (
    <ul className={"me-transactions " + (ledger ? "is-ledger" : "is-sales")} aria-label={ledger ? "포인트 내역" : "판매 내역"}>
      {rows.map((row, index) => (
        <li className="me-transaction" key={index}>
          <span className="me-transaction-icon" aria-hidden="true">{ledger ? <CoinsIcon size={22} /> : <ReceiptIcon size={22} />}</span>
          <div className="me-transaction-main">
            <p>{ledger ? ledgerLabel(row, symbolByEntry) : <><b>{row.symbol}</b> 매크로 판매</>}</p>
            <div>{!ledger ? <span>@{row.buyer}</span> : null}<Stamp value={ledger ? row.created_at : row.at} now={now} /></div>
          </div>
          <div className="me-transaction-amount"><strong className={"num " + ((ledger ? row.delta : row.earned) >= 0 ? "me-credit" : "me-debit")}>{signedPoints(ledger ? row.delta : row.earned)}</strong>{ledger ? <span>잔액 <span className="num">{formatPoints(row.balance_after)}</span></span> : null}</div>
        </li>
      ))}
    </ul>
  );
}

function Posts({ rows, now }) {
  if (!rows.length) return <EmptyActivity kind="posts" />;
  return <ul className="me-posts" aria-label="게시글">{rows.map((post) => (
    <li className="me-post" key={post.id}>
      <Link to={"/board/" + post.id} aria-label={post.title + (post.comment_count > 0 ? ", 댓글 " + post.comment_count + "개" : "") + (post.has_image ? ", 사진 첨부" : "") + ", " + fullKst(post.created_ms)}>
        <div className="me-post-title"><h3>{post.title}</h3>{post.has_image ? <ImageIcon size={18} aria-hidden="true" /> : null}<ArrowUpRightIcon size={20} aria-hidden="true" /></div>
        <div className="me-post-meta"><Stamp value={post.created_ms} now={now} /><span><ChatCircleIcon size={16} aria-hidden="true" /><span className="num">{post.comment_count || 0}</span></span></div>
      </Link>
    </li>
  ))}</ul>;
}

function Skeleton() {
  return <div className="me-page" aria-hidden="true">
    <div className="me-profile-rail"><div className="me-identity"><span className="me-skeleton is-avatar" /><span className="me-skeleton is-name" /><span className="me-skeleton is-bio" /></div></div>
    <div className="me-content"><div className="me-stats">{[0, 1, 2].map((i) => <div key={i} className="me-stat"><span className="me-skeleton" /><span className="me-skeleton is-value" /></div>)}</div><div className="me-macro-grid">{[0, 1].map((i) => <div key={i} className="me-macro-card me-loading-card"><span className="me-skeleton is-name" /><span className="me-skeleton" /><span className="me-skeleton" /></div>)}</div></div>
  </div>;
}

export default function MyPage() {
  const { token, user: authUser } = useAuth();
  const leavingAccount = useRef(false);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [tierOpen, setTierOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const param = searchParams.get("tab");
  const tab = TAB_KEYS.has(param) ? param : "created";
  const section = tab === "posts" ? "posts" : ["sales", "ledger"].includes(tab) ? "points" : "macros";
  const filters = FILTERS[section] || [];

  useEffect(() => {
    setData(null);
    setError("");
    setTierOpen(false);
    setKeyOpen(false);
    if (!token) { if (!leavingAccount.current) navigate("/login?next=%2Fmypage", { replace: true }); return; }
    let alive = true;
    const controller = new AbortController();
    const userAtStart = getAuthUser();
    api.myDashboard({ signal: controller.signal }).then((value) => {
      if (!alive || getToken() !== token) return;
      const user = mergeFetchedAuthUser(value.user, userAtStart);
      setData({ ...value, user });
      updateAuthUser(user);
      setNow(Date.now());
    }).catch((reason) => { if (alive && reason.name !== "AbortError") setError(String(reason.message || reason)); });
    return () => { alive = false; controller.abort(); };
  }, [navigate, token]);

  const symbolByEntry = useMemo(() => Object.fromEntries([...(data?.created || []), ...(data?.purchased || [])].map((item) => [item.entry_id, item.symbol])), [data]);
  if (!token) return null;
  if (error) return <ErrorNote>내 활동을 불러오지 못했어요: {error}</ErrorNote>;
  if (!data) return <Skeleton />;

  const { tier, totals, created, purchased, sales, ledger, my_posts = [] } = data;
  const user = authUser?.id === data.user.id ? { ...data.user, ...authUser } : data.user;
  const counts = { created: created.length, purchased: purchased.length, sales: sales.length, ledger: ledger.length, posts: my_posts.length };
  const logout = () => { leavingAccount.current = true; clearAuth(); navigate("/login", { replace: true }); };
  const pickTab = (key) => setSearchParams(key === "created" ? {} : { tab: key }, { replace: true });
  const moveTab = (event, index) => {
    const next = { ArrowRight: (index + 1) % filters.length, ArrowLeft: (index + filters.length - 1) % filters.length, Home: 0, End: filters.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    pickTab(filters[next].key);
    document.getElementById("me-tab-" + filters[next].key)?.focus({ preventScroll: true });
  };
  const longStats = [formatPoints(user.points_balance), formatPoints(totals.earned), totals.sales + "건"].some((value) => value.length > 7);
  const title = section === "macros" ? "내 매크로" : section === "points" ? "포인트·판매" : "내 게시글";
  const metrics = [
    { label: "보유 포인트", value: formatPoints(user.points_balance), Icon: CoinsIcon, target: "ledger", points: true },
    { label: "판매 수익", value: formatPoints(totals.earned), Icon: TrendUpIcon, target: "sales" },
    { label: "누적 판매", value: totals.sales + "건", Icon: ReceiptIcon, target: "sales" },
  ];
  return <div className="me-page">
    <aside className="me-profile-rail">
      <div className="me-identity">
        <Link className="me-settings-link me-icon-button" to="/mypage/settings?tab=security" aria-label="프로필 설정"><GearSixIcon size={24} aria-hidden="true" /></Link>
        <Link className="me-portrait-link" to="/mypage/settings" aria-label="프로필 사진 변경"><UserAvatar src={user.avatar_url} name={user.username} size={144} className="me-avatar" /></Link>
        <div className="me-name-line"><h1 className="me-name">{user.username}</h1>
        <button className="me-tier-toggle" type="button" aria-label={"등급 안내 · " + tier.name} aria-haspopup="dialog" onClick={() => setTierOpen(true)}><TierIcon name={tier.name} size={22} /><span>{tier.name}</span><CaretRightIcon size={14} aria-hidden="true" /></button></div>
        {user.bio ? <p className="me-bio">{user.bio}</p> : null}
        <div className="me-identity-footer"><p className="me-joined num">{joinedLabel(user.created_at)}</p></div>
        <div className="me-profile-actions">
          <button type="button" aria-expanded={keyOpen} aria-controls="me-member-key" onClick={() => setKeyOpen(!keyOpen)}><KeyIcon size={18} aria-hidden="true" />회원 키</button>
          <button type="button" onClick={logout}><SignOutIcon size={18} aria-hidden="true" />로그아웃</button>
        </div>
        {keyOpen ? <section id="me-member-key" className="me-member-key" aria-label="회원 키 관리"><RunnerKeyPanel key={token} menu /></section> : null}
      </div>
    </aside>
    <div className="me-content">
      <dl className={"me-stats" + (longStats ? " has-long-values" : "")}>{metrics.map(({ label, value, Icon, target, points }) => <div className={"me-stat" + (points ? " is-points" : "")} key={label}><dt><Icon size={20} aria-hidden="true" />{label}</dt><dd className="num">{value}</dd><button type="button" className="me-stat-link" aria-label={label + " 내역 보기"} onClick={() => pickTab(target)} /></div>)}</dl>
      <nav className="me-section-nav" aria-label="프로필 메뉴">{SECTIONS.map(({ key, label, first, Icon }) => <button key={key} type="button" aria-pressed={section === key} onClick={() => pickTab(first)}><Icon size={22} aria-hidden="true" /><span>{label}</span></button>)}</nav>
      <section className={"me-workspace is-" + section} aria-labelledby="me-workspace-title">
        <header className="me-workspace-heading"><h2 id="me-workspace-title">{title}{section === "posts" ? <span className="num">{my_posts.length}</span> : null}</h2>{section === "macros" ? <Link to="/builder" className="me-create-link" aria-label="매크로 만들기"><PlusIcon size={18} aria-hidden="true" /><span>매크로 만들기</span></Link> : section === "posts" ? <Link to="/board/write" className="me-create-link"><PencilSimpleIcon size={18} aria-hidden="true" /><span>글쓰기</span></Link> : null}</header>
        {filters.length ? <div className="me-filters" role="tablist" aria-label="내 활동 종류">{filters.map((filter, index) => <button key={filter.key} type="button" role="tab" id={"me-tab-" + filter.key} aria-selected={tab === filter.key} aria-controls="me-panel" tabIndex={tab === filter.key ? 0 : -1} onClick={() => pickTab(filter.key)} onKeyDown={(event) => moveTab(event, index)}>{filter.label}<span className="num">{counts[filter.key]}</span></button>)}</div> : null}
        <section className="me-panel" id="me-panel" role={filters.length ? "tabpanel" : "region"} tabIndex={0} aria-labelledby={filters.length ? "me-tab-" + tab : "me-workspace-title"}>
          {section === "macros" ? <MacroCards rows={tab === "purchased" ? purchased : created} purchased={tab === "purchased"} now={now} onOpen={(macro) => navigate("/builder", { state: { macro } })} /> : section === "points" ? <Transactions rows={tab === "ledger" ? ledger : sales} ledger={tab === "ledger"} now={now} symbolByEntry={symbolByEntry} /> : <Posts rows={my_posts} now={now} />}
        </section>
      </section>
    </div>
    {tierOpen ? <TierDialog tier={tier} onClose={() => setTierOpen(false)} /> : null}
  </div>;
}
