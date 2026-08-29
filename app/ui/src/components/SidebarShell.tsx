/**
 * SidebarShell — the two-column "soft inset sidebar + light
 * content pane" layout used by every tab that needs a
 * second-level navigation: Chat (6 EVA-output categories +
 * 新对话/搜索对话 + history list), Knowledge (Skills /
 * Connectors / Contacts), MAGI Council (智群管理 / 智能体管理).
 *
 * The shell owns the outer container, the inset sidebar
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
    // Edge-to-edge: h-full + min-h-0 lets the inner flex
    // column fill whatever vertical space the parent
    // ``.flex-1`` row above the header gives it (the parent
    // is already ``h-screen flex flex-col`` with the header
    // pinned at h-12). No more ``calc(100vh - Nrem)`` —
    // that math was tied to the old centred ``max-w-6xl``
    // padding, which is gone now.
    <div className="surface h-full overflow-hidden">
      <div className="flex h-full min-h-0">
        {/* Soft inset sidebar column. Reads as "navigation
            panel" without the translucent glass strip of
            the previous design. Active = accent pill (see
            SidebarNavItem). ``min-h-0`` lets the inner
            scrollable history list (rendered via
            ``belowItems``) own its own overflow instead of
            pushing the whole card taller. */}
        <nav
          className="w-56 shrink-0 bg-surface-2 border-r border-border p-3 flex flex-col min-h-0"
          aria-label={props.ariaLabel}
        >
          <ul className="space-y-1 shrink-0">
            {props.items.map((it) => (
              <SidebarNavItem
                key={it.id}
                item={it}
                active={it.id === props.selectedId}
                onClick={() => props.onSelect(it.id)}
              />
            ))}
          </ul>
          {/*
            ``flex-1 + min-h-0 + overflow-y-auto`` turns the
            belowItems region (Chat tab uses it for the
            history list) into its own scroll column. The
            scroll stays inside the sidebar; the page
            itself never grows past viewport.
          */}
          <div className="flex-1 min-h-0 overflow-y-auto">{props.belowItems}</div>
        </nav>
        <div className="flex-1 min-h-0 p-6 overflow-y-auto">{props.children}</div>
      </div>
    </div>
  );
}
