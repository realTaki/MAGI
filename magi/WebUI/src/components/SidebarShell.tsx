/**
 * SidebarShell — the two-column "light sky sidebar + light
 * content pane" layout used by every tab that needs a
 * second-level navigation: Chat (6 EVE-output categories +
 * 新对话/搜索对话 + history list), Knowledge (Skills /
 * Connectors / Contacts), Organization (部门管理 / 员工管理).
 *
 * The shell owns the outer container, the sky-tinted sidebar
 * column, and the light content pane. The caller owns:
 *   - which items appear in the sidebar (`items` prop)
 *   - which one is selected (`selectedId` + `onSelect`)
 *   - what's rendered to the right of it (`children`)
 *   - optional content rendered below the nav items, inside the
 *     same sidebar — used by the Chat tab to stack a
 *     separator, the action buttons, the history list, and the
 *     "查看全部" link (see ChatTab for the only consumer)
 *
 * `items[].id` is intentionally `string` so the consumer can use
 * whatever union fits (`"action-items"`, `"skills"`, etc.). The
 * shell itself doesn't care about the value — it just passes
 * `id` back via `onSelect`.
 */
import SidebarNavItem from "./SidebarNavItem";

export type SidebarItem = {
  id: string;
  label: string;
  icon: React.ReactNode;
};

export default function SidebarShell(props: {
  items: SidebarItem[];
  selectedId: string;
  onSelect: (id: string) => void;
  /** Used as `aria-label` on the inner <nav> for screen readers. */
  ariaLabel: string;
  children: React.ReactNode;
  /** Optional slot rendered below the nav items, inside the sidebar
   *  column. Use for separators + extra content (the Chat tab
   *  stacks a "新对话" row + history list here). */
  belowItems?: React.ReactNode;
}) {
  return (
    <div className="glass-card overflow-hidden">
      <div className="flex min-h-[420px]">
        {/* Light sky-tinted sidebar. Subtle but distinct from
            the surrounding sky gradient body — reads as
            "navigation panel", not a heavy dark bar. Active =
            sky-deep blue pill so the user always knows where
            they are. */}
        <nav
          className="w-56 shrink-0 bg-sky-pale/70 backdrop-blur-md border-r border-sky-light/40 p-3 flex flex-col"
          aria-label={props.ariaLabel}
        >
          <ul className="space-y-1">
            {props.items.map((it) => (
              <SidebarNavItem
                key={it.id}
                item={it}
                active={it.id === props.selectedId}
                onClick={() => props.onSelect(it.id)}
              />
            ))}
          </ul>
          {props.belowItems}
        </nav>
        <div className="flex-1 p-6">{props.children}</div>
      </div>
    </div>
  );
}
