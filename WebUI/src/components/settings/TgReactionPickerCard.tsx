/**
 * TgReactionPickerCard — combined card for the two TG
 * reactions the EVA bot sets on the user's inbound
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
 *
 * Migrated to react-query: ``useTgReaction(kind)`` +
 * ``useUpdateTgReaction(kind)`` with optimistic update
 * baked into the mutation (``onMutate`` / ``onError`` /
 * ``onSettled``). Each kind is its own query, so the
 * two rows render in parallel via react-query's dedup.
 */

import { useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";
import { useTgReaction, useUpdateTgReaction } from "../../lib/queries";

type Kind = "read" | "done";

export function TgReactionPickerCard() {
  const t = useT();
  const readQuery = useTgReaction("read");
  const doneQuery = useTgReaction("done");
  // Per-row inline error; not driven by the mutation
  // (the mutation's onError is shared across both kinds
  // and would surface the same error in both rows).
  const [readError, setReadError] = useState<string | null>(null);
  const [doneError, setDoneError] = useState<string | null>(null);

  const readMut = useUpdateTgReaction("read");
  const doneMut = useUpdateTgReaction("done");

  async function pick(kind: Kind, emoji: string) {
    const setError = kind === "read" ? setReadError : setDoneError;
    setError(null);
    try {
      // The mutation owns the optimistic update + rollback;
      // a rejection propagates as a thrown Error.
      if (kind === "read") {
        await readMut.mutateAsync(emoji);
      } else {
        await doneMut.mutateAsync(emoji);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("settings.networkError"));
    }
  }

  return (
    <ConsoleCard
      title={t("settings.tgReactions")}
      headerRight={<InfoTip text={t("settings.tgReactionsDesc")} />}
    >
      <ReactionRow
        label={t("settings.tgReadEmoji")}
        data={readQuery.data}
        isLoading={readQuery.isLoading}
        error={readError}
        onPick={(e) => pick("read", e)}
      />
      <ReactionRow
        label={t("settings.tgDoneEmoji")}
        data={doneQuery.data}
        isLoading={doneQuery.isLoading}
        error={doneError}
        onPick={(e) => pick("done", e)}
        className="mt-5"
      />
    </ConsoleCard>
  );
}


function ReactionRow(props: {
  label: string;
  data: { current: string; default: string; choices: { value: string }[] } | undefined;
  isLoading: boolean;
  error: string | null;
  onPick: (emoji: string) => void;
  className?: string;
}) {
  const choices = props.data?.choices.map((c) => c.value) ?? [];
  const picked = props.data?.current ?? "";
  return (
    <div className={"mt-4 " + (props.className ?? "")}>
      <div className="text-sm font-medium text-accent-ink mb-2">
        {props.label}
      </div>
      {props.isLoading && choices.length === 0 ? (
        <p className="text-xs text-ink-3">Loading…</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {choices.map((emoji) => {
            const selected = emoji === picked;
            return (
              <button
                key={emoji}
                type="button"
                onClick={() => props.onPick(emoji)}
                className={
                  "w-10 h-10 rounded-full text-xl flex items-center justify-center transition " +
                  (selected
                    ? "ring-2 ring-accent bg-accent-soft"
                    : "ring-1 ring-border hover:bg-surface-2")
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
      {props.error && (
        <p className="form-error mt-2">✗ {props.error}</p>
      )}
    </div>
  );
}
