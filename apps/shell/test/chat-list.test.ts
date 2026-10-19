import assert from "node:assert/strict";
import test from "node:test";
import { EVA_MAGI_HANDLE, SHELL_MAGI_HANDLE, USER_HANDLE } from "@magi/bus";
import { groupChatRooms } from "../web/src/chat-list.ts";

const current = { registeredHandles: new Set([SHELL_MAGI_HANDLE, EVA_MAGI_HANDLE]), chatIds: new Set(["greeting", "provider", "shell", "melchior", "group", "peer", "extra"]) };

const room = (id: string, handle = SHELL_MAGI_HANDLE, name = "BALTHASAR") => ({
  id, name, magiHandle: handle, kind: "dm" as "dm" | "group",
  state: "active" as "active" | "ended", participantsKnown: true,
  members: [{ id: USER_HANDLE }, { id: handle }],
  thread: [id], unread: id === "provider",
});

test("one sidebar identity retains each room and its own history, including unread cards", () => {
  const rooms = [room("greeting"), room("provider")];
  const before = structuredClone(rooms);
  const groups = groupChatRooms(rooms, USER_HANDLE, current);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].key, `agent:${SHELL_MAGI_HANDLE}`);
  assert.deepEqual(groups[0].rooms.map((item) => item.id), ["greeting", "provider"]);
  assert.equal(groups[0].rooms[1], rooms[1]);
  assert.deepEqual(rooms, before);
  assert.equal(groupChatRooms([...rooms].reverse(), USER_HANDLE, current)[0].key, groups[0].key);
});

test("display names, other MAGI handles, groups and non-owner DMs never collapse identities", () => {
  const rooms = [room("shell"), room("melchior", EVA_MAGI_HANDLE),
    { ...room("group"), kind: "group" as const },
    { ...room("peer"), members: [{ id: "@peer.magi" }, { id: SHELL_MAGI_HANDLE }] },
    { ...room("extra"), members: [...room("extra").members, { id: "@peer.magi" }] }];
  assert.equal(groupChatRooms(rooms, USER_HANDLE, current).length, rooms.length);
});


test("unregistered, ended, cached-only and incomplete participant metadata stay independent", () => {
  for (const change of [
    { magiHandle: "@unknown.magi", members: [{ id: USER_HANDLE }, { id: "@unknown.magi" }] },
    { magiHandle: undefined },
    { magiHandle: "" },
    { state: "ended" as const },
    { state: undefined },
    { participantsKnown: false },
  ]) {
    const rooms = [{ ...room("greeting"), ...change }, { ...room("provider"), ...change }];
    assert.equal(groupChatRooms(rooms, USER_HANDLE, current).length, 2, JSON.stringify(change));
  }
  const rooms = [room("greeting"), room("provider")];
  assert.equal(groupChatRooms(rooms, USER_HANDLE, { ...current, chatIds: new Set(["greeting"]) }).length, 2);
  assert.equal(groupChatRooms(rooms, USER_HANDLE, { ...current, registeredHandles: new Set() }).length, 2);
});
