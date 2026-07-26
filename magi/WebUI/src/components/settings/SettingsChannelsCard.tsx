/**
 * SettingsChannelsCard — platform adapters.
 *
 * Simple list: WebUI (always on), Telegram (configurable),
 * and three planned channels (WeChat / Lark / Teams) grayed
 * out below a subtle divider.
 */
import { useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";
import type { OnboardingData } from "../../pages/onboardingTypes";
import { BotTokenField } from "./BotTokenField";

// -- status badge -----------------------------------------------------------

function Badge(props: { status: "connected" | "disconnected" | "coming" }) {
  const t = useT();
  switch (props.status) {
    case "connected":
      return <span className="status-pill status-pill--connected text-[10px]">{t("settings.statusConnected")}</span>;
    case "disconnected":
      return <span className="status-pill status-pill--disconnected text-[10px]">{t("settings.statusDisconnected")}</span>;
    case "coming":
      return <span className="text-[10px] text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">{t("settings.statusComingSoon")}</span>;
  }
}

// -- main card --------------------------------------------------------------

export function SettingsChannelsCard(props: {
  data: OnboardingData | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);

  const tgConnected = !!props.data?.bot.username;
  const tgDetail = props.data
    ? `@${props.data.bot.username}` +
      (props.data.bot.token
        ? `  ·  ${props.data.bot.token.slice(0, 6)}…${props.data.bot.token.slice(-4)}`
        : "")
    : t("settings.notConfigured");

  return (
    <ConsoleCard
      title={t("settings.channels")}
      headerRight={<InfoTip text={t("settings.channelsDesc")} />}
    >
      <table className="w-full text-sm">
        <tbody>
          {/* WebUI */}
          <tr className="border-b border-sky-light/20">
            <td className="py-2.5 pr-3 text-ink">{t("settings.channelWebui")}</td>
            <td className="py-2.5 pr-3"><Badge status="connected" /></td>
            <td className="py-2.5 pr-3 text-xs text-ink-soft font-mono">localhost:42069</td>
            <td className="py-2.5 text-right text-xs text-ink-soft/50">—</td>
          </tr>

          {/* Telegram */}
          <tr className="border-b border-sky-light/20">
            <td className="py-2.5 pr-3 text-ink">{t("settings.channelTelegram")}</td>
            <td className="py-2.5 pr-3">
              <Badge status={tgConnected ? "connected" : "disconnected"} />
            </td>
            <td className="py-2.5 pr-3 text-xs text-ink-soft font-mono">{tgDetail}</td>
            <td className="py-2.5 text-right">
              {!editing && (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="text-xs text-sky-700 hover:text-sky-deep transition font-medium"
                >
                  {tgConnected ? t("settings.btnReSet") : t("settings.btnConfigure")}
                </button>
              )}
            </td>
          </tr>

          {/* planned — gray, single merged cell per row */}
          <tr><td colSpan={4} className="pt-3 pb-1" /></tr>

          <tr className="opacity-40">
            <td className="py-1.5 pr-3 text-ink-soft text-xs">{t("settings.channelWechat")}</td>
            <td className="py-1.5 pr-3" colSpan={2}><Badge status="coming" /></td>
            <td className="py-1.5 text-right text-xs text-ink-soft/40">—</td>
          </tr>
          <tr className="opacity-40">
            <td className="py-1.5 pr-3 text-ink-soft text-xs">{t("settings.channelLark")}</td>
            <td className="py-1.5 pr-3" colSpan={2}><Badge status="coming" /></td>
            <td className="py-1.5 text-right text-xs text-ink-soft/40">—</td>
          </tr>
          <tr className="opacity-40">
            <td className="py-1.5 pr-3 text-ink-soft text-xs">{t("settings.channelTeams")}</td>
            <td className="py-1.5 pr-3" colSpan={2}><Badge status="coming" /></td>
            <td className="py-1.5 text-right text-xs text-ink-soft/40">—</td>
          </tr>
        </tbody>
      </table>

      {editing && (
        <div className="mt-4 border-t border-sky-light/30 pt-4">
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
