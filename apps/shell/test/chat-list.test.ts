import assert from "node:assert/strict";
import test from "node:test";
import { groupChatRooms } from "../web/src/chat-list.ts";

const room = (id: string, handle = "@shell.magi", name = "BALTHASAR") => ({
  id, name, magiHandle: handle, kind: "dm" as "dm" | "group",
  members: [{ id: "@user.magi" }, { id: handle }],
  thread: [id], unread: id === "provider",
});

test("one sidebar identity retains each room and its own history, including unread cards", () => {
  const rooms = [room("greeting"), room("provider")];
  const before = structuredClone(rooms);
  const groups = groupChatRooms(rooms, "@user.magi");
  assert.equal(groups.length, 1);
  assert.equal(groups[0].key, "agent:@shell.magi");
  assert.deepEqual(groups[0].rooms.map((item) => item.id), ["greeting", "provider"]);
  assert.equal(groups[0].rooms[1], rooms[1]);
  assert.deepEqual(rooms, before);
  assert.equal(groupChatRooms([...rooms].reverse(), "@user.magi")[0].key, groups[0].key);
});

test("display names, former Shell handles, groups and non-owner DMs never collapse identities", () => {
  const rooms = [room("shell"), room("old-shell", "@eva-000.magi"),
    { ...room("group"), kind: "group" as const },
    { ...room("peer"), members: [{ id: "@peer.magi" }, { id: "@shell.magi" }] },
    { ...room("extra"), members: [...room("extra").members, { id: "@peer.magi" }] }];
  assert.equal(groupChatRooms(rooms, "@user.magi").length, rooms.length);
});
