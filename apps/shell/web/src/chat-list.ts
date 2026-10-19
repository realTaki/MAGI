type Room = {
  id: string;
  kind: "dm" | "group";
  magiHandle?: string;
  members: { id: string }[];
};

/** Identity is the exact participant handle, never a display name or room ID. */
export function groupChatRooms<T extends Room>(rooms: T[], ownerHandle: string): { key: string; rooms: T[] }[] {
  const groups = new Map<string, { key: string; rooms: T[] }>();
  for (const room of rooms) {
    const members = new Set(room.members.map((member) => member.id));
    const ownerDm = room.kind === "dm" && room.magiHandle
      && members.size === 2 && members.has(ownerHandle) && members.has(room.magiHandle);
    const key = ownerDm ? `agent:${room.magiHandle}` : `chat:${room.id}`;
    const group = groups.get(key);
    if (group) group.rooms.push(room);
    else groups.set(key, { key, rooms: [room] });
  }
  return [...groups.values()];
}
