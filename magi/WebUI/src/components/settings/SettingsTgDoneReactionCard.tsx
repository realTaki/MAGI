/**
 * SettingsTgDoneReactionCard — pick the emoji the EVE
 * bot stamps on each incoming TG message via
 * ``set_message_reaction`` as a "task complete" signal
 * that fires **after** the assistant's reply lands.
 *
 * Telegram replaces any prior bot reaction on the same
 * message, so the user sees the read receipt (👀 etc.)
 * get "upgraded" to the done reaction (🏆 etc.) the moment
 * the reply arrives — no double-stamping, no flicker.
 *
 * Save hits ``PUT /api/tg-settings/done-reaction`` and
 * takes effect on the *next* inbound TG message; no
 * restart, no reload. Same allowlist as the read
 * reaction (Telegram has a single reaction whitelist)
 * — see :component:`SettingsTgReadReactionCard`.
 */

import { useT } from "../../i18n/index";

import { TgReactionPickerCard } from "./TgReactionPickerCard";

export function SettingsTgDoneReactionCard() {
  const t = useT();
  return (
    <TgReactionPickerCard
      title={t("settings.tgDoneEmoji")}
      description={t("settings.tgDoneEmojiDesc")}
      endpoint="/api/tg-settings/done-reaction"
      savedNotice={t("settings.tgReactionSavedNotice")}
    />
  );
}