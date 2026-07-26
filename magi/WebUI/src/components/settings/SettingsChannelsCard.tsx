/**
 * SettingsChannelsCard — platform adapters table.
 */
import { useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
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
    : t("settings.notConfigured");

  return (
    <ConsoleCard
      title={t("settings.channels")}
      headerRight={<InfoTip text={t("settings.channelsDesc")} />}
    >
      <table className="w-full text-sm mt-4">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
            <th className="py-2 pr-4 font-medium">{t("settings.tableHeaderName")}</th>
            <th className="py-2 pr-4 font-medium w-32">{t("settings.tableHeaderStatus")}</th>
            <th className="py-2 pr-4 font-medium">{t("settings.tableHeaderNotes")}</th>
            <th className="py-2 font-medium w-24 text-right">{t("settings.tableHeaderAction")}</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-sky-light/30">
            <td className="py-2 pr-4 text-ink">{t("settings.channelWebui")}</td>
            <td className="py-2 pr-4">
              <ChannelStatusBadge status="connected" />
            </td>
            <td className="py-2 pr-4 text-ink-soft font-mono text-xs">:42069</td>
            <td className="py-2 text-right text-xs text-ink-soft">—</td>
          </tr>

          <tr className="border-b border-sky-light/30">
            <td className="py-2 pr-4 text-ink">{t("settings.channelTelegram")}</td>
            <td className="py-2 pr-4">
              <ChannelStatusBadge
                status={tgConnected ? "connected" : "disconnected"}
              />
            </td>
            <td className="py-2 pr-4 text-ink-soft font-mono text-xs">{tgNote}</td>
            <td className="py-2 text-right">
              {tgConnected && !editing && (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="text-sm text-sky-700 hover:text-sky-deep transition"
                >
                  {t("settings.btnReSet")}
                </button>
              )}
            </td>
          </tr>

          <ComingChannelRow name={t("settings.channelWechat")} />
          <ComingChannelRow name={t("settings.channelLark")} />
          <ComingChannelRow name={t("settings.channelTeams")} />
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
  const t = useT();
  switch (props.status) {
    case "connected":
      return (
        <span className="status-pill status-pill--connected">
          {t("settings.statusConnected")}
        </span>
      );
    case "disconnected":
      return (
        <span className="status-pill status-pill--disconnected">
          {t("settings.statusDisconnected")}
        </span>
      );
    case "coming":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">
          {t("settings.statusComingSoon")}
        </span>
      );
  }
}
