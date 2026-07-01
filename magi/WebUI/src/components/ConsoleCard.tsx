/**
 * ConsoleCard — the small white card with a uppercase title used
 * across the dashboard (System, Employees, EVE nodes, Audit log,
 * Connectors, the admin/employee/connector tables, etc.).
 *
 * `title` may be empty — when it is, the card collapses to just
 * the padded content area (used by the 部门/员工 tables where the
 * title is rendered outside the card to make room for a "+ Create"
 * button on the right).
 */
export default function ConsoleCard(props: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl bg-white/80 backdrop-blur-md shadow-lg shadow-sky-900/5 border border-white/60 p-5">
      {props.title && (
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500">
          {props.title}
        </h2>
      )}
      <div className={props.title ? "mt-3" : ""}>{props.children}</div>
    </div>
  );
}
