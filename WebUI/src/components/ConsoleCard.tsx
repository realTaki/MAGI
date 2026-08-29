/**
 * ConsoleCard — the white surface card with a sentence-case title
 * used across the dashboard (System, Contacts, EVA nodes, Audit log,
 * Connectors, the admin/contact/connector tables, etc.).
 *
 * `title` may be empty — when it is, the card collapses to just
 * the padded content area (used by the MAGI/Magis tables where the
 * title is rendered outside the card to make room for a "+ Create"
 * button on the right).
 *
 * `headerRight` renders immediately to the right of the
 * title so a card can mount a small adornment — typically
 * an ``<InfoTip />`` that surfaces a longer explanation
 * behind a ``?`` icon. Optional; default keeps the title
 * flush left as before.
 *
 * `headerAction` renders at the far right of the header
 * row — typically a "Create" / "Add" / toggle button.
 * Pushed to the right edge via a flex-1 spacer so it
 * never bumps into ``headerRight``. Optional.
 */
export default function ConsoleCard(props: {
  title: string;
  children: React.ReactNode;
  headerRight?: React.ReactNode;
  headerAction?: React.ReactNode;
}) {
  return (
    <div className="surface p-5">
      {props.title && (
        <div className="flex items-start gap-3">
          <h2 className="text-sm font-medium text-ink shrink-0">
            {props.title}
          </h2>
          {props.headerRight && (
            <div className="flex items-center gap-2 shrink-0">{props.headerRight}</div>
          )}
          {props.headerAction && <div className="flex-1" />}
          {props.headerAction && (
            <div className="shrink-0">{props.headerAction}</div>
          )}
        </div>
      )}
      <div className={props.title ? "mt-3" : ""}>{props.children}</div>
    </div>
  );
}
