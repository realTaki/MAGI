/**
 * SettingsTgReadReactionCard — pick the emoji the EVE
 * bot stamps on each incoming TG message via
 * ``set_message_reaction`` as a "seen, working on it"
 * signal that fires **before** the LLM call so the user
 * sees it instantly even if the reply takes 30s.
 *
 * Save hits ``PUT /api/tg-settings/read-reaction`` and
 * takes effect on the *next* inbound TG message; no
 * restart, no reload. The backend allowlists 10 emoji
 * (see ``magi.channels.telegram.config.REACTION_CHOICES``);
 * anything the API returns is one of those, so the
 * radio rows are guaranteed to round-trip.
 *
 * The picker UI is shared with
 * :data:`SettingsTgDoneReactionCard` via
 * :component:`TgReactionPickerCard` — only the endpoint,
 * title, and description differ.
 */

import { useT } from "../../i18n/index";

import { TgReactionPickerCard } from "./TgReactionPickerCard";

export function SettingsTgReadReactionCard() {
  const t = useT();
  return (
    <TgReactionPickerCard
      title={t("settings.tgReadEmoji")}
      description={t("settings.tgReadEmojiDesc")}
      endpoint="/api/tg-settings/read-reaction"
      savedNotice={t("settings.tgReactionSavedNotice")}
    />
  );
}