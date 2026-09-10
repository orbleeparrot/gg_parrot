import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { clearAuth, getAuthUser, getToken, setAuth, useAuth, updateAuthUser, mergeFetchedAuthUser } from "../lib/auth.js";
import { CameraIcon } from "@phosphor-icons/react/dist/csr/Camera";
import { CaretDownIcon } from "@phosphor-icons/react/dist/csr/CaretDown";
import { CaretRightIcon } from "@phosphor-icons/react/dist/csr/CaretRight";
import { PlusIcon } from "@phosphor-icons/react/dist/csr/Plus";
import { PlantIcon } from "@phosphor-icons/react/dist/csr/Plant";
import { MedalIcon } from "@phosphor-icons/react/dist/csr/Medal";
import { DiamondIcon } from "@phosphor-icons/react/dist/csr/Diamond";
import {
  formatPoints, fullKst, joinedLabel, ledgerLabel, signedPoints, stampKst, tierNextLabel, tierStepAt, tierSteps,
} from "../lib/profileText.js";
import { EmptyState, ErrorNote } from "../components/Page.jsx";
import CoinIcon from "../components/CoinIcon.jsx";
import UserAvatar from "../components/UserAvatar.jsx";
import ProfileEditor, { PasswordChangeDialog, DeleteAccountDialog } from "../components/ProfileEditor.jsx";
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/csr/PencilSimple";
import { GearSixIcon } from "@phosphor-icons/react/dist/csr/GearSix";
import { ImageIcon } from "../components/boardIcons.jsx";
import "./Board.css"; // 게시글 탭은 게시판 목록 문법(.board-table)을 그대로 쓴다
import "./MyPage.css";
import "./MyPageMobile.css";

// 내 활동의 종류와 개수를 한 탐색 안에서 보여 준다.
const TABS = [
  { key: "created", label: "만든 매크로" },
  { key: "purchased", label: "구매한 매크로" },
  { key: "sales", label: "판매 내역" },
  { key: "ledger", label: "포인트 내역" },
  { key: "posts", label: "게시글" },
];
const TAB_KEYS = new Set(TABS.map((t) => t.key));

function Stamp({ value, now, className = "", label }) {
  const full = fullKst(value);
  return (
    <time className={`me-time num ${className}`} dateTime={full ? new Date(value).toISOString() : undefined} title={full || undefined}>
      {label ? <span className="me-mobile-label">{label}</span> : null}<span>{stampKst(value, now)}</span>
    </time>
  );
}

function ActivityValue({ label, children, className = "" }) {
  return <span className={`me-cell me-activity-value ${className}`}><span className="me-mobile-label">{label}</span><span className="num">{children}</span></span>;
}

function TierIcon({ name }) {
  const Icon = name === "새싹" ? PlantIcon : name === "다이아" ? DiamondIcon : MedalIcon;
  const tone = { "새싹": "seed", "브론즈": "bronze", "실버": "silver", "골드": "gold", "다이아": "diamond" }[name] || "silver";
  return <Icon className={`me-tier-icon is-${tone}`} size={24} weight="duotone" aria-hidden="true" />;
}

function TierGuide({ tier, open }) {
  return (
    <div className="me-tier" id="me-tier-guide" hidden={!open}>
      <p className="me-tier-next">{tierNextLabel(tier)}</p>
      <ol className="me-tier-list" aria-label="판매 등급별 조건">
        {tierSteps(tier).map((step) => (
          <li key={step.name} className={step.state === "current" ? "is-current" : undefined} aria-current={step.state === "current" ? "step" : undefined}>
            <TierIcon name={step.name} />
            <b>{step.name}</b>
            <span className="num">{tierStepAt(step.at)}</span>
          </li>
        ))}
      </ol>
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
      <EmptyState title="아직 등록한 매크로가 없어요" action={<Link to="/builder" className="btn btn-s btn-secondary">매크로 만들기</Link>} />
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
          <div className="me-row-details">
          <ActivityValue label="판매" className="is-right me-row-sales">{m.sales}건</ActivityValue>
          <ActivityValue label="수익" className={`is-right me-row-earned${m.earned > 0 ? " me-credit" : ""}`}>{m.earned > 0 ? signedPoints(m.earned) : formatPoints(0)}</ActivityValue>
          <Stamp value={m.created_ms ?? m.created_kst} now={now} className="is-right me-row-date" label="등록" />
          </div>
          <button type="button" onClick={() => onOpen(m.macro)} disabled={!m.macro} className="btn btn-s btn-secondary">빌더에서 열기</button>
        </li>
      ))}
    </ul>
  );
}

function PurchasedTab({ rows, now, onOpen }) {
  if (rows.length === 0) {
    return (
      <EmptyState title="구매한 매크로가 없어요" action={<Link to="/leaderboard" className="btn btn-s btn-secondary">리더보드 보기</Link>} />
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
          <div className="me-row-details">
          <ActivityValue label="구매 금액" className="is-right me-row-price">{signedPoints(-m.price)}</ActivityValue>
          <Stamp value={m.unlocked_at} now={now} className="is-right me-row-date" label="구매" />
          </div>
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
          <Stamp value={s.at} now={now} className="me-row-date" />
          <span className="me-cell me-text">
            <b>@{s.buyer}</b> 님이 <b className="num">{s.symbol}</b> 매크로를 언락
          </span>
          <ActivityValue label="수익" className="is-right me-credit me-row-earned">{signedPoints(s.earned)}</ActivityValue>
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
          <Stamp value={l.created_at} now={now} className="me-row-date" />
          <span className="me-cell me-text">{ledgerLabel(l, symbolByEntry)}</span>
          <ActivityValue label="변동" className={`is-right me-row-change ${l.delta >= 0 ? "me-credit" : "me-debit"}`}>{signedPoints(l.delta)}</ActivityValue>
          <ActivityValue label="잔액" className="is-right me-balance me-row-balance">{formatPoints(l.balance_after)}</ActivityValue>
        </li>
      ))}
    </ul>
  );
}

function PostsTab({ rows, now }) {
  if (rows.length === 0) {
    return (
      <EmptyState title="아직 쓴 글이 없어요" action={<Link to="/board/write" className="btn btn-s btn-secondary">글쓰기</Link>} />
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
        <div className="me-profile">
          <span className="me-skeleton is-avatar" />
          <div className="me-id"><span className="me-skeleton" style={{ width: 200, height: 36 }} /><span className="me-skeleton" style={{ width: 160, height: 16 }} /></div>
          <div className="me-stats">{Array.from({ length: 3 }, (_, i) => <div className="me-stat" key={i}><span className="me-skeleton" style={{ width: 64 }} /><span className="me-skeleton" style={{ width: 96, height: 28 }} /></div>)}</div>
          <div className="me-head-actions"><span className="me-skeleton" style={{ width: 112, height: 36 }} /></div>
        </div>
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
  const leavingAccount = useRef(false);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [editor, setEditor] = useState(null);
  const [notice, setNotice] = useState("");
  const tabListRef = useRef(null);
  const [tabOverflow, setTabOverflow] = useState({ visible: false, next: false });
  const [accountOpen, setAccountOpen] = useState(false);
  const [tierOpen, setTierOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const paramTab = searchParams.get("tab");
  const tab = TAB_KEYS.has(paramTab) ? paramTab : "created";

  useEffect(() => {
    const list = tabListRef.current;
    if (!list) return;
    const updateOverflow = () => {
      const first = list.firstElementChild.getBoundingClientRect();
      const last = list.lastElementChild.getBoundingClientRect();
      const visible = last.right - first.left > list.parentElement.clientWidth + 1;
      const next = last.right > list.getBoundingClientRect().right + 1;
      setTabOverflow((previous) => previous.visible === visible && previous.next === next ? previous : { visible, next });
    };
    const revealSelected = () => {
      const selected = list.querySelector('[aria-selected="true"]');
      if (!selected) return;
      const frame = list.getBoundingClientRect();
      const item = selected.getBoundingClientRect();
      if (item.left < frame.left) list.scrollLeft += item.left - frame.left;
      else if (item.right > frame.right) list.scrollLeft += item.right - frame.right;
      updateOverflow();
    };
    revealSelected();
    const observer = new ResizeObserver(revealSelected);
    observer.observe(list);
    observer.observe(list.parentElement);
    list.addEventListener("scroll", updateOverflow, { passive: true });
    return () => {
      observer.disconnect();
      list.removeEventListener("scroll", updateOverflow);
    };
  }, [tab, data]);

  useEffect(() => {
    setData(null);
    setError("");
    setEditor(null);
    setAccountOpen(false);
    setTierOpen(false);
    if (!token) {
      if (!leavingAccount.current) navigate("/login?next=%2Fmypage");
      return;
    }
    let alive = true;
    const controller = new AbortController();
    const userAtStart = getAuthUser();
    api.myDashboard({ signal: controller.signal })
      .then((d) => {
        if (!alive || getToken() !== token) return;
        const user = mergeFetchedAuthUser(d.user, userAtStart);
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
  const user = authUser?.id === data.user.id ? { ...data.user, ...authUser } : data.user;
  const saveProfile = (updatedUser) => {
    setData((previous) => ({ ...previous, user: updatedUser }));
    updateAuthUser(updatedUser);
    setNotice("프로필을 저장했어요.");
  };
  const savePassword = (response) => {
    setAuth(response.token, response.user);
    setNotice("비밀번호를 변경했어요.");
  };
  const logout = () => { leavingAccount.current = true; clearAuth(); navigate("/login", { replace: true }); };
  const deleted = () => { leavingAccount.current = true; clearAuth(); navigate("/login?notice=" + encodeURIComponent("회원 탈퇴가 완료됐어요."), { replace: true }); };
  const counts = { created: created.length, purchased: purchased.length, sales: sales.length, ledger: ledger.length, posts: my_posts.length };
  const openInBuilder = (macro) => navigate("/builder", { state: { macro } });
  const pickTab = (key) => setSearchParams(key === "created" ? {} : { tab: key }, { replace: true });
  const revealNextTab = () => {
    const list = tabListRef.current;
    const edge = list.getBoundingClientRect().right;
    const next = Array.from(list.children).find((item) => item.getBoundingClientRect().right > edge + 1);
    if (!next) return;
    pickTab(next.id.replace("me-tab-", ""));
    next.focus({ preventScroll: true });
  };
  const moveTab = (event, index) => {
    const next = { ArrowRight: (index + 1) % TABS.length, ArrowLeft: (index + TABS.length - 1) % TABS.length, Home: 0, End: TABS.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    pickTab(TABS[next].key);
    document.getElementById(`me-tab-${TABS[next].key}`)?.focus({ preventScroll: true });
  };
  // Eight monospaced characters exceed one of the three columns at 320px.
  const longStats = [formatPoints(user.points_balance), formatPoints(totals.earned), `${totals.sales}건`].some((value) => value.length > 7);

  return (
    <div className="me-page">
      {/* 신원 → 같은 기준선의 수치 → 활동. 설정과 등급 조건은 필요할 때 펼친다. */}
      <header className="me-head">
        <div className="me-profile">
        <button type="button" className="me-avatar-edit" aria-label="프로필 사진 변경" aria-haspopup="dialog" onClick={() => setEditor("profile")}>
          <UserAvatar src={user.avatar_url} name={user.username} size={128} className="me-avatar" />
          <span className="me-avatar-camera"><CameraIcon size={16} weight="bold" aria-hidden="true" /></span>
        </button>
        <div className="me-id">
          <div className="me-name-row">
            <h1 className="me-name">{user.username}</h1>
            <button type="button" className="me-tier-toggle" aria-label={`등급 안내 · ${tier.name}`} aria-expanded={tierOpen} aria-controls="me-tier-guide" onClick={() => setTierOpen(!tierOpen)}><TierIcon name={tier.name} /><span>{tier.name}</span><CaretDownIcon size={12} aria-hidden="true" /></button>
          </div>
          <p className="me-meta">
            {joinedLabel(user.created_at) ? <span className="num">{joinedLabel(user.created_at)}</span> : null}
          </p>
          {user.bio ? <p className="me-bio">{user.bio}</p> : null}
        </div>
        <dl className={`me-stats${longStats ? " has-long-values" : ""}`}>
          <div className="me-stat is-points"><dt>보유 포인트</dt><dd className="num">{formatPoints(user.points_balance)}</dd></div>
          <div className="me-stat"><dt>판매 수익</dt><dd className="num">{formatPoints(totals.earned)}</dd></div>
          <div className="me-stat"><dt>누적 판매</dt><dd className="num">{totals.sales}건</dd></div>
        </dl>
        <div className="me-head-actions">
          <button type="button" className="btn btn-s btn-secondary me-edit-button" aria-label="프로필 편집" aria-haspopup="dialog" onClick={() => setEditor("profile")}><PencilSimpleIcon size={18} aria-hidden="true" /><span>프로필 편집</span></button>
          <button type="button" className="btn btn-s btn-ghost me-settings-toggle" aria-label="계정 설정" aria-expanded={accountOpen} aria-controls="me-account-settings" onClick={() => setAccountOpen(!accountOpen)}><GearSixIcon size={20} aria-hidden="true" /><span>계정 설정</span></button>
        </div>
        </div>
        <TierGuide tier={tier} open={tierOpen} />
      </header>
      <div className="me-account-settings" id="me-account-settings" hidden={!accountOpen}>
        <section className="me-account" aria-labelledby="me-account-title">
        <div className="me-account-id">
          <h2 id="me-account-title">계정 설정</h2>
          <span>{user.email}</span>
          <span className="me-signin-method">{user.can_change_password ? "이메일 · 비밀번호" : "Google 로그인"}</span>
        </div>
        <div className="me-account-actions">
          {user.can_change_password ? <button type="button" className="btn btn-s btn-secondary" aria-haspopup="dialog" onClick={() => setEditor("password")}>비밀번호 변경</button> : null}
          <button type="button" className="btn btn-s btn-ghost" onClick={logout}>로그아웃</button>
          <button type="button" className="btn btn-s btn-ghost me-delete-account" aria-haspopup="dialog" onClick={() => setEditor("delete")}>회원 탈퇴</button>
        </div>
        </section>
      </div>
      {notice ? <p className="me-notice" role="status">{notice}</p> : null}
      {editor === "profile" ? <ProfileEditor key={token} user={user} onClose={() => setEditor(null)} onSaved={saveProfile} /> : null}
      {editor === "password" && user.can_change_password ? <PasswordChangeDialog key={token} user={user} onClose={() => setEditor(null)} onSaved={savePassword} /> : null}
      {editor === "delete" ? <DeleteAccountDialog key={token} user={user} onClose={() => setEditor(null)} onDeleted={deleted} /> : null}

      <div className="me-tabs">
        <div className="me-tab-list" role="tablist" aria-label="내 활동 종류" ref={tabListRef}>
          {TABS.map((t, index) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={`me-tab-${t.key}`}
              aria-selected={tab === t.key}
              aria-controls="me-panel"
              tabIndex={tab === t.key ? 0 : -1}
              className="me-tab"
              onClick={() => pickTab(t.key)}
              onKeyDown={(event) => moveTab(event, index)}
            >
              {t.label}<span className="me-tab-count num">{counts[t.key]}</span>
            </button>
          ))}
        </div>
        {tabOverflow.visible ? <button type="button" className="me-tab-next" aria-label="다음 활동 보기" disabled={!tabOverflow.next} onClick={revealNextTab}><CaretRightIcon size={22} aria-hidden="true" /></button> : null}
      </div>

      <section id="me-panel" role="tabpanel" tabIndex={0} aria-labelledby={`me-tab-${tab}`} className="me-panel">
        {tab === "created" && created.length > 0 ? <div className="me-panel-actions"><Link to="/builder" className="me-create-link"><PlusIcon size={18} aria-hidden="true" />매크로 만들기</Link></div> : null}
        {tab === "created" && <CreatedTab rows={created} now={now} onOpen={openInBuilder} />}
        {tab === "purchased" && <PurchasedTab rows={purchased} now={now} onOpen={openInBuilder} />}
        {tab === "sales" && <SalesTab rows={sales} now={now} />}
        {tab === "ledger" && <LedgerTab rows={ledger} now={now} symbolByEntry={symbolByEntry} />}
        {tab === "posts" && <PostsTab rows={my_posts} now={now} />}
      </section>
    </div>
  );
}
