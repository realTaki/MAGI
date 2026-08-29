/**
 * SidebarNavItem — one row inside a SidebarShell's inset
 * sidebar. Renders an icon (slot) + label, with an accent
 * "active" treatment when the row matches the current
 * selection. Used by SidebarShell; not used directly.
 */
import { useT } from "../i18n/index";
import type { SidebarItem } from "./SidebarShell";

export default function SidebarNavItem(props: {
  item: SidebarItem;
  active: boolean;
  onClick: () => void;
}) {
  const t = useT();
  return (
    <li>
      <button
        type="button"
        onClick={props.onClick}
        className={
          "w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors " +
          (props.active
            ? "bg-accent text-white"
            : "text-ink-2 hover:bg-surface-2 hover:text-ink")
        }
        aria-current={props.active ? "page" : undefined}
      >
        <span
          className={
            props.active ? "text-white" : "text-ink-3"
          }
        >
          {props.item.icon}
        </span>
        {/* ``item.label`` may be either a raw string (legacy
            callers) or an i18n key like ``"sidebar.newChat"``.
            Detect keys by dotted prefix and translate; fall
            through to the raw value otherwise. */}
        <span className="font-medium">
          {props.item.label.includes(".")
            ? t(props.item.label)
            : props.item.label}
        </span>
      </button>
    </li>
  );
}
