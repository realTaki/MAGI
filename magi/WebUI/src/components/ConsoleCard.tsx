/**
 * ConsoleCard — the small white card with a uppercase title used
 * across the dashboard (System, Employees, EVE nodes, Audit log,
 * Connectors, the admin/employee/connector tables, etc.).
 *
 * `title` may be empty — when it is, the card collapses to just
 * the padded content area (used by the 部门/员工 tables where the
 * title is rendered outside the card to make room for a "+ Create"
 * button on the right).
 *
 * `headerRight` renders next to the title (a flex row
 * with `justify-between`) so a card can mount a small
 * adornment — typically an ``<InfoTip />`` that surfaces
 * a longer explanation behind a ``?`` icon. Optional;
 * default keeps the title flush left as before.
 */
export default function ConsoleCard(props: {
  title: string;
  children: React.ReactNode;
  headerRight?: React.ReactNode;
}) {
  return (
    <div className="glass-card p-5">
      {props.title && (
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-ocean">
            {props.title}
          </h2>
          {props.headerRight}
        </div>
      )}
      <div className={props.title ? "mt-3" : ""}>{props.children}</div>
    </div>
  );
}
