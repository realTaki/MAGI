type Room = {
  id: string;
  kind: "dm" | "group";
  magiHandle?: string;
  state?: "active" | "ended";
  participantsKnown?: boolean;
  members: { id: string }[];
};

export type CurrentChats = {
  registeredHandles: ReadonlySet<string>;
  chatIds: ReadonlySet<string>;
};

/** Only current owner rooms and known handles establish a shared sidebar entry. */
export function groupChatRooms<T extends Room>(rooms: T[], ownerHandle: string, current: CurrentChats): { key: string; rooms: T[] }[] {
  const groups = new Map<string, { key: string; rooms: T[] }>();
  for (const room of rooms) {
    const members = new Set(room.members.map((member) => member.id));
    const ownerDm = room.kind === "dm" && room.magiHandle
      && room.state === "active" && room.participantsKnown
      && current.chatIds.has(room.id) && current.registeredHandles.has(room.magiHandle)
      && members.size === 2 && members.has(ownerHandle) && members.has(room.magiHandle);
    const key = ownerDm ? `agent:${room.magiHandle}` : `chat:${room.id}`;
    const group = groups.get(key);
    if (group) group.rooms.push(room);
    else groups.set(key, { key, rooms: [room] });
  }
  return [...groups.values()];
}
