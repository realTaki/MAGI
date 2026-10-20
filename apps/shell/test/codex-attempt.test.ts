import { test } from "node:test";
import assert from "node:assert/strict";
import { CodexAttempt } from "../web/src/codex-attempt.ts";
test("delayed OAuth events and uncertain delivery cannot replace the active attempt", () => {
  const attempt = new CodexAttempt();
  assert.equal(attempt.begin("first"), true);
  // Start delivery has returned, but no server event has arrived yet.
  assert.equal(attempt.begin("double-click"), false);
  assert.equal(attempt.id, "first");
  assert.equal(attempt.observe("older", "saved"), false);
  assert.equal(attempt.observe("first", "pending"), true);
  assert.equal(attempt.begin("third"), false);
  assert.equal(attempt.observe("first", "verifying"), true);
  assert.equal(attempt.observe("first", "saved"), true);
  assert.equal(attempt.observe("first", "pending"), false, "stale poll cannot undo terminal state");
  assert.equal(attempt.observe("first", "verifying"), false);
  assert.equal(attempt.pending, false);
  assert.equal(attempt.begin("next"), true);
});
test("restored pending attempt stays correlated until explicit terminal event", () => {
  const attempt = new CodexAttempt();
  assert.equal(attempt.observe("restored", "pending"), true);
  assert.equal(attempt.begin("new"), false);
  assert.equal(attempt.observe("restored", "failed"), true);
  assert.equal(attempt.begin("new"), true);
});
