// 관리자 대시보드 — 여섯 탭(사용자 · 가입/전환/유지 · 매크로 · 뉴스 수집 · API 비용 · 회원 관리).
// 탭·기간은 주소(?tab&days)에 있고, 문서가 보이는 동안 1분마다 다시 받는다. 숫자는 서버가 계산한 것을
// 그대로 보여 주고, 합계 행처럼 화면에서 더하는 값도 서버 행에서만 더한다(없는 값은 "—").
// 회원 관리는 지표가 아니라 목록이라 쪽·검색·상태 필터까지 주소에 있고(?page&q&status&size), 기간은 무관해 숨긴다.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import {
  AGGREGATION_START, BOARD_STATUS, CHANNEL_DETAIL, CHANNEL_LABELS, COST_METHOD_LABELS, DEVICE_LABELS, ENGINE_STATUS,
  METHOD_LABELS, PAGE_LABELS, PURPOSE_LABELS, fmtDayTimeKst, fmtDuration, fmtInt, fmtKst, fmtLimit, fmtMonthLabel, fmtNum, fmtPct,
  fmtRelative, fmtSignedPct, fmtStamp, fmtTimeKst, fmtTokens, fmtUntil, fmtUsd, labelOf, ratioPct, sumBy, weightedMean,
} from "../lib/adminFormat.js";
import {
  MEMBER_PAGE_SIZES, MEMBER_STATUSES, MEMBER_Q_MAX, blockActionKind, clampPage, memberActionError, memberEmail,
  memberQueryString, memberResultLine, memberSearchParams, memberSignup, memberState, memberTier, pageCount, parseMemberQuery,
} from "../lib/memberList.js";
import { AdminBlock, AdminKpis, AdminTable, AdminTerms, ErrorBlock, RangePicker, Skeleton, StatusPill, TabNav } from "../components/admin/AdminBlocks.jsx";
import { MemberActionDialogs, MemberRowActions } from "../components/admin/MemberActions.jsx";
import {
  BarChart, Donut, FunnelChart, HBarChart, HeatCell, Legend, LineChart, SERIES, StackedChart, bucketHours,
} from "../components/admin/AdminCharts.jsx";
import "./AdminDashboard.css";

const TABS = [
  { key: "users", label: "사용자 지표" },
  { key: "signups", label: "가입 · 전환 · 유지" },
  { key: "macros", label: "매크로 지표" },
  { key: "news", label: "뉴스 수집 현황" },
  { key: "costs", label: "API 비용" },
  { key: "members", label: "회원 관리" },
];
const RANGED = new Set(["users", "signups", "macros"]);
const RANGES = [7, 30, 90];
const COST_MONTHS = 6;
// Gemini 용도는 6개인데 계열색은 s1~s5 뿐이라 여섯 번째는 하늘색(s2)의 반투명으로 만든다 — 라이트·다크 어느 쪽에서도
// s2(진한 하늘색)·s5(옅은 회색)와 구분된다. AdminCharts 가 s6 을 내놓으면 그것을 우선한다.
const SERIES_6 = SERIES.s6 || "rgb(var(--c-sky-700) / 0.45)";
const FETCHERS = {
  users: (days, o) => api.adminUsers(days, o),
  signups: (days, o) => api.adminSignups(days, o),
  macros: (days, o) => api.adminMacros(days, o),
  news: (_days, o) => api.adminNews(o),
  costs: (_days, o) => api.adminCosts(COST_MONTHS, o),
  members: (_days, o, query) => api.adminMembers(query || {}, o),
};

function parseTab(value) {
  return TABS.some((t) => t.key === value) ? value : "users";
}
function parseDays(value) {
  const n = Number(value);
  return RANGES.includes(n) ? n : 30;
}

// 탭·기간별로 받은 응답을 기억해 두고, 돌아오면 먼저 보여 준 뒤 조용히 새로 받는다.
// 돌려주는 값은 항상 "지금 키" 기준으로 고른다 — 탭을 바꾼 직후의 렌더에서 view 는 아직 이전 키의 응답이라,
// 그대로 내보내면 새 탭 컴포넌트가 이전 탭 자료로 한 번 그려진다.
// 회원 관리는 같은 탭에서도 쪽·검색·필터마다 다른 응답이라 그 조회 조건까지 키에 넣는다 —
// 키가 바뀌면 폴러가 새로 서고(진행 중 요청은 abort) 응답은 언제나 "지금 키" 것만 쓰인다.
function useAdminData(tab, days, enabled, query = null) {
  const cacheRef = useRef(new Map());
  const key = `${tab}:${days}:${query ? memberQueryString(query) : ""}`;
  const [view, setView] = useState(() => ({ key, data: null, error: "", loading: true }));
  const [badges, setBadges] = useState({});
  const load = useCallback(async (signal) => {
    try {
      const data = await FETCHERS[tab](days, { signal }, query);
      cacheRef.current.set(key, data);
      setView({ key, data, error: "", loading: false });
      if (tab === "macros") setBadges((b) => ({ ...b, macros: sumBy(data?.sessions, "error") || 0 }));
      if (tab === "news") setBadges((b) => ({ ...b, news: Number(data?.kpis?.tickers_failing) || 0 }));
    } catch (e) {
      if (e?.name === "AbortError") throw e;
      // 실패해도 이 키로 받아 둔 값이 있으면 그대로 보여 준다(다른 키의 응답은 절대 섞지 않는다).
      setView({ key, data: cacheRef.current.get(key) || null, error: e?.message || "불러오지 못했어요", loading: false });
      throw e;
    }
    // query 는 key 에 녹아 있다 — 조회 조건이 바뀌면 key 가 바뀌고, 폴러는 key 로만 다시 선다.
  }, [tab, days, key]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = useAdaptivePolling(load, { intervalMs: 60_000, maxIntervalMs: 60_000, enabled, pollKey: key });
  const current = view.key === key
    ? view
    : { data: cacheRef.current.get(key) || null, error: "", loading: !cacheRef.current.has(key) };
  return { ...current, badges, refresh };
}

export default function AdminDashboard() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = parseTab(params.get("tab"));
  const days = parseDays(params.get("days"));
  const ranged = RANGED.has(tab);
  // 회원 조회 조건은 주소가 원본 — 새로 고쳐도 같은 쪽·검색어·필터가 남는다.
  const memberQuery = useMemo(() => (tab === "members" ? parseMemberQuery(params) : null), [tab, params]);
  const { data, error, loading, badges, refresh } = useAdminData(tab, ranged ? days : 0, Boolean(user?.is_admin), memberQuery);

  const hrefFor = useCallback((nextTab) => {
    // 보고 있는 탭을 다시 누를 때 조회 조건을 잃지 않는다.
    if (nextTab === "members") return `?${memberQuery ? memberSearchParams(memberQuery) : new URLSearchParams({ tab: "members" })}`;
    const q = new URLSearchParams({ tab: nextTab });
    if (RANGED.has(nextTab) && days !== 30) q.set("days", String(days));
    return `?${q}`;
  }, [days, memberQuery]);
  const setDays = (d) => {
    const q = new URLSearchParams({ tab });
    if (d !== 30) q.set("days", String(d));
    setParams(q, { replace: true });
  };
  // 조회 조건을 하나 바꾸면 나머지는 그대로, 쪽은 1로 되돌린다(쪽을 바꾼 게 아니라면).
  const setMemberQuery = useCallback((next) => {
    const merged = { ...(memberQuery || {}), ...next };
    if (!("page" in next)) merged.page = 1;
    setParams(memberSearchParams(merged), { replace: true });
  }, [memberQuery, setParams]);

  if (!user) return <Navigate to="/login" replace />;
  if (!user.is_admin) return <Navigate to="/mypage" replace />;

  const badgeLabels = {
    macros: badges.macros ? `오류 ${fmtInt(badges.macros)}` : "",
    news: badges.news ? `실패 ${fmtInt(badges.news)}` : "",
  };
  const stale = Boolean(error && data);

  return (
    <div className="admin-page">
      <div className="adm-head">
        <h1>관리자 대시보드</h1>
        <div className="adm-head-right">
          {ranged ? <RangePicker value={days} options={RANGES} onChange={setDays} /> : null}
          {data ? (
            <span className={`adm-stamp${stale ? " is-stale" : ""}`} role="status">
              {stale ? "갱신 실패 · 마지막 값 " : ""}{fmtStamp(data.generated_at)}
              {stale ? <button type="button" onClick={refresh}>다시 시도</button> : null}
            </span>
          ) : null}
        </div>
      </div>
      <TabNav tabs={TABS} active={tab} hrefFor={hrefFor} badges={badgeLabels} />
      {/* 회원 관리는 검색칸이 살아 있어야 해서(조회마다 화면이 사라지면 포커스를 잃는다) 스켈레톤·오류를 탭 안에서 다룬다. */}
      {!data && loading && tab !== "members" ? <Skeleton /> : null}
      {!data && !loading && error && tab !== "members" ? <ErrorBlock message={error} onRetry={refresh} /> : null}
      {data && tab === "users" ? <UsersTab data={data} days={days} /> : null}
      {data && tab === "signups" ? <SignupsTab data={data} days={days} /> : null}
      {data && tab === "macros" ? <MacrosTab data={data} days={days} /> : null}
      {data && tab === "news" ? <NewsTab data={data} /> : null}
      {data && tab === "costs" ? <CostsTab data={data} /> : null}
      {tab === "members" ? (
        <MembersTab
          data={data} loading={loading} error={error} query={memberQuery}
          selfId={user?.id ?? null} onQuery={setMemberQuery} onRefresh={refresh}
        />
      ) : null}
    </div>
  );
}

// ── 공통: 최근 7일 / 전체 토글 ───────────────────────────────────────────────
function useRecentRows(rows) {
  const [all, setAll] = useState(false);
  const list = rows || [];
  const visible = all || list.length <= 7 ? list : list.slice(-7);
  const toggle = list.length > 7
    ? <button type="button" className="adm-more" onClick={() => setAll((v) => !v)} aria-expanded={all}>{all ? "접기" : "더 보기"}</button>
    : null;
  return { visible, all, toggle };
}

function paperReturnCell(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  return <span className={n > 0 ? "adm-up" : n < 0 ? "adm-down" : undefined}>{fmtSignedPct(n)}</span>;
}

function downIfPositive(value) {
  const n = Number(value) || 0;
  return n > 0 ? <span className="adm-down">{fmtInt(n)}</span> : fmtInt(value);
}

// ── 탭 1 · 사용자 지표 ────────────────────────────────────────────────────────
function UsersTab({ data, days }) {
  const k = data.kpis || {};
  const series = data.series || {};
  const dayList = series.days || [];
  const daily = data.daily || [];
  const channels = data.channels || [];
  const { visible, all, toggle } = useRecentRows(daily);
  const sessionSum = sumBy(visible, "sessions");
  // 활성·재방문은 날짜별 '서로 다른 브라우저' 수라 더하면 여러 날 온 사람이 겹쳐 센다(WAU 보다 커진다).
  // 보이는 창이 7일이면 WAU, 30일이면 MAU 를 그대로 쓰고, 그 밖(90일)과 재방문은 "—".
  const windowActive = visible.length === 7 ? k.wau : visible.length === 30 ? k.mau : null;
  const total = {
    day: all || daily.length <= 7 ? `${days}일` : "최근 7일",
    active: windowActive, new: sumBy(visible, "new"), returning: null,
    sessions: sessionSum, pageviews: sumBy(visible, "pageviews"),
    pv_per_session: sessionSum ? sumBy(visible, "pageviews") / sessionSum : null,
    avg_session_sec: weightedMean(visible, "avg_session_sec", "sessions"),
    bounce_pct: weightedMean(visible, "bounce_pct", "sessions"),
  };
  const channelTotal = {
    label: "합계", sessions: sumBy(channels, "sessions"), share_pct: channels.length ? 100 : null,
    new_visitors: sumBy(channels, "new_visitors"),
    bounce_pct: weightedMean(channels, "bounce_pct", "sessions"),
    signup_rate_pct: weightedMean(channels, "signup_rate_pct", "new_visitors"),
  };
  const peak = data.peak_hours || [];
  return (
    <>
      <div className="adm-tabhead">
        <h2>사용자 지표</h2>
        <AdminTerms items={[
          ["활성 사용자", "그 기간에 한 번이라도 화면을 본 브라우저 수(로그인 여부 무관)"], ["DAU · WAU · MAU", "하루 · 7일 · 30일 활성 사용자"],
          ["고착도", "DAU ÷ MAU. 매일 오는 비율"], ["세션", "한 방문 묶음. 30분 이상 쉬면 새 세션"], ["페이지뷰", "화면 진입 횟수"],
          ["이탈률", "페이지 하나만 보고 떠난 세션 비율"], ["신규 · 재방문", "그 브라우저의 첫 방문인지"],
          ["채널", "직접 · 검색 · 추천 링크 · 소셜 · 캠페인(utm)"], ["종료율", "그 페이지에서 세션이 끝난 비율"],
        ]} />
      </div>
      <AdminKpis items={[
        { label: "DAU (오늘)", value: fmtInt(k.dau) }, { label: "WAU (7일)", value: fmtInt(k.wau) }, { label: "MAU (30일)", value: fmtInt(k.mau) },
        { label: "고착도", value: fmtNum(k.stickiness_pct), unit: "%" }, { label: "지금 접속 중", value: fmtInt(k.online_5m), unit: "5분" },
        { label: "오늘 이탈률", value: fmtNum(k.bounce_pct_today), unit: "%" }, { label: "평균 세션 (오늘)", value: fmtDuration(k.avg_session_sec_today) },
      ]} />
      <AdminBlock title="활성 사용자 추이" caption={`${days}일 · 선 그래프`}>
        <Legend items={[{ label: "DAU", color: SERIES.s1, line: true }, { label: "WAU", color: SERIES.s2, line: true }, { label: "MAU", color: SERIES.s3, line: true }]} />
        <LineChart days={dayList} yTitle="활성 사용자 (명)" digits={0} series={[
          { label: "MAU", data: series.mau, color: SERIES.s3 }, { label: "WAU", data: series.wau, color: SERIES.s2 }, { label: "DAU", data: series.dau, color: SERIES.s1 },
        ]} />
      </AdminBlock>
      <AdminBlock title="일별 트래픽" caption={all ? `${days}일 전체` : `최근 7일 · ${days}일 전체는 ‘더 보기’`} actions={toggle}>
        <AdminTable rows={visible} total={total} rowKey={(r) => r.day} columns={[
          { key: "day", label: "날짜" }, { key: "active", label: "활성 사용자", num: true, render: (r) => fmtInt(r.active) },
          { key: "new", label: "신규", num: true, render: (r) => fmtInt(r.new) }, { key: "returning", label: "재방문", num: true, render: (r) => fmtInt(r.returning) },
          { key: "sessions", label: "세션", num: true, render: (r) => fmtInt(r.sessions) }, { key: "pageviews", label: "페이지뷰", num: true, render: (r) => fmtInt(r.pageviews) },
          { key: "pv_per_session", label: "페이지뷰/세션", num: true, render: (r) => fmtNum(r.pv_per_session, 2) },
          { key: "avg_session_sec", label: "평균 세션 시간", num: true, render: (r) => fmtDuration(r.avg_session_sec) },
          { key: "bounce_pct", label: "이탈률", num: true, render: (r) => fmtPct(r.bounce_pct) },
        ]} />
      </AdminBlock>
      <div className="adm-cols2">
        <AdminBlock title="유입 채널" caption={`${days}일 · 세션`}>
          <HBarChart title="유입 채널" rows={channels.map((c) => ({ label: labelOf(CHANNEL_LABELS, c.channel), value: c.sessions, extra: fmtPct(c.share_pct) }))} />
          <AdminTable rows={channels} total={channelTotal} rowKey={(r) => r.channel} columns={[
            { key: "channel", label: "채널", render: (r) => r.label === "합계" ? r.label : labelOf(CHANNEL_DETAIL, r.channel) },
            { key: "sessions", label: "세션", num: true, render: (r) => fmtInt(r.sessions) },
            { key: "new_visitors", label: "신규 방문자", num: true, render: (r) => fmtInt(r.new_visitors) },
            { key: "bounce_pct", label: "이탈률", num: true, render: (r) => fmtPct(r.bounce_pct) },
            { key: "signup_rate_pct", label: "가입 전환율", num: true, render: (r) => fmtPct(r.signup_rate_pct) },
          ]} />
        </AdminBlock>
        <AdminBlock title="페이지별 참여" caption={`${days}일 · 페이지뷰 순`}>
          <AdminTable rows={data.pages || []} rowKey={(r) => r.path} columns={[
            { key: "path", label: "페이지", render: (r) => `${r.path} ${labelOf(PAGE_LABELS, r.path, r.label)}` },
            { key: "pageviews", label: "페이지뷰", num: true, render: (r) => fmtInt(r.pageviews) },
            { key: "avg_dwell_sec", label: "평균 체류", num: true, render: (r) => fmtDuration(r.avg_dwell_sec) },
            { key: "landings", label: "랜딩", num: true, render: (r) => fmtInt(r.landings) },
            { key: "exit_pct", label: "종료율", num: true, render: (r) => fmtPct(r.exit_pct) },
          ]} />
        </AdminBlock>
      </div>
      <div className="adm-cols2">
        <AdminBlock title="유입 상세" caption={`${days}일 · 상위 10`}>
          <AdminTable rows={data.sources || []} rowKey={(r) => r.source} columns={[
            { key: "source", label: "출처" }, { key: "channel", label: "채널", render: (r) => labelOf(CHANNEL_LABELS, r.channel) },
            { key: "sessions", label: "세션", num: true, render: (r) => fmtInt(r.sessions) },
            { key: "signups", label: "가입", num: true, render: (r) => fmtInt(r.signups) },
          ]} />
        </AdminBlock>
        <AdminBlock title="기기" caption={`${days}일 · 세션`}>
          <Donut
            parts={(data.devices || []).map((d, i) => ({ label: labelOf(DEVICE_LABELS, d.device, d.label), value: d.sessions, color: [SERIES.s2, SERIES.s1, SERIES.s5][i % 3], avg_session_sec: d.avg_session_sec }))}
            extraColumns={[{ key: "avg_session_sec", label: "평균 세션", num: true, render: (r) => fmtDuration(r.avg_session_sec) }]}
          />
        </AdminBlock>
      </div>
      <AdminBlock title="시간대별 세션" caption={`${days}일 · 세션 시작 시각 합 · KST`}>
        <StackedChart cats={Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}시`)} yTitle="세션 (건)" xTitle="시각 (KST)" height={200}
          series={[{ label: "세션", data: Array.from({ length: 24 }, (_, h) => Number(peak.find((p) => Number(p.hour) === h)?.sessions) || 0), color: SERIES.s2 }]} />
      </AdminBlock>
    </>
  );
}

// ── 탭 2 · 가입 · 전환 · 유지 ─────────────────────────────────────────────────
function SignupsTab({ data, days }) {
  const k = data.kpis || {};
  const series = data.series || {};
  const dayList = series.days || [];
  const daily = data.daily || [];
  const { visible, all, toggle } = useRecentRows(daily);
  const total = {
    day: `${days}일`, signups: sumBy(daily, "signups"), signup_rate_pct: k.signup_rate_pct, deletions: sumBy(daily, "deletions"),
    cumulative: daily.length ? daily[daily.length - 1].cumulative : null, first_backtest_same_day: sumBy(daily, "first_backtest_same_day"),
    quest_active: daily.length ? `평균 ${fmtNum(sumBy(daily, "quest_active") / daily.length, 0)}` : "—",
  };
  const methods = data.methods || [];
  const methodTotal = {
    label: "합계", signups: sumBy(methods, "signups"), share_pct: methods.length ? 100 : null,
    // D7 은 가입 뒤 7일이 지난 계정만 분모라 가입 수로 가중하면 어긋난다 — 서버가 준 전체 값을 쓴다.
    d7_retention_pct: k.d7_retention_pct, first_backtest_same_day: sumBy(methods, "first_backtest_same_day"),
  };
  return (
    <>
      <div className="adm-tabhead">
        <h2>가입 · 전환 · 유지</h2>
        <AdminTerms items={[
          ["가입 전환율", "가입 ÷ 신규 방문자"], ["퍼널", `${days}일 안에 그 단계까지 간 사람 수`],
          ["코호트 리텐션", "같은 주에 가입한 사람 중 N일 뒤 다시 방문한 비율"], ["재방문율", `${days}일 중 2일 이상 방문한 활성 사용자 비율`],
          ["가입 당일 첫 백테스트", "가입한 날 백테스트를 실행한 계정"], ["퀘스트 활동 계정", "그날 일일 퀘스트를 1개 이상 완료한 계정"],
        ]} />
      </div>
      <AdminKpis items={[
        { label: "누적 회원", value: fmtInt(k.total_users) }, { label: `${days}일 가입`, value: fmtInt(k.signups) },
        { label: "가입 전환율", value: fmtNum(k.signup_rate_pct), unit: "%" }, { label: `재방문율 (${days}일)`, value: fmtNum(k.revisit_pct), unit: "%" },
        { label: "D7 리텐션", value: fmtNum(k.d7_retention_pct, 0), unit: "%" }, { label: "탈퇴 (누적)", value: fmtInt(k.deletions) },
      ]} />
      <AdminBlock title="일별 가입과 전환율" caption={`${days}일 · 막대 = 가입, 점선 = 전환율`}>
        <Legend items={[{ label: "가입 (명)", color: SERIES.s1 }, { label: "가입 전환율 (%)", color: SERIES.s2, line: true }]} />
        <BarChart days={dayList} bars={[{ label: "가입", data: series.signups, color: SERIES.s1 }]} line={{ label: "전환율", data: series.signup_rate_pct, color: SERIES.s2 }} yTitle="가입 (명)" lineTitle="전환율 (%)" height={200} />
      </AdminBlock>
      <AdminBlock title="일별 가입" caption={all ? `${days}일 전체` : "최근 7일"} actions={toggle}>
        <AdminTable rows={visible} total={total} rowKey={(r) => r.day} columns={[
          { key: "day", label: "날짜" }, { key: "signups", label: "가입", num: true, render: (r) => fmtInt(r.signups) },
          { key: "signup_rate_pct", label: "가입 전환율", num: true, render: (r) => fmtPct(r.signup_rate_pct) },
          { key: "deletions", label: "탈퇴", num: true, render: (r) => fmtInt(r.deletions) },
          { key: "cumulative", label: "누적 회원", num: true, render: (r) => fmtInt(r.cumulative) },
          { key: "first_backtest_same_day", label: "가입 당일 첫 백테스트", num: true, render: (r) => fmtInt(r.first_backtest_same_day) },
          { key: "quest_active", label: "퀘스트 활동 계정", num: true, render: (r) => (typeof r.quest_active === "string" ? r.quest_active : fmtInt(r.quest_active)) },
        ]} />
      </AdminBlock>
      <AdminBlock title="전환 퍼널" caption={`${days}일 · 사람 수`}>
        <FunnelChart steps={(data.funnel || []).map((s) => ({ label: s.label || s.key, count: s.count }))} />
      </AdminBlock>
      <div className="adm-cols2">
        <AdminBlock title="가입 코호트 리텐션" caption="가입 주 기준 · 진할수록 높음 · — 는 아직 지나지 않음">
          <div className="adm-tbl">
            <table>
              <thead><tr><th scope="col">가입 주</th><th scope="col" className="num">가입</th>{["D1", "D3", "D7", "D14", "D30"].map((h) => <th key={h} scope="col" className="num">{h}</th>)}</tr></thead>
              <tbody>
                {(data.cohorts || []).length === 0 ? (
                  <tr className="adm-empty"><td colSpan={7}>아직 데이터 없음 · 집계 시작 {AGGREGATION_START}</td></tr>
                ) : (data.cohorts || []).map((c) => (
                  <tr key={c.week}>
                    <td>{c.week}</td><td className="num">{fmtInt(c.signups)}</td>
                    {["d1", "d3", "d7", "d14", "d30"].map((key) => <HeatCell key={key} value={c[key]} />)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AdminBlock>
        <AdminBlock title="가입 방법" caption={`${days}일`}>
          <AdminTable rows={methods} total={methodTotal} rowKey={(r) => r.method} columns={[
            { key: "method", label: "방법", render: (r) => r.label === "합계" ? r.label : labelOf(METHOD_LABELS, r.method, r.label) },
            { key: "signups", label: "가입", num: true, render: (r) => fmtInt(r.signups) },
            { key: "share_pct", label: "비율", num: true, render: (r) => fmtPct(r.share_pct) },
            { key: "d7_retention_pct", label: "D7 리텐션", num: true, render: (r) => fmtPct(r.d7_retention_pct, 0) },
            { key: "first_backtest_same_day", label: "가입 당일 첫 백테스트", num: true, render: (r) => fmtInt(r.first_backtest_same_day) },
          ]} />
        </AdminBlock>
      </div>
    </>
  );
}

// ── 탭 3 · 매크로 지표 ────────────────────────────────────────────────────────
function MacrosTab({ data, days }) {
  const k = data.kpis || {};
  const series = data.series || {};
  const dayList = series.days || [];
  const daily = data.daily || [];
  const { visible, all, toggle } = useRecentRows(daily);
  const total = {
    day: `${days}일`, registered: sumBy(daily, "registered"), impressions: sumBy(daily, "impressions"), opens: sumBy(daily, "opens"),
    ctr_pct: ratioPct(sumBy(daily, "opens"), sumBy(daily, "impressions")), unlocks: sumBy(daily, "unlocks"),
    cvr_pct: ratioPct(sumBy(daily, "unlocks"), sumBy(daily, "opens")), revenue_points: sumBy(daily, "revenue_points"), creator_points: sumBy(daily, "creator_points"),
  };
  return (
    <>
      <div className="adm-tabhead">
        <h2>매크로 지표</h2>
        <AdminTerms items={[
          ["등록", "리더보드에 올린 매크로 수(AI 봇 제외)"], ["노출", "리더보드 목록에 보인 횟수(세션당 매크로 1회)"],
          ["열람", "매크로 행에서 빌더로 가져오기 · 빠른 실행 · 언락 중 하나를 누름(세션당 매크로 1회)"], ["클릭률", "열람 ÷ 노출"],
          ["구매", "언락(포인트로 잠금 해제)"], ["구매 전환율", "구매 ÷ 열람"], ["매출", "언락에 쓰인 포인트"],
          ["제작자 수익", "제작자에게 간 포인트(포인트 원장 unlock_earn)"], ["모의 수익률", "그 매크로의 모의 세션 수익률"],
        ]} />
      </div>
      <AdminKpis items={[
        { label: `${days}일 등록`, value: fmtInt(k.registered) }, { label: `${days}일 구매`, value: fmtInt(k.unlocks) },
        { label: `${days}일 매출`, value: fmtInt(k.revenue_points), unit: "P" }, { label: "제작자 수익", value: fmtInt(k.creator_points), unit: "P" },
        { label: "평균 클릭률", value: fmtNum(k.ctr_pct), unit: "%" }, { label: "구매 전환율", value: fmtNum(k.cvr_pct), unit: "%" },
        { label: "실행 중 에이전트", value: fmtInt(k.agents_running) },
      ]} />
      <div className="adm-cols2">
        <AdminBlock title="일별 등록 · 구매" caption={`${days}일 · 막대`}>
          <Legend items={[{ label: "등록", color: SERIES.s1 }, { label: "구매(언락)", color: SERIES.s2 }]} />
          <BarChart days={dayList} bars={[{ label: "등록", data: series.registered, color: SERIES.s1 }, { label: "구매", data: series.unlocks, color: SERIES.s2 }]} yTitle="건수 (건)" height={200} />
        </AdminBlock>
        <AdminBlock title="일별 클릭률 · 구매 전환율" caption={`${days}일 · 선 · 집계 시작 ${AGGREGATION_START}`}>
          <Legend items={[{ label: "클릭률", color: SERIES.s1, line: true }, { label: "구매 전환율", color: SERIES.s2, line: true }]} />
          <LineChart days={dayList} series={[{ label: "클릭률", data: series.ctr_pct, color: SERIES.s1 }, { label: "구매 전환율", data: series.cvr_pct, color: SERIES.s2 }]} yTitle="비율 (%)" unit="%" height={200} />
        </AdminBlock>
      </div>
      <AdminBlock title="일별 매크로 활동" caption={`${all ? `${days}일 전체` : "최근 7일"} · 노출·열람 집계 시작 ${AGGREGATION_START}`} actions={toggle}>
        <AdminTable rows={visible} total={total} rowKey={(r) => r.day} columns={[
          { key: "day", label: "날짜" }, { key: "registered", label: "등록", num: true, render: (r) => fmtInt(r.registered) },
          { key: "impressions", label: "노출", num: true, render: (r) => fmtInt(r.impressions) }, { key: "opens", label: "열람", num: true, render: (r) => fmtInt(r.opens) },
          { key: "ctr_pct", label: "클릭률", num: true, render: (r) => fmtPct(r.ctr_pct) }, { key: "unlocks", label: "구매", num: true, render: (r) => fmtInt(r.unlocks) },
          { key: "cvr_pct", label: "전환율", num: true, render: (r) => fmtPct(r.cvr_pct) },
          { key: "revenue_points", label: "매출 (P)", num: true, render: (r) => fmtInt(r.revenue_points) },
          { key: "creator_points", label: "제작자 수익 (P)", num: true, render: (r) => fmtInt(r.creator_points) },
        ]} />
      </AdminBlock>
      <AdminBlock title="매크로별 성과" caption={`${days}일 · 매출 순 상위 20 (매출 없으면 열람 순)`}>
        <AdminTable rows={data.top || []} rowKey={(r) => r.entry_id} columns={[
          { key: "name", label: "매크로" }, { key: "creator", label: "제작자" }, { key: "symbol", label: "종목" },
          { key: "registered_day", label: "등록일", render: (r) => r.registered_day || "—" },
          { key: "impressions", label: "노출", num: true, render: (r) => fmtInt(r.impressions) }, { key: "opens", label: "열람", num: true, render: (r) => fmtInt(r.opens) },
          { key: "ctr_pct", label: "클릭률", num: true, render: (r) => fmtPct(r.ctr_pct) }, { key: "unlocks", label: "구매", num: true, render: (r) => fmtInt(r.unlocks) },
          { key: "cvr_pct", label: "전환율", num: true, render: (r) => fmtPct(r.cvr_pct) },
          { key: "revenue_points", label: "매출 (P)", num: true, render: (r) => fmtInt(r.revenue_points) },
          { key: "creator_points", label: "제작자 수익 (P)", num: true, render: (r) => fmtInt(r.creator_points) },
          { key: "paper_return_pct", label: "모의 수익률", num: true, render: (r) => paperReturnCell(r.paper_return_pct) },
        ]} />
      </AdminBlock>
      <AdminBlock title="에이전트 · 모의 세션" caption="현재">
        <AdminTable rows={data.sessions || []} rowKey={(r) => r.kind} columns={[
          { key: "label", label: "종류", render: (r) => r.label || r.kind },
          { key: "running", label: "실행 중", num: true, render: (r) => fmtInt(r.running) }, { key: "stopped", label: "종료", num: true, render: (r) => fmtInt(r.stopped) },
          { key: "error", label: "오류", num: true, render: (r) => downIfPositive(r.error) },
          { key: "started_period", label: `${days}일 시작`, num: true, render: (r) => fmtInt(r.started_period) },
          { key: "mainnet", label: "메인넷 (실자금)", num: true, render: (r) => (r.mainnet === null || r.mainnet === undefined ? "—" : fmtInt(r.mainnet)) },
        ]} />
      </AdminBlock>
    </>
  );
}

// ── 탭 4 · 뉴스 수집 현황 ────────────────────────────────────────────────────
function NewsTab({ data }) {
  const k = data.kpis || {};
  const now = Date.now();
  const buckets = useMemo(() => bucketHours(data.hourly || [], 2), [data.hourly]);
  const board = data.board || [];
  const counts = board.reduce((acc, b) => { acc[b.status] = (acc[b.status] || 0) + 1; return acc; }, {});
  const sources = data.sources || [];
  return (
    <>
      <div className="adm-tabhead">
        <h2>뉴스 수집 크롤링 현황</h2>
        <AdminTerms items={[
          ["엔진", "Prefect 배포(worker) 또는 웹 프로세스 안의 수집 루프"], ["오늘", "KST 자정 이후"], ["실행", "flow 실행 횟수"],
          ["대상", "처리한 종목 · 페어 수"], ["수집", "저장한 기사 · 거래 수"], ["실패", "소스 호출 실패 횟수"],
          ["보강", "기사에 AI 요약 · 번역을 붙이는 단계"], ["정체 쉼", "무진전 회차 뒤 60초 쉬고 5행만 탐침"], ["백오프", "실패 뒤 다음 시도까지 늘린 간격"],
        ]} />
      </div>
      <AdminKpis items={[
        { label: "종목 뉴스 정상", value: fmtInt(k.tickers_ok), unit: `/ ${fmtInt(k.tickers_total)}` },
        { label: "수집 실패 종목", value: fmtInt(k.tickers_failing), tone: Number(k.tickers_failing) > 0 ? "down" : undefined },
        { label: "오늘 수집 기사", value: fmtInt(k.articles_today) },
        { label: "오늘 실패", value: fmtInt(k.failures_today), tone: Number(k.failures_today) > 0 ? "down" : undefined },
        { label: "보강 대기 기사", value: fmtInt(k.pending), tone: Number(k.pending) > 0 ? "warn" : undefined },
        { label: "뉴스 AI 예산 (오늘)", value: fmtInt(k.ai_budget_used), unit: `/ ${fmtInt(k.ai_budget_limit)}` },
      ]} />
      <AdminBlock title="크롤링 엔진" caption="지금 · 오늘">
        <AdminTable rows={data.engines || []} rowKey={(r) => r.engine} columns={[
          { key: "label", label: "엔진", render: (r) => r.label || r.engine }, { key: "mode", label: "실행 방식" },
          { key: "status", label: "상태", render: (r) => { const s = ENGINE_STATUS[r.status] || ENGINE_STATUS.idle; return <StatusPill tone={s.tone}>{r.status_label || s.label}</StatusPill>; } },
          { key: "last_run_ms", label: "마지막 실행", render: (r) => fmtRelative(r.last_run_ms, now) },
          { key: "runs_today", label: "오늘 실행", num: true, render: (r) => fmtInt(r.runs_today) },
          { key: "targets_today", label: "오늘 대상", num: true, render: (r) => fmtInt(r.targets_today) },
          { key: "items_today", label: "오늘 수집", num: true, render: (r) => fmtInt(r.items_today) },
          { key: "failures_today", label: "오늘 실패", num: true, render: (r) => downIfPositive(r.failures_today) },
          { key: "last_error", label: "최근 오류", render: (r) => <span className="adm-err" title={r.last_error || ""}>{r.last_error || "—"}</span> },
        ]} />
      </AdminBlock>
      <AdminBlock title="시간대별 수집 · 실패" caption="오늘 · 2시간 단위 · 누적 막대 · 종목 뉴스 엔진">
        <Legend items={[{ label: "수집 기사", color: SERIES.s2 }, { label: "실패", color: SERIES.down }]} />
        <StackedChart cats={buckets.map((b) => b.label)} yTitle="건수 (건)" xTitle="시각 (KST)" height={200}
          series={[{ label: "수집 기사", data: buckets.map((b) => b.items), color: SERIES.s2 }, { label: "실패", data: buckets.map((b) => b.failures), color: SERIES.down }]} />
      </AdminBlock>
      <AdminBlock title="소스별 수집" caption="오늘 · 종목 뉴스 엔진 내부">
        <HBarChart title="소스별 수집 기사" unit="건" rows={sources.filter((s) => Number(s.items) > 0).sort((a, b) => Number(b.items) - Number(a.items)).map((s) => ({ label: s.label || s.source, value: s.items }))} />
        <AdminTable rows={sources} rowKey={(r) => r.source} columns={[
          { key: "label", label: "소스", render: (r) => r.label || r.source },
          { key: "targets", label: "대상 종목", num: true, render: (r) => fmtInt(r.targets) }, { key: "calls", label: "호출", num: true, render: (r) => fmtInt(r.calls) },
          { key: "items", label: "수집 기사", num: true, render: (r) => fmtInt(r.items) }, { key: "failures", label: "실패", num: true, render: (r) => downIfPositive(r.failures) },
          { key: "failure_pct", label: "실패율", num: true, render: (r) => fmtPct(r.failure_pct) },
          { key: "last_success_ms", label: "마지막 성공", render: (r) => fmtRelative(r.last_success_ms, now) },
          { key: "last_error", label: "최근 오류", render: (r) => <span className="adm-err" title={r.last_error || ""}>{r.last_error || "—"}</span> },
        ]} />
      </AdminBlock>
      <AdminBlock title="종목별 수집 상태판" caption={`정상 ${fmtInt(counts.ok || 0)} · 비어 있음 ${fmtInt(counts.empty || 0)} · 실패 ${fmtInt(counts.bad || 0)} · 수집 중 ${fmtInt(counts.wait || 0)} · 오래된 순 · 값 = 마지막 성공`}>
        {board.length === 0 ? <p className="adm-empty-note">아직 데이터 없음 · 집계 시작 {AGGREGATION_START}</p> : (
          <div className="adm-board">
            {board.map((b) => (
              <span key={b.asset} className="adm-cell" title={`${b.asset} · ${labelOf(BOARD_STATUS, b.status, b.label)}`}>
                <b className={`is-${b.status}`} />{b.asset}
                <small>{b.status === "bad" ? `실패 ${fmtInt(b.failures)}` : b.status === "ok" ? fmtRelative(b.last_success_ms, now) : labelOf(BOARD_STATUS, b.status, b.label)}</small>
              </span>
            ))}
          </div>
        )}
      </AdminBlock>
      <div className="adm-cols2">
        <AdminBlock title="수집 실패 종목" caption="연속 실패 1회 이상">
          <AdminTable rows={data.failing || []} rowKey={(r) => r.asset} empty="실패 종목 없음" columns={[
            { key: "asset", label: "종목" }, { key: "failures", label: "연속 실패", num: true, render: (r) => fmtInt(r.failures) },
            { key: "last_attempt_ms", label: "마지막 시도", render: (r) => fmtTimeKst(r.last_attempt_ms) },
            { key: "last_success_ms", label: "마지막 성공", render: (r) => fmtDayTimeKst(r.last_success_ms) },
            { key: "next_attempt_ms", label: "다음 시도", render: (r) => (r.next_attempt_ms ? `${fmtTimeKst(r.next_attempt_ms)} (${fmtUntil(r.next_attempt_ms, now)})` : "—") },
            { key: "error", label: "오류", render: (r) => <span className="adm-err" title={r.error || ""}>{r.error || "—"}</span> },
          ]} />
        </AdminBlock>
        <AdminBlock title="기사 보강 · 예산" caption="지금">
          <AdminTable rows={data.enrichment || []} rowKey={(r) => r.key} columns={[
            { key: "label", label: "항목", render: (r) => r.label || r.key },
            { key: "value", label: "값", num: true, render: (r) => (typeof r.value === "number" ? fmtInt(r.value) : (r.value ?? "—")) },
          ]} />
        </AdminBlock>
      </div>
    </>
  );
}

// ── 탭 5 · API 비용 ──────────────────────────────────────────────────────────
function CostsTab({ data }) {
  const k = data.kpis || {};
  const monthly = data.monthly || [];
  const providers = data.providers || [];
  const purposes = data.purposes || [];
  const daily = data.daily || [];
  const { visible, all, toggle } = useRecentRows(daily);
  const monthLabel = fmtMonthLabel(data.month, { current: true });
  const other = (m) => ["coindesk", "vercel", "prefect"].reduce((acc, p) => acc + (Number(m.providers?.[p]) || 0), 0);
  const providerTotal = { label: "합계", method: "", month_usd: sumBy(providers, "month_usd"), last_month_usd: sumBy(providers, "last_month_usd"), plan: "" };
  const purposeTotal = {
    label: "합계", code: "", calls: sumBy(purposes, "calls"), input_tokens: sumBy(purposes, "input_tokens"),
    output_tokens: sumBy(purposes, "output_tokens"), cost_usd: sumBy(purposes, "cost_usd"), daily_limit: "",
  };
  const dailyTotal = {
    day: all || daily.length <= 7 ? `${daily.length}일` : "최근 7일", gemini_calls: sumBy(visible, "gemini_calls"), input_tokens: sumBy(visible, "input_tokens"),
    output_tokens: sumBy(visible, "output_tokens"), cost_usd: sumBy(visible, "cost_usd"), coindesk_calls: sumBy(visible, "coindesk_calls"),
  };
  const palette = [SERIES.s2, SERIES.s1, SERIES.s3, SERIES.s4, SERIES.s5, SERIES_6];
  return (
    <>
      <div className="adm-tabhead">
        <h2>API 비용</h2>
        <AdminTerms items={[
          ["추정 비용", "응답 토큰 수 × 모델 단가 (서버 설정값, 1M 토큰 기준)"], ["실제 청구액", "청구 API 로 받은 값(제공하는 곳만, 1~2일 지연)"],
          ["구독형", "월 고정액을 설정값으로 넣은 항목"], ["용도", "Gemini 를 부르는 코드 위치 6곳"], ["통화", "USD"],
          [monthLabel, `1일 ~ ${fmtInt(data.month_days_elapsed)}일까지`],
        ]} />
      </div>
      <AdminKpis items={[
        { label: "이번 달 추정 합계", value: fmtUsd(k.month_total_usd) }, { label: "지난달", value: fmtUsd(k.last_month_total_usd) },
        { label: "이번 달 Gemini", value: fmtUsd(k.gemini_month_usd) }, { label: "Gemini 호출", value: fmtInt(k.gemini_calls_month) },
        { label: "오늘 Gemini", value: fmtUsd(k.gemini_today_usd) },
      ]} />
      <div className="adm-cols2">
        <AdminBlock title="월별 비용" caption={`최근 ${COST_MONTHS}개월 · 제공자별 누적 · USD`}>
          <Legend items={[{ label: "Gemini", color: SERIES.s2 }, { label: "Render", color: SERIES.s1 }, { label: "Supabase", color: SERIES.s3 }, { label: "기타 (CoinDesk · Vercel · Prefect)", color: SERIES.s5 }]} />
          <StackedChart cats={monthly.map((m) => m.label || fmtMonthLabel(m.month, { current: m.month === data.month }))} yTitle="비용 (USD)" xTitle="월" prefix="$" digits={2} height={260}
            series={[
              { label: "Gemini", data: monthly.map((m) => Number(m.providers?.gemini) || 0), color: SERIES.s2 },
              { label: "Render", data: monthly.map((m) => Number(m.providers?.render) || 0), color: SERIES.s1 },
              { label: "Supabase", data: monthly.map((m) => Number(m.providers?.supabase) || 0), color: SERIES.s3 },
              { label: "기타", data: monthly.map(other), color: SERIES.s5 },
            ]} />
        </AdminBlock>
        <AdminBlock title="Gemini 용도별 비용" caption="이번 달 · USD">
          <Donut prefix="$" digits={2} parts={purposes.map((p, i) => ({ label: labelOf(PURPOSE_LABELS, p.purpose, p.label), value: p.cost_usd, color: palette[i % palette.length] }))} />
        </AdminBlock>
      </div>
      <AdminBlock title="월별 청구 예상" caption={`${data.month || "—"} · 지난달 · USD`}>
        <AdminTable rows={providers} total={providerTotal} rowKey={(r) => r.provider} columns={[
          { key: "label", label: "제공자", render: (r) => r.label || r.provider },
          { key: "method", label: "계산", render: (r) => (r.method ? labelOf(COST_METHOD_LABELS, r.method) : "") },
          { key: "calls", label: "호출", num: true, render: (r) => (r.calls === null || r.calls === undefined ? "—" : fmtInt(r.calls)) },
          { key: "input_tokens", label: "입력 토큰", num: true, render: (r) => fmtTokens(r.input_tokens) },
          { key: "output_tokens", label: "출력 토큰", num: true, render: (r) => fmtTokens(r.output_tokens) },
          { key: "month_usd", label: "이번 달", num: true, render: (r) => fmtUsd(r.month_usd) },
          { key: "last_month_usd", label: "지난달", num: true, render: (r) => fmtUsd(r.last_month_usd) },
          { key: "plan", label: "요금제", render: (r) => r.plan || "" },
        ]} />
      </AdminBlock>
      <AdminBlock title="Gemini 용도별" caption="이번 달">
        <AdminTable rows={purposes} total={purposeTotal} rowKey={(r) => r.purpose} columns={[
          { key: "label", label: "용도", render: (r) => r.label === "합계" ? r.label : labelOf(PURPOSE_LABELS, r.purpose, r.label) },
          { key: "code", label: "코드", render: (r) => r.code ?? r.purpose ?? "" },
          { key: "calls", label: "호출", num: true, render: (r) => fmtInt(r.calls) },
          { key: "input_tokens", label: "입력 토큰", num: true, render: (r) => fmtTokens(r.input_tokens) },
          { key: "output_tokens", label: "출력 토큰", num: true, render: (r) => fmtTokens(r.output_tokens) },
          { key: "cost_usd", label: "추정 비용", num: true, render: (r) => fmtUsd(r.cost_usd) },
          { key: "daily_limit", label: "일일 한도", render: (r) => (r.label === "합계" ? "" : fmtLimit(r.daily_limit)) },
        ]} />
      </AdminBlock>
      <AdminBlock title="일별 사용" caption={all ? `${daily.length}일 전체` : "최근 7일"} actions={toggle}>
        <AdminTable rows={visible} total={dailyTotal} rowKey={(r) => r.day} columns={[
          { key: "day", label: "날짜" }, { key: "gemini_calls", label: "Gemini 호출", num: true, render: (r) => fmtInt(r.gemini_calls) },
          { key: "input_tokens", label: "입력 토큰", num: true, render: (r) => fmtTokens(r.input_tokens) },
          { key: "output_tokens", label: "출력 토큰", num: true, render: (r) => fmtTokens(r.output_tokens) },
          { key: "cost_usd", label: "추정 비용", num: true, render: (r) => fmtUsd(r.cost_usd) },
          { key: "coindesk_calls", label: "CoinDesk 호출", num: true, render: (r) => fmtInt(r.coindesk_calls) },
        ]} />
      </AdminBlock>
    </>
  );
}

// ── 탭 6 · 회원 관리 ────────────────────────────────────────────────────────────
// 지표 탭과 달리 목록 + 조치다. 조회 조건은 주소에 있고(부모가 준다), 조치가 끝나면 곧바로 같은 쪽을 다시 받는다.
// 결과는 토스트가 아니라 표 위 한 줄 — 무엇이 달라졌는지 남아 있어야 다음 조치를 판단할 수 있다.
function MembersTab({ data, loading, error, query, selfId, onQuery, onRefresh }) {
  const now = Date.now();
  const counts = data?.counts || {};
  const items = data?.items || [];
  const total = data?.total;
  const pages = Number(data?.total_pages) || pageCount(total, query.pageSize);
  const page = clampPage(data?.page || query.page, pages);
  const queryKey = memberQueryString(query);

  const [term, setTerm] = useState(query.q);
  const committedRef = useRef(query.q);
  const [action, setAction] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [dialogError, setDialogError] = useState("");
  const aliveRef = useRef(true);
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false; }; }, []);

  // 주소가 밖에서 바뀌면(뒤로 가기·탭 재진입) 입력칸을 맞춘다.
  useEffect(() => {
    if (query.q === committedRef.current) return;
    committedRef.current = query.q;
    setTerm(query.q);
  }, [query.q]);
  // 검색은 300ms 디바운스 — 글자마다 요청하지 않고, 확정되면 1쪽으로 되돌린다.
  useEffect(() => {
    if (term.trim() === query.q) return undefined;
    const timer = window.setTimeout(() => {
      committedRef.current = term.trim();
      onQuery({ q: term.trim() });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [term, query.q, onQuery]);
  // 조회 조건이 바뀌면 앞 조치의 결과 문구는 지운다(다른 쪽·다른 필터에 남아 있으면 오해를 부른다).
  useEffect(() => { setResult(null); setDialogError(""); }, [queryKey]);

  const closeDialog = useCallback(() => { setAction(null); setDialogError(""); }, []);

  async function submitAction(payload) {
    if (!action) return;
    const { kind, member } = action;
    setBusy(true);
    setDialogError("");
    try {
      if (kind === "message") await api.adminMemberMessage(member.id, payload);
      else if (kind === "block") await api.adminMemberBlock(member.id, { blocked: !member.is_blocked, reason: payload.reason });
      else await api.adminMemberDelete(member.id, { reason: payload.reason });
      if (!aliveRef.current) return;
      setAction(null);
      setResult({ text: memberResultLine(kind === "block" ? blockActionKind(member) : kind, member), bad: false });
      onRefresh();
    } catch (e) {
      if (!aliveRef.current) return;
      const message = memberActionError(e);
      setResult({ text: message, bad: true });
      // 메시지 폼은 창을 닫지 않는다 — 제목·내용을 다시 쓰게 만들지 않기 위해(오류 문구는 창 안에도 적는다).
      if (kind === "message") setDialogError(message);
      else setAction(null);
    } finally {
      if (aliveRef.current) setBusy(false);
    }
  }

  const stale = Boolean(error && data);
  return (
    <>
      <div className="adm-tabhead">
        <h2>회원 관리</h2>
        <AdminTerms items={[
          ["메시지", "그 회원의 알림창으로 관리자 메시지를 보낸다(알림 kind admin). 접속 중이면 실시간 알림까지 뜬다"],
          ["차단", "채팅·게시글·댓글을 쓸 수 없다. 로그인·열람·백테스트는 그대로. 되돌릴 수 있다"],
          ["탈퇴", "계정을 지우고 내용은 ‘탈퇴한 회원’ 으로 익명화(포인트 회수). 되돌릴 수 없고 같은 이메일로 다시 가입할 수 없다"],
          ["이메일", "서버가 마스킹해서 준다 (a***@gmail.com)"],
          ["가입 방법", "구글 간편 가입 · 이메일 가입"],
          ["등급", "포인트로 올라가는 회원 등급 (새싹 · 골드…)"],
          ["구매 · 판매", "언락으로 산 매크로 수 · 내 매크로가 팔린 수"],
          ["마지막 방문", `마지막 방문 기록 시각. 집계 시작 ${AGGREGATION_START}`],
        ]} />
      </div>
      <AdminBlock
        title="회원 목록"
        caption={`총 ${fmtInt(total)}명 · ${fmtInt(page)} / ${fmtInt(pages)} 페이지 · 최신 가입 순`}
        actions={stale ? <button type="button" className="adm-more" onClick={onRefresh}>다시 시도</button> : null}
      >
        <div className="adm-mem-bar">
          <div className="adm-mem-chips" role="group" aria-label="상태 필터">
            {MEMBER_STATUSES.map((s) => (
              <button
                key={s.key} type="button" className={`adm-mem-chip${query.status === s.key ? " is-on" : ""}`}
                aria-pressed={query.status === s.key} onClick={() => onQuery({ status: s.key })}
              >
                {s.label}<span className="num">{fmtInt(counts[s.key])}</span>
              </button>
            ))}
          </div>
          <label className="adm-mem-search">
            <span className="sr-only">회원 검색</span>
            <input
              type="search" className="field field-sm" value={term} maxLength={MEMBER_Q_MAX}
              placeholder="아이디 · 이메일 검색" onChange={(e) => setTerm(e.target.value)}
            />
          </label>
        </div>
        {result ? <p className={`adm-mem-result${result.bad ? " is-bad" : ""}`} role="status">{result.text}</p> : null}
        {!data && loading ? <Skeleton rows={6} /> : null}
        {!data && !loading && error ? <ErrorBlock message={error} onRetry={onRefresh} /> : null}
        {data ? (
          <div className="adm-mem-wrap" aria-busy={loading || undefined}>
            <AdminTable
              rows={items} rowKey={(r) => r.id}
              empty={query.q ? `‘${query.q}’ 검색 결과가 없어요` : "조건에 맞는 회원이 없어요"}
              columns={[
                { key: "username", label: "아이디" },
                { key: "email_masked", label: "이메일", render: (r) => memberEmail(r) },
                { key: "created_at", label: "가입일", render: (r) => fmtKst(r.created_at) },
                { key: "signup_method", label: "가입 방법", render: (r) => memberSignup(r) },
                { key: "tier_name", label: "등급", render: (r) => memberTier(r) },
                { key: "points_balance", label: "포인트", num: true, render: (r) => fmtInt(r.points_balance) },
                { key: "macros", label: "매크로", num: true, render: (r) => fmtInt(r.macros) },
                { key: "posts", label: "글", num: true, render: (r) => fmtInt(r.posts) },
                { key: "comments", label: "댓글", num: true, render: (r) => fmtInt(r.comments) },
                { key: "unlocks_bought", label: "구매", num: true, render: (r) => fmtInt(r.unlocks_bought) },
                { key: "sales", label: "판매", num: true, render: (r) => fmtInt(r.sales) },
                { key: "last_seen_ms", label: "마지막 방문", render: (r) => fmtRelative(r.last_seen_ms, now) },
                {
                  key: "state",
                  label: "상태",
                  render: (r) => {
                    const state = memberState(r);
                    return (
                      <span className="adm-mem-state" title={r.blocked_reason || undefined}>
                        <StatusPill tone={state.tone}>{state.label}</StatusPill>
                        {r.is_admin ? <span className="adm-mem-admin">관리자</span> : null}
                      </span>
                    );
                  },
                },
                {
                  key: "act",
                  label: "조치",
                  render: (r) => <MemberRowActions member={r} selfId={selfId} disabled={busy} onAction={setAction} />,
                },
              ]}
            />
          </div>
        ) : null}
        {/* 쪽 이동은 응답이 온 뒤에만 — 받기 전에는 "1 / 1 페이지 · 총 —명" 을 지어내지 않는다. */}
        {data ? (
        <div className="adm-mem-pager">
          <button type="button" className="adm-act" disabled={page <= 1} onClick={() => onQuery({ page: page - 1 })}>이전</button>
          <span className="adm-mem-pageno"><b className="num">{fmtInt(page)}</b> / <span className="num">{fmtInt(pages)}</span> 페이지</span>
          <button type="button" className="adm-act" disabled={page >= pages} onClick={() => onQuery({ page: page + 1 })}>다음</button>
          <label className="adm-mem-size">
            <span>쪽당</span>
            <select className="field field-sm" value={query.pageSize} onChange={(e) => onQuery({ pageSize: Number(e.target.value) })}>
              {MEMBER_PAGE_SIZES.map((n) => <option key={n} value={n}>{n}명</option>)}
            </select>
          </label>
          <span className="adm-mem-total">총 <b className="num">{fmtInt(total)}</b>명</span>
        </div>
        ) : null}
      </AdminBlock>
      <MemberActionDialogs action={action} busy={busy} error={dialogError} onSubmit={submitAction} onCancel={closeDialog} />
    </>
  );
}
