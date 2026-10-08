import { cloneElement, createContext, isValidElement, useContext, useEffect, useId, useRef, useState } from "react";
import { RULE_TYPES, PERIOD_PRESETS, CANDLE_INTERVALS, MAX_LEVERAGE, FILTERABLE_RULE_TYPES, FILTER_KINDS, withTypeDefaults, evenWeights, splitWeights, weightsAfterAdd, defaultForm } from "../lib/macro.js";
import { EXCHANGES, isDomestic, normalizeExchange, quoteForExchange } from "../lib/exchanges.js";
import InfoTooltip from "./InfoTooltip.jsx";
import { api } from "../api.js";
import { quoteOf, baseOf, fmtKrw } from "../lib/format.js";
import { portfolioWeight } from "../lib/portfolio.js";
import { useUsdKrw } from "../lib/usdkrw.js";
import CoinIcon from "./CoinIcon.jsx";
import "./Builder.css";
import { useSymbolList } from "../hooks/useSymbolList.js";
import { useExchangeSwitch } from "../hooks/useExchangeSwitch.js";
import { searchSymbols, resolveSymbol, marketTags } from "../lib/symbolSearch.js";
import { Icon } from "./icons.jsx";
import LegRuleEditor from "./LegRuleEditor.jsx";

// 촘촘한 판(variant="dense") — 직접 만들기의 좁은 조건 판용. Field·Group 이 이 값을 보고 규격을 바꾼다.
const DenseContext = createContext(false);

// USDT amount plus an approximate KRW reference (when a rate is available).
const money = (v, symbol, rate) => {
  const usdt = `${Number(v || 0).toLocaleString("en-US")} ${quoteOf(symbol)}`;
  if (quoteOf(symbol) === "KRW") return usdt;
  const krw = rate ? fmtKrw(v, rate) : "";
  return krw ? `${usdt} · ${krw}` : usdt;
};

// Live risk read-out for a chosen leverage. Price move to liquidation ≈ 100/N %.
// 청산 경고는 '끼어드는 것'이라 §1-3 예외로 상자를 유지한다(alert).
function leverageRisk(lev) {
  const n = Math.max(1, Math.round(Number(lev) || 1));
  if (n <= 1) return null;
  const movePct = 100 / n;
  let level, cls;
  if (n >= 10) { level = n >= 20 ? "매우 위험" : "고위험"; cls = "alert-risk"; }
  else { level = n >= 4 ? "주의" : "낮음"; cls = "alert-warn"; }
  return { n, movePct, level, cls };
}

// §4 자리별 적용표: 입력 라벨 14/600, 도움말 14/500 — 둘 다 '작은 글씨' 단계.
// name 은 form 의 키 — 검증 오류가 이 칸을 가리키면(error) 노랗게 띄우고 라벨 아래 문구를 적는다. data-field 로 화면이 스크롤·포커스한다.
function Field({ label, term, children, hint, anchor, wide = false, name, error = null }) {
  const dense = useContext(DenseContext);
  const controlId = useId();
  const labelId = `${controlId}-label`;
  const hintId = `${controlId}-hint`;
  const errorId = `${controlId}-error`;
  const directControl =
    isValidElement(children) &&
    typeof children.type === "string" &&
    ["input", "select", "textarea"].includes(children.type);
  const renderedControl = directControl
    ? cloneElement(children, {
        id: children.props.id || controlId,
        "aria-invalid": error ? true : children.props["aria-invalid"],
        "aria-describedby": [children.props["aria-describedby"], hint ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined,
      })
    : children;

  if (dense) {
    return (
      <div
        className={(wide ? "bd-field bd-field-wide" : "bd-field") + (error ? " is-invalid" : "")}
        data-tour={anchor}
        data-field={name}
        role={directControl ? undefined : "group"}
        aria-labelledby={directControl ? undefined : labelId}
        aria-describedby={!directControl && hint ? hintId : undefined}
      >
        <div className="bd-label">
          {directControl ? (
            <label id={labelId} htmlFor={children.props.id || controlId}>{label}</label>
          ) : (
            <span id={labelId}>{label}</span>
          )}
          {term && <InfoTooltip term={term} label={typeof label === "string" ? `${label} 설명` : undefined} />}
        </div>
        {renderedControl}
        {/* 오류와 설명은 서브그리드의 셋째 줄 한 칸을 같이 쓴다 — 따로 두면 넷째 항목이 칸 밖으로 넘쳐 겹쳤다. */}
        {(error || hint) && (
          <div className="bd-foot">
            {error && <div id={errorId} className="bd-error" role="alert">{error}</div>}
            {hint && <div id={hintId} className="bd-hint">{hint}</div>}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className={"block" + (error ? " is-invalid" : "")}
      data-tour={anchor}
      data-field={name}
      role={directControl ? undefined : "group"}
      aria-labelledby={directControl ? undefined : labelId}
      aria-describedby={!directControl && hint ? hintId : undefined}
    >
      <div className="flex items-center t-small font-semibold text-slate-700 mb-2">
        {directControl ? (
          <label id={labelId} htmlFor={children.props.id || controlId}>{label}</label>
        ) : (
          <span id={labelId}>{label}</span>
        )}
        {term && <InfoTooltip term={term} label={typeof label === "string" ? `${label} 설명` : undefined} />}
      </div>
      {renderedControl}
      {error && <div id={errorId} className="t-small font-semibold text-amber-700 mt-2" role="alert">{error}</div>}
      {hint && <div id={hintId} className="t-small text-slate-500 mt-2">{hint}</div>}
    </div>
  );
}

// 빌더의 입력 묶음. 상자로 감싸지 않고 괘선 + 제목으로만 나눈다(§1-3) —
// 폼 상자 예외는 화면 전체를 감싸는 폼(로그인·모달)에만 적용한다.
function Group({ title, term, children, note, anchor }) {
  const dense = useContext(DenseContext);
  if (dense) {
    return (
      <section className="bd-sec" data-tour={anchor}>
        <div className="bd-h">
          <h3>{title}</h3>
          {term && <InfoTooltip term={term} label={typeof title === "string" ? `${title} 설명` : undefined} />}
          {note}
        </div>
        {children}
      </section>
    );
  }
  return (
    <section className="pt-5 border-t border-slate-200" data-tour={anchor}>
      <div className="flex items-center text-slate-700 mb-3">
        <h3 className="t-title">{title}</h3>
        {term && <InfoTooltip term={term} label={typeof title === "string" ? `${title} 설명` : undefined} />}
        {note}
      </div>
      {children}
    </section>
  );
}

const inputCls = "field";

// 종목 칩 — 쉼표 목록(form.symbol)을 칩으로 보여 주고, 입력칸에서 Enter·쉼표로 더한다. 값은 그대로 "BTCUSDT, ETHUSDT".
// 종목 고르기 — 위는 검색창, 아래는 고른 종목의 행 목록(로고 · 티커 · 시장 · 비중 · 빼기). 실제 거래 가능한 종목(/api/symbols)만 들어간다.
// 글자를 치면 관련 종목이 검색창 아래 목록으로 뜨고 Enter·클릭으로 고른다. `CHIP` 처럼 base 만 쳐도 CHIPUSDT 로 맞춘다.
const MAX_SYMBOLS = 5;
// onWeights 를 주지 않으면 비중은 **읽기 전용 표시**(1/N · NN%)다 — 기본 빌더는 스펙대로 균등이고,
// 비중 입력은 프로 빌더(variant="pro")에서만 켠다.
function SymbolPicker({ value, onChange, weights = "", onWeights = null, exchange = "binance", showLegRules = false, legRules = {}, onLegRule = () => {} }) {
  const [draft, setDraft] = useState("");
  const [openRule, setOpenRule] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [note, setNote] = useState("");
  // 목록이 낡았다는 안내(stale)는 보여 주지 않는다 — 응답이 늦게 와 생기는 순간 검색칸 아래가 52px 밀렸다(레이아웃 이동,
  // 2026-10-08 사용자 결정). 대신 낡은 목록에서 검색 결과가 없으면 조용히 목록을 다시 받는다 — 안내의 '다시 확인'
  // 버튼이 하던 일이고, 없으면 막 상장된 종목을 찾을 길이 없다. 잘못된 종목은 고를 때 canChoose 가 막는다.
  const { items, loading, error, reload, stale, canChoose } = useSymbolList(exchange);
  const rootRef = useRef(null);
  const blurTimer = useRef(null);
  const listId = useId().replace(/:/g, "");
  const symbols = String(value || "").split(",").map((part) => part.trim().toUpperCase()).filter(Boolean);
  const query = draft.trim();
  const matches = items && query ? searchSymbols(items, query, { limit: 8, exclude: symbols }) : [];
  const showList = open && query.length > 0;
  const staleMissRef = useRef("");
  useEffect(() => {
    if (!stale || loading || !items || !query || matches.length) return;
    const key = `${exchange}|${query.toUpperCase()}`;
    if (staleMissRef.current === key) return; // 같은 검색어로는 한 번만 — 서버 목록이 계속 낡아도 되풀이하지 않는다
    staleMissRef.current = key;
    reload();
  }, [exchange, items, loading, matches.length, query, reload, stale]);
  const full = symbols.length >= MAX_SYMBOLS;
  const weight = portfolioWeight(symbols.length);
  // 비중을 고칠 수 있는 판인지 — onWeights 가 있을 때만. 없으면 예전처럼 읽기 전용으로 1/N 을 보여 준다.
  const canEditWeights = typeof onWeights === "function";
  // 비중 문자열이 비어 있으면 균등값을 보여 준다 — 사용자가 손대기 전에는 '균등' 이 사실이다.
  // 빈 칸은 걸러내지 않고 자리를 지킨다 — 한 칸을 비우는 사이에 뒤 칸 숫자가 그 자리로 밀려오면
  // 타이핑하는 중에 다른 종목의 비중이 바뀐다.
  const weightList = splitWeights(weights);
  const even = evenWeights(symbols.length);
  // 빈 칸은 빈 칸으로 돌려준다 — 균등값으로 되돌리면 지우는 동작 자체가 불가능해진다.
  const weightAt = (i) => (weightList[i] !== undefined ? weightList[i] : String(even[i] ?? ""));
  const setWeightAt = (i, next) => onWeights(symbols.map((_, idx) => (idx === i ? next : weightAt(idx))).join(", "));
  // 합 계산에서만 빈 칸을 0 으로 센다 — 비운 칸이 있으면 합이 100 이 아니라고 알려 준다.
  const weightTotal = symbols.map((_, i) => Number(weightAt(i))).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
  const weightsOff = symbols.length > 1 && Math.abs(weightTotal - 100) > 0.01;
  const infoOf = (symbol) => (items ? items.find((item) => item.symbol === symbol) : null);
  // 종목마다 규칙 바꾸기 — 프로 판 · 종목이 둘 이상일 때만. 종목 하나는 묶음이 아니다.
  const legRulesOn = showLegRules && symbols.length > 1;

  const add = (symbol) => {
    if (!symbol) return;
    if (symbols.includes(symbol)) { setDraft(""); setNote(""); return; }
    if (full) { setNote(`종목은 최대 ${MAX_SYMBOLS}개까지예요.`); return; }
    if (!canChoose(symbol)) { setNote("종목 목록이 변경되었거나 오래되었어요. 다시 확인한 뒤 선택해 주세요."); reload(); return; }
    onChange([...symbols, symbol].join(", "));
    // 비중을 손댔다면 새 종목에도 몫을 준다 — 셈은 weightsAfterAdd 한 자리에서 한다.
    if (canEditWeights && weightList.length) onWeights(weightsAfterAdd(symbols.map((_, i) => weightAt(i)).join(", ")));
    setDraft(""); setNote(""); setCursor(0);
  };
  const commit = () => {
    if (!query) return;
    if (!items) { setNote(error ? "종목 목록을 못 불러왔어요. 다시 시도해 주세요." : "종목 목록을 불러오는 중이에요."); return; }
    const picked = matches.length ? matches[Math.min(cursor, matches.length - 1)].symbol : resolveSymbol(items, query);
    if (!picked) { setNote(`'${query.toUpperCase()}' 는 거래 가능한 종목이 아니에요.`); return; }
    add(picked);
  };
  const remove = (symbol) => {
    // 비중을 손댔다면 빠진 종목의 몫도 같이 뺀다 — 안 그러면 남은 종목에 엉뚱한 비중이 붙는다.
    if (canEditWeights && weightList.length) onWeights(weightList.filter((_, i) => symbols[i] !== symbol).join(", "));
    // 빠진 종목의 레그 규칙도 같이 지운다 — 같은 종목을 다시 넣었을 때 옛 규칙이 되살아나지 않게.
    if (legRules[symbol]) onLegRule(symbol, null);
    if (openRule === symbol) setOpenRule("");
    onChange(symbols.filter((item) => item !== symbol).join(", "));
    setNote("");
  };

  useEffect(() => { setCursor(0); }, [query]);
  useEffect(() => () => window.clearTimeout(blurTimer.current), []);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div className="bd-symbols" ref={rootRef}>
      <div className={"bd-search" + (full ? " is-full" : "")}>
        <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="9" cy="9" r="5.5" /><path d="M13.5 13.5 17 17" /></svg>
        <input
          className="field"
          value={draft}
          placeholder={full ? `종목 ${MAX_SYMBOLS}개 · 더 넣으려면 하나를 빼요` : symbols.length ? "종목 검색 · 더 넣기" : "종목 검색 · BTC, ETH…"}
          aria-label="종목 검색"
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList && matches[cursor] ? `${listId}-${matches[cursor].symbol}` : undefined}
          autoComplete="off"
          spellCheck={false}
          disabled={full}
          onChange={(event) => { setDraft(event.target.value); setOpen(true); setNote(""); }}
          onFocus={() => { window.clearTimeout(blurTimer.current); setOpen(true); }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); setCursor((c) => Math.min(c + 1, Math.max(0, matches.length - 1))); }
            else if (event.key === "ArrowUp") { event.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
            else if (event.key === "Enter" || event.key === ",") { event.preventDefault(); commit(); }
            else if (event.key === "Escape") { setOpen(false); }
          }}
          onBlur={() => {
            // 목록의 클릭이 먼저 먹도록 잠깐 뒤에 닫는다. 글자가 종목과 정확히 맞으면 그때 넣는다.
            window.clearTimeout(blurTimer.current);
            blurTimer.current = window.setTimeout(() => {
              blurTimer.current = null;
              setOpen(false);
              if (items && query && resolveSymbol(items, query)) add(resolveSymbol(items, query));
            }, 120);
          }}
        />
        {showList && (
          <div className="bd-suggest" role="listbox" id={listId} aria-label="종목 검색 결과">
            {!items && loading && <div className="bd-suggest-note">종목 목록을 불러오는 중…</div>}
            {!items && !loading && error && (
              <div className="bd-suggest-note">종목 목록을 못 불러왔어요. <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={reload}>다시 시도</button></div>
            )}
            {items && matches.length === 0 && <div className="bd-suggest-note">'{query.toUpperCase()}' 에 맞는 종목이 없어요.</div>}
            {matches.map((item, index) => (
              <button
                type="button"
                key={item.symbol}
                id={`${listId}-${item.symbol}`}
                role="option"
                aria-label={item.symbol}
                aria-selected={index === cursor}
                className={"bd-suggest-item" + (index === cursor ? " is-on" : "")}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setCursor(index)}
                onClick={() => add(item.symbol)}
              >
                <CoinIcon symbol={item.symbol} size={20} alt="" />
                <span className="bd-suggest-sym num"><b>{item.base}</b><small>{item.quote}</small></span>
                <span className="bd-suggest-tags" aria-hidden="true">{marketTags(item).map((tag) => <i key={tag}>{tag}</i>)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {note && <div className="bd-error" role="alert">{note}</div>}
      {symbols.length > 0 && (
        <ul className="bd-symrows" aria-label="고른 종목">
          {symbols.map((symbol, index) => {
            const info = infoOf(symbol);
            const tags = info ? marketTags(info) : [];
            return (
              <li key={symbol} className="bd-symrow">
                <div className="bd-symrow-main">
                <CoinIcon symbol={symbol} size={20} alt="" />
                <span className="bd-symrow-sym num"><b>{baseOf(symbol)}</b><small>{quoteOf(symbol)}</small></span>
                {tags.length > 0 && <span className="bd-symrow-tag">{tags.join("·")}</span>}
                {symbols.length > 1 && canEditWeights ? (
                  <>
                    <input
                      className="bd-symrow-w num"
                      type="number"
                      min="0.01" max="100" step="0.01"
                      value={weightAt(index)}
                      aria-label={`${baseOf(symbol)} 비중(%)`}
                      onChange={(event) => setWeightAt(index, event.target.value)}
                    />
                    <span className="bd-symrow-pct" aria-hidden="true">%</span>
                  </>
                ) : (
                  // 읽기 전용 — 비중을 정한 매크로(프로 빌더에서 만든 것)를 들고 왔으면 그 비중을 적는다.
                  // 손대지 않았으면 전과 같이 1/N 이다. 여기서 1/N 을 고집하면 70/30 묶음이 50% 로 보인다.
                  <span className="bd-symrow-w num" title="자금 비중">
                    {weightList.length && weightAt(index) !== "" ? `${weightAt(index)}%` : `${weight.fraction} · ${weight.percent}`}
                  </span>
                )}
                {legRulesOn && (
                  <button
                    type="button"
                    className={"bd-symrow-rule" + (legRules[symbol] ? " is-on" : "")}
                    onClick={() => setOpenRule(openRule === symbol ? "" : symbol)}
                    aria-expanded={openRule === symbol}
                    aria-controls={`${listId}-rule-${symbol}`}
                    aria-label={`${baseOf(symbol)} ${legRules[symbol] ? RULE_TYPES[legRules[symbol].rule_type]?.label || "규칙 바뀜" : "규칙 바꾸기"}`}
                    title={legRules[symbol] ? "이 종목만 다른 규칙으로 돌리는 중" : "이 종목만 다른 규칙으로 돌리기"}
                  >
                    {legRules[symbol] ? RULE_TYPES[legRules[symbol].rule_type]?.label || "규칙 바뀜" : "규칙 바꾸기"}
                  </button>
                )}
                <button type="button" className="bd-symrow-x" onClick={() => remove(symbol)} aria-label={`${symbol} 빼기`}><Icon name="x" size={14} strokeWidth={2.25} /></button>
                </div>
                {legRulesOn && openRule === symbol && (
                  <LegRuleEditor
                    id={`${listId}-rule-${symbol}`}
                    symbol={symbol}
                    exchange={exchange}
                    rule={legRules[symbol] || withTypeDefaults({ ...defaultForm(), exchange }, "E")}
                    onChange={(next) => onLegRule(symbol, next)}
                    onClear={() => { onLegRule(symbol, null); setOpenRule(""); }}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}
      {symbols.length > 1 && canEditWeights && (
        <div className="bd-weights-foot">
          {weightsOff && (
            <div className="bd-error" role="alert">
              비중의 합이 {Number(weightTotal.toFixed(2))}% 예요 · 100% 로 맞춰 주세요
            </div>
          )}
          <button type="button" className="bd-weights-even" onClick={() => onWeights("")}>
            균등하게
          </button>
        </div>
      )}
      <div className="bd-hint">
        {symbols.length > 1
          ? `${symbols.length}종목 · ${weightList.length ? "종목마다 비중을 정했어요" : "자금을 종목 수만큼 똑같이 나눠요"} · 최대 ${MAX_SYMBOLS}개`
          : `여러 종목을 넣으면 자금을 나눠요 · 최대 ${MAX_SYMBOLS}개`}
      </div>
    </div>
  );
}

// `chartSlot(basicSettings)` 은 기본 설정을 감싸 참고 차트와 한 블록으로 묶는
// 래퍼다. Studio 가 넘겨준다 — 폼 컴포넌트가 차트·시세 폴링까지 끌어안지 않도록
// 자리만 비워 둔다. 넘어오지 않으면 기본 설정만 그대로 그린다.
// intervalOptions — 봉 간격 선택지를 밖에서 준다(예: 테스트 기간에서 봉 수 한도를 넘는 간격은 disabled + title).
// scope="leg" — 묶음 안 종목 하나의 규칙 판(LegRuleEditor 가 쓴다). 매매 방식 · 전략 조건 · 진입 조건만 그린다.
//   종목 고르기를 그리지 않으므로 종목 행의 '규칙 바꾸기'(= LegRuleEditor)도 없다 — 둘이 서로를 부르는 고리를 여기서 끊는다.
export default function Builder({ form, setForm, chartSlot = null, variant = "default", intervalOptions = null, fieldError = null, scope = "bundle" }) {
  const dense = variant === "dense";
  // 프로 판(variant="pro") — 넉넉한 격자를 그대로 쓰면서 묶음 기능(비중 입력 · 레그 규칙 · 묶음 한도)을 켠다.
  // dense 는 "촘촘한 판" 이라는 레이아웃 이름이고 "프로" 가 아니다 — 그 둘을 섞으면 프로 빌더 전체가 다시 조판된다.
  const pro = variant === "pro";
  // 종목 고르기(검색 + 종목 행)를 쓰는 판 — 촘촘한 판과 프로 판. 기본 변형은 평문 입력칸이다.
  const picker = dense || pro;
  const leg = scope === "leg";
  // 격자 — 기본은 sm 에서 2·3열, 조건 판은 컨테이너 너비에 따라 1·2열.
  const g2 = dense ? "bd-grid" : "grid grid-cols-1 sm:grid-cols-2 gap-4";
  const g3 = dense ? "bd-grid" : "grid grid-cols-1 sm:grid-cols-3 gap-4";
  const g2y = dense ? "bd-grid" : "grid grid-cols-1 sm:grid-cols-2 gap-4 gap-y-5";
  const g3m = dense ? "bd-grid" : "grid grid-cols-1 sm:grid-cols-3 gap-4 gap-y-5 mt-4";
  const g2full = dense ? "col-span-full bd-grid" : "col-span-full grid grid-cols-1 sm:grid-cols-2 gap-4 items-end";
  const instanceId = useId().replace(/:/g, "");
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  // 검증 오류가 가리키는 칸 — 그 칸만 문구와 노랑을 받는다.
  const errOf = (k) => (fieldError && fieldError.field === k ? fieldError.message : null);
  const fieldCls = (k, base) => base + (errOf(k) ? " is-invalid" : "");
  const setChk = (k) => (e) => setForm({ ...form, [k]: e.target.checked });
  const rt = form.rule_type;
  const symbolCount = new Set(String(form.symbol || "").split(",").map((part) => part.trim().toUpperCase()).filter(Boolean)).size;
  const meta = RULE_TYPES[rt];
  const isShort = form.position_side === "short";
  const exchange = normalizeExchange(form.exchange);
  const domestic = isDomestic(exchange);
  const allowShort = meta.allowShort && !domestic;
  const quote = quoteForExchange(exchange);
  const moneySymbol = form.symbol || (domestic ? "KRW-BTC" : "BTCUSDT");
  // 거래소를 바꿀 때 종목 유지·비움 안내는 화면에 띄우지 않는다(2026-10-02) — 종목 칸이 그대로 보여 준다.
  const { switchExchange } = useExchangeSwitch(form, setForm);
  const { rate: krwRate } = useUsdKrw();
  const [fundingBusy, setFundingBusy] = useState(false);
  const [fundingMsg, setFundingMsg] = useState("");

  async function loadFunding() {
    setFundingBusy(true);
    setFundingMsg("");
    try {
      const d = await api.fundingRate(form.symbol.toUpperCase(), form.preset, form.start, form.end);
      if (d.available && d.avg_daily_funding_pct != null) {
        setForm((f) => normalizeExchange(f.exchange) === exchange ? ({ ...f, funding_pct: d.avg_daily_funding_pct }) : f);
        setFundingMsg(`실제 평균 펀딩비 적용: 일 ${d.avg_daily_funding_pct}%`);
      } else {
        setFundingMsg("이 종목은 선물 펀딩 데이터가 없어요 (현물 전용일 수 있음).");
      }
    } catch (e) {
      setFundingMsg("펀딩비 조회 실패: " + String(e.message || e));
    } finally {
      setFundingBusy(false);
    }
  }

  // Field builders — plain functions (invoked, not JSX components) so inputs
  // keep focus across keystrokes. They close over the current `form`.
  const num = (k, label, opts = {}) => {
    if (dense) {
      // 라벨 끝의 "(단위)" 를 칸 안 접미사로. 단위를 따로 주면 라벨은 그대로 둔다.
      const match = opts.unit ? null : /^(.*?)\s*\(([^(),]+)\)\s*$/.exec(label);
      const text = match ? match[1] : label;
      const unit = opts.unit || (match ? match[2] : "");
      return (
        <Field key={k} name={k} error={errOf(k)} label={opts.denseLabel || text} term={opts.term} hint={opts.hint} anchor={opts.anchor} wide={opts.wide}>
          <div className="bd-unit" style={{ "--bd-unit-width": unit ? `${Math.max(30, unit.length * 7 + 18)}px` : "9px" }}>
            <input className={fieldCls(k, "field num")} type="number" step={opts.step || "any"} value={form[k]} onChange={set(k)} aria-label={label} aria-invalid={errOf(k) ? true : undefined} />
            {unit && <span className="bd-unit-tag">{unit}</span>}
          </div>
        </Field>
      );
    }
    return (
      <Field key={k} name={k} error={errOf(k)} label={label} term={opts.term} hint={opts.hint} anchor={opts.anchor}>
        <input className={fieldCls(k, "field num")} type="number" step={opts.step || "any"} value={form[k]} onChange={set(k)} />
      </Field>
    );
  };
  const sel = (k, label, options, opts = {}) => (
    <Field key={k} name={k} error={errOf(k)} label={label} term={opts.term} hint={opts.hint} anchor={opts.anchor} wide={opts.wide}>
      <select className={fieldCls(k, inputCls)} value={form[k]} onChange={set(k)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={!!o.disabled} title={o.title}>{o.label}{o.disabled ? " · 불가" : ""}</option>
        ))}
      </select>
    </Field>
  );
  const chk = (k, label, opts = {}) => (
    <div key={k} className={dense ? "bd-check" : "flex items-center gap-1 h-12 t-small text-slate-700"}>
      <label htmlFor={`${instanceId}-${k}`} className="flex items-center gap-2 cursor-pointer">
        <input id={`${instanceId}-${k}`} type="checkbox" checked={!!form[k]} onChange={setChk(k)} />
        {label}
      </label>
      {opts.term && <InfoTooltip term={opts.term} label={typeof label === "string" ? `${label} 설명` : undefined} />}
    </div>
  );
  // 체크박스가 딸린 숫자 칸 — 촘촘한 판에서는 라벨의 "(단위)" 를 칸 안 접미사로 옮긴다.
  const unitLabel = (label) => (dense ? label.replace(/\s*\([^()]+\)\s*$/, "") : label);
  const unitInput = (input, unit) =>
    dense ? (
      <div className="bd-unit flex-1" style={{ "--bd-unit-width": unit.length > 1 ? "38px" : "30px" }}>
        {input}
        <span className="bd-unit-tag">{unit}</span>
      </div>
    ) : (
      input
    );
  // 레그 판에는 시작 자금 칸이 없다 — 서버가 종목 비중대로 나눠 덮어쓴다(Macro.for_leg).
  const cap = leg ? null : num("initial_capital", `시작 자금 (${quote})`, {
    hint: money(form.initial_capital, moneySymbol, krwRate),
  });

  // 차트를 정하는 값들 — 종목·매매 방식·포지션·봉 간격·기간. 차트 섹션 안으로
  // 들어가 "무엇을 볼지 정하고 바로 아래에서 본다"가 한 덩어리로 읽힌다.
  const symbolField = (
    // 종목 고르기를 쓰는 판은 도움말 문장 대신 라벨 옆 ⓘ 하나(용어 'symbols').
    <Field label="종목" anchor="symbol" term={picker ? "symbols" : undefined} hint={picker ? undefined : "여러 종목은 쉼표로 나눠 써요. 자금을 종목 수만큼 똑같이 나눠 종목마다 따로 돌리고, 결과는 총합이에요."}>
      {picker ? (
        // 비중을 **고치는** 것은 프로 판에서만 — onWeights 를 안 넘기면 SymbolPicker 가 읽기 전용으로 그린다.
        // weights 는 두 판에 다 넘긴다: 비중을 정한 매크로를 기본 빌더로 들고 왔을 때 70/30 을 1/N 으로
        // 적으면 거짓이 되므로, 고칠 수 없어도 사실은 보여 준다.
        <SymbolPicker key={exchange} exchange={exchange} value={form.symbol} weights={form.leg_weights || ""}
          {...(pro ? { onWeights: (value) => setForm((current) => ({ ...current, leg_weights: value })) } : {})}
          showLegRules={pro && !leg}
          legRules={form.leg_rules || {}}
          onLegRule={(symbol, next) => setForm((current) => {
            const rules = { ...(current.leg_rules || {}) };
            if (next === null) delete rules[symbol]; else rules[symbol] = next;
            return { ...current, leg_rules: rules };
          })}
          onChange={(value) => setForm((current) => normalizeExchange(current.exchange) === exchange ? { ...current, symbol: value } : current)} />
      ) : (
        <input className={inputCls} value={form.symbol} onChange={set("symbol")} placeholder={domestic ? "KRW-BTC 또는 KRW-BTC, KRW-ETH" : "BTCUSDT 또는 BTCUSDT, ETHUSDT"} />
      )}
    </Field>
  );
  const exchangeField = (
    // 세 거래소를 포지션처럼 한 줄 segmented 로 — 드롭다운보다 한 번에 읽힌다(2026-10-02).
    <Field name="exchange" error={errOf("exchange")} label="거래소" anchor="exchange">
      <div className={"seg bd-seg bd-seg-exchange" + (dense ? "" : " w-full")} role="group" aria-label="거래소">
        {EXCHANGES.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => { if (item.value !== exchange) switchExchange(item.value); }}
            aria-pressed={item.value === exchange}
            title={`${item.label} · ${item.quote}`}
            className={"seg-item " + (item.value === exchange ? "seg-item-on" : "")}
          >
            <img className="bd-exchange-logo" src={`/exchanges/${item.value}.${item.value === "binance" ? "svg" : "png"}`} width="16" height="16" alt="" aria-hidden="true" draggable="false" />
            {item.label}
          </button>
        ))}
      </div>
    </Field>
  );
  const strategyField = (
    <Field label="매매 방식" anchor="strategy" term={`strat_${rt}`}>
      <select className={inputCls} value={rt} onChange={(e) => setForm(withTypeDefaults(form, e.target.value))}>
        {Object.entries(RULE_TYPES).map(([k, v]) => (
          <option key={k} value={k} disabled={domestic && k === "K"}>{v.label}{domestic && k === "K" ? " · 국내 현물 불가" : ""}</option>
        ))}
      </select>
    </Field>
  );
  const positionHint = (
    <span className="inline-flex items-center flex-wrap">
      롱 <InfoTooltip term="long" />
      <span className="ml-2">숏</span> <InfoTooltip term="short" />
    </span>
  );
  const positionField = dense ? (
    // 촘촘한 판은 두 값뿐이라 segmented — select 보다 한 번에 읽힌다. 롱·숏 설명은 라벨 옆 ⓘ 하나('position').
    <Field name="position_side" error={errOf("position_side")} label="포지션" anchor="position" term="position">
      <div className="seg bd-seg" role="group" aria-label="포지션">
        <button type="button" onClick={() => setForm({ ...form, position_side: "long" })} aria-pressed={!isShort} className={"seg-item " + (!isShort ? "seg-item-on" : "")}>롱</button>
        <button
          type="button"
          onClick={() => setForm({ ...form, position_side: "short" })}
          disabled={!allowShort}
          title={!allowShort ? (domestic ? "국내 원화 현물에서는 숏을 사용할 수 없어요" : "이 매매 방식은 숏을 지원하지 않아요") : undefined}
          aria-pressed={isShort}
          className={"seg-item " + (isShort ? "seg-item-on" : "")}
        >
          숏
        </button>
      </div>
    </Field>
  ) : (
    <Field name="position_side" error={errOf("position_side")} label="포지션" anchor="position" hint={positionHint}>
      <select className={fieldCls("position_side", inputCls)} value={form.position_side} onChange={set("position_side")} disabled={!allowShort}>
        <option value="long">롱 (long)</option>
        <option value="short" disabled={!allowShort}>숏 (short)</option>
      </select>
    </Field>
  );
  const intervalChoices = (intervalOptions || CANDLE_INTERVALS).map((item) => domestic && rt === "C" && item.value !== "1d" ? { ...item, disabled: true, title: "국내 정기 분할매수는 일봉으로만 확인해요" } : item);
  const intervalField = sel("candle_interval", "봉 간격", intervalChoices, {
    term: "candle_interval",
    anchor: "interval",
    hint: meta.indicator ? "지표 계산 기준(필수)" : "체결 판정 기준",
  });
  const periodField = (
    <Field label="테스트 기간" anchor="period" term="backtest">
      <select className={inputCls} value={form.preset} onChange={set("preset")}>
        {PERIOD_PRESETS.map((p) => (
          <option key={p.value} value={p.value}>{p.label}</option>
        ))}
      </select>
    </Field>
  );
  const customRange = form.preset === "custom" && (
    <div className={g2}>
      <Field label="시작일" hint="YYYY-MM-DD">
        <input className={inputCls} type="date" value={form.start} onChange={set("start")} />
      </Field>
      <Field label="종료일" hint="YYYY-MM-DD">
        <input className={inputCls} type="date" value={form.end} onChange={set("end")} />
      </Field>
    </div>
  );

  // 국내(원화) 거래소 제약 — 거래소 고르기 바로 아래 노란 경고 글씨(거래소가 정한 제약이라서).
  const domesticWarning = domestic
    ? <p className="bd-domestic-warning t-caption text-amber-700" role="note">원화 현물 전용 · 숏·레버리지·선물은 사용할 수 없어요.</p>
    : null;

  // 차트를 정하는 값들 — 종목·매매 방식·포지션·봉 간격·기간. 차트 섹션 안으로
  // 들어가 "무엇을 볼지 정하고 바로 아래에서 본다"가 한 덩어리로 읽힌다.
  const basicSettings = leg ? (
    // 레그 판 — 거래소 · 종목 · 포지션 · 봉 간격 · 기간은 묶음이 정한다. 규칙 고르기만 둔다.
    dense ? <section className="bd-sec"><div className="bd-grid">{strategyField}</div></section> : <div>{strategyField}</div>
  ) : dense ? (
    <section className="bd-sec">
      <div className="bd-grid">{exchangeField}</div>
      {domesticWarning}
      <div className="bd-grid">{symbolField}{strategyField}</div>
      <div className="bd-grid">{positionField}{intervalField}{periodField}</div>
      {customRange}
    </section>
  ) : (
    <div className="space-y-5">
      <div className="space-y-2">{exchangeField}{domesticWarning}</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 gap-y-5">{symbolField}{strategyField}</div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">{positionField}{intervalField}{periodField}</div>
      {customRange}
    </div>
  );

  return (
    <DenseContext.Provider value={dense}>
    <div className={dense ? "builder-dense" : pro ? "builder-pro space-y-5" : "space-y-5"}>
      {/* 차트를 움직이는 설정과 차트를 한 블록으로 묶어 맨 위에 고정한다.
          Studio 가 스티키 섹션으로 감싸므로 여기서는 자리만 만든다. */}
      {chartSlot && !leg ? chartSlot(basicSettings) : basicSettings}

      {/* rule-specific params */}
      <Group title={<>전략 조건 · <span className="text-slate-900">{meta.label}</span></>} anchor="strategy-params">
        {rt === "A" && (
          <div className={g2}>
            {num("take_profit_pct", "익절 기준 (%)", { term: "take_profit" })}
            {cap}
          </div>
        )}
        {rt === "B" && (
          <div className={g3}>
            {num("buy_price", `살 가격 (${quote})`, { term: "limit_order", hint: isShort ? "숏을 되사서 정리할 가격이에요" : undefined })}
            {num("sell_price", `팔 가격 (${quote})`, { term: "limit_order", hint: isShort ? "팔아서 숏에 들어갈 가격이에요" : undefined })}
            {cap}
          </div>
        )}
        {rt === "C" && (
          <div className={g2}>
            {cap}
            {num("amount_per_buy", `한 번에 살 금액 (${quote})`, { term: "dca", hint: money(form.amount_per_buy, moneySymbol, krwRate) })}
            {num("interval_days", "매수 간격 (일)", { term: "dca" })}
          </div>
        )}

        {rt === "D" && (
          <div className={g2}>
            {num("lower_price", `가격 범위 하단 (${quote})`, { term: "grid" })}
            {num("upper_price", `가격 범위 상단 (${quote})`, { term: "grid" })}
            {num("grid_count", "나눌 칸 수", { term: "grid_count", step: "1" })}
            {sel("grid_mode", "칸 간격", [{ value: "arithmetic", label: "같은 금액 간격" }, { value: "geometric", label: "같은 비율 간격" }], { term: "grid_mode" })}
            {num("per_grid_invest", `격자당 투입액 (빈칸=균등, ${quote})`, { denseLabel: "격자당 투입액", unit: quote, hint: "비우면 예산을 격자 수로 균등 분배", wide: true })}
            {sel("band_exit_action", "가격 범위를 벗어나면", [{ value: "stop", label: "전량 정리하고 중단" }, { value: "hold", label: "보유 유지" }], { wide: true })}
            <div className={g2full}>
              {chk("rebalance_on_start", "시작 가격에 맞춰 칸 다시 배치")}
              {cap}
            </div>
          </div>
        )}

        {rt === "E" && (
          <div className={g2}>
            {sel("entry_mode", "처음 들어갈 때", [{ value: "immediate", label: "바로 진입" }, { value: "dip", label: "가격이 내리면 진입" }], { wide: true })}
            {num("entry_dip", "진입할 하락폭 (%)", { hint: "가격이 내리면 진입을 골랐을 때 써요" })}
            {num("activation_profit", "추적을 시작할 이익 (%)", { term: "activation_profit" })}
            {num("trail_percent", "고점에서 허용할 하락폭 (%)", { term: "trail_percent" })}
            {chk("reenter_after_exit", "정리한 뒤 다시 진입")}
            {cap}
          </div>
        )}

        {rt === "F" && (
          <div className={g2}>
            {num("rsi_period", "RSI 계산 기간", { term: "rsi", step: "1" })}
            {num("confirm_candles", "신호를 확인할 봉 수", { step: "1", hint: "연속해서 조건을 만족해야 신호로 봐요" })}
            {num("entry_threshold", isShort ? "숏을 정리할 RSI" : "진입할 RSI", {
              hint: isShort ? "이 숫자 이하로 내려오면 숏을 정리해요" : "이 숫자 이하일 때 진입해요",
            })}
            {num("exit_threshold", isShort ? "숏에 진입할 RSI" : "정리할 RSI", {
              hint: isShort ? "이 숫자 이상일 때 팔아서 숏에 들어가요" : "이 숫자 이상일 때 정리해요",
            })}
            {sel("exit_mode", "정리 기준", [{ value: "indicator", label: "RSI 신호" }, { value: "take_profit", label: "익절 기준" }, { value: "both", label: "둘 중 먼저" }])}
            {num("take_profit", "익절 기준 (%)", { hint: "익절 기준을 포함할 때 써요" })}
            {cap}
          </div>
        )}

        {rt === "G" && (
          <div className={g2}>
            {num("bb_period", "평균 계산 기간", { term: "bollinger", step: "1" })}
            {num("bb_std", "밴드 폭 (표준편차 σ)", { term: "bollinger", denseLabel: "밴드 폭 · 표준편차", unit: "σ" })}
            {sel("strategy", "밴드를 쓰는 방식", [{ value: "reversion", label: "밴드 안으로 되돌아오기" }, { value: "breakout", label: "밴드 밖으로 돌파하기" }], {
              wide: true,
              hint: isShort
                ? "숏은 방향이 반대예요 — 되돌아오기는 상단 밴드, 돌파하기는 하단 이탈에서 진입해요"
                : undefined,
            })}
            {sel("exit_target", "정리할 위치", [{ value: "mid", label: "가운데 선" }, { value: "opposite", label: "반대쪽 밴드" }], { wide: true })}
            <div className={g2full}>
              {chk("squeeze_filter", "변동성이 줄어든 구간만 사용", { term: "squeeze" })}
              {num("squeeze_lookback", "변동성 비교 기간", { step: "1" })}
              {dense && cap}
            </div>
            {!dense && cap}
          </div>
        )}

        {rt === "H" && (
          <div className={g2}>
            {num("base_order_size", `처음 살 금액 (${quote})`, { term: "martingale" })}
            {num("safety_order_size", `첫 추가매수 금액 (${quote})`, { term: "safety_order" })}
            {num("price_deviation", "추가매수할 하락 간격 (%)", { hint: "가격이 이만큼 더 내릴 때마다 추가로 사요" })}
            {num("max_safety_orders", "최대 추가매수 횟수", { step: "1" })}
            {num("safety_order_step_scale", "하락 간격 배율")}
            {num("safety_order_volume_scale", "추가매수 금액 배율")}
            {num("take_profit", "평균 매수가 기준 익절 (%)", { hint: "전체 평균 매수가를 기준으로 계산해요" })}
            {cap}
            <div className="col-span-full t-caption text-amber-700">손절은 평균 매수가를 기준으로 적용돼요. 총 소요자금이 (시작 자금 × 투입 비율)을 넘으면 저장되지 않아요.</div>
          </div>
        )}

        {rt === "I" && (
          <div className={g2}>
            {num("k", "돌파 기준 계수 (k)", { term: "volatility_breakout" })}
            {sel("exit_mode", "정리 기준", [{ value: "next_open", label: "다음 봉 시작 가격" }, { value: "trailing", label: "고점 추적" }, { value: "take_profit", label: "익절 기준" }], { wide: true })}
            {num("trail_percent", "고점에서 허용할 하락폭 (%)", { term: "trail_percent", hint: "고점 추적을 골랐을 때 써요" })}
            {num("take_profit", "익절 기준 (%)", { hint: "익절 기준을 골랐을 때 써요" })}
            {num("ma_filter_period", "이동평균 필터 기간", { step: "1", hint: "비워두면 사용하지 않아요" })}
            {num("session_start_hour", "하루 계산 시작 시각", { step: "1" })}
            {cap}
            <div className="col-span-full t-caption text-slate-500">봉 단위 데이터로 전일 변동폭을 계산해요.</div>
          </div>
        )}

        {rt === "J" && (
          <div className={g2}>
            {sel("ma_type", "이동평균 종류", [{ value: "SMA", label: "단순 이동평균 (SMA)" }, { value: "EMA", label: "최근 가격 비중이 큰 평균 (EMA)" }], { term: "ma_cross", wide: true })}
            {!dense && num("confirm_candles", "신호를 확인할 봉 수", { step: "1" })}
            {num("fast_period", "짧은 이동평균 기간", { step: "1" })}
            {num("slow_period", "긴 이동평균 기간", { step: "1" })}
            {sel("exit_signal", "정리 기준", [{ value: "dead_cross", label: isShort ? "평균선이 위로 교차" : "평균선이 아래로 교차" }, { value: "take_profit", label: "익절 기준" }, { value: "both", label: "둘 중 먼저" }], {
              wide: true,
              hint: isShort ? "숏은 데드크로스에서 들어가고 골든크로스에서 정리해요" : undefined,
            })}
            {dense && num("confirm_candles", "신호를 확인할 봉 수", { step: "1" })}
            {num("take_profit", "익절 기준 (%)", { hint: "익절 기준을 포함할 때 써요" })}
            {cap}
          </div>
        )}

        {rt === "K" && (
          <div className={g2}>
            {num("drop_trigger_pct", "방어를 시작할 하락폭 (%)", { hint: "진입가보다 이만큼 내리면 방어를 시작해요" })}
            {num("partial_exit_pct", "방어할 때 팔 비율 (%)", { hint: "들고 있는 수량 중 몇 %를 팔지 정해요" })}
            {chk("flip_to_short", "일부를 판 뒤 숏으로 전환")}
            {num("long_take_profit_pct", "롱 익절 기준 (%)", { hint: "비워두면 하락 방어만 사용해요" })}
            {num("short_take_profit_pct", "숏 익절 기준 (%)", { hint: "숏 전환 뒤 가격이 더 내릴 때 정리해요" })}
            {num("short_stop_loss_pct", "숏 손절 기준 (%)", { hint: "가격이 반등할 때 손실을 제한해요. 필수예요" })}
            {chk("reenter_long_after", "숏을 끝낸 뒤 롱으로 다시 진입")}
            {cap}
            <div className="col-span-full t-caption text-amber-700">숏 전환이 포함돼 선물(USDT-M)로 실행돼요. 숏 손절 기준은 필수예요.</div>
          </div>
        )}
        {/* 진입 조건 — 필터를 쓰는 일곱 규칙에서만 그린다. 못 쓰는 규칙(A~D)은 아무것도 그리지 않는다. */}
        {FILTERABLE_RULE_TYPES.includes(rt) && (
          <div className={g2full + (dense ? "" : " mt-4")}>
            {/* chk 는 opts.term 만 받는다 — 설명은 종류 칸의 hint 로 붙인다. */}
            {chk("use_entry_filter", "진입 조건 더 달기")}
            {/* 변동성 돌파(I)에는 따로 이동평균 필터가 있다 — 둘 다 따로 작동하므로 화면이 둘 다 맞아야 진입한다고 말해 준다. */}
            {form.use_entry_filter && sel("filter_kind", "진입 조건 종류", FILTER_KINDS,
              { hint: "이 조건이 아닐 때는 사지 않아요. 청산은 그대로예요" + (rt === "I" ? ". 이 전략에는 따로 '이동평균 필터 기간' 칸이 있어요. 거기에 값을 넣었다면 둘 다 맞아야 진입해요" : "") })}
            {form.use_entry_filter && form.filter_kind === "ma" && (
              <>
                {sel("filter_ma_type", "조건 이동평균 종류", [{ value: "SMA", label: "단순(SMA)" }, { value: "EMA", label: "지수(EMA)" }])}
                {num("filter_ma_period", "조건 이동평균 기간 (봉)", { step: "1", hint: rt === "I" ? "위 '이동평균 필터 기간'과는 별개예요. 둘 다 맞아야 진입해요" : undefined })}
                {sel("filter_ma_side", "어느 쪽일 때 살까", [{ value: "above", label: "이평선 위" }, { value: "below", label: "이평선 아래" }])}
              </>
            )}
            {form.use_entry_filter && form.filter_kind === "rsi" && (
              <>
                {num("filter_rsi_period", "조건 RSI 기간 (봉)", { step: "1" })}
                {num("filter_rsi_min", "조건 RSI 아래 한도", { hint: "비워두면 아래 한도 없음" })}
                {num("filter_rsi_max", "조건 RSI 위 한도", { hint: "비워두면 위 한도 없음" })}
              </>
            )}
            {form.use_entry_filter && form.filter_kind === "bb" && (
              <>
                {num("filter_bb_period", "조건 볼린저 기간 (봉)", { step: "1" })}
                {num("filter_bb_num_std", "조건 표준편차 배수")}
                {sel("filter_bb_zone", "어느 자리일 때 살까", [
                  { value: "inside", label: "밴드 안" },
                  { value: "below_lower", label: "하단 밖" },
                  { value: "above_upper", label: "상단 밖" },
                ])}
              </>
            )}
            {form.use_entry_filter && form.filter_kind === "volume" && (
              <>
                {num("filter_vol_period", "조건 거래량 평균 기간 (봉)", { step: "1" })}
                {num("filter_vol_multiple", "평균의 몇 배 이상")}
              </>
            )}
          </div>
        )}
      </Group>

      {/* common risk */}
      {!leg && (
      <Group title="손실 제한" anchor="risk">
        <div className={g2y}>
          {num("invest_ratio_pct", "한 번에 사용할 자금 (%)", { term: "invest_ratio", hint: "시작 자금 중 한 번에 얼마를 쓸지 정해요" })}
          <Field name="stop_loss_pct" error={errOf("stop_loss_pct")} label={unitLabel("손절 기준 (%)")} term="stop_loss" hint={isShort && (rt === "A" || rt === "B") ? "숏은 손절이 필수예요" : "사용하지 않으려면 체크를 풀어요"}>
            <div className="flex items-center gap-2">
              <label className="bd-check-hit"><input aria-label="손절 기준 사용" type="checkbox" checked={form.use_stop_loss} disabled={isShort && (rt === "A" || rt === "B")} onChange={setChk("use_stop_loss")} /></label>
              {unitInput(
                <input aria-label="손절률 (%)" className={fieldCls("stop_loss_pct", "field num")} type="number" value={form.stop_loss_pct} disabled={!form.use_stop_loss} onChange={set("stop_loss_pct")} aria-invalid={errOf("stop_loss_pct") ? true : undefined} />,
                "%",
              )}
            </div>
          </Field>
        </div>
        {/* 묶음 한도 — 프로 판 · 종목이 둘 이상일 때만. 레그(종목) 하나의 위험 관리와 섞이지 않게 라벨마다 '묶음' 을 붙인다. */}
        {pro && symbolCount > 1 && (
          <div className="bd-bundle-risk">
            {chk("use_bundle_risk", "묶음 한도 쓰기", { term: "bundle_risk" })}
            {form.use_bundle_risk && (
              <div className={g2y}>
                {num("bundle_max_positions", "묶음 동시 보유 종목 수", {
                  step: 1,
                  hint: `한 번에 포지션을 들고 있을 종목 수. 종목 수(${symbolCount}개)보다 작아야 의미가 있어요.`,
                })}
                {num("bundle_max_exposure_pct", "묶음 총 노출 한도 (%)", {
                  step: 0.1,
                  hint: "진입 기준 금액 합이 이 비율에 닿으면 새로 안 사요. 들고 있는 건 그대로 팔아요.",
                })}
              </div>
            )}
          </div>
        )}
      </Group>
      )}

      {/* advanced common risk. For DCA (rule C) the time-based holding/cooldown
          controls don't apply (buy-and-accumulate, no round-trip exits), so they
          are disabled with a note; 일일 최대손실 still works (halts buys for the day). */}
      {!leg && (() => {
        const isDca = rt === "C";
        return (
          <details className={dense ? "bd-sec bd-details" : "pt-5 border-t border-slate-200"} data-tour="advanced-risk">
            <summary className={dense ? "bd-sum" : "t-label text-slate-700 cursor-pointer"}>
              고급 위험 관리
              {dense && <small>{form.use_daily_max_loss || (!isDca && form.use_max_holding) ? "사용 중" : "미사용"}</small>}
            </summary>
            <div className={g3m}>
              <Field label={unitLabel("일일 최대손실 (%)")} term="daily_max_loss" hint={isDca ? "도달하면 그날 추가 매수를 멈춰요" : "도달하면 그날 거래를 멈춰요"}>
                <div className="flex items-center gap-2">
                  <label className="bd-check-hit"><input aria-label="일일 최대손실 사용" type="checkbox" checked={form.use_daily_max_loss} onChange={setChk("use_daily_max_loss")} /></label>
                  {unitInput(
                    <input aria-label="일일 최대손실률 (%)" className="field num" type="number" value={form.daily_max_loss_pct} disabled={!form.use_daily_max_loss} onChange={set("daily_max_loss_pct")} />,
                    "%",
                  )}
                </div>
              </Field>
              <Field label="최대 보유시간" term="max_holding" hint={isDca ? "분할매수에는 사용하지 않아요" : "이 시간을 넘기면 강제로 정리해요"}>
                <div className="flex items-center gap-2">
                  <label className="bd-check-hit"><input aria-label="최대 보유시간 사용" type="checkbox" checked={!isDca && form.use_max_holding} disabled={isDca} onChange={setChk("use_max_holding")} /></label>
                  {unitInput(
                    <input aria-label="최대 보유시간 (시간)" className="field num" type="number" value={form.max_holding_hours} disabled={isDca || !form.use_max_holding} onChange={set("max_holding_hours")} />,
                    "시간",
                  )}
                </div>
              </Field>
              {isDca ? (
                <Field label="손절 뒤 쉬는 시간 (분)" term="cooldown" hint="분할매수에는 사용하지 않아요">
                  <input className="field num" type="number" value={form.cooldown_minutes} disabled onChange={set("cooldown_minutes")} />
                </Field>
              ) : (
                num("cooldown_minutes", "손절 뒤 쉬는 시간 (분)", { term: "cooldown", hint: "이 시간 동안 다시 진입하지 않아요" })
              )}
            </div>
            {isDca && (
              <div className="notice mt-4 t-small text-slate-700">
                DCA 전략은 사고 나서 계속 들고 가는 방식이라 <b className="text-slate-900">최대 보유시간·재진입 금지</b>는 적용되지 않아요. 익절·청산이 있는 전략(A·B·E~J)에서 동작해요.
              </div>
            )}
          </details>
        );
      })()}

      {/* fees */}
      {!leg && (
      <details className={dense ? "bd-sec bd-details" : "pt-5 border-t border-slate-200"} data-tour="fees">
        <summary className={dense ? "bd-sum" : "t-label text-slate-700 cursor-pointer"}>
          {domestic ? "거래 비용" : "거래 비용과 펀딩비"}
          {dense && <small className="num">수수료 {form.commission_pct}% · 슬리피지 {form.slippage_pct}%</small>}
        </summary>
        <div className={g3m}>
          {num("commission_pct", "거래 수수료 (%)", { term: "commission", step: "0.01" })}
          {num("slippage_pct", "체결 가격 차이 (%)", { term: "slippage", step: "0.01" })}
          {!domestic && num("funding_pct", dense ? "하루 펀딩비 · 숏" : "하루 펀딩비 (숏, %)", { step: "0.01", unit: dense ? "%" : undefined })}
        </div>
        {!domestic && <div className="mt-3 flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={loadFunding}
            disabled={fundingBusy}
            className="btn btn-s btn-secondary"
          >
            {fundingBusy ? "불러오는 중…" : "실제 펀딩비 가져오기"}
          </button>
          <span className="t-caption text-slate-500">
            {fundingMsg || "선물 시장의 실제 평균 펀딩비(일)를 이 기간 기준으로 가져와 채워요."}
          </span>
        </div>}
      </details>
      )}

      {/* leverage — a macro condition (backtest/paper only; C is excluded) */}
      {!leg && !domestic && rt !== "C" && (() => {
        const lev = Math.max(1, Math.round(Number(form.leverage) || 1));
        const risk = leverageRisk(lev);
        return (
          <Group
            title="레버리지"
            term="leverage"
            anchor="leverage"
            note={<span className="ml-2 t-caption text-slate-500">격리(isolated) · 백테스트·모의만</span>}
          >
            <div data-field="leverage" className={errOf("leverage") ? "is-invalid" : undefined}>
              <div className={dense ? "bd-range" : "flex items-center gap-4"}>
                <input
                  aria-label="레버리지 배수"
                  type="range" min="1" max={MAX_LEVERAGE} step="1" value={lev}
                  onChange={set("leverage")}
                  className="flex-1 accent-red-500"
                />
                <div className="flex items-center gap-2">
                  <input
                    aria-label="레버리지 배수 직접 입력"
                    className={fieldCls("leverage", "field w-20 text-center num")}
                    type="number" min="1" max={MAX_LEVERAGE} step="1" value={form.leverage}
                    onChange={set("leverage")}
                    aria-invalid={errOf("leverage") ? true : undefined}
                  />
                  <span className="t-label text-slate-700">배</span>
                </div>
              </div>
              {errOf("leverage") && <div className={dense ? "bd-error" : "t-small font-semibold text-amber-700 mt-2"} role="alert">{errOf("leverage")}</div>}
            </div>
            {risk ? (
              <div className={"alert mt-3 t-small flex items-start gap-2 " + risk.cls}>
                <span>
                  <b>레버리지 <span className="num">{risk.n}</span>배 · {risk.level}</b> — 가격이 약{" "}
                  <b className="num">{risk.movePct.toFixed(risk.movePct < 1 ? 2 : 1)}%</b> 반대로 움직이면{" "}
                  <b>청산(전액 손실)</b>돼요.
                  {lev >= 10 && " 처음이라면 특히 위험해요."}
                  <InfoTooltip term="liquidation" />
                </span>
              </div>
            ) : (
              <div className="mt-3 t-small text-slate-500">1배 = 현물과 같아요(청산 없음). 배수를 올리면 수익도 손실도 그만큼 커지고 청산 위험이 생겨요.</div>
            )}
          </Group>
        );
      })()}
    </div>
    </DenseContext.Provider>
  );
}
