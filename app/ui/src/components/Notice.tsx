/**
 * Notice — a single tone-aware banner used wherever the app
 * needs to surface a warning, error, success, or info message
 * (e.g. "verification not enabled", "saved", "search failed").
 *
 * Replaces a long tail of inline `bg-amber-50 border-amber-200
 * text-amber-900` / `bg-emerald-50 border-emerald-200` blocks
 * across `LoginPage`, `BotTokenField`, the settings cards,
 * the search pane, and the memory pane.
 *
 * Tones map to the semantic colour tokens in `styles.css`:
 *   - info    → sky-soft / sky-ink
 *   - warning → warning-soft / warning
 *   - danger  → danger-soft / danger
 *   - success → success-soft / success
 *
 * The component is a plain <div> — caller controls the parent
 * spacing (typically `mt-3` / `mt-4`).
 */
type NoticeTone = "info" | "warning" | "danger" | "success";

type NoticeProps = {
  tone: NoticeTone;
  title?: string;
  children: React.ReactNode;
};

const TONE_STYLES: Record<NoticeTone, string> = {
  info: "bg-sky-soft text-sky-ink border-sky-soft",
  warning: "bg-warning-soft text-warning border-warning-soft",
  danger: "bg-danger-soft text-danger border-danger-soft",
  success: "bg-success-soft text-success border-success-soft",
};

export default function Notice(props: NoticeProps) {
  return (
    <div
      role={props.tone === "danger" || props.tone === "warning" ? "alert" : undefined}
      className={[
        "rounded-md border px-3 py-2 text-xs leading-relaxed",
        TONE_STYLES[props.tone],
      ].join(" ")}
    >
      {props.title && <p className="mb-0.5 font-medium">{props.title}</p>}
      <p>{props.children}</p>
    </div>
  );
}