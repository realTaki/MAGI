/**
 * SettingsTab — left-nav + 7 setting cards.
 *
 * Each card is its own component in
 * ``magi/WebUI/src/components/settings/``; this file is
 * the dispatch shell that picks which card to render based
 * on the sidebar selection. The Shell mounts
 * ``<SettingsTab data=… onBotUpdated=… onAdminsChanged=… onRestart=… />``
 * — the only tab that takes props, because some cards bubble
 * state out to App (bot username refresh, admin-list refresh).
 *
 * SidebarItem.label convention: each entry's ``label`` is an
 * i18n key (e.g. ``settings.navChannels``); the inline renderer
 * resolves it via ``t()`` so this is the one tab that doesn't
 * pass through either raw keys or raw Chinese.
 *
 * Adding a new section:
 * 1. Drop ``SettingsFooCard.tsx`` under ``components/settings/``
 * 2. Add a sidebar entry to ``SETTINGS_SECTIONS``
 * 3. Add ``id`` to the ``SettingSection`` union
 * 4. Add a render branch in the dispatcher below
 */

import { useState } from "react";

import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import {
  IconActionItems,
  IconConnectors,
  IconEmployees,
  IconReminders,
  IconScheduledTasks,
  IconSkills,
} from "../components/icons";
import { SettingsAgentCard } from "../components/settings/SettingsAgentCard";
import { SettingsChannelsCard } from "../components/settings/SettingsChannelsCard";
import { SettingsOnboardingCard } from "../components/settings/SettingsOnboardingCard";
import { SettingsPersonaCard } from "../components/settings/SettingsPersonaCard";
import { SettingsSystemTimezoneCard } from "../components/settings/SettingsSystemTimezoneCard";
import { SettingsTgReadReactionCard } from "../components/settings/SettingsTgReadReactionCard";
import { SettingsWebuiAccessCard } from "../components/settings/SettingsWebuiAccessCard";
import { useT } from "../i18n/index";
import type { OnboardingData } from "./onboardingTypes";

export type SettingSection =
  | "channels"
  | "persona"
  | "tg-read"
  | "tz"
  | "agent"
  | "webui-access"
  | "onboarding";

export const SETTINGS_SECTIONS: SidebarItem[] = [
  { id: "channels", label: "settings.navChannels", icon: <IconConnectors /> },
  { id: "persona", label: "settings.navPersona", icon: <IconSkills /> },
  { id: "tg-read", label: "settings.navTgRead", icon: <IconReminders /> },
  { id: "tz", label: "settings.navTz", icon: <IconScheduledTasks /> },
  { id: "agent", label: "settings.navAgent", icon: <IconScheduledTasks /> },
  { id: "webui-access", label: "settings.navWebuiAccess", icon: <IconEmployees /> },
  { id: "onboarding", label: "settings.navOnboarding", icon: <IconActionItems /> },
];

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
          label: it.label.includes(".") ? t(it.label) : it.label,
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
        {section === "agent" && <SettingsAgentCard />}
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