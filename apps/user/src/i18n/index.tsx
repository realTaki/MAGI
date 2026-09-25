/**
 * i18n core — locale state + message catalog.
 *
 * Lightweight runtime: no external deps (no i18next, no
 * react-intl). The catalog is hand-curated in
 * ``messages.ts``; missing keys fall back to the key
 * itself, which doubles as a "todo: translate this" hint
 * when the operator switches to English / Japanese and
 * sees the raw English / Chinese.
 *
 * Persistence: ``localStorage[magi.locale]``. v0 reads
 * it on mount and writes back on every change. No
 * backend dependency — switching language is purely a
 * client-side rendering concern.
 *
 * Detection order on first load:
 *   1. ``localStorage[magi.locale]`` if it is a supported
 *      locale or the explicit ``system`` preference
 *   2. ``navigator.language`` if it matches one of our
 *      supported locales ("zh", "en", "ja" with optional
 *      region tag)
 *   3. ``zh`` as the project default (current Chinese
 *      strings were the source of truth)
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { MESSAGES, type Catalog } from "./messages";

export type Locale = "zh" | "en" | "ja";
export type LocalePreference = Locale | "system";

export const SUPPORTED_LOCALES: Locale[] = ["zh", "en", "ja"];

export const LOCALE_LABELS: Record<Locale, string> = {
  zh: "中文",
  en: "English",
  ja: "日本語",
};

const STORAGE_KEY = "magi.locale";

function navigatorLocale(): Locale {
  try {
    const nav = (window.navigator.language || "").toLowerCase();
    const primary = nav.split("-")[0];
    if (isSupported(primary)) return primary;
  } catch {
    // Fall through to the project default.
  }
  return "zh";
}

function readPreference(): LocalePreference {
  if (typeof window === "undefined") return "system";
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "system") return "system";
    if (stored && isSupported(stored)) return stored;
  } catch {
    // localStorage may throw in private-mode browsers.
  }
  return "system";
}

function detectLocale(): Locale {
  const preference = readPreference();
  return preference === "system" ? navigatorLocale() : preference;
}

function isSupported(s: string): s is Locale {
  return (SUPPORTED_LOCALES as string[]).includes(s);
}

type I18nContextValue = {
  locale: Locale;
  localePreference: LocalePreference;
  setLocale: (l: Locale) => void;
  setLocalePreference: (p: LocalePreference) => void;
  /** Translate a dotted key. Falls back to the key itself
   *  when missing — better than crashing the page. */
  t: (key: string) => string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  // ``lazy init`` so the first render already has the right
  // locale — no flash of the default language before the
  // detector runs.
  const [locale, setLocaleState] = useState<Locale>(() => detectLocale());
  const [localePreference, setPreferenceState] = useState<LocalePreference>(() =>
    readPreference(),
  );

  const setLocalePreference = useCallback((p: LocalePreference) => {
    setPreferenceState(p);
    setLocaleState(p === "system" ? navigatorLocale() : p);
    try {
      window.localStorage.setItem(STORAGE_KEY, p);
    } catch {
      // Private-mode browsers — the in-memory change still
      // takes effect for this visit.
    }
  }, []);

  const setLocale = useCallback(
    (l: Locale) => {
      setLocalePreference(l);
    },
    [setLocalePreference],
  );

  useEffect(() => {
    if (localePreference !== "system") {
      return;
    }
    function onLanguageChange() {
      setLocaleState(navigatorLocale());
    }
    window.addEventListener("languagechange", onLanguageChange);
    return () => window.removeEventListener("languagechange", onLanguageChange);
  }, [localePreference]);

  // Cross-tab sync: if the operator opens the dashboard in
  // two tabs and changes the language in one, the other
  // tab re-renders too. Keeps localStorage as the source
  // of truth.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key !== STORAGE_KEY || !e.newValue) {
        return;
      }
      if (e.newValue === "system") {
        setPreferenceState("system");
        setLocaleState(navigatorLocale());
        return;
      }
      if (isSupported(e.newValue)) {
        setPreferenceState(e.newValue);
        setLocaleState(e.newValue);
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // ``t()`` is memoised per locale so callers can use it
  // freely in render without re-doing the lookup chain.
  const t = useMemo(() => {
    const catalog: Catalog = MESSAGES[locale];
    return (key: string): string => {
      // Look up via dotted path. ``a.b.c`` walks
      // ``catalog[a][b][c]``. Missing branches fall back to
      // the key (visible "todo" hint during development;
      // visible raw key in production if a translator
      // missed an entry).
      const parts = key.split(".");
      let cur: unknown = catalog;
      for (const p of parts) {
        if (cur && typeof cur === "object" && p in (cur as Record<string, unknown>)) {
          cur = (cur as Record<string, unknown>)[p];
        } else {
          return key;
        }
      }
      return typeof cur === "string" ? cur : key;
    };
  }, [locale]);

  const value = useMemo(
    () => ({ locale, localePreference, setLocale, setLocalePreference, t }),
    [locale, localePreference, setLocale, setLocalePreference, t],
  );

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nContextValue {
  const v = useContext(I18nContext);
  if (!v) {
    throw new Error("useI18n must be used inside <I18nProvider>");
  }
  return v;
}

/** Hook alias — shorter at call sites. */
export function useT() {
  return useI18n().t;
}
