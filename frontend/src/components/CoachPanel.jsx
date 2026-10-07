// 프로 빌더 코치 패널 — 옆에서 질문 하나씩, 선택지 3~5개로 좁혀 가며 조건 판을 채운다.
// 질문 · 선택지 라벨 · 근거(why) · 폼에 더할 값(form_patch)은 전부 서버(/api/coach/*)가 보낸 것만 쓴다.
// 이 파일에는 라벨 사전이 없다 — 같은 문구를 두 벌 두면 한쪽이 낡는다.
// 입력형 질문은 둘뿐이다: number(시작 자금) · symbols(종목). 종목은 평문 + 쉼표로 받고, 검증은 서버와 폼 검증이 한다.
// (Builder 의 SymbolPicker 는 Builder 의 거래소 · 비중 · 레그 규칙 상태에 묶여 있어 끼우지 않는다.)
import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { isDomestic, normalizeExchange } from "../lib/exchanges.js";
import "./CoachPanel.css";

const TITLE = "껄무새 코치";
const IDLE_TEXT = "무엇을 만들까요? 몇 가지만 물어보고 판을 채워 드려요.";
const START_LABEL = "시작하기";
const MORE_LABEL = "다른 선택지 보기";
const BACK_LABEL = "한 단계 되돌리기";
const SUBMIT_LABEL = "정했어요";
const WRAPPED_TEXT = "여기까지 정했어요 · 나머지는 기본값으로 뒀으니 판에서 고쳐요";
const DONE_TEXT = "다 정했어요 · 판에서 이어서 고쳐 보세요";
const NO_QUOTA_TEXT = "오늘은 다 썼어요 · 내일 다시 와 주세요";
const FAIL_TEXT = "코치와 이야기하지 못했어요. 잠시 뒤에 다시 해 주세요.";

// 대화가 사라진 응답(없음 · 만료) — 시작하기부터 다시 해야 한다.
const SESSION_GONE = new Set([404, 410]);
const TYPED_KINDS = new Set(["number", "symbols"]);

function symbolPlaceholder(exchange) {
  try {
    return isDomestic(normalizeExchange(exchange)) ? "KRW-BTC, KRW-ETH" : "BTCUSDT, ETHUSDT";
  } catch {
    return "BTCUSDT, ETHUSDT";
  }
}

// 서버 응답 → 패널 상태. turns(기록)는 호출한 쪽이 넘긴다 — 서버는 라벨을 기억하지 않는다.
function fromReply(reply, turns) {
  return {
    sessionId: reply.session_id,
    question: reply.question || null,
    turn: reply.turn ?? 0,
    max_turns: reply.max_turns ?? 14,
    remaining_today: reply.remaining_today ?? 0,
    done: Boolean(reply.done),
    wrapped_up: Boolean(reply.wrapped_up),
    turns,
  };
}

// number · symbols 질문의 입력칸. 질문마다 새로 만들어(key) 이전 질문의 글자가 남지 않게 한다.
function TypedAnswer({ question, exchange, busy, onSubmit }) {
  const [text, setText] = useState("");
  const isSymbols = question.kind === "symbols";
  const submit = (event) => {
    event.preventDefault();
    const value = text.trim();
    if (value && !busy) onSubmit(value, value);
  };
  return (
    <form className="coach-typed" onSubmit={submit}>
      <input
        className="coach-input" type="text" value={text} onChange={(e) => setText(e.target.value)}
        inputMode={isSymbols ? "text" : "decimal"} autoComplete="off" spellCheck={false}
        placeholder={isSymbols ? symbolPlaceholder(exchange) : ""} aria-label={question.ask} disabled={busy}
      />
      <button type="submit" className="coach-submit" disabled={busy || !text.trim()}>{SUBMIT_LABEL}</button>
    </form>
  );
}

export default function CoachPanel({ onPatch, onDone, exchange = "binance", initialState = null }) {
  // null = 아직 시작 전. 시험은 initialState 로 상태를 넣어 그려지는 것을 본다.
  const [state, setState] = useState(initialState);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialState?.error || "");
  const doneSent = useRef(false);

  // 마무리되면 onDone 을 한 번만 부른다. 되돌아가서 다시 진행 중이 되면 다음 마무리에 또 부를 수 있다.
  useEffect(() => {
    if (state?.done && !doneSent.current) {
      doneSent.current = true;
      onDone?.();
    }
    if (!state?.done) doneSent.current = false;
  }, [state?.done]); // eslint-disable-line react-hooks/exhaustive-deps

  const fail = (err) => {
    if (SESSION_GONE.has(err?.status)) setState(null);
    if (err?.status === 429) {
      setState({ sessionId: null, question: null, turn: 0, max_turns: 14, remaining_today: 0, done: false, wrapped_up: false, turns: [] });
    }
    setError(err?.message || FAIL_TEXT);
  };

  const run = async (call, after) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      after(await call());
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const start = () => run(() => api.coachStart({ exchange }), (reply) => setState(fromReply(reply, [])));

  // 답하면 폼에 더할 값을 올리고 다음 질문으로. label 은 기록에 남길 글(서버가 준 선택지 라벨, 또는 입력한 글).
  const answer = (question, value, label) => run(
    () => api.coachAnswer({ session_id: state.sessionId, key: question.key, value }),
    (reply) => {
      if (reply.form_patch && Object.keys(reply.form_patch).length) onPatch?.(reply.form_patch);
      setState(fromReply(reply, [...(state.turns || []), { key: question.key, label }]));
    },
  );

  const more = (question) => run(
    () => api.coachMore({ session_id: state.sessionId, key: question.key }),
    (reply) => setState(fromReply(reply, state.turns || [])),
  );

  // 되돌아가면 서버가 마지막 턴을 버린다. 폼은 서버가 준 form(쌓인 전부)으로 다시 맞춘다.
  const back = () => run(
    () => api.coachBack({ session_id: state.sessionId }),
    (reply) => {
      if (reply.form && Object.keys(reply.form).length) onPatch?.(reply.form);
      setState(fromReply(reply, (state.turns || []).slice(0, reply.turn ?? 0)));
    },
  );

  const question = state?.question || null;
  const typed = Boolean(question) && TYPED_KINDS.has(question.kind);
  const turns = state?.turns || [];
  const noQuota = Boolean(state) && !question && !state.done && (state.remaining_today ?? 0) <= 0;

  return (
    <section className="coach" aria-label={TITLE}>
      <header className="coach-head">
        <b className="coach-title">{TITLE}</b>
        {state && (
          <span className="coach-meta">
            <span>남은 횟수 {state.remaining_today ?? 0}회</span>
            <span className="coach-turn">{state.turn ?? 0} / {state.max_turns ?? 14}</span>
          </span>
        )}
      </header>

      {!state && (
        <div className="coach-idle">
          <p className="coach-ask">{IDLE_TEXT}</p>
          <button type="button" className="coach-start" onClick={start} disabled={busy}>{START_LABEL}</button>
        </div>
      )}

      {noQuota && <p className="coach-note">{NO_QUOTA_TEXT}</p>}

      {state?.done && <p className="coach-note">{state.wrapped_up ? WRAPPED_TEXT : DONE_TEXT}</p>}

      {question && (
        <div className="coach-question">
          <p className="coach-ask">{question.ask}</p>
          {typed ? (
            <TypedAnswer
              key={question.key} question={question} exchange={exchange} busy={busy}
              onSubmit={(value, label) => answer(question, value, label)}
            />
          ) : (
            <div className="coach-choices">
              {(question.choices || []).map((choice) => (
                <button
                  key={choice.value} type="button" className="coach-choice" disabled={busy}
                  onClick={() => answer(question, choice.value, choice.label)}
                >
                  <b>{choice.label}</b>
                  {choice.why ? <small>{choice.why}</small> : null}
                </button>
              ))}
            </div>
          )}
          {!typed && question.has_more && (
            <button type="button" className="coach-more" disabled={busy} onClick={() => more(question)}>{MORE_LABEL}</button>
          )}
        </div>
      )}

      {error && <p className="coach-error" role="alert">{error}</p>}

      {turns.length > 0 && (
        <ul className="coach-log" aria-label="지금까지 정한 것">
          {turns.map((turn, index) => <li key={`${turn.key}-${index}`}><span>{turn.label}</span></li>)}
        </ul>
      )}

      {state && turns.length > 0 && (
        <div className="coach-foot">
          <button type="button" className="coach-back" disabled={busy} onClick={back}>{BACK_LABEL}</button>
        </div>
      )}
    </section>
  );
}
