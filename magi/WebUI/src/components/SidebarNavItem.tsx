/**
 * SidebarNavItem — one row inside a SidebarShell's light-sky
 * sidebar. Renders an icon (slot) + label, with a sky-deep
 * "active" treatment when the row matches the current selection.
 * Used by SidebarShell; not used directly.
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
            ? "bg-sky-deep text-white shadow-sm"
            : "text-ocean hover:bg-sky-light/60 hover:text-sky-deep")
        }
        aria-current={props.active ? "page" : undefined}
      >
        <span
          className={
            props.active ? "text-white" : "text-sky-deep/80"
          }
        >
          {props.item.icon}
        </span>
        <span className="font-medium">{props.item.label}</span>
      </button>
    </li>
  );
}
