/**
 * EmptyState — a centred, low-chrome placeholder for panes that
 * have nothing to show yet.
 *
 * Used by chat-history rows, knowledge lists, action items, and
 * the placeholder tabs in `ChatTab`. The visual is deliberately
 * minimal: icon (optional) + a one-line title + an optional hint
 * paragraph + an optional CTA slot.
 *
 * Intentionally not coupled to a specific i18n key — callers pass
 * already-translated strings, just like the existing panes do.
 */
type EmptyStateProps = {
  title: string;
  hint?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
};

export default function EmptyState(props: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      {props.icon && <div className="mb-3 text-ink-3">{props.icon}</div>}
      <h3 className="text-sm font-medium text-ink">{props.title}</h3>
      {props.hint && (
        <p className="mt-1 max-w-sm text-xs text-ink-3">{props.hint}</p>
      )}
      {props.action && <div className="mt-4">{props.action}</div>}
    </div>
  );
}