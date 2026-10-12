import assert from "node:assert/strict";
import test from "node:test";
import { createNewsReceiptSender } from "../src/features/agents/newsReceipts.js";

const receipt = { sessionId: 41, key: "a".repeat(64), token: "signed-observation" };

test("an unchanged observation and concurrent consumers send one receipt", async () => {
  let count = 0;
  let complete;
  const pending = new Promise((resolve) => { complete = resolve; });
  const sender = createNewsReceiptSender({ send: async () => { count += 1; await pending; return { ok: true }; } });
  const first = sender.submit(receipt);
  const second = sender.submit({ ...receipt, token: "refreshed-token" });
  assert.equal(count, 1);
  complete();
  await Promise.all([first, second]);
  await sender.submit(receipt);
  assert.equal(count, 1);
  sender.stop();
});

test("receipt identity includes the selected macro session", async () => {
  const ids = [];
  const sender = createNewsReceiptSender({ send: async (id) => { ids.push(id); return { ok: true }; } });
  await sender.submit(receipt);
  await sender.submit({ ...receipt, sessionId: 42 });
  assert.deepEqual(ids, [41, 42]);
  sender.stop();
});

test("network failure stops after two attempts and ordinary polls do not retry forever", async () => {
  let count = 0;
  const notices = [];
  const sender = createNewsReceiptSender({
    send: async () => { count += 1; throw new Error("offline"); },
    wait: async () => {}, onStatus: (value) => notices.push(value),
  });
  await sender.submit(receipt);
  for (let n = 0; n < 20; n += 1) await sender.submit({ ...receipt, token: `refresh-${n}` });
  assert.equal(count, 2);
  assert.equal(notices.at(-1).status, "error");
  await sender.submit(receipt, { retry: true });
  assert.equal(count, 4);
  sender.stop();
});

test("bad or expired receipt is not automatically retried", async () => {
  let count = 0;
  const sender = createNewsReceiptSender({ send: async () => {
    count += 1;
    throw Object.assign(new Error("invalid receipt"), { status: 422 });
  }, wait: async () => {} });
  await sender.submit(receipt);
  assert.equal(count, 1);
  sender.stop();
});

test("closing the account view aborts pending requests and does not announce stale success", async () => {
  const notices = [];
  let signal;
  let complete;
  const pending = new Promise((resolve) => { complete = resolve; });
  const sender = createNewsReceiptSender({ send: async (_id, _token, options) => {
    signal = options.signal;
    await pending;
    return { ok: true };
  }, onStatus: (value) => notices.push(value) });
  const first = sender.submit(receipt);
  sender.stop();
  assert.equal(signal.aborted, true);
  complete();
  await first;
  assert.equal(notices.some((n) => n.status === "ready"), false);
});

test("missing server-signed observation never emits arbitrary log writes", async () => {
  let count = 0;
  const sender = createNewsReceiptSender({ send: async () => { count += 1; return { ok: true }; } });
  await sender.submit({ sessionId: 41, key: "", token: "" });
  await sender.submit({ ...receipt, sessionId: -1 });
  assert.equal(count, 0);
  sender.stop();
});
