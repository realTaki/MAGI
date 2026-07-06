/**
 * LanguageSwitcher — globe icon + dropdown menu.
 *
 * Lives next to the "Sign out" button on the topbar.
 * Click the globe → menu of three locales (中文 / English
 * / 日本語). The active locale is highlighted. Selection
 * is persisted to ``localStorage[magi.locale]`` and
 * syncs across tabs via the ``storage`` event.
 *
 * Close on outside-click — small detail that makes the
 * menu feel native. We don't need a portal here; the
 * topbar has plenty of vertical room above the body
 * content and the menu is small (3 rows).
 */

import { useEffect, useRef, useState } from "react";
import {
  LOCALE_LABELS,
  SUPPORTED_LOCALES,
  useI18n,
  type Locale,
} from "../i18n";

function GlobeIcon({ className }: { className?: string }) {
  // Inline SVG to avoid pulling in an icon library for a
  // single glyph. Sized to match the existing topbar button
  // icons (16 px square).
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3a14 14 0 0 1 0 18" />
      <path d="M12 3a14 14 0 0 0 0 18" />
    </svg>
  );
}

export default function LanguageSwitcher() {
  const { locale, setLocale } = useI18n();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Outside-click close. ``pointerdown`` rather than
  // ``click`` so the menu closes the moment a touch lands
  // outside, not after the synthetic mouseup.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function pick(l: Locale) {
    setLocale(l);
    setOpen(false);
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={LOCALE_LABELS[locale]}
        aria-haspopup="menu"
        aria-expanded={open}
        title={LOCALE_LABELS[locale]}
        className="btn btn-secondary text-xs flex items-center gap-1.5"
      >
        <GlobeIcon />
        <span aria-hidden="true">{localeShort(locale)}</span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Language"
          className="absolute right-0 mt-1 w-32 rounded-md border border-sky-light/60 bg-white shadow-lg overflow-hidden z-50"
        >
          {SUPPORTED_LOCALES.map((l) => {
            const active = l === locale;
            return (
              <button
                key={l}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                onClick={() => pick(l)}
                className={
                  "w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-sky-pale/40 " +
                  (active ? "bg-sky-pale/60 text-ink font-medium" : "text-ink-soft")
                }
              >
                <span>{LOCALE_LABELS[l]}</span>
                {active && (
                  <span aria-hidden="true" className="text-ocean">
                    ✓
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function localeShort(l: Locale): string {
  // Compact two-letter tag for the button face.
  switch (l) {
    case "zh":
      return "ZH";
    case "en":
      return "EN";
    case "ja":
      return "JA";
  }
}