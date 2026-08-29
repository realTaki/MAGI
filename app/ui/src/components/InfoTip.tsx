/**
 * InfoTip — a small "?" icon that surfaces a longer
 * explanation on hover / focus.
 *
 * Used to keep the main card body lean — the panel's
 * primary heading is the operator's first-stop, the
 * body is the actionable surface, and the "why does
 * this exist / how does it work" prose lives behind a
 * `?` that's visible only when the operator asks.
 *
 * Tooltip is rendered through ``createPortal`` to
 * ``document.body`` so it escapes any parent
 * ``overflow: hidden`` / ``overflow: auto`` that might
 * otherwise clip it (the dashboard's sidebar uses
 * ``overflow-y-auto`` for scrolling, which would chop
 * the popover when the ? sits near the right edge of
 * the main column).
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { IconHelp } from "./icons";

type InfoTipProps = {
  /** The hint text. Plain string — no markdown, no rich
   *  formatting. Operators expect the tooltip to be
   *  terse; anything longer belongs in the card body. */
  text: string;
  /** Override the icon size (defaults to 14×14 so it
   *  sits next to a card title without competing with
   *  it visually). */
  size?: number;
};

export function InfoTip({ text, size = 14 }: InfoTipProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const closeTimer = useRef<number | null>(null);
  const iconRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    return () => {
      if (closeTimer.current !== null) {
        window.clearTimeout(closeTimer.current);
      }
    };
  }, []);

  function recomputePosition() {
    const el = iconRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    // tooltip opens to the **right** of the ? — flush at the
    // icon's vertical centre, with a small gap.
    setPos({
      left: rect.right + 8,
      top: rect.top + rect.height / 2,
    });
  }

  function scheduleClose() {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
    }
    closeTimer.current = window.setTimeout(() => {
      setOpen(false);
      closeTimer.current = null;
    }, 80);
  }
  function cancelClose() {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }

  return (
    <>
      <button
        ref={iconRef}
        type="button"
        aria-label={text}
        onMouseEnter={() => { cancelClose(); recomputePosition(); setOpen(true); }}
        onMouseLeave={scheduleClose}
        onFocus={() => { recomputePosition(); setOpen(true); }}
        onBlur={() => setOpen(false)}
        className="inline-flex items-center justify-center
                   text-ink-2 hover:text-ink
                   focus:outline-none focus-visible:ring-2
                   focus-visible:ring-accent-soft rounded-full
                   transition-colors"
        style={{ width: size, height: size }}
      >
        <IconHelp className="" />
      </button>
      {open && pos && createPortal(
        <span
          role="tooltip"
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
          className="fixed z-[100] w-72 max-w-xs
                     -translate-y-1/2
                     rounded-md border border-border
                     bg-surface
                     px-3 py-2 text-xs leading-relaxed text-ink-2
                     shadow-sm
                     whitespace-normal pointer-events-auto"
          style={{ left: pos.left, top: pos.top }}
        >
          {text}
        </span>,
        document.body,
      )}
    </>
  );
}
