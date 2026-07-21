// humanizeCron: turn the raw 5-field cron string the API
// surfaces into the operator-facing Chinese phrase.
//
// Scope is intentionally narrow: v0 ships four preset shapes
// (see magi.agent.proactive.cron_utils.preset_to_cron).
// Anything outside those four falls through to the raw cron
// text plus a question mark hint. We don't try to be a
// general cron parser; that's a future cron-utils story.
//
// humanizeRunAt formats the ISO 8601 timestamp the API
// returns for once-shot (frequency=once) tasks. The cell
// chooses the branch off the row's run_at vs cron shape
// rather than the cron string alone, so an older row with
// both populated still renders sensibly (run_at wins).

const WEEKDAY_LABELS = [
  "周一",
  "周二",
  "周三",
  "周四",
  "周五",
  "周六",
  "周日",
];

type CronShape = "hourly" | "daily" | "weekly" | "monthly" | "unknown";

// Classify a 5-field cron string. Returns "unknown" for
// anything that doesn't match one of the four v0 shapes.
//
// Cron's 5 fields: ``min hour dom mon dow`` (left-to-right).
// v0 only ships the four precise shapes below — anything
// else (``*/5 * * * *``, ``1,15 * * * *``, etc.) falls
// through to "unknown" so the cell shows the raw cron +
// "?" hint instead of producing junk like "每小时 NaN
// 分". Future shape support belongs in
// preset_to_cron in the Python side; the dashboard picks
// it up via this same classifier.
function classifyCron(cron: string): CronShape {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return "unknown";
  const [min, hour, dom, mon, dow] = parts;

  // daily: minute + hour set, dom + mon + dow all "*".
  if (
    /^\d+$/.test(min) &&
    /^\d+$/.test(hour) &&
    dom === "*" &&
    mon === "*" &&
    dow === "*"
  ) {
    return "daily";
  }
  // weekly: minute + hour set, dom + mon "*", DOW is one
  // digit OR a comma-separated list of digits (``1,5``).
  // The list form is something the backend's preset builder
  // doesn't ship in v0, but pre-existing rows or hand-crafted
  // cron strings may surface it; we render the weekdays in
  // the row's own order rather than re-sorting (the cron
  // operator probably picked them deliberately).
  const dowItems = dow.split(",");
  if (
    /^\d+$/.test(min) &&
    /^\d+$/.test(hour) &&
    dom === "*" &&
    mon === "*" &&
    dowItems.every((s) => /^\d+$/.test(s))
  ) {
    return "weekly";
  }
  // monthly: minute + hour + DOM set (digit), mon + dow "*".
  if (
    /^\d+$/.test(min) &&
    /^\d+$/.test(hour) &&
    /^\d+$/.test(dom) &&
    mon === "*" &&
    dow === "*"
  ) {
    return "monthly";
  }
  // hourly: minute set (digit), the other four all "*".
  if (
    /^\d+$/.test(min) &&
    hour === "*" &&
    dom === "*" &&
    mon === "*" &&
    dow === "*"
  ) {
    return "hourly";
  }
  return "unknown";
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

function hhmm(hour: number, min: number): string {
  return `${pad(hour)}:${pad(min)}`;
}

// Convert a v0-preset cron to a Chinese phrase.
// "unknown" shapes pass through the raw cron + "?" hint.
export function humanizeCron(cron: string): string {
  const shape = classifyCron(cron);
  if (shape === "unknown") {
    return `${cron} ?`;
  }

  const parts = cron.trim().split(/\s+/);
  const minute = Number(parts[0]);
  const hour = Number(parts[1]);
  // cron's 5 fields: min hour DOM mon dow. DOM is index 2.
  const dom = Number(parts[2]);
  // cron's DOW uses Sun=0..Sat=6; the v0 preset builder
  // shifts our Mon=0 into cron's Sun=0 (see
  // magi.agent.proactive.cron_utils.preset_to_cron; weekly
  // preset uses ``cron_dow = (day_of_week + 1) % 7``).
  // Convert back so the rendered weekday matches the v0
  // preset family that the dashboard asks for.
  const cronDowItems = parts[4].split(",").map((s) => Number(s));
  // Map each cron-DOW digit to a 0=Mon..6=Sun index.
  const ourDows = cronDowItems.map((d) => (d + 6) % 7);
  // Render the labels in the row's natural cron order
  // (e.g. "周一、周五" rather than re-sorted "五一").
  const weekdayLabels = ourDows.map((i) => WEEKDAY_LABELS[i]);

  switch (shape) {
    case "hourly":
      return `每小时 ${pad(minute)} 分`;
    case "daily":
      return `每天 ${hhmm(hour, minute)}`;
    case "weekly":
      return `每${weekdayLabels.join("、")} ${hhmm(hour, minute)}`;
    case "monthly":
      return `每月 ${dom} 日 ${hhmm(hour, minute)}`;
    default:
      // Unreachable: classifyCron is the only path that
      // picks the shape; the switch is exhaustive.
      return cron;
  }
}

// Format an ISO 8601 timestamp as "YYYY-MM-DD HH:MM"
// **at the timestamp's own timezone offset**, with the
// offset labelled so the operator doesn't accidentally
// read it in their local timezone.
//
// The ISO string carries the offset the row was authored
// in; that's the wall-clock instant the operator clicked
// on, so we render it directly rather than converting
// through the operator's local tz (which would hide the
// actual fire time when the row came from a different
// region).
//
// Naive timestamps (no offset) are interpreted as UTC —
// matches the v0 validator in
// magi.agent.proactive.cron_utils.validate_run_at.
//
// Examples:
//   "2026-08-01T15:30:00+08:00" -> "2026-08-01 15:30 (+08:00)"
//   "2026-08-01T07:30:00+00:00" -> "2026-08-01 07:30 (UTC)"
export function humanizeRunAt(iso: string): string {
  // Match the offset out of the ISO string directly so
  // the rendered wall-clock + offset always agree.
  // Format: ``YYYY-MM-DDTHH:MM:SS`` optionally followed
  // by ``.ffffff`` then ``±HH:MM``. We only need the
  // first two fields and the offset.
  const match = iso.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/,
  );
  if (!match) {
    // Malformed row: return the raw ISO with a hint rather
    // than render an empty string.
    return `${iso} ?`;
  }
  const [, yyyy, mm, dd, hh, min, offset] = match;
  const ymdhm = `${yyyy}-${mm}-${dd} ${hh}:${min}`;
  if (!offset) {
    // Naive: render as UTC, matching the validator's
    // naive=UTC fallback.
    return `${ymdhm} (UTC)`;
  }
  const offsetLabel = offset === "Z" ? "UTC" : offset;
  return `${ymdhm} (${offsetLabel})`;
}
