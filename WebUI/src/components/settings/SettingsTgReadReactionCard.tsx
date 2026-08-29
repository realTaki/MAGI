/**
 * SettingsTgReadReactionCard — re-export of the combined
 * TgReactionPickerCard. The "read" / "done" reactions
 * live in the same Settings tab now (they're both emoji
 * on the same inbound message), so there's no need for
 * a separate component. The export name is kept so
 * SettingsTab's import line doesn't need a churn.
 */

export { TgReactionPickerCard as SettingsTgReadReactionCard } from "./TgReactionPickerCard";