export type DemoMessage =
  | { type: "time"; text: string }
  | { type: "meta"; text: string }
  | { type: "user"; text: string }
  | { type: "bot"; text: string }
  | { type: "typing" }
  | { type: "card"; lines: { k: string; v: string }[] };

export type DemoRoutineRun = {
  mark: string;
  color: string;
  text: string;
  time: string;
};

export type DemoRoutine = {
  name: string;
  when: string;
  instruction?: string;
  active?: boolean;
  runs?: DemoRoutineRun[];
};

export type DemoScreen = {
  host: string;
  title: string;
  lines: string[];
};

export type DemoBot = {
  id: string;
  name: string;
  color: string;
  time: string;
  preview: string;
  routines: DemoRoutine[];
  screen: DemoScreen;
  thread: DemoMessage[];
};

export const OPERATOR = { name: "Operator", initials: "OP", handle: "user" };
