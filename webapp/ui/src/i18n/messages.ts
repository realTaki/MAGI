/**
 * Message catalog — three locales (zh / en / ja).
 *
 * Structure: nested objects keyed by namespace (e.g.
 * ``topbar.signOut``, ``chatSearch.empty.browseHint``).
 * The ``t()`` runtime walks the dotted path and falls
 * back to the key string when a translation is missing
 * — that way a partially-translated page still renders
 * (with raw keys visible where work is pending) instead
 * of crashing.
 *
 * Locale content lives in ``locales/zh.ts``, ``en.ts``,
 * ``ja.ts`` to keep per-language files browseable (~400
 * lines each).
 */

import zh from "./locales/zh";
import en from "./locales/en";
import ja from "./locales/ja";

export type Catalog = {
  [key: string]: string | Catalog;
};

export const MESSAGES: Record<"zh" | "en" | "ja", Catalog> = {
  zh,
  en,
  ja,
};
