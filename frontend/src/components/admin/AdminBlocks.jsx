// 관리자 대시보드의 조각들 — 탭 줄 · 기간 선택 · 용어 표 · KPI 줄 · 블록 · 상태 알약 · 표 · 로딩/오류.
// 상자 없이 괘선과 크기로만 위계를 세운다(DESIGN §1-3). 숫자는 전부 `.num`.
import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EMPTY_NOTE } from "../../lib/adminFormat.js";

const BlockTitleId = createContext(undefined);

export function TabNav({ tabs, active, hrefFor, badges = {} }) {
  return (
    <nav className="adm-tabs" aria-label="대시보드 탭">
      {tabs.map((tab) => {
        const on = tab.key === active;
        const badge = badges[tab.key];
        return (
          <Link key={tab.key} to={hrefFor(tab.key)} className={on ? "is-on" : ""} aria-current={on ? "page" : undefined} replace>
            {tab.label}
            {badge ? <span className="adm-tab-n num">{badge}</span> : null}
          </Link>
        );
      })}
    </nav>
  );
}

export function RangePicker({ value, options = [7, 30, 90], onChange }) {
  return (
    <div className="adm-range" role="group" aria-label="집계 기간">
      {options.map((d) => (
        <button key={d} type="button" className={d === value ? "is-on" : ""} aria-pressed={d === value} onClick={() => onChange(d)}>
          {d}일
        </button>
      ))}
    </div>
  );
}

// 용어는 줄글 대신 dt/dd 표 — 정의가 화면마다 흩어지지 않게 탭 머리에 한 번.
export function AdminTerms({ items }) {
  return (
    <dl className="adm-terms">
      {items.map(([term, definition]) => (
        <div key={term}><dt>{term}</dt><dd>{definition}</dd></div>
      ))}
    </dl>
  );
}

// items: [{ label, value, unit?, tone?: "up"|"down"|"warn" }]
export function AdminKpis({ items }) {
  return (
    <dl className="adm-kpis">
      {items.map((k) => (
        <div key={k.label}>
          <dt>{k.label}</dt>
          <dd className={`num${k.tone ? ` is-${k.tone}` : ""}`}>{k.value}{k.unit ? <i>{k.unit}</i> : null}</dd>
        </div>
      ))}
    </dl>
  );
}

export function AdminBlock({ title, caption, actions, children }) {
  const titleId = useId();
  return (
    <section className="adm-blk">
      <div className="adm-blk-head">
        <h3 id={titleId}>{title}</h3>
        {caption ? <span className="adm-cap">{caption}</span> : null}
        {actions ? <div className="adm-blk-actions">{actions}</div> : null}
      </div>
      <BlockTitleId.Provider value={titleId}>{children}</BlockTitleId.Provider>
    </section>
  );
}

// 가로로 넘치는 표 상자 — 넘칠 때만 Tab 으로 들어와 화살표로 밀 수 있게 한다(키보드만 쓰면 잘린 열을 못 봤다).
// 이름은 블록 제목.
export function AdminScroll({ children }) {
  const ref = useRef(null);
  const titleId = useContext(BlockTitleId);
  const [overflows, setOverflows] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const check = () => setOverflows(el.scrollWidth > el.clientWidth + 1);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      className="adm-tbl"
      tabIndex={overflows ? 0 : undefined}
      role={overflows ? "region" : undefined}
      aria-labelledby={overflows ? titleId : undefined}
    >
      {children}
    </div>
  );
}

// tone: ok | bad | wait | off
export function StatusPill({ tone = "off", children }) {
  return <span className={`adm-pill is-${tone}`}>{children}</span>;
}

// columns: [{ key, label, num?, render?(row, i) }] · rows: 객체 배열 · total: 합계 행 객체(없으면 생략)
// 빈 표는 가짜 숫자 대신 한 줄 안내 — 집계 시작일이 적혀 있어야 "왜 비었나"를 안 묻는다.
export function AdminTable({ columns, rows, total = null, empty = EMPTY_NOTE, rowKey }) {
  const cell = (col, row, i) => (col.render ? col.render(row, i) : row?.[col.key]);
  return (
    <AdminScroll>
      <table>
        <thead>
          <tr>{columns.map((col) => <th key={col.key} className={col.num ? "num" : undefined} scope="col">{col.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr className="adm-empty"><td colSpan={columns.length}>{empty}</td></tr>
          ) : rows.map((row, i) => (
            <tr key={rowKey ? rowKey(row, i) : i}>
              {columns.map((col) => <td key={col.key} className={col.num ? "num" : undefined}>{cell(col, row, i)}</td>)}
            </tr>
          ))}
          {total && rows.length > 0 ? (
            <tr className="adm-total">
              {columns.map((col) => <td key={col.key} className={col.num ? "num" : undefined}>{cell(col, total, -1)}</td>)}
            </tr>
          ) : null}
        </tbody>
      </table>
    </AdminScroll>
  );
}

export function EmptyNote({ children = EMPTY_NOTE }) {
  return <p className="adm-empty-note">{children}</p>;
}

export function Skeleton({ rows = 5 }) {
  return (
    <div className="adm-skel" role="status" aria-label="불러오는 중">
      <div className="adm-skel-kpis">{Array.from({ length: 6 }, (_, i) => <span key={i} />)}</div>
      <div className="adm-skel-chart" />
      {Array.from({ length: rows }, (_, i) => <div key={i} className="adm-skel-row" />)}
    </div>
  );
}

export function ErrorBlock({ message, onRetry }) {
  return (
    <div className="adm-error" role="alert">
      <p>{message || "불러오지 못했어요"}</p>
      <button type="button" className="btn-secondary adm-retry" onClick={onRetry}>다시 시도</button>
    </div>
  );
}
