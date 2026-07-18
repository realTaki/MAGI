/**
 * TgReactionPickerCard — combined card for the two TG
 * reactions the EVE bot sets on the user's inbound
 * message:
 *
 *  - "已读" (read):  fired **before** the LLM runs, the
 *    "I've seen this and I'm working on it" signal.
 *  - "完成" (done):  fired **after** the assistant reply
 *    lands; Telegram replaces the prior bot reaction on
 *    the same message, so the user sees the read receipt
 *    get "upgraded" to done.
 *
 * UX shape: each row is a row of emoji buttons. The
 * currently-picked emoji is ring-highlighted. Click
 * another emoji and the new choice is PUT to the
 * corresponding endpoint immediately — no Save button.
 * The intent is "this is a low-stakes toggle, don't
 * make the operator reach for a button". A failed
 * PUT shows an inline error next to the row, but the
 * picker stays usable.
 *
 * The two endpoints
 * (``/api/tg-settings/read-reaction`` and
 * ``/api/tg-settings/done-reaction``) hit the same
 * backend allowlist
 * (``magi.channels.telegram.config.REACTION_CHOICES``)
 * so the choice set is identical; we read both on mount
 * and PUT each independently.
 *
 * Backend labels are NOT rendered here — the operator
 * already knows the difference between 👀 and 🏆, and
 * a label-per-emoji row would push the picker past the
 * visible area of the card. The semantic split
 * (read row / done row) is the only labelling needed.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";

type ReactionOut = {
  current: string;
  default: string;
  choices: { value: string }[];
};

type Kind = "read" | "done";

const ENDPOINTS: Record<Kind, string> = {
  read: "/api/tg-settings/read-reaction",
  done: "/api/tg-settings/done-reaction",
};

export function TgReactionPickerCard() {
  const t = useT();
  // One state slot per kind so the two rows don't
  // stomp on each other during the initial load.
  const [read, setRead] = useState<{
    picked: string;
    choices: string[];
    error: string | null;
    saving: boolean;
  }>({ picked: "", choices: [], error: null, saving: false });
  const [done, setDone] = useState<{
    picked: string;
    choices: string[];
    error: string | null;
    saving: boolean;
  }>({ picked: "", choices: [], error: null, saving: false });

  useEffect(() => {
    void (async () => {
      // Fetch both in parallel — the two endpoints are
      // independent. One failing doesn't block the other.
      const results = await Promise.all(
        (Object.keys(ENDPOINTS) as Kind[]).map(async (kind) => {
          try {
            const r = await fetch(ENDPOINTS[kind], {
              credentials: "include",
            });
            if (!r.ok) return [kind, null] as const;
            const body = (await r.json()) as ReactionOut;
            return [
              kind,
              {
                picked: body.current,
                choices: body.choices.map((c) => c.value),
              },
            ] as const;
          } catch {
            return [kind, null] as const;
          }
        }),
      );
      for (const [kind, payload] of results) {
        if (!payload) continue;
        if (kind === "read") {
          setRead((s) => ({ ...s, ...payload }));
        } else {
          setDone((s) => ({ ...s, ...payload }));
        }
      }
    })();
  }, []);

  async function pick(kind: Kind, emoji: string) {
    const set = kind === "read" ? setRead : setDone;
    // Optimistic update — flip the visual immediately so
    // the click feels instant. On PUT failure we revert.
    const previous =
      kind === "read" ? read.picked : done.picked;
    set((s) => ({ ...s, picked: emoji, error: null, saving: true }));
    try {
      const r = await fetch(ENDPOINTS[kind], {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ emoji }),
        credentials: "include",
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as {
          detail?: string;
        };
        set((s) => ({
          ...s,
          picked: previous,
          error: body.detail ?? `Save failed (${r.status})`,
        }));
        return;
      }
      const body = (await r.json()) as ReactionOut;
      set((s) => ({
        ...s,
        picked: body.current,
        error: null,
        saving: false,
      }));
    } catch (err) {
      set((s) => ({
        ...s,
        picked: previous,
        error: err instanceof Error ? err.message : "Network error",
      }));
    }
  }

  return (
    <ConsoleCard title={t("settings.tgReactions")}>
      <p className="text-sm text-ink-soft">
        {t("settings.tgReactionsDesc")}
      </p>
      <ReactionRow
        label={t("settings.tgReadEmoji")}
        state={read}
        onPick={(e) => pick("read", e)}
      />
      <ReactionRow
        label={t("settings.tgDoneEmoji")}
        state={done}
        onPick={(e) => pick("done", e)}
        className="mt-5"
      />
    </ConsoleCard>
  );
}


function ReactionRow(props: {
  label: string;
  state: {
    picked: string;
    choices: string[];
    error: string | null;
    saving: boolean;
  };
  onPick: (emoji: string) => void;
  className?: string;
}) {
  return (
    <div className={"mt-4 " + (props.className ?? "")}>
      <div className="text-sm font-medium text-sky-deep mb-2">
        {props.label}
      </div>
      {props.state.choices.length === 0 ? (
        <p className="text-xs text-ink-soft">Loading…</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {props.state.choices.map((emoji) => {
            const selected = emoji === props.state.picked;
            return (
              <button
                key={emoji}
                type="button"
                onClick={() => props.onPick(emoji)}
                disabled={props.state.saving && selected}
                className={
                  "w-10 h-10 rounded-full text-xl flex items-center justify-center transition " +
                  (selected
                    ? "ring-2 ring-sky-deep bg-sky-pale/50"
                    : "ring-1 ring-sky-light/40 hover:bg-sky-pale/20")
                }
                aria-pressed={selected}
                title={emoji}
              >
                {emoji}
              </button>
            );
          })}
        </div>
      )}
      {props.state.error && (
        <p className="form-error mt-2">✗ {props.state.error}</p>
      )}
    </div>
  );
}