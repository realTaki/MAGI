/**
 * MagicsTab — 智群管理 + 智能体管理 panes.
 *
 * Adam-only — EVE doesn't see this tab.
 *
 * Two sidebar sections:
 *   - 智群管理 (Magics) — MAGIC tree.
 *   - 智能体管理 (Magis) — flat list of every Magi agent row.
 */

import { useState } from "react";

import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import { IconMagic, IconMagis } from "../components/icons";
import { useT } from "../i18n/index";
import { MagicsPane } from "./magics/MagicsPane";
import { MagisPane } from "./magics/MagisPane";

type MagicsSection = "magics" | "magis";

const MAGICS_SECTIONS: SidebarItem[] = [
  { id: "magics", label: "sidebar.magicMagics", icon: <IconMagic /> },
  { id: "magis", label: "sidebar.magicMagis", icon: <IconMagis /> },
];

/** Backend response shape for ``GET /api/contacts``. */
export type ContactRow = {
  id: number;
  name: string;
  display_name: string | null;
  provider: string | null;
  api_key_set: boolean;
  api_key_last4: string | null;
  separated_at: string | null;
  role: "admin" | "assigned" | "contact" | "guest";
  telegram_id: number | null;
  notes: string;
  source: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
};

export default function MagicsTab() {
  const t = useT();
  const [section, setSection] = useState<MagicsSection>("magics");

  return (
    <SidebarShell
      items={MAGICS_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as MagicsSection)}
      ariaLabel={t("sidebar.magicNavAria")}
    >
      {section === "magics" && <MagicsPane />}
      {section === "magis" && <MagisPane />}
    </SidebarShell>
  );
}
