/**
 * MagicsTab — MAGI Societies + MAGI Citizens panes.
 *
 * Adam-only — EVE doesn't see this tab.
 *
 * Two sidebar sections:
 *   - MAGI Societies (magis) — group tree.
 *   - MAGI Citizens (magic) — flat list of every MAGIC agent row.
 */

import { useState } from "react";

import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import { IconMagic, IconMagis } from "../components/icons";
import { useT } from "../i18n/index";
import { MagicsPane } from "./magics/MagicsPane";
import { MagisPane } from "./magics/MagisPane";

type MagicsSection = "magis" | "magic";

const MAGICS_SECTIONS: SidebarItem[] = [
  { id: "magis", label: "sidebar.magicMagics", icon: <IconMagic /> },
  { id: "magic", label: "sidebar.magicMagis", icon: <IconMagis /> },
];

/** Backend response shape for ``GET /api/contacts``. */
export type ContactRow = {
  id: number;
  name: string;
  display_name: string | null;
  separated_at: string | null;
  role: "assigned" | "guest";
  admin: boolean;
  telegram_id: number | null;
  notes: string;
  notes_count: number;
  source: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
};

export default function MagicsTab() {
  const t = useT();
  const [section, setSection] = useState<MagicsSection>("magis");

  return (
    <SidebarShell
      items={MAGICS_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as MagicsSection)}
      ariaLabel={t("sidebar.magicNavAria")}
    >
      {section === "magis" && <MagicsPane />}
      {section === "magic" && <MagisPane />}
    </SidebarShell>
  );
}
