import assert from "node:assert/strict";
import test from "node:test";
import { renderComponent, textOf } from "./renderHelper.js";

const session = { session_id: 41, symbol: "LINKUSDT", mode: "live", status: "running" };
const entry = (changes = {}) => ({ id: 1, kind: "fill", category: "trade", severity: "INFO",
  source: "runner", component: "runner", event_name: "runner.order_result", message: "체결 결과 보고",
  ts: "2026-10-13T03:00:00Z", received_at: "2026-10-13T03:00:01Z", details: {}, ...changes });

async function render(item) {
  return renderComponent("src/components/AgentSessionLog.jsx", { session,
    state: { items: [item], status: "ready", hasMore: false } });
}

test("collapsed terminal row exposes recorded mock mode even if session currently says live", async () => {
  const html = await render(entry({ details: { mode: "mock", executed_qty: 2, fill_price: 100, fees_known: true,
    reconciliation_scope: "simulated_fee_model", verification: "runner_reported_not_exchange_verified" } }));
  const line = html.match(/<summary class="agent-session-log-line">([\s\S]*?)<\/summary>/)?.[1];
  assert.ok(line);
  assert.match(textOf(line), /모의 · 실제 주문 없음/);
  assert.match(textOf(line), /실행기 보고 · 거래소 독립 검증 아님/);
  assert.match(textOf(html), /fees_known=true/);
  assert.match(textOf(html), /실행기 어댑터 정합성 플래그 · 수수료 금액 확인 아님/);
  assert.doesNotMatch(html, /수수료 확인 여부/);
});

test("old events without recorded mode do not borrow current session live mode", async () => {
  const html = await render(entry());
  const line = html.match(/<summary class="agent-session-log-line">([\s\S]*?)<\/summary>/)?.[1];
  assert.doesNotMatch(textOf(line), /실전 모드|모의 · 실제 주문 없음|테스트넷/);
});

test("historical zero-fill command report keeps stored message but exposes its incomplete evidence while collapsed", async () => {
  const html = await render(entry({ event_name: "command.execution_reported", message: "체결 보고 · 0 @ 0",
    details: { executed_qty: 0, fill_price: 0 } }));
  const line = html.match(/<summary class="agent-session-log-line">([\s\S]*?)<\/summary>/)?.[1];
  assert.match(textOf(line), /체결 보고 · 0 @ 0/);
  assert.match(textOf(line), /체결 수량·가격 확인 정보 없음 · 명령 성공 보고만 기록됨/);
  assert.doesNotMatch(textOf(line), /실제 주문 없음|실전 모드/);
});

test("browser news receipt and server command signal remain distinct from collection or trade verification", async () => {
  const receipt = await render(entry({ kind: "news", category: "news", source: "news_receipt",
    component: "news_delivery", event_name: "news.response_received", details: { count: 3, collection_status: "ready" } }));
  assert.match(textOf(receipt), /브라우저 응답 수신 · 수집 완료\/사용자 열람 증명 아님/);
  assert.match(textOf(receipt), /뉴스 응답 수신 확인/);
  const signal = await render(entry({ kind: "signal", category: "execution", component: "engine",
    event_name: "command.created", details: { verification: "server_signal_not_exchange_order" } }));
  const line = signal.match(/<summary class="agent-session-log-line">([\s\S]*?)<\/summary>/)?.[1];
  assert.match(textOf(line), /서버 조건 신호 · 거래소 주문 아님/);
  const failure = await render(entry({ kind: "news_error", category: "news", source: "news_receipt",
    event_name: "news.request_failed", source_label: "뉴스 요청 실패", details: { http_status: 429 } }));
  assert.match(textOf(failure), /뉴스 요청 실패/);
  assert.match(textOf(failure), /서버 요청 실패 기록 · 브라우저 수신 확인 아님/);
  assert.doesNotMatch(textOf(failure), /브라우저 응답 수신 ·/);
});

test("article examples are visibly bounded and do not claim full crawler coverage or raw stdout streaming", async () => {
  const examples = Array.from({ length: 4 }, (_, index) => ({ title: `저장 예시 기사 ${index + 1}`,
    source: "테스트 출처", url: `https://fixture.example/article-${index + 1}` }));
  const html = await render(entry({ kind: "news", category: "news", source: "news_receipt",
    event_name: "news.response_received", details: { count: 10, examples } }));
  const visibleExamples = html.match(/<details class="agent-session-log-news-details">([\s\S]*?)<\/details>/)?.[1];
  assert.match(textOf(visibleExamples), /응답 기사 예시 3 건 \/ 응답 기사 10 건/);
  assert.doesNotMatch(textOf(visibleExamples), /저장 예시 기사 4/);
  assert.match(textOf(html), /stdout·Prefect 전체 로그 스트림은 아니에요/);
  assert.match(textOf(html), /수집 회차 ID가 모든 기사의 생성 회차를 증명하지는 않아요/);
});
