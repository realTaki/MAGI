import assert from "node:assert/strict";
import test from "node:test";

import { parseRebuildLink } from "../main/deeplink.ts";

const self = "magi://runtime/rebuild?target=self&handle=%40eva-000.magi";

test("a rebuild link names one target and the MAGI that asked", () => {
  assert.deepEqual(parseRebuildLink(self), { target: "self", handle: "@eva-000.magi" });
  assert.deepEqual(parseRebuildLink("magi://runtime/rebuild?target=app&handle=@eva-000.magi"), {
    target: "app",
    handle: "@eva-000.magi",
  });
  assert.deepEqual(parseRebuildLink("magi://runtime/rebuild/?target=asp&handle=@balthasar.magi"), {
    target: "asp",
    handle: "@balthasar.magi",
  });
});

test("the link EVA opens is the link the desktop accepts", () => {
  const url = new URL("magi://runtime/rebuild");
  url.searchParams.set("target", "self");
  url.searchParams.set("handle", "@eva-000.magi");
  assert.equal(url.href, self);
  assert.deepEqual(parseRebuildLink(url.href), { target: "self", handle: "@eva-000.magi" });
});

test("anything that is not a rebuild link is refused", () => {
  for (const raw of [
    "",
    "https://example.com/rebuild",
    "magi://runtime/rebuild?target=self",
    "magi://runtime/rebuild?target=shell&handle=@eva-000.magi",
    "magi://runtime/rebuild?target=self&handle=eva-000",
    "magi://runtime/rebuild?target=self&handle=@eva-000.magi&exec=rm",
    "magi://user:secret@runtime/rebuild?target=self&handle=@eva-000.magi",
    "magi://runtime/other?target=self&handle=@eva-000.magi",
    `${self}#frag`,
  ]) {
    assert.equal(parseRebuildLink(raw), null, raw);
  }
});

