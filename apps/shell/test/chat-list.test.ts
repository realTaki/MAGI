import assert from "node:assert/strict";
import test from "node:test";
import { groupChatRooms } from "../web/src/chat-list.ts";

const current = { registeredHandles: new Set(["@shell.magi", "@eva-000.magi"]), chatIds: new Set(["greeting", "provider", "shell", "old-shell", "group", "peer", "extra"]) };

const room = (id: string, handle = "@shell.magi", name = "BALTHASAR") => ({
  id, name, magiHandle: handle, kind: "dm" as "dm" | "group",
  state: "active" as "active" | "ended", participantsKnown: true,
  members: [{ id: "@user.magi" }, { id: handle }],
  thread: [id], unread: id === "provider",
});

test("one sidebar identity retains each room and its own history, including unread cards", () => {
  const rooms = [room("greeting"), room("provider")];
  const before = structuredClone(rooms);
  const groups = groupChatRooms(rooms, "@user.magi", current);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].key, "agent:@shell.magi");
  assert.deepEqual(groups[0].rooms.map((item) => item.id), ["greeting", "provider"]);
  assert.equal(groups[0].rooms[1], rooms[1]);
  assert.deepEqual(rooms, before);
  assert.equal(groupChatRooms([...rooms].reverse(), "@user.magi", current)[0].key, groups[0].key);
});

test("display names, former Shell handles, groups and non-owner DMs never collapse identities", () => {
  const rooms = [room("shell"), room("old-shell", "@eva-000.magi"),
    { ...room("group"), kind: "group" as const },
    { ...room("peer"), members: [{ id: "@peer.magi" }, { id: "@shell.magi" }] },
    { ...room("extra"), members: [...room("extra").members, { id: "@peer.magi" }] }];
  assert.equal(groupChatRooms(rooms, "@user.magi", current).length, rooms.length);
});


test("unregistered, ended, cached-only and incomplete participant metadata stay independent", () => {
  for (const change of [
    { magiHandle: "@unknown.magi", members: [{ id: "@user.magi" }, { id: "@unknown.magi" }] },
    { magiHandle: undefined },
    { magiHandle: "" },
    { state: "ended" as const },
    { state: undefined },
    { participantsKnown: false },
  ]) {
    const rooms = [{ ...room("greeting"), ...change }, { ...room("provider"), ...change }];
    assert.equal(groupChatRooms(rooms, "@user.magi", current).length, 2, JSON.stringify(change));
  }
  const rooms = [room("greeting"), room("provider")];
  assert.equal(groupChatRooms(rooms, "@user.magi", { ...current, chatIds: new Set(["greeting"]) }).length, 2);
  assert.equal(groupChatRooms(rooms, "@user.magi", { ...current, registeredHandles: new Set() }).length, 2);
});
