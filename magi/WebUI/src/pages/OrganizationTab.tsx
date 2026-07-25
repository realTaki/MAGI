/**
 * OrganizationTab — Magics + Magis + Employees panes.
 *
 * 智群 (Swarm) is Adam-only — EVE doesn't see this tab.
 *
 * Three sidebar sections:
 *   - MAGI 团队 (Magics) — MAGIC tree of teams (councils).
 *     Each team has one ADAM Magi; the tree structure lets
 *     the operator nest sub-teams. Replaces the old "部门管理
 *     (Departments)" section in the post-refactor reframe.
 *   - 智能体管理 (Magis) — flat list of every Magi agent row.
 *   - 员工管理 (Employees) — flat list of every employee.
 *
 * Cross-tab type exports
 * -----------------------
 * ``EmployeeRow`` is the only shape SettingsTab needs from
 * here — it's the shape of the JSON returned by
 * ``GET /api/employees?...``, which both ``EmployeesPane``
 * and ``SettingsWebuiAccessCard`` parse. ``import type``
 * keeps it compile-time only, no runtime cycle.
 */

import { useState } from "react";

import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import { IconEmployees, IconMagic, IconMagis } from "../components/icons";
import { useT } from "../i18n/index";
import { EmployeesPane } from "./organization/EmployeesPane";
import { MagicsPane } from "./organization/MagicsPane";
import { MagisPane } from "./organization/MagisPane";

type OrgSection = "magics" | "magis" | "employees";

const ORG_SECTIONS: SidebarItem[] = [
  { id: "magics", label: "sidebar.orgMagics", icon: <IconMagic /> },
  { id: "magis", label: "sidebar.orgMagis", icon: <IconMagis /> },
  { id: "employees", label: "sidebar.orgEmployees", icon: <IconEmployees /> },
];

/** Backend response shape for ``GET /api/magics``. Kept here
 *  so other panes (and tests) can import it via ``import type``. */
export type MAGICRow = {
  id: number;
  name: string;
  parent_id: number | null;
  adam_id: number | null;
  child_count: number;
  created_at: string;
  updated_at: string;
};

/** Backend response shape shared by EmployeesPane and
 *  ``SettingsWebuiAccessCard`` (admin table). */
export type EmployeeRow = {
  id: number;
  name: string;
  display_name: string | null;
  provider: string | null;
  api_key_set: boolean;
  api_key_last4: string | null;
  separated_at: string | null;
  role: "admin" | "assigned" | "employee" | "guest";
  telegram_id: number | null;
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
      {section === "employees" && <EmployeesPane />}
    </SidebarShell>
  );
}
