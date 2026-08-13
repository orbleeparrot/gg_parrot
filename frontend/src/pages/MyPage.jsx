import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { useAuth, updateAuthUser } from "../lib/auth.js";
import { PageHeader, SectionTitle, EmptyRow, Loading, ErrorNote } from "../components/Page.jsx";

const P = (n) => `${(n ?? 0).toLocaleString()}P`;

const REASON_KO = {
  signup_grant: "가입 보너스",
  unlock_spend: "매크로 언락(구매)",
  unlock_earn: "판매 수익",
  topup: "충전",
};

function Section({ title, count, children }) {
  return (
    <section className="pt-6 border-t border-slate-200">
      <SectionTitle count={count}>{title}</SectionTitle>
      {children}
    </section>
  );
}

const Empty = EmptyRow;

export default function MyPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      navigate("/login?next=%2Fmypage");
      return;
    }
    let alive = true;
    api.myDashboard()
      .then((d) => {
        if (!alive) return;
        setData(d);
        updateAuthUser(d.user); // sync header points
      })
      .catch((e) => {
        if (alive) setError(String(e.message || e));
      });
    return () => {
      alive = false;
    };
  }, [navigate, token]);

  if (!token) return null;
  if (error) return <ErrorNote>오류: {error}</ErrorNote>;
  if (!data) return <Loading />;

  const { user, tier, totals, created, purchased, sales, ledger, my_posts = [] } = data;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="계정·포인트·매크로"
        title="내 활동"
        description="만든 매크로와 언락한 전략, 판매 수익과 포인트 변동을 한곳에서 확인해요."
      />
      {/* profile + tier + points */}
      <div className="pb-5 flex items-center justify-between flex-wrap gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="t-h4 text-slate-900">{user.username}</span>
            <span className="badge badge-flat">{tier.name} 등급</span>
          </div>
          <div className="t-small text-slate-700 mt-1">{user.email}</div>
          {tier.next_name && (
            <div className="t-caption text-slate-500 mt-1">
              다음 등급 <b className="text-slate-700">{tier.next_name}</b>까지 판매 <span className="num">{tier.to_next}</span>건 남음 (누적 판매 <span className="num">{tier.sales}</span>건)
            </div>
          )}
        </div>
        {/* stat — 상자 없이 캡션 위, 수치 아래. 포인트는 §2-3 에 따라 인디고. */}
        <div className="text-right">
          <div className="stat-label">보유 포인트</div>
          <div className="t-h2 num text-indigo-800">🪙 {P(user.points_balance)}</div>
        </div>
      </div>

      {/* totals */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          ["내가 만든 매크로", totals.created + "개"],
          ["누적 판매", totals.sales + "건"],
          ["판매 수익", P(totals.earned)],
          ["구매한 매크로", totals.purchased + "개"],
        ].map(([label, value]) => (
          <div key={label} className="min-w-0">
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
          </div>
        ))}
      </div>

      {/* created */}
      <Section title="내가 만든 매크로" count={created.length}>
        {created.length === 0 ? (
          <Empty>아직 리더보드에 등록한 매크로가 없어요. 빌더에서 만들어 등록해보세요.</Empty>
        ) : (
          <div className="divide-y divide-slate-200">
            {created.map((m) => (
              <div key={m.entry_id} className="flex items-center justify-between gap-3 py-3 flex-wrap">
                <div className="min-w-0">
                  <div className="t-label font-bold text-slate-900 num">{m.symbol}</div>
                  <div className="t-small text-slate-500 truncate">{m.human_summary}</div>
                </div>
                <div className="t-small text-slate-700">
                  판매 <b className="num text-slate-900">{m.sales}</b> · 수익 <b className="num text-indigo-800">{P(m.earned)}</b>
                  <span className="t-caption text-slate-500 num"> · {m.created_kst}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* my board posts */}
      <Section title="내가 쓴 게시글" count={my_posts.length}>
        {my_posts.length === 0 ? (
          <Empty>아직 작성한 게시글이 없어요. 게시판에 첫 글을 남겨보세요.</Empty>
        ) : (
          <div className="divide-y divide-slate-200">
            {my_posts.map((p) => (
              <button
                type="button"
                key={p.id}
                onClick={() => navigate(`/board/${p.id}`)}
                className="w-full text-left flex items-center justify-between gap-3 py-3 hover:bg-slate-100 -mx-2 px-2 rounded-lg"
              >
                <div className="min-w-0">
                  <div className="t-label font-bold text-slate-900 truncate">
                    {p.has_image && <span className="badge badge-flat mr-2">사진</span>}
                    {p.title}
                    {p.comment_count > 0 && <span className="ml-2 t-caption font-bold num text-indigo-800">[{p.comment_count}]</span>}
                  </div>
                  {p.snippet && <div className="t-small text-slate-500 truncate">{p.snippet}</div>}
                </div>
                <div className="t-caption text-slate-500 num shrink-0">{p.created_kst}</div>
              </button>
            ))}
          </div>
        )}
      </Section>

      {/* purchased */}
      <Section title="구매한 매크로" count={purchased.length}>
        {purchased.length === 0 ? (
          <Empty>구매(언락)한 매크로가 없어요. 리더보드에서 마음에 드는 전략을 열어보세요.</Empty>
        ) : (
          <div className="divide-y divide-slate-200">
            {purchased.map((m, i) => (
              <div key={i} className="flex items-center justify-between gap-3 py-3 flex-wrap">
                <div className="min-w-0">
                  <div className="t-label font-bold text-slate-900 num">{m.symbol} <span className="t-caption text-slate-500">· @{m.seller}</span></div>
                  <div className="t-small text-slate-500 truncate">{m.human_summary}</div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="t-small font-semibold text-slate-500 num">-{P(m.price)}</span>
                  <button
                    onClick={() => navigate("/builder", { state: { macro: m.macro } })}
                    className="btn btn-s btn-secondary"
                  >
                    빌더로 복사
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* sales history */}
      <Section title="내 매크로 판매 내역" count={sales.length}>
        {sales.length === 0 ? (
          <Empty>아직 판매(다른 사람의 언락)가 없어요.</Empty>
        ) : (
          <div className="divide-y divide-slate-200">
            {sales.map((s, i) => (
              <div key={i} className="flex items-center justify-between gap-3 py-3 t-small">
                <div className="text-slate-700">
                  <b className="text-slate-900">@{s.buyer}</b> 님이 <b className="text-slate-900 num">{s.symbol}</b> 매크로를 언락
                </div>
                <div className="font-bold num text-indigo-800">+{P(s.earned)}</div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* ledger */}
      <Section title="포인트 내역" count={ledger.length}>
        {ledger.length === 0 ? (
          <Empty>포인트 변동 내역이 없어요.</Empty>
        ) : (
          <div className="divide-y divide-slate-200">
            {ledger.map((l, i) => (
              <div key={i} className="flex items-center justify-between gap-3 py-3 t-small">
                <div className="text-slate-700">{REASON_KO[l.reason] || l.reason}</div>
                <div className="flex items-center gap-3 num">
                  <span className={"font-bold " + (l.delta >= 0 ? "text-green-600" : "text-red-600")}>
                    {l.delta >= 0 ? "+" : ""}{l.delta.toLocaleString()}P
                  </span>
                  <span className="t-caption text-slate-500 w-16 text-right">잔액 {l.balance_after.toLocaleString()}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <p className="t-caption text-slate-500 text-center">
        포인트는 서비스 안에서만 쓰는 가상 재화이고, 본 서비스는 실거래·투자 자문이 아니에요.
      </p>
    </div>
  );
}
