import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseFeedSource, initialFeedSelection, reconcileFeedSelection } from "../web/src/feed-selection.ts";

const original = { name: "public__search", fingerprint: "source-contract-A" };
const replacement = { name: "public__search", fingerprint: "source-contract-B" };

test("Feed catalog refresh never reauthorizes a same-name replacement", () => {
  const selected = initialFeedSelection([original.name], [original]);
  assert.deepEqual(selected, { public__search: "source-contract-A" });
  const afterReload = reconcileFeedSelection(selected, [replacement]);
  assert.deepEqual(afterReload, {}, "card reload/feedback refresh must clear the changed binding");
  assert.equal(selected.public__search, "source-contract-A", "an in-flight stale save still carries A, never B");
  const explicitlySelected = chooseFeedSource(afterReload, replacement, true);
  assert.deepEqual(explicitlySelected, { public__search: "source-contract-B" });
});

test("Feed refresh preserves unchanged choices and drops disconnected sources", () => {
  const selected = chooseFeedSource({}, original, true);
  assert.deepEqual(reconcileFeedSelection(selected, [{ ...original }]), selected);
  assert.deepEqual(reconcileFeedSelection(selected, []), {});
  assert.deepEqual(chooseFeedSource(selected, original, false), {});
  assert.deepEqual(initialFeedSelection([], [replacement]), {});
});
