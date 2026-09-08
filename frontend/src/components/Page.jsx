// 화면 골격 — 제목 줄, 빈 상태, 로딩, 오류.
//
// 이 네 가지는 화면마다 다시 짜여 있었다. 리더보드는 제목+뱃지, 게시판은
// 제목+부제+버튼, 코인동향은 제목+부제, 마이페이지는 제목이 아예 없었고,
// "불러오는 중…"이 여섯 가지 크기·색으로 흩어져 있었다. 같은 것은 같게 보여야
// 화면을 옮겨 다닐 때 눈이 다시 적응하지 않는다.

// 페이지 머리 — 모든 화면이 같은 타이포 규격을 쓴다(코인동향·리더보드에서 시작한 형태).
// 아이브로(11/750/자간) → 제목(clamp 30~44px/800) → meta(기준일·카운트다운 같은 수치 한 줄)
// → 설명(16~19/500, 40ch) → note(13, 고지). 상자 없이 크기·여백으로만 위계(§1-3).
// 우측 액션은 제목 밑선에 맞춘다. 제목이 없는 화면(내 에이전트)은 이 컴포넌트를 쓰지 않는다.
export function PageHeader({ title, description, meta, note, actions, eyebrow, headingAs: Heading = "h1" }) {
  return (
    <header className="page-head">
      <div className="page-head-copy">
        {eyebrow && <span className="page-head-eyebrow">{eyebrow}</span>}
        <Heading className="page-head-title">{title}</Heading>
        {meta && <p className="page-head-meta">{meta}</p>}
        {description && <p className="page-head-description">{description}</p>}
        {note && <p className="page-head-note">{note}</p>}
      </div>
      {actions && <div className="page-head-actions">{actions}</div>}
    </header>
  );
}

// 섹션 제목 — 페이지 안 구획. 개수는 캡션으로 뒤에 붙인다.
export function SectionTitle({ children, count, className = "" }) {
  return (
    <div className={"flex items-center gap-2 mb-3 " + className}>
      <h2 className="t-title text-slate-900">{children}</h2>
      {count != null && <span className="t-caption text-slate-500 num">({count})</span>}
    </div>
  );
}

export function Loading({ label = "불러오는 중…" }) {
  return <div className="py-10 text-center t-small text-slate-500" role="status">{label}</div>;
}

export function ErrorNote({ children }) {
  return <div className="notice-risk t-small text-slate-700" role="alert">{children}</div>;
}

// 빈 상태 — 제목 한 줄 + 다음에 뭘 하면 되는지. 상자를 두르지 않는다(§1-3).
export function EmptyState({ title, children, action }) {
  return (
    <div className="py-14 text-center">
      <div className="t-title text-slate-900">{title}</div>
      {children && <p className="mt-2 t-small text-slate-700 measure mx-auto">{children}</p>}
      {action && <div className="mt-6 flex justify-center">{action}</div>}
    </div>
  );
}

// 목록 안에서 쓰는 축약형 — 섹션 하나가 비었을 때.
export function EmptyRow({ children }) {
  return <div className="py-8 text-center t-small text-slate-500">{children}</div>;
}
