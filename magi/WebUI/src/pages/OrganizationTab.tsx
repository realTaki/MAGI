/**
 * OrganizationTab — Magics + Magis panes.
 *
 * 智群 (Swarm) is Adam-only — EVE doesn't see this tab.
 *
 * Two sidebar sections:
 *   - MAGI 团队 (Magics) — MAGIC tree of teams (councils).
 *   - 智能体管理 (Magis) — flat list of every Magi agent row.
 *
 * Contacts live in the Knowledge → Contacts pane; the unified
 * ``contacts`` table serves both the admin directory and
 * LLM-managed contact notes.
 */

import { useState } from "react";

import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import { IconMagic, IconMagis } from "../components/icons";
import { useT } from "../i18n/index";
import { MagicsPane } from "./organization/MagicsPane";
import { MagisPane } from "./organization/MagisPane";

type OrgSection = "magics" | "magis";

const ORG_SECTIONS: SidebarItem[] = [
  { id: "magics", label: "sidebar.orgMagics", icon: <IconMagic /> },
  { id: "magis", label: "sidebar.orgMagis", icon: <IconMagis /> },
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

export default function OrganizationTab() {
  const t = useT();
  const [section, setSection] = useState<OrgSection>("magics");

  return (
    <SidebarShell
      items={ORG_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as OrgSection)}
      ariaLabel={t("sidebar.orgNavAria")}
    >
      {section === "magics" && <MagicsPane />}
      {section === "magis" && <MagisPane />}
    </SidebarShell>
  );
}
