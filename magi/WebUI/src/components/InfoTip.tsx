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
 * Why a stateful overlay instead of the native ``title=``
 * attribute:
 *
 *   - ``title=`` triggers a multi-second browser delay
 *     before showing; an operator reading quickly past
 *     the card needs the tooltip to feel instant.
 *   - ``title=`` positions itself wherever the OS picks
 *     (often off-screen near the right edge, since the
 *     OS reads the cursor's screen position). The
 *     stateful popover anchors to the icon's right
 *     edge so we always know where it lives.
 *   - ``title=`` is not keyboard-accessible (a tab stop
 *     can't trigger it). The button + on-focus state
 *     makes the explanation reachable without a mouse.
 *   - ``title=`` can't be styled; the in-card popover
 *     matches the dashboard's sky-pale / sky-light
 *     palette so the hint doesn't read as a separate
 *     system surface.
 *
 * Popover direction: opens to the **left** by default
 * (``right-full`` + ``mr-2``). Card titles sit at the
 * top of the panel and the rest of the page has
 * plenty of right-side room — popping to the right
 * would push the tooltip off the viewport for any
 * card that touches the screen edge.
 */

import { useEffect, useRef, useState } from "react";

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
  // Mouse leave on the wrapper can fire before mouse enter
  // on the popover (the two elements are separate DOM
  // nodes). The ref + small grace timer below prevents the
  // popover from flickering as the cursor crosses the gap.
  const closeTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      // Drop any pending close-timer on unmount so a
      // re-render in the same tick doesn't try to set state
      // on a dead component.
      if (closeTimer.current !== null) {
        window.clearTimeout(closeTimer.current);
      }
    };
  }, []);

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
    <span className="relative inline-flex items-center">
      <button
        type="button"
        aria-label={text}
        onMouseEnter={() => {
          cancelClose();
          setOpen(true);
        }}
        onMouseLeave={scheduleClose}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="inline-flex items-center justify-center
                   text-ink-soft hover:text-ink
                   focus:outline-none focus-visible:ring-2
                   focus-visible:ring-sky-300 rounded-full
                   transition-colors"
        style={{ width: size, height: size }}
      >
        <IconHelp className="" />
      </button>
      {open && (
        <span
          role="tooltip"
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
          // Anchor to the **right** of the trigger with a
          // negative margin so the popover sits flush
          // left of the `?` icon — keeps the whole
          // thing on-screen when the card title is at
          // the right edge of the viewport. ``top-full``
          // + ``mt-2`` drops the popover below the
          // trigger (the card title is on the same row;
          // popping left or right would push it into the
          // card body or off the right edge).
          className="absolute z-20 right-full top-1/2 -translate-y-1/2 mr-2
                     w-72 max-w-xs
                     rounded-md border border-sky-light/40
                     bg-white/95 backdrop-blur
                     px-3 py-2 text-xs leading-relaxed text-ink-soft
                     shadow-sm
                     whitespace-normal"
        >
          {text}
        </span>
      )}
    </span>
  );
}