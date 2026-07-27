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
  // LLM credentials (``provider`` / ``api_key``) live on
  // the ``magis`` table, not on Contact — see ``MagiRow``
  // below and the ``/api/magis`` endpoints.
  separated_at: string | null;
  // ``role`` is the relationship to MAGI (assigned /
  // contact / guest). WebUI sign-in rights are NOT in
  // the role enum — they're a separate boolean below.
  // Pre-2024 this enum had ``"admin"``; that value moved
  // out to ``admin: boolean`` so a contact can be both
  // ``role='assigned'`` AND ``admin=True``.
  role: "assigned" | "contact" | "guest";
  // WebUI sign-in rights — independent of ``role``.
  // ``true`` means this contact can authenticate to the
  // operator console (``/api/auth/me`` accepts the
  // session cookie; tasks creator gate allows them).
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
