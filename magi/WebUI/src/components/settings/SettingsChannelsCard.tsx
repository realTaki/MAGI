/**
 * SettingsChannelsCard — platform adapters.
 *
 * Layout: two sections — "active" (WebUI + Telegram, always
 * visible) and a collapsed "planned" disclosure for channels
 * that aren't wired yet (WeChat / Lark / Teams). Each active
 * row is a flex card row with an icon, name, status badge,
 * detail line, and an action button on the right.
 */
import { useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";
import type { OnboardingData } from "../../pages/onboardingTypes";
import { BotTokenField } from "./BotTokenField";

// -- channel row ------------------------------------------------------------

function ChannelRow(props: {
  icon: React.ReactNode;
  name: string;
  status: "connected" | "disconnected" | "coming";
  detail: string;
  action?: React.ReactNode;
  dimmed?: boolean;
}) {
  return (
    <div
      className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors ${
        props.dimmed
          ? "opacity-50"
          : "hover:bg-sky-pale/10"
      }`}
    >
      <div className="shrink-0 text-ink-soft">{props.icon}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-ink">{props.name}</span>
          <ChannelStatusBadge status={props.status} />
        </div>
        <p className="text-xs text-ink-soft font-mono mt-0.5">{props.detail}</p>
      </div>
      {props.action && <div className="shrink-0">{props.action}</div>}
    </div>
  );
}

// -- icons ------------------------------------------------------------------

function GlobeIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="10" cy="10" r="7.5" />
      <ellipse cx="10" cy="10" rx="3.5" ry="7.5" />
      <path d="M2.5 10h15" />
    </svg>
  );
}

function BotIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="3" y="6" width="14" height="10" rx="2" />
      <path d="M10 2v4M7 2l3 4M13 2l-3 4" />
      <circle cx="8" cy="11" r="1" fill="currentColor" stroke="none" />
      <circle cx="12" cy="11" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

function ChannelStatusBadge(props: {
  status: "connected" | "disconnected" | "coming";
}) {
  const t = useT();
  switch (props.status) {
    case "connected":
      return (
        <span className="status-pill status-pill--connected text-[10px]">
          {t("settings.statusConnected")}
        </span>
      );
    case "disconnected":
      return (
        <span className="status-pill status-pill--disconnected text-[10px]">
          {t("settings.statusDisconnected")}
        </span>
      );
    case "coming":
      return (
        <span className="text-[10px] text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">
          {t("settings.statusComingSoon")}
        </span>
      );
  }
}

// -- main card --------------------------------------------------------------

export function SettingsChannelsCard(props: {
  data: OnboardingData | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [showPlanned, setShowPlanned] = useState(false);

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
      <div className="mt-2 space-y-1">
        {/* WebUI — always on, no config needed */}
        <ChannelRow
          icon={<GlobeIcon />}
          name={t("settings.channelWebui")}
          status="connected"
          detail="localhost:42069 — 始终在线"
        />

        {/* Telegram — configurable */}
        <ChannelRow
          icon={<BotIcon />}
          name={t("settings.channelTelegram")}
          status={tgConnected ? "connected" : "disconnected"}
          detail={tgDetail}
          action={
            tgConnected && !editing ? (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="text-xs text-sky-700 hover:text-sky-deep transition font-medium"
              >
                {t("settings.btnReSet")}
              </button>
            ) : !tgConnected ? (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="btn btn-primary text-xs py-1 px-3"
              >
                {t("settings.btnConfigure")}
              </button>
            ) : null
          }
        />

        {editing && (
          <div className="mx-3 mt-2 mb-3 p-3 rounded-lg border border-sky-light/40 bg-sky-pale/10">
            <BotTokenField
              onSaved={(token, username) => {
                props.onBotUpdated({ token, username });
                setEditing(false);
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        )}

        {/* Planned — collapsed by default */}
        <div className="mt-3 pt-2 border-t border-sky-light/30">
          <button
            type="button"
            onClick={() => setShowPlanned((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-ink-soft hover:text-ink transition-colors px-3 py-1"
          >
            <svg
              viewBox="0 0 16 16"
              className={`h-3 w-3 transition-transform ${showPlanned ? "rotate-90" : ""}`}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M6 3l6 5-6 5" />
            </svg>
            {t("settings.channelsPlanned").replace("{count}", "3")}
          </button>
          {showPlanned && (
            <div className="mt-1 space-y-0.5">
              <ChannelRow
                icon={<BotIcon />}
                name={t("settings.channelWechat")}
                status="coming"
                detail={t("settings.statusComingSoon")}
                dimmed
              />
              <ChannelRow
                icon={<BotIcon />}
                name={t("settings.channelLark")}
                status="coming"
                detail={t("settings.statusComingSoon")}
                dimmed
              />
              <ChannelRow
                icon={<BotIcon />}
                name={t("settings.channelTeams")}
                status="coming"
                detail={t("settings.statusComingSoon")}
                dimmed
              />
            </div>
          )}
        </div>
      </div>
    </ConsoleCard>
  );
}
