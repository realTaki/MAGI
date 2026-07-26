/**
 * ChatConversationPane — message display + input composer.
 *
 * Receives all state via props from ChatTab.
 */
import { useRef } from "react";

import { useT } from "../../i18n/index";

type ChatMessageRow = {
  id: number;
  role: "user" | "assistant" | "system";
  text: string;
  ts: string;
};

export function ChatConversationPane(props: {
  messages: ChatMessageRow[];
  input: string;
  onInputChange: (v: string) => void;
  sending: boolean;
  error: { code: string; detail: string } | null;
  onSend: () => void;
  title: string | null;
  preview: string | null;
  hasMoreOlder: boolean;
  totalActive: number;
  loadedCount: number;
  onLoadOlder: () => void;
  loadingOlder: boolean;
  onNewChat: () => void;
}) {
  const t = useT();
  const scrollerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const headerLabel =
    props.title
    ?? props.preview
    ?? t("sidebar.newChat");

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header — session title + new chat link */}
      <div className="shrink-0 flex items-center justify-between px-1 pb-3 border-b border-sky-light/30">
        <h3 className="text-sm font-medium text-ink truncate">
          {headerLabel}
        </h3>
        <button
          type="button"
          onClick={props.onNewChat}
          className="text-xs text-sky-700 hover:text-sky-deep transition shrink-0 ml-3"
        >
          {t("sidebar.newChat")}
        </button>
      </div>

      {/* Messages area */}
      <div
        ref={scrollerRef}
        className="flex-1 min-h-0 overflow-y-auto px-1 py-3 space-y-3"
      >
        {/* Load older messages affordance */}
        {props.hasMoreOlder && (
          <div className="text-center">
            <button
              type="button"
              disabled={props.loadingOlder}
              onClick={props.onLoadOlder}
              className="text-xs text-sky-700 hover:text-sky-deep disabled:opacity-50 transition"
            >
              {props.loadingOlder
                ? t("common.loading")
                : t("chat.loadOlder")
                    .replace("{loaded}", String(props.loadedCount))
                    .replace("{total}", String(props.totalActive))}
            </button>
          </div>
        )}

        {props.messages.length === 0 && !props.sending && (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-ink-soft">{t("chat.emptyHint")}</p>
          </div>
        )}

        {props.messages.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={
                "max-w-[80%] min-w-0 break-all rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap " +
                (m.role === "user"
                  ? "bg-sky-deep text-white"
                  : "bg-sky-pale/60 text-ink border border-sky-light/40")
              }
            >
              {m.text}
            </div>
          </div>
        ))}

        {props.sending && (
          <div className="flex justify-start">
            <div className="rounded-2xl bg-sky-pale/60 text-ink-soft border border-sky-light/40 px-4 py-2.5 text-sm flex items-center gap-2">
              <span>{t("chat.sending")}</span>
              <span className="inline-flex gap-1">
                <span className="animate-pulse">·</span>
                <span className="animate-pulse" style={{ animationDelay: "0.2s" }}>·</span>
                <span className="animate-pulse" style={{ animationDelay: "0.4s" }}>·</span>
              </span>
            </div>
          </div>
        )}

        {props.error && (
          <div className="text-center">
            <p className="text-sm text-rose-600">
              {props.error.code === "chat.llm_credentials_required"
                ? t("chat.errorCredentials")
                : props.error.code === "chat.unknown_sender"
                  ? t("chat.errorAuth")
                  : props.error.detail || t("chat.errorNetwork")}
            </p>
          </div>
        )}
      </div>

      {/* Input composer */}
      <div className="shrink-0 pt-3 border-t border-sky-light/30">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={props.input}
            onChange={(e) => props.onInputChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (props.input.trim() && !props.sending) props.onSend();
              }
            }}
            placeholder={t("chat.inputPlaceholder")}
            rows={2}
            disabled={props.sending}
            className="form-input flex-1 text-sm py-2 px-3 resize-none"
          />
          <button
            type="button"
            onClick={props.onSend}
            disabled={props.sending || !props.input.trim()}
            className="btn btn-primary text-sm py-2 px-4 shrink-0 self-end"
          >
            {props.sending ? t("common.loading") : t("chat.send")}
          </button>
        </div>
      </div>
    </div>
  );
}
