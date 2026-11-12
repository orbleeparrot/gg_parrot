import assert from "node:assert/strict";
import test from "node:test";
import { reconnectTicketState, requireReconnectTicket, showRunnerReconnect } from "../src/lib/runnerReconnect.js";

const ticket = {
  launch_id: 41,
  launch_url: `ggparrot://launch/?v=2&env=production&ticket=${"a".repeat(43)}`,
  expires_at: "2026-11-12T09:02:00Z",
};

test("reconnect appears for an interrupted session and auto-ended recoverable history", () => {
  assert.equal(showRunnerReconnect({ status: "running", connected: false }), true);
  assert.equal(showRunnerReconnect({ status: "stopped", connected: false, recovery: { available: true } }), true);
  for (const session of [null, { status: "running", connected: true }, { status: "error" },
    { status: "stopped" }, { status: "running", stopping: true }]) {
    assert.equal(showRunnerReconnect(session), false);
  }
});

test("reconnect tickets contain only the approved launcher address and opaque ticket", () => {
  assert.deepEqual(requireReconnectTicket(ticket), ticket);
  for (const url of ["https://example.invalid/", "javascript:alert(1)",
    ticket.launch_url + "&macro={}", ticket.launch_url + "&ticket=duplicate",
    ticket.launch_url.replace("production", "other"), ticket.launch_url.replace("launch/", "other/"),
    ticket.launch_url + "#secret", ticket.launch_url.replace("launch/", "user@launch/")]) {
    assert.throws(() => requireReconnectTicket({ ...ticket, launch_url: url }));
  }
  assert.throws(() => requireReconnectTicket({ ...ticket, launch_id: 0 }));
  assert.throws(() => requireReconnectTicket({ ...ticket, expires_at: "invalid" }));
});

test("receiving a ticket is distinct from resuming the original execution", () => {
  assert.equal(reconnectTicketState({ status: "claimed" }), "claimed");
  assert.equal(reconnectTicketState({ status: "rejected" }), "outdated");
  assert.equal(reconnectTicketState({ status: "expired" }), "expired");
  assert.equal(reconnectTicketState({ status: "ready" }), "ready");
});
