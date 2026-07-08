/**
 * SettingsChannelsCard — platform adapters table.
 *
 * One row per platform adapter the node can mount. WebUI
 * and Telegram are the live ones today (WebUI is the
 * console you're using; Telegram is the IM channel the
 * wizard configured). The rest — WeChat, Lark, Teams —
 * are listed as "coming soon" so the deployer can see
 * the planned surface area. The Telegram row carries
 * the "Re-set" action; the others are inert for C0.
 *
 * "Coming soon" rows are rendered with reduced opacity to
 * communicate "not actionable" without taking them out
 * of the list. A future Phase 2 / 3 lands Email
 * (IMAP/SMTP), Calendar (Google / Microsoft) and the
 * WeChat / Lark / Teams adapters — at that point each
 * new row gets its own inline config form modelled on
 * the Telegram Re-set token flow.
 *
 * Sub-components live in the same file because they are
 * table-internal pieces (a row + a status pill) that are
 * only used here. Promoting them to their own file would
 * force callers to import a four-line component to render
 * a single row — over-fragmentation the project memory's
 * "minimal by default" rule explicitly warns against.
 *
 * ``BotTokenField`` is the *only* sibling that escaped —
 * it's also used directly by ``SettingsWebuiAccessCard``
 * to admin-edit a Telegram bot token, and rendering it
 * inline here would have required duplicated state.
 */

import { useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";
import type { OnboardingData } from "../../pages/onboardingTypes";
import { BotTokenField } from "./BotTokenField";

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

function ComingChannelRow(props: { name: string }) {
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

function ChannelStatusBadge(props: {
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
