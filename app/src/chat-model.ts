export type ChatMessage =
  | { type: "time"; text: string }
  | { type: "meta"; text: string }
  | { type: "user"; text: string }
  | { type: "pending"; text: string }
  | { type: "bot"; text: string }
  | { type: "typing" }
  | { type: "card"; lines: { k: string; v: string }[] };

export type RoutineRun = {
  mark: string;
  color: string;
  text: string;
  time: string;
};

export type Routine = {
  name: string;
  when: string;
  instruction?: string;
  active?: boolean;
  runs?: RoutineRun[];
};

export type ChatSummary = {
  id: string;
  name: string;
  color: string;
  time: string;
  preview: string;
  routines: Routine[];
  thread: ChatMessage[];
};

export const OPERATOR = { name: "Operator", initials: "OP", handle: "@user" };
