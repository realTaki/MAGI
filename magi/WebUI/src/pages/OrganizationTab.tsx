/**
 * OrganizationTab — Magis + Employees panes.
 *
 * 组织 (Organization) is Adam-only — EVE doesn't see this tab.
 *
 * Two sidebar sections:
 *   - 智能体管理 (Magis) — list of MAGI teams (councils) and
 *     the per-team agent rows. Replaces the old
 *     "部门管理 (Departments)" section in the post-refactor
 *     reframe (the dept sub-tree concept is gone; the org
 *     structure is now ``MAGIC`` → ``Magi`` → ``User``).
 *   - 员工管理 (Employees) — flat list of every employee.
 *     The ``department_id`` join was dropped from the wire
 *     shape when the dept tree was removed; the pane itself
 *     still imports the legacy ``DepartmentRow`` type because
 *     the Employees pane hasn't been reshaped yet (pending
 *     follow-up).
 *
 * The two panes live under :mod:`pages/organization/`; this
 * file is the dispatch shell that picks which pane to render
 * based on the sidebar selection. Each pane owns its own
 * helper types because nothing outside the pane consumes
 * them — promoting them to a shared module would force a
 * second import line in a downstream file for no gain.
 *
 * SidebarItem.label convention in this file: raw Chinese
 * strings ("智能体管理" / "员工管理"). The shell passes the label
 * through verbatim.
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
import { IconEmployees, IconMagis } from "../components/icons";
import { useT } from "../i18n/index";
import { EmployeesPane } from "./organization/EmployeesPane";
import { MagisPane } from "./organization/MagisPane";

type OrgSection = "magis" | "employees";

const ORG_SECTIONS: SidebarItem[] = [
  { id: "magis", label: "智能体管理", icon: <IconMagis /> },
  { id: "employees", label: "员工管理", icon: <IconEmployees /> },
];

/** Backend response shape for ``GET /api/magics``. Kept here
 *  so other panes (and tests) can import it via ``import type``. */
export type DepartmentRow = {
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
  // Soft-delete flag — ISO timestamp string, ``null`` means
  // the employee is active. Surfaced as a "已离职" badge in
  // the table; flip via the detail panel.
  separated_at: string | null;
  // Per-MAGI-perspective role: ``admin`` signs in to
  // Adam's WebUI; ``assigned`` is the employee this MAGI
  // serves; ``employee`` / ``guest`` are reserved for the
  // cross-MAGI future (C6+).
  role: "admin" | "assigned" | "employee" | "guest";
  // Bound TG chat id, when known. ``null`` until the
  // binding flow runs (C2 self-serve, or the admin endpoint
  // for v0). Unique across the company.
  telegram_id: number | null;
};

export default function OrganizationTab() {
  const t = useT();
  const [section, setSection] = useState<OrgSection>("magis");

  return (
    <SidebarShell
      items={ORG_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as OrgSection)}
      ariaLabel={t("sidebar.orgNavAria")}
    >
      {section === "magis" && <MagisPane />}
      {section === "employees" && <EmployeesPane />}
    </SidebarShell>
  );
}