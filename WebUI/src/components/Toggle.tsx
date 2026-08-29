/**
 * Toggle — a single Linear-style on/off switch.
 *
 * Replaces the duplicated `peer`/`after` checkbox switches that
 * were hand-rolled inside `SettingsChannelsCard.tsx` and
 * `KnowledgeMCPPane.tsx`. Track is a 16x32 rounded rectangle
 * (h-5 w-8); thumb is a 16x16 circle that slides on check.
 *
 * Track colours:
 *   - unchecked: surface-2 with a border
 *   - checked:   accent (indigo)
 *
 * Disabled toggles show 40% opacity and refuse clicks. The
 * switch is rendered as a real <button role="switch"> so screen
 * readers announce the state correctly.
 */
type ToggleProps = {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  ariaLabel: string;
};

export default function Toggle(props: ToggleProps) {
  const disabled = props.disabled ?? false;
  return (
    <button
      role="switch"
      type="button"
      aria-checked={props.checked}
      aria-label={props.ariaLabel}
      disabled={disabled}
      onClick={() => {
        if (!disabled) props.onChange(!props.checked);
      }}
      className={[
        "relative inline-flex h-5 w-8 shrink-0 items-center rounded-full transition-colors",
        disabled ? "opacity-40" : "cursor-pointer",
        props.checked
          ? "bg-accent border border-accent"
          : "bg-surface-2 border border-border",
      ].join(" ")}
    >
      <span
        aria-hidden="true"
        className={[
          "inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform",
          props.checked ? "translate-x-[18px]" : "translate-x-[3px]",
        ].join(" ")}
      />
    </button>
  );
}