/**
 * SettingsTab — left-nav + 8 setting cards.
 *
 * Each card is its own component (`SettingsChannelsCard`,
 * `SettingsPersonaCard`, etc.). The Shell renders
 * ``<SettingsTab data=… onBotUpdated=… onAdminsChanged=… onRestart=… />``
 * — the only tab that takes props, because some cards bubble state
 * out to App (bot username refresh, admin-list refresh).
 *
 * SidebarItem.label convention: each entry's ``label`` is an i18n
 * key (e.g. ``settings.navChannels``); the inline renderer
 * resolves it via ``t()`` so this is the one tab that doesn't
 * pass through either raw keys or raw Chinese.
 *
 * Cross-tab type imports
 * -----------------------
 * ``EmployeeRow`` is owned by :mod:`OrganizationTab`. It
 * surfaces here only because ``SettingsWebuiAccessCard`` queries
 * ``GET /api/employees?role=admin`` and shares its response
 * shape. We pull the type via ``import type`` — compile-time
 * only, no runtime cycle.
 *
 * ``AddAdminForm`` once lived in OrganizationTab's section by
 * virtue of line position; the only consumer is
 * ``SettingsWebuiAccessCard``, so it moves with Settings here.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../components/ConsoleCard";
import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import {
  IconActionItems,
  IconConnectors,
  IconEmployees,
  IconReminders,
  IconScheduledTasks,
  IconSkills,
} from "../components/icons";
import { useT } from "../i18n/index";
import type { OnboardingData } from "./onboardingTypes";
// ``EmployeeRow`` is owned by :mod:`OrganizationTab`. It
// surfaces here only because ``SettingsWebuiAccessCard``
// queries ``GET /api/employees?role=admin`` and shares its
// response shape. ``import type`` keeps this compile-time
// only — no runtime cycle.
import type { EmployeeRow } from "./OrganizationTab";

// Inline add-admin form: chat_id → Send code → 6 digits → Verify.
// Mirrors the wizard's Step 3 row but as a single self-contained
// subcomponent (no add-another-row affordance — if you want
// another, click "+ Add admin" again after this one verifies).
export function AddAdminForm(props: {
  onAdded: (chatId: string, displayName: string | null) => void;
  onCancel: () => void;
}) {
  const [chatId, setChatId] = useState("");
  const [code, setCode] = useState("");
  const [state, setState] = useState<
    "idle" | "sending" | "code-sent" | "verifying" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);

  async function sendCode() {
    const cid = chatId.trim();
    if (!/^-?\d+$/.test(cid)) {
      setState("error");
      setError("chat_id must be numeric");
      return;
    }
    setState("sending");
    setError(null);
    try {
      const r = await fetch("/api/onboarding/send-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: cid }),
        credentials: "include",
      });
      const data = (await r.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        setState("code-sent");
      } else {
        setState("error");
        setError(data.error ?? "Failed to send code");
      }
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Network error");
    }
  }

  async function verifyCode() {
    const cid = chatId.trim();
    const c = code.trim();
    if (c.length !== 6) {
      setState("error");
      setError("Code must be 6 digits");
      return;
    }
    setState("verifying");
    setError(null);
    try {
      const r = await fetch("/api/onboarding/verify-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: cid, code: c }),
        credentials: "include",
      });
      const data = (await r.json()) as {
        ok: boolean;
        display_name?: string | null;
        error?: string;
      };
      if (data.ok) {
        // The endpoint already appended the chat_id to settings; we
        // just need to tell the parent to refresh.
        props.onAdded(cid, data.display_name ?? null);
      } else {
        setState("error");
        setError(data.error ?? "Code did not match");
      }
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Network error");
    }
  }

  const codeInputVisible =
    state === "code-sent" || state === "verifying" || state === "error";

  return (
    <div className="mt-4 rounded-lg border border-sky-light/40 bg-white/60 p-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={chatId}
          onChange={(e) => {
            setChatId(e.target.value);
            if (state === "error") setState("idle");
          }}
          placeholder="TG chat ID"
          className="form-input flex-1 text-sm py-2 px-3 font-mono"
        />
        <button
          type="button"
          onClick={sendCode}
          disabled={
            state === "sending" ||
            state === "verifying" ||
            !chatId.trim()
          }
          className="btn btn-primary text-sm py-2 px-3 shrink-0"
        >
          {state === "sending"
            ? "Sending…"
            : state === "code-sent"
              ? "Resend"
              : "Send code"}
        </button>
        <button
          type="button"
          onClick={props.onCancel}
          className="btn btn-secondary text-sm py-2 px-2 shrink-0"
          title="Cancel"
        >
          ✕
        </button>
      </div>

      {codeInputVisible && (
        <div className="mt-2 flex items-center gap-2">
          <input
            type="text"
            inputMode="numeric"
            maxLength={6}
            value={code}
            onChange={(e) =>
              setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
            }
            placeholder="6-digit code from TG"
            className="form-input flex-1 text-sm py-2 px-3 font-mono tracking-widest"
            disabled={state === "verifying"}
          />
          <button
            type="button"
            onClick={verifyCode}
            disabled={state === "verifying" || code.length !== 6}
            className="btn btn-primary text-sm py-2 px-3 shrink-0"
          >
            {state === "verifying" ? "Verifying…" : "Verify"}
          </button>
        </div>
      )}

      {state === "error" && error && (
        <p className="form-error mt-2 text-xs">✗ {error}</p>
      )}
      {state === "code-sent" && (
        <p className="mt-2 text-xs text-sky-700">
          Code sent — check the Telegram chat and enter the 6 digits.
        </p>
      )}
    </div>
  );
}

// -- tab: settings ----------------------------------------------------------
//
// Three things live here:
//   1. Telegram bot token (re-set flow)
//   2. WebUI Access — the chat_ids that may sign in to Adam
//      (super admins + assigned employees; this used to be the
//      "Admin contacts" card on the old Admin tab; it's a
//      system concern, not an org concern)
//   3. Onboarding escape hatch (re-run the wizard)
//
// Per-checkpoint settings (LLM provider keys, audit retention,
// quiet hours) get added here as those checkpoints land.
//
// As the panel count crossed ~6 in D.18, a single column got
// hard to scan. The layout now mirrors <OrganizationTab> /
// <KnowledgeTab>: a left nav with one entry per panel, and
// only the selected panel rendered on the right. Each panel
// still owns its own fetch + state — switching panels swaps
// the right pane, no state leaks between them, and the
// left-nav's highlighted entry tells the operator where they
// are at a glance.
export type SettingSection =
  | "channels"
  | "persona"
  | "tg-read"
  | "tz"
  | "tool-loop"
  | "auto-compact"
  | "webui-access"
  | "onboarding";

export const SETTINGS_SECTIONS: SidebarItem[] = [
  { id: "channels", label: "settings.navChannels", icon: <IconConnectors /> },
  { id: "persona", label: "settings.navPersona", icon: <IconSkills /> },
  { id: "tg-read", label: "settings.navTgRead", icon: <IconReminders /> },
  { id: "tz", label: "settings.navTz", icon: <IconScheduledTasks /> },
  { id: "tool-loop", label: "settings.navToolLoop", icon: <IconScheduledTasks /> },
  { id: "auto-compact", label: "settings.navAutoCompact", icon: <IconScheduledTasks /> },
  { id: "webui-access", label: "settings.navWebuiAccess", icon: <IconEmployees /> },
  { id: "onboarding", label: "settings.navOnboarding", icon: <IconActionItems /> },
];

export default function SettingsTab(props: SettingsTabProps) {
  const t = useT();
  const [section, setSection] = useState<SettingSection>("channels");
  return (
    <div className="space-y-4">
      <SidebarShell
        items={SETTINGS_SECTIONS.map((it) => ({
          ...it,
          // Translate the i18n key in the consumer (sidebar
          // shell expects pre-resolved labels). ``label`` is
          // dotted — resolve via t() here so downstream
          // components don't need their own i18n hooks.
          label: it.label.includes(".")
            ? t(it.label)
            : it.label,
        }))}
        selectedId={section}
        onSelect={(id) => setSection(id as SettingSection)}
        ariaLabel={t("settings.navAria")}
      >
        {section === "channels" && (
          <SettingsChannelsCard
            data={props.data}
            onBotUpdated={props.onBotUpdated}
          />
        )}
        {section === "persona" && <SettingsPersonaCard />}
        {section === "tg-read" && <SettingsTgReadReactionCard />}
        {section === "tz" && <SettingsSystemTimezoneCard />}
        {section === "tool-loop" && <SettingsToolLoopCard />}
        {section === "auto-compact" && <SettingsCompactCard />}
        {section === "webui-access" && (
          <SettingsWebuiAccessCard
            signedInUser={props.signedInUser}
            onAdminsChanged={props.onAdminsChanged}
          />
        )}
        {section === "onboarding" && (
          <SettingsOnboardingCard onRestart={props.onRestart} />
        )}
      </SidebarShell>
    </div>
  );
}

// -- Channels card ------------------------------------------------------------
//
// One row per platform adapter the node can mount. WebUI and
// Telegram are the live ones today (WebUI is the console we're
// using right now; Telegram is the IM channel the wizard
// configured). The rest — WeChat, Lark, Teams — are listed as
// "coming soon" so the deployer can see the planned surface
// area. The Telegram row carries the "Re-set" action; the others
// are inert for C0.
//
// "Coming soon" rows are rendered with reduced opacity to
// communicate "not actionable" without taking them out of the
// list. A future Phase 2 / 3 lands Email (IMAP/SMTP), Calendar
// (Google / Microsoft) and the WeChat / Lark / Teams adapters
// — at that point each new row gets its own inline config form
// modelled on the Telegram Re-set token flow.
export function SettingsChannelsCard(props: {
  data: OnboardingData | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);

  const tgConnected = !!props.data?.bot.username;
  const tgNote = props.data
    ? `@${props.data.bot.username}` +
      (props.data.bot.token
        ? ` · ${props.data.bot.token.slice(0, 6)}…${props.data.bot.token.slice(-4)}`
        : "")
    : "(not configured)";

  return (
    <ConsoleCard title={t("settings.channels")}>
      <p className="text-sm text-ink-soft">
        Platform adapters the node can mount. WebUI is the
        console you're using; Telegram is the IM channel the
        wizard configured. The rest are planned.
      </p>

      <table className="w-full text-sm mt-4">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium w-32">Status</th>
            <th className="py-2 pr-4 font-medium">Notes</th>
            <th className="py-2 font-medium w-24 text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-sky-light/30">
            <td className="py-2 pr-4 text-ink">WebUI</td>
            <td className="py-2 pr-4">
              <ChannelStatusBadge status="connected" />
            </td>
            <td className="py-2 pr-4 text-ink-soft font-mono text-xs">
              :42069
            </td>
            <td className="py-2 text-right text-xs text-ink-soft">—</td>
          </tr>

          <tr className="border-b border-sky-light/30">
            <td className="py-2 pr-4 text-ink">Telegram</td>
            <td className="py-2 pr-4">
              <ChannelStatusBadge
                status={tgConnected ? "connected" : "disconnected"}
              />
            </td>
            <td className="py-2 pr-4 text-ink-soft font-mono text-xs">
              {tgNote}
            </td>
            <td className="py-2 text-right">
              {tgConnected && !editing && (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="text-sm text-sky-700 hover:text-sky-deep transition"
                >
                  Re-set
                </button>
              )}
            </td>
          </tr>

          <ComingChannelRow name="WeChat" />
          <ComingChannelRow name="Lark" />
          <ComingChannelRow name="Teams" />
        </tbody>
      </table>

      {editing && (
        <div className="mt-4 border-t border-sky-light/40 pt-4">
          <BotTokenField
            onSaved={(token, username) => {
              props.onBotUpdated({ token, username });
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        </div>
      )}
    </ConsoleCard>
  );
}

export function ComingChannelRow(props: { name: string }) {
  return (
    <tr className="border-b border-sky-light/30 last:border-0 opacity-50">
      <td className="py-2 pr-4 text-ink-soft">{props.name}</td>
      <td className="py-2 pr-4">
        <ChannelStatusBadge status="coming" />
      </td>
      <td className="py-2 pr-4 text-ink-soft">—</td>
      <td className="py-2 text-right text-xs text-ink-soft">—</td>
    </tr>
  );
}

export function ChannelStatusBadge(props: {
  status: "connected" | "disconnected" | "coming";
}) {
  switch (props.status) {
    case "connected":
      return (
        <span className="status-pill status-pill--connected">
          connected
        </span>
      );
    case "disconnected":
      return (
        <span className="status-pill status-pill--disconnected">
          disconnected
        </span>
      );
    case "coming":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">
          coming soon
        </span>
      );
  }
}

export function SettingsWebuiAccessCard(props: {
  signedInUser: { chat_id: string; display_name: string | null };
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
}) {
  const t = useT();
  // WebUI Access = employees WHERE role=admin. The unified
  // table means a single GET returns the list, the new
  // employees / remove flow can delete rows directly, and
  // we don't have to keep two views in sync.
  const [admins, setAdmins] = useState<EmployeeRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [addingNew, setAddingNew] = useState(false);

  async function refresh() {
    setLoadError(null);
    try {
      const r = await fetch(
        "/api/employees?role=admin&page=1&page_size=100",
        { credentials: "include" },
      );
      if (!r.ok) {
        setLoadError("Failed to load access list");
        return;
      }
      const data = (await r.json()) as {
        items: EmployeeRow[];
        total: number;
      };
      setAdmins(data.items);
      // Bubble the updated admin list up to App so the rest of
      // the dashboard (header, etc.) stays consistent.
      props.onAdminsChanged(
        data.items
          .filter((e) => e.telegram_id !== null)
          .map((e) => ({
            chatId: String(e.telegram_id),
            displayName: e.display_name ?? e.name,
          })),
      );
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleRemoveAdmin(emp: EmployeeRow) {
    if (String(emp.telegram_id ?? "") === props.signedInUser.chat_id) {
      return; // belt + suspenders
    }
    if (
      !confirm(
        `确定移除管理员「${emp.name}」？这会从 employees 表删掉这一行。`,
      )
    ) {
      return;
    }
    // Re-saving the full list (minus this one) is the
    // current API surface; it also drops the Employee row
    // because the new save-admin deletes admins not in the
    // incoming set.
    const remaining =
      (admins ?? [])
        .filter((e) => e.id !== emp.id && e.telegram_id !== null)
        .map((e) => String(e.telegram_id));
    const r = await fetch("/api/onboarding/save-admin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_ids: remaining }),
      credentials: "include",
    });
    if (r.ok) {
      await refresh();
    } else {
      setLoadError("Failed to remove admin");
    }
  }

  return (
    <ConsoleCard title={t("settings.webuiAccess")}>
      <p className="text-sm text-ink-soft">
        Sign-in list. Each row is an <code>Employee</code> with
        <span className="font-medium"> role=admin</span> and a
        bound <code>telegram_id</code>. The wizard
        (step 3) creates these from the verified chat_ids;
        the table below mirrors that state. Removing a row
        calls the same wizard endpoint with the smaller list
        — the server drops the deleted rows from the
        employees table.
      </p>

      <div className="mt-4">
        {admins === null && !loadError && (
          <p className="text-sm text-ink-soft">Loading…</p>
        )}
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {admins !== null && admins.length === 0 && (
          <p className="text-sm text-ink-soft">
            No one has access yet. Run the first-time wizard
            to add a super admin.
          </p>
        )}
        {admins !== null && admins.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium w-44">Role</th>
                <th className="py-2 pr-4 font-medium">TG chat_id</th>
                <th className="py-2 font-medium w-28 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {admins.map((emp) => {
                const isSelf =
                  String(emp.telegram_id ?? "") ===
                  props.signedInUser.chat_id;
                return (
                  <tr key={emp.id} className="">
                    <td className="py-2 pr-4 text-ink">
                      {emp.display_name ?? emp.name}
                    </td>
                    <td className="py-2 pr-4">
                      <RoleBadge role={emp.role} />
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-ink-soft">
                      {emp.telegram_id ?? (
                        <span className="text-ink-soft">—</span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {isSelf ? (
                        <span className="status-pill status-pill--connected">
                          you
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleRemoveAdmin(emp)}
                          title="Remove this super admin"
                          className="btn btn-secondary text-xs py-1 px-2"
                        >
                          ✕ Remove
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {!addingNew && (
          <button
            type="button"
            onClick={() => setAddingNew(true)}
            className="mt-3 text-sm text-sky-700 hover:text-sky-deep transition"
          >
            + Add super admin
          </button>
        )}

        {addingNew && (
          <AddAdminForm
            onAdded={() => {
              setAddingNew(false);
              void refresh();
            }}
            onCancel={() => setAddingNew(false)}
          />
        )}
      </div>
    </ConsoleCard>
  );
}

export function RoleBadge(props: { role: EmployeeRow["role"] }) {
  switch (props.role) {
    case "admin":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">
          super admin
        </span>
      );
    case "assigned":
      return (
        <span className="text-xs text-white bg-sky-deep border border-sky-deep rounded px-1.5 py-0.5">
          assigned
        </span>
      );
    case "employee":
      return (
        <span className="text-xs text-ink-soft bg-white border border-sky-light/40 rounded px-1.5 py-0.5">
          employee
        </span>
      );
    case "guest":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/60 border border-sky-light/40 rounded px-1.5 py-0.5">
          guest
        </span>
      );
  }
}

export function SettingsOnboardingCard(props: { onRestart: () => void }) {
  const t = useT();
  return (
    <ConsoleCard title={t("settings.onboarding")}>
      <p className="text-sm text-ink-soft">
        Re-run the first-time setup wizard. Saved bot and admin
        rows stay in SQLite; the wizard will resume from wherever
        it left off.
      </p>
      <button
        type="button"
        onClick={props.onRestart}
        className="mt-3 btn btn-secondary px-4 py-2"
      >
        Restart onboarding
      </button>
    </ConsoleCard>
  );
}

// -- persona card ------------------------------------------------------------
//
// Edits the workspace ``SOUL.md`` — the text the agent loop
// passes as the system prompt on every chat turn. Single
// company-wide persona for v0; per-employee personas are C4.
//
// ``GET /api/soul`` returns the current text + a flag telling
// us whether the agent is reading the bundled default (file
// missing on disk) or an already-customised workspace copy.
// The flag drives a one-line warning banner so the operator
// knows "saving here overwrites the bundled fallback" — they
// might not have realised the workspace file was missing.
//
// Save / Reset are separate actions: Save writes the textarea
// content verbatim; Reset overwrites the workspace file with
// the bundled ``prompts/soul.md`` (the immutable template).
// Both go through the same atomic-write endpoint and audit
// row; the only difference is the body and the kind string.
//
// Live char counter mirrors the backend's 8 KB cap so an
// operator pastes a long doc, sees the count climb past the
// limit, and gets a friendlier hint than the 422 they'll
// get on save.
export function SettingsPersonaCard() {
  const t = useT();
  // One textarea. The loaded value IS the editable value:
  // the operator sees the on-disk SOUL.md content right away
  // (no separate read-only block) and edits in place. Click
  // Save to commit; click Reset to restore the bundled default.
  //
  // ``savedContent`` is a *baseline* — the value the textarea
  // had immediately after the last load / save / reset. The
  // ``dirty`` flag (``draftContent !== savedContent``) tells
  // us when the operator has unsaved changes and drives the
  // Save button's disabled state + the "放弃改动" revert
  // affordance.
  //
  // Why one textarea instead of "current view + draft" —
  // the operator wants to **see what the agent is using**
  // and **edit it**. Two views force them to translate
  // between "what's in the editor" and "what the agent
  // sees"; one view + Save makes the contract explicit:
  // until you press Save, the textarea is your scratch
  // pad, not the agent's persona.
  const [draftContent, setDraftContent] = useState<string>("");
  const [savedContent, setSavedContent] = useState<string>("");
  const [modifiedAt, setModifiedAt] = useState<string | null>(null);
  const [isFallback, setIsFallback] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);

  // 8 KB cap mirrors the backend's
  // ``magi.channels.webui.api.soul._MAX_SOUL_CHARS``.
  const SOUL_MAX = 8000;
  // Warning at 80% so the operator gets a visual cue before
  // the textarea overflows the layout.
  const SOUL_WARN = SOUL_MAX * 0.8;
  const chars = draftContent.length;
  const overLimit = chars > SOUL_MAX;
  const nearLimit = chars > SOUL_WARN;
  const dirty = draftContent !== savedContent;

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch("/api/soul", { credentials: "include" });
      if (!r.ok) {
        setLoadError(`${t("persona.loadFailed")} (${r.status})`);
        return;
      }
      const data = (await r.json()) as {
        content: string;
        modified_at: string | null;
        is_bundled_fallback: boolean;
      };
      // Both slots collapse to the same value — the
      // textarea shows what's on disk, ``dirty`` is false
      // until the operator types something.
      setSavedContent(data.content);
      setDraftContent(data.content);
      setModifiedAt(data.modified_at);
      setIsFallback(data.is_bundled_fallback);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
    // ``t`` is stable across renders (the i18n context
    // returns a memoised value), so this doesn't refire on
    // locale switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    const trimmed = draftContent.trim();
    if (!trimmed) {
      setSaveError("Persona 内容不能为空（空白不算）");
      return;
    }
    setSaving(true);
    try {
      const r = await fetch("/api/soul", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: trimmed }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setSaveError(body.detail ?? `${t("persona.saveFailed")} (${r.status})`);
        return;
      }
      const data = (await r.json()) as { modified_at: string };
      // Promote the textarea value to "saved" baseline.
      // ``dirty`` flips false; the textarea stays exactly
      // where the operator left it (no need to re-mount).
      setSavedContent(trimmed);
      setDraftContent(trimmed);
      setModifiedAt(data.modified_at);
      setIsFallback(false);
      setSavedNotice(t("persona.savedNotice"));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  async function resetToDefault() {
    if (!confirm(t("persona.resetConfirm"))) {
      return;
    }
    setSaveError(null);
    setSavedNotice(null);
    setResetting(true);
    try {
      const r = await fetch("/api/soul/reset", {
        method: "POST",
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setSaveError(body.detail ?? `${t("persona.resetFailed")} (${r.status})`);
        return;
      }
      // Re-load so the textarea picks up the canonical
      // truth the backend just wrote.
      await load();
      setSavedNotice(t("persona.resetNotice"));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setResetting(false);
    }
  }

  // ``modifiedAt`` comes back as an ISO UTC string; render a
  // compact "YYYY-MM-DD HH:MM" in local time. Skipped when
  // the persona is the bundled fallback (no mtime yet).
  function formatModified(iso: string | null): string {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, "0");
      return (
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
        `${pad(d.getHours())}:${pad(d.getMinutes())}`
      );
    } catch {
      return iso;
    }
  }

  return (
    <ConsoleCard title={t("persona.title")}>
      <p className="text-sm text-ink-soft">{t("persona.description")}</p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {isFallback && !loadError && (
        <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {t("persona.fallbackBanner")}
        </div>
      )}

      {/* Single editable textarea.
          ``rows={14}`` + ``min/maxHeight`` give a comfortable
          multi-line editing surface that doesn't push the
          Save button off-screen on long personas. The
          "未保存" marker floats to the right when ``dirty``
          is true, so the operator always knows whether
          their last edit has been committed. */}
      <div className="mt-4">
        <div className="flex items-baseline justify-between">
          <h3 className="text-xs font-medium text-ink-soft uppercase tracking-wide">
            {t("persona.draftLabel")}
          </h3>
          {dirty && (
            <span className="text-[10px] text-amber-700 normal-case tracking-normal">
              · {t("persona.dirty")}
            </span>
          )}
        </div>
        <p className="mt-1 text-[11px] text-ink-soft">
          {t("persona.draftHint")}
        </p>
        <textarea
          value={draftContent}
          onChange={(e) => setDraftContent(e.target.value)}
          rows={14}
          spellCheck={false}
          className={
            "mt-2 form-input w-full text-sm font-mono leading-relaxed py-2 px-3 resize-y " +
            (overLimit ? "border-rose-400 focus:border-rose-500" : "")
          }
          style={{ minHeight: "260px", maxHeight: "520px" }}
        />
        <div className="flex items-center justify-between text-xs mt-1">
          <span
            className={
              overLimit
                ? "text-rose-600 font-medium"
                : nearLimit
                  ? "text-amber-700"
                  : "text-ink-soft"
            }
          >
            {t("persona.charsLine")
              .replace("{chars}", chars.toLocaleString())
              .replace("{max}", SOUL_MAX.toLocaleString())}
            {overLimit && t("persona.overLimitHint")}
          </span>
          {modifiedAt && (
            <span className="text-ink-soft">
              {t("persona.modifiedLabel")}：
              <span className="font-mono">{formatModified(modifiedAt)}</span>
            </span>
          )}
        </div>
      </div>

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && <p className="mt-3 text-xs text-emerald-700">✓ {savedNotice}</p>}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-sky-light/40">
        <button
          type="button"
          onClick={save}
          disabled={saving || resetting || !dirty || overLimit}
          className="btn btn-primary text-sm py-1.5 px-4"
          title={
            !dirty
              ? t("persona.dirty")
              : overLimit
                ? t("persona.overLimitHint")
                : t("persona.saveButton")
          }
        >
          {saving ? `${t("persona.saveButton")}…` : t("persona.saveButton")}
        </button>
        <button
          type="button"
          onClick={resetToDefault}
          disabled={saving || resetting}
          className="btn btn-secondary text-sm py-1.5 px-4"
        >
          {resetting ? `${t("persona.resetButton")}…` : t("persona.resetButton")}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={() => {
              // Revert the textarea to the on-disk truth.
              // ``dirty`` flips false; the saved version stays
              // the same so the next comparison is meaningful.
              setDraftContent(savedContent);
              setSaveError(null);
              setSavedNotice(null);
            }}
            disabled={saving || resetting}
            className="btn btn-ghost text-sm py-1.5 px-3"
          >
            {t("persona.discardChanges")}
          </button>
        )}
      </div>
    </ConsoleCard>
  );
}

// -- tg read-reaction card -------------------------------------------------
//
// One row per emoji choice surfaced by
// ``GET /api/tg-settings/read-reaction``. The selected
// emoji is what the EVE bot stamps on each incoming TG
// message with ``set_message_reaction`` — a "seen, working
// on it" signal that fires before the LLM call so the user
// sees it instantly even if the reply takes 30s.
//
// Save hits ``PUT /api/tg-settings/read-reaction`` and
// takes effect on the *next* inbound TG message; no
// restart, no reload.
//
// The backend allowlists 5 emoji (see
// ``magi.channels.telegram.config.REACTION_CHOICES``);
// anything the API returns is one of those, so the radio
// rows are guaranteed to round-trip.
export function SettingsTgReadReactionCard() {
  const t = useT();
  type ReactionChoice = { value: string; label: string };
  type ReactionOut = {
    current: string;
    default: string;
    choices: ReactionChoice[];
  };

  const [data, setData] = useState<ReactionOut | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Read-only mode when the bot isn't connected (no
  // outgoing reactions will fire anyway). We surface the
  // Channels card's data via the same ``props.data`` path
  // the persona card uses, but the helper just reads
  // ``bot.username`` — keep it minimal.
  async function load() {
    setLoadError(null);
    try {
      const r = await fetch("/api/tg-settings/read-reaction", {
        credentials: "include",
      });
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as ReactionOut;
      setData(body);
      setPicked(body.current);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const dirty = data !== null && picked !== data.current;

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    setSaving(true);
    try {
      const r = await fetch("/api/tg-settings/read-reaction", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ emoji: picked }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setSaveError(body.detail ?? `Save failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as ReactionOut;
      setData(body);
      setPicked(body.current);
      setSavedNotice("已保存。下一条 TG 消息就会用新的 emoji。");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={t("settings.tgReadEmoji")}>
      <p className="text-sm text-ink-soft">
        员工给 EVE bot 发消息时，bot 给那条消息加一个 emoji 作为「已收到 / 在处理」的信号。
        改完保存即生效，下一条消息就生效。
      </p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-2">
          {data.choices.map((c) => {
            const selected = picked === c.value;
            return (
              <label
                key={c.value}
                className={
                  "flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer transition " +
                  (selected
                    ? "border-sky-deep bg-sky-pale/40"
                    : "border-sky-light/40 hover:bg-sky-pale/20")
                }
              >
                <input
                  type="radio"
                  name="tg-read-reaction"
                  value={c.value}
                  checked={selected}
                  onChange={() => setPicked(c.value)}
                  className="accent-sky-deep"
                />
                <span className="text-sm text-ink">{c.label}</span>
              </label>
            );
          })}
          {data.default !== data.current && (
            <p className="text-xs text-ink-soft mt-1">
              未配置时用默认 <span className="font-mono">{data.default}</span>。
            </p>
          )}
        </div>
      )}

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && <p className="mt-3 text-xs text-emerald-700">✓ {savedNotice}</p>}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-sky-light/40">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="btn btn-primary text-sm py-1.5 px-4"
          title={!dirty ? "没有改动" : "保存"}
        >
          {saving ? "保存中…" : "保存"}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setPicked(data?.current ?? "");
              setSaveError(null);
              setSavedNotice(null);
            }}
            disabled={saving}
            className="btn btn-ghost text-sm py-1.5 px-3"
          >
            放弃改动
          </button>
        )}
      </div>
    </ConsoleCard>
  );
}

// -- system timezone card --------------------------------------------------
//
// D.15 — the timezone this MAGI node uses for "natural
// week" / "natural month" bucket boundaries. The token
// bill aggregation endpoint reads the same value on every
// call, so a Save here is immediately reflected in the
// next ``GET /api/employees/{id}/token-usage``.
//
// The dropdown lists the full IANA tz database
// (zoneinfo.available_timezones()) sorted alphabetically.
// v0 doesn't have a region-grouped preferences panel —
// the alphabetical list is uniform and works for any
// locale. The backend rejects unknown tz with 400 so a
// stale client doesn't get a silent fall-back to UTC.
export function SettingsSystemTimezoneCard() {
  const t = useT();
  type TzOut = {
    current: string;
    default: string;
    choices: string[];
  };

  const [data, setData] = useState<TzOut | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch("/api/system-settings/timezone", {
        credentials: "include",
      });
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as TzOut;
      setData(body);
      setPicked(body.current);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const dirty = data !== null && picked !== data.current;

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    setSaving(true);
    try {
      const r = await fetch("/api/system-settings/timezone", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timezone: picked }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          code?: string;
          detail?: string;
        };
        setSaveError(body.detail ?? `Save failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as TzOut;
      setData(body);
      setPicked(body.current);
      setSavedNotice("已保存。下次 token 用量查询就用新时区。");
      // (saved notice remains in zh for v0; localized copy
      // lands when we extract a setting-specific notice key.)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={t("settings.timezone")}>
      <p className="text-sm text-ink-soft">
        {t("settings.timezoneDesc")}
      </p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-2">
          <select
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
            className="form-input text-sm py-2 px-3 w-full sm:w-auto"
          >
            {data.choices.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </select>
          {data.default !== data.current && (
            <p className="text-xs text-ink-soft">
              未设置时用默认 <span className="font-mono">{data.default}</span>。
            </p>
          )}
        </div>
      )}

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && <p className="mt-3 text-xs text-emerald-700">✓ {savedNotice}</p>}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-sky-light/40">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="btn btn-primary text-sm py-1.5 px-4"
          title={!dirty ? "没有改动" : "保存"}
        >
          {saving ? "保存中…" : "保存"}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setPicked(data?.current ?? "");
              setSaveError(null);
              setSavedNotice(null);
            }}
            disabled={saving}
            className="btn btn-ghost text-sm py-1.5 px-3"
          >
            放弃改动
          </button>
        )}
      </div>
    </ConsoleCard>
  );
}

// -- tool-loop max iterations card ----------------------------------------
//
// D.16 — caps how many LLM ↔ tool cycles one chat turn
// can run. The agent loop reads this on every inbound chat
// and aborts past the limit (with a fallback reply). Each
// iteration is one round-trip + tool execution, so the cap
// also bounds the wall-clock cost of one turn.
//
// Bound is enforced server-side in
// ``magi.channels.webui.api.system_settings`` (MIN=1 MAX=50);
// the form here mirrors those bounds so the operator can't
// even type a value that the API would 422 on.
export function SettingsToolLoopCard() {
  const t = useT();
  type IterationsOut = {
    current: number;
    default: number;
    min: number;
    max: number;
  };

  const [data, setData] = useState<IterationsOut | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch(
        "/api/system-settings/tool-max-iterations",
        { credentials: "include" },
      );
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as IterationsOut;
      setData(body);
      setPicked(String(body.current));
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const dirty =
    data !== null && Number(picked) !== data.current && picked !== "";

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    const value = Number(picked);
    if (!Number.isInteger(value)) {
      setSaveError("必须是整数");
      return;
    }
    if (data !== null && (value < data.min || value > data.max)) {
      setSaveError(`必须介于 ${data.min} 和 ${data.max} 之间`);
      return;
    }
    setSaving(true);
    try {
      const r = await fetch(
        "/api/system-settings/tool-max-iterations",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
          credentials: "include",
        },
      );
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setSaveError(body.detail ?? `Save failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as IterationsOut;
      setData(body);
      setPicked(String(body.current));
      setSavedNotice(
        "已保存。下一条消息生效（正在进行的 tool loop 用旧值）。",
      );
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={t("settings.toolLoop")}>
      <p className="text-sm text-ink-soft">
        一次对话中，LLM 可以连续调用多少次 tool（read_file / write_file / list_files /
        send_message）才会停。超出后 agent 直接返回 fallback reply。
      </p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-2">
          <div className="flex items-center gap-3">
            <input
              type="number"
              min={data.min}
              max={data.max}
              step={1}
              value={picked}
              onChange={(e) => setPicked(e.target.value)}
              className="form-input text-sm font-mono py-2 px-3 w-24"
            />
            <span className="text-xs text-ink-soft">
              范围 {data.min} – {data.max} · 默认 {data.default}
            </span>
          </div>
          {data.default !== data.current && (
            <p className="text-xs text-ink-soft">
              当前生效值 {data.current}。
            </p>
          )}
        </div>
      )}

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && (
        <p className="mt-3 text-xs text-emerald-700">✓ {savedNotice}</p>
      )}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-sky-light/40">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="btn btn-primary text-sm py-1.5 px-4"
          title={!dirty ? "没有改动" : "保存"}
        >
          {saving ? "保存中…" : "保存"}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setPicked(data?.current !== undefined ? String(data.current) : "");
              setSaveError(null);
              setSavedNotice(null);
            }}
            disabled={saving}
            className="btn btn-ghost text-sm py-1.5 px-3"
          >
            放弃改动
          </button>
        )}
      </div>
    </ConsoleCard>
  );
}

// -- compact config card (D.17) --------------------------------------------
//
// Three knobs the operator dials for the auto-compaction
// behaviour:
//
// - context_window  : how many tokens the model can take
//                      (default 100000 = Minimax M2.7 spec)
// - threshold_pct   : at what %% of context_window to
//                      trigger a compaction pass
// - keep_recent     : after compaction, how many of the
//                      most-recent original messages stay
//                      in the active list (in addition to
//                      the summary at messages[0])
//
// All three are server-validated; the form mirrors the
// server-side bounds (min/max on the input element + a
// sanity check before send) so an operator typing outside
// the range gets a fast client-side error instead of a
// round-trip + 422.
export function SettingsCompactCard() {
  const t = useT();
  type CompactOut = {
    context_window: number;
    threshold_pct: number;
    keep_recent: number;
    default_context_window: number;
    default_threshold_pct: number;
    default_keep_recent: number;
  };

  const [data, setData] = useState<CompactOut | null>(null);
  const [contextWindow, setContextWindow] = useState<string>("");
  const [thresholdPct, setThresholdPct] = useState<string>("");
  const [keepRecent, setKeepRecent] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoadError(null);
    try {
      const r = await fetch("/api/system-settings/compact-config", {
        credentials: "include",
      });
      if (!r.ok) {
        setLoadError(`Failed to load (${r.status})`);
        return;
      }
      const body = (await r.json()) as CompactOut;
      setData(body);
      setContextWindow(String(body.context_window));
      setThresholdPct(String(body.threshold_pct));
      setKeepRecent(String(body.keep_recent));
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const dirty =
    data !== null &&
    (Number(contextWindow) !== data.context_window ||
      Number(thresholdPct) !== data.threshold_pct ||
      Number(keepRecent) !== data.keep_recent);

  async function save() {
    setSaveError(null);
    setSavedNotice(null);
    const cw = Number(contextWindow);
    const tp = Number(thresholdPct);
    const kr = Number(keepRecent);
    if (!Number.isInteger(cw) || !Number.isInteger(tp) || !Number.isInteger(kr)) {
      setSaveError("三个值必须是整数");
      return;
    }
    if (data !== null) {
      if (cw < 16000 || cw > 200000) {
        setSaveError("context_window 必须介于 16000 与 200000 之间");
        return;
      }
      if (tp < 50 || tp > 95) {
        setSaveError("threshold_pct 必须介于 50 与 95 之间");
        return;
      }
      if (kr < 5 || kr > 100) {
        setSaveError("keep_recent 必须介于 5 与 100 之间");
        return;
      }
    }
    setSaving(true);
    try {
      const r = await fetch("/api/system-settings/compact-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          context_window: cw,
          threshold_pct: tp,
          keep_recent: kr,
        }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        setSaveError(body.detail ?? `Save failed (${r.status})`);
        return;
      }
      const body = (await r.json()) as CompactOut;
      setData(body);
      setContextWindow(String(body.context_window));
      setThresholdPct(String(body.threshold_pct));
      setKeepRecent(String(body.keep_recent));
      setSavedNotice(
        "已保存。下一条消息生效（正在进行的 chat 用旧值）。",
      );
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ConsoleCard title={t("settings.autoCompact")}>
      <p className="text-sm text-ink-soft">
        长 session 在消息数累积到 context_window × threshold_pct% 时触发压缩:
        老的 N 条调 LLM 生成 summary (写到 messages[0])、原文进 archive、
        active 只留最近 keep_recent 条。
      </p>

      {loadError && <p className="form-error mt-3">✗ {loadError}</p>}

      {!loadError && data && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="form-label">Context window</label>
              <input
                type="number"
                min={16000}
                max={200000}
                step={1000}
                value={contextWindow}
                onChange={(e) => setContextWindow(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
              <p className="mt-1 text-xs text-ink-soft">
                默认 {data.default_context_window.toLocaleString()}
              </p>
            </div>
            <div>
              <label className="form-label">Threshold (%)</label>
              <input
                type="number"
                min={50}
                max={95}
                step={1}
                value={thresholdPct}
                onChange={(e) => setThresholdPct(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
              <p className="mt-1 text-xs text-ink-soft">
                默认 {data.default_threshold_pct}
              </p>
            </div>
            <div>
              <label className="form-label">Keep recent</label>
              <input
                type="number"
                min={5}
                max={100}
                step={1}
                value={keepRecent}
                onChange={(e) => setKeepRecent(e.target.value)}
                className="form-input text-sm font-mono py-2 px-3 w-full"
              />
              <p className="mt-1 text-xs text-ink-soft">
                默认 {data.default_keep_recent}
              </p>
            </div>
          </div>
          {(data.context_window !== data.default_context_window ||
            data.threshold_pct !== data.default_threshold_pct ||
            data.keep_recent !== data.default_keep_recent) && (
            <p className="text-xs text-ink-soft">
              当前生效值 {data.context_window.toLocaleString()} / {data.threshold_pct}% / keep {data.keep_recent}
            </p>
          )}
        </div>
      )}

      {saveError && <p className="form-error mt-3">✗ {saveError}</p>}
      {savedNotice && (
        <p className="mt-3 text-xs text-emerald-700">✓ {savedNotice}</p>
      )}

      <div className="flex items-center gap-2 pt-3 mt-3 border-t border-sky-light/40">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="btn btn-primary text-sm py-1.5 px-4"
          title={!dirty ? "没有改动" : "保存"}
        >
          {saving ? "保存中…" : "保存"}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setContextWindow(String(data?.context_window ?? ""));
              setThresholdPct(String(data?.threshold_pct ?? ""));
              setKeepRecent(String(data?.keep_recent ?? ""));
              setSaveError(null);
              setSavedNotice(null);
            }}
            disabled={saving}
            className="btn btn-ghost text-sm py-1.5 px-3"
          >
            放弃改动
          </button>
        )}
      </div>
    </ConsoleCard>
  );
}

// Bot token verify + save form, identical to wizard step 1.

// Bot token verify + save form, identical to wizard step 1.
// Returns the verified + saved token + username via onSaved so
// the parent can update its state.
export function BotTokenField(props: {
  onSaved: (token: string, username: string) => void;
  onCancel: () => void;
}) {
  const [token, setToken] = useState("");
  const [testState, setTestState] = useState<
    "idle" | "testing" | "success" | "error"
  >("idle");
  const [username, setUsername] = useState("");
  const [verifiedToken, setVerifiedToken] = useState<string | null>(null);
  const [testError, setTestError] = useState("");
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");
  const [saveError, setSaveError] = useState("");

  function handleTokenChange(newValue: string) {
    setToken(newValue);
    if (testState === "success" || testState === "error") {
      setTestState("idle");
      setTestError("");
    }
  }

  async function handleTest() {
    setTestState("testing");
    setTestError("");
    try {
      const res = await fetch("/api/onboarding/verify-bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
        credentials: "include",
      });
      const data = (await res.json()) as {
        ok: boolean;
        username?: string;
        error?: string;
      };
      if (data.ok && data.username) {
        setTestState("success");
        setUsername(data.username);
        setVerifiedToken(token.trim());
      } else {
        setTestState("error");
        setTestError(data.error ?? "Verification failed");
      }
    } catch (err) {
      setTestState("error");
      setTestError(err instanceof Error ? err.message : "Network error");
    }
  }

  async function handleSave() {
    if (!verifiedToken) return;
    setSaveState("saving");
    setSaveError("");
    try {
      const res = await fetch("/api/onboarding/save-bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: verifiedToken, username }),
        credentials: "include",
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        setSaveState("saved");
        props.onSaved(verifiedToken, username);
      } else {
        setSaveState("error");
        setSaveError(data.error ?? "Save failed");
      }
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "Network error");
    }
  }

  const canSave =
    testState === "success" &&
    token === verifiedToken &&
    saveState !== "saving";

  return (
    <div className="space-y-2">
      <label htmlFor="settings-bot-token" className="form-label">
        New Telegram bot token
      </label>
      <div className="flex gap-2">
        <input
          id="settings-bot-token"
          type="password"
          value={token}
          onChange={(e) => handleTokenChange(e.target.value)}
          placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
          autoComplete="off"
          spellCheck={false}
          disabled={saveState === "saved"}
          className="form-input flex-1 text-sm py-2 px-3 font-mono"
        />
        <button
          type="button"
          onClick={handleTest}
          disabled={testState === "testing" || !token.trim() || saveState === "saved"}
          className="btn btn-primary text-sm py-2 px-3 shrink-0"
        >
          {testState === "testing" ? "Testing…" : "Test"}
        </button>
      </div>

      {testState === "success" && (
        <p className="text-sm text-emerald-700">
          ✓ Verified — bot is <span className="font-mono">@{username}</span>
        </p>
      )}
      {testState === "error" && (
        <p className="form-error">✗ {testError}</p>
      )}

      {testState === "success" && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="btn btn-primary text-sm py-2 px-4"
          >
            {saveState === "saving"
              ? "Saving…"
              : saveState === "saved"
                ? "Saved ✓"
                : "Save bot token"}
          </button>
          <button
            type="button"
            onClick={props.onCancel}
            disabled={saveState === "saving"}
            className="btn btn-ghost text-sm py-2 px-3"
          >
            Cancel
          </button>
          {saveState === "error" && (
            <p className="form-error">✗ {saveError}</p>
          )}
        </div>
      )}

      {testState !== "success" && (
        <button
          type="button"
          onClick={props.onCancel}
          className="text-xs text-ink-soft hover:text-sky-deep transition"
        >
          Cancel
        </button>
      )}
    </div>
  );
}


/** Props the Shell passes in. ``onBotUpdated`` and
 *  ``onAdminsChanged`` bubble state out to App; the others are
 *  user-context or restart hooks. */
export type SettingsTabProps = {
  data: OnboardingData | null;
  signedInUser: { chat_id: string; display_name: string | null };
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
  onRestart: () => void;
};
