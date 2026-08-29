/**
 * AgenticSocietyTab — MAGIS and MAGIC panes.
 *
 * Adam-only — EVA doesn't see this tab.
 *
 * Two sidebar sections:
 *   - MAGI Societies (magis) — group tree.
 *   - MAGI Citizens (magic) — flat list of every MAGIC agent row.
 */

import { useState } from "react";

import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import { IconMagic, IconMagis } from "../components/icons";
import { useT } from "../i18n/index";
import { MagicPane } from "./agentic-society/MagicPane";
import { MagisPane } from "./agentic-society/MagisPane";

type AgenticSocietySection = "magis" | "magic";

const AGENTIC_SOCIETY_SECTIONS: SidebarItem[] = [
  { id: "magis", label: "sidebar.magisLabel", icon: <IconMagis /> },
  { id: "magic", label: "sidebar.magicLabel", icon: <IconMagic /> },
];

/** Backend response shape for ``GET /api/contacts``. */
export type ContactRow = {
  id: number;
  name: string;
  display_name: string | null;
  role: "assigned" | "guest";
  tgid: number | null;
  notes: string;
  notes_count: number;
  source: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
};

export default function AgenticSocietyTab() {
  const t = useT();
  const [section, setSection] = useState<AgenticSocietySection>("magis");

  return (
    <SidebarShell
      items={AGENTIC_SOCIETY_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as AgenticSocietySection)}
      ariaLabel={t("sidebar.magisNavAria")}
    >
      {section === "magis" && <MagisPane />}
      {section === "magic" && <MagicPane />}
    </SidebarShell>
  );
}
