/**
 * SidebarNavItem — one row inside a SidebarShell's dark column.
 * Renders an icon (slot) + label, with the slate-700 "active"
 * treatment when the row matches the current selection. Used by
 * SidebarShell; not used directly.
 */
import type { SidebarItem } from "./SidebarShell";

export default function SidebarNavItem(props: {
  item: SidebarItem;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={props.onClick}
        className={
          "w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition " +
          (props.active
            ? "bg-slate-700 text-white"
            : "text-slate-300 hover:bg-slate-800 hover:text-white")
        }
        aria-current={props.active ? "page" : undefined}
      >
        <span className={props.active ? "text-white" : "text-slate-400"}>
          {props.item.icon}
        </span>
        <span className="font-medium">{props.item.label}</span>
      </button>
    </li>
  );
}
