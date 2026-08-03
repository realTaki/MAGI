import { useState } from 'react';
import { useT } from '../../../i18n/index';
import { BotTokenField } from '../../../components/settings/BotTokenField';
import { InfoTip } from '../../../components/InfoTip';


type ChannelOption = { id: string; name: string; descriptionKey: string; available: boolean };
type Mode = "with_tg" | "webui_only";

// Hardcoded deep-link to Telegram's bot registration flow. The handle
// (`@BotFather`) is intentionally not internationalized — it is a
// Telegram-side identifier that does not change with our UI locale.
const BOT_FATHER_URL = "https://t.me/BotFather";

const channels: ChannelOption[] = [
  { id: 'telegram', name: 'Telegram', descriptionKey: 'onboarding.channelTelegramDesc', available: true },
  { id: 'slack', name: 'Slack', descriptionKey: 'onboarding.channelSlackDesc', available: false },
  { id: 'wechat', name: 'WeChat', descriptionKey: 'onboarding.channelWechatDesc', available: false },
];

export function Step1View(props: {
  step1Mode: "view" | "edit";
  existingBot: { token: string; username: string } | null;
  /** ``null`` means the wizard hasn't picked a mode yet — the
   *  branch boxes render as the "first question" of the wizard. */
  mode: Mode | null;
  onModeChange: (mode: Mode) => void;
  onContinue: () => void;
  onReSet: () => void;
  onSaved: (token: string, username: string) => void;
}) {
  const t = useT();
  const [channel, setChannel] = useState("telegram");

  const selected = channels.find((c) => c.id === channel);
  const showBotToken = selected?.available && selected.id === "telegram";

  return (
    <>
      <div className="mt-6 flex items-center gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">
          {t("onboarding.step1ModeTitle")}
        </h1>
      </div>
      <p className="mt-2 text-ink-soft">
        {t("onboarding.step1ModeDesc")}
      </p>

      <ModePicker
        mode={props.mode}
        onChange={props.onModeChange}
      />

      {props.mode === "webui_only" && (
        <div className="mt-6 rounded-lg border border-sky-200 bg-sky-50/40 p-4">
          <p className="text-sm text-sky-900">
            {t("onboarding.modeWebuiOnlyDesc")}
          </p>
          <button
            type="button"
            onClick={props.onContinue}
            className="btn btn-primary mt-3 px-5 py-2.5"
          >
            {t("common.next")}
          </button>
        </div>
      )}

      {props.mode === "with_tg" && (
        <>
          <div className="mt-6 flex items-center gap-2">
            <h2 className="text-lg font-medium text-ink">
              {t("onboarding.step1Title")}
            </h2>
            <InfoTip text={t("onboarding.step1Help")} size={16} />
          </div>
          <p className="mt-2 text-ink-soft">
            {t("onboarding.step1Desc")}
          </p>

          <ChannelSelect value={channel} onChange={setChannel} />
          <ChannelDescription channel={selected} />

          {showBotToken &&
            (props.step1Mode === "view" && props.existingBot ? (
              <BotTokenConfiguredView
                bot={props.existingBot}
                onNext={props.onContinue}
                onReSet={props.onReSet}
              />
            ) : (
              <BotTokenField onSaved={props.onSaved} onCancel={() => {}} />
            ))}
        </>
      )}
    </>
  );
}

function ModePicker(props: { mode: Mode | null; onChange: (m: Mode) => void }) {
  const t = useT();
  const options: { mode: Mode; titleKey: string; descKey: string }[] = [
    { mode: "with_tg", titleKey: "onboarding.modeWithTg", descKey: "onboarding.modeWithTgDesc" },
    { mode: "webui_only", titleKey: "onboarding.modeWebuiOnly", descKey: "onboarding.modeWebuiOnlyDesc" },
  ];
  return (
    <div className="mt-4 grid grid-cols-1 gap-3">
      {options.map((o) => (
        <button
          key={o.mode}
          type="button"
          onClick={() => props.onChange(o.mode)}
          className={`text-left p-4 rounded-lg border transition ${
            props.mode === o.mode
              ? "border-sky-deep bg-sky-50/60"
              : "border-sky-100 hover:border-sky-300"
          }`}
        >
          <div className="font-medium text-sky-deep">{t(o.titleKey)}</div>
          <div className="mt-1 text-sm text-ink-soft">{t(o.descKey)}</div>
        </button>
      ))}
    </div>
  );
}

function ChannelSelect(props: { value: string; onChange: (id: string) => void }) {
  return (
    <div className="mt-4 flex flex-col gap-2">
      {channels.map((c) => (
        <button
          key={c.id}
          type="button"
          disabled={!c.available}
          onClick={() => props.onChange(c.id)}
          className={`btn px-4 py-2 text-left ${
            props.value === c.id ? "btn-primary" : ""
          } ${c.available ? "" : "opacity-50 cursor-not-allowed"}`}
        >
          {c.name}
        </button>
      ))}
    </div>
  );
}

function ChannelDescription(props: { channel: ChannelOption | undefined }) {
  const t = useT();
  if (!props.channel) return null;
  // Telegram gets a deep-link to @BotFather so the operator can
  // jump straight to the bot-registration flow. Other channels
  // fall back to the plain description.
  const showBotFatherLink = props.channel.id === "telegram";
  return (
    <div className="mt-2 text-sm text-ink-soft">
      <p>{t(props.channel.descriptionKey)}</p>
      {showBotFatherLink && (
        <p className="mt-1">
          <a
            href={BOT_FATHER_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sky-deep underline decoration-sky-deep/40 underline-offset-2 hover:decoration-sky-deep"
          >
            {t("onboarding.channelTelegramBotFatherLink")}
          </a>
        </p>
      )}
    </div>
  );
}

function BotTokenConfiguredView(props: {
  bot: { token: string; username: string };
  onNext: () => void;
  onReSet: () => void;
}) {
  const t = useT();
  return (
    <div className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50/60 p-4">
      <p className="text-sm font-medium text-emerald-900">
        {t("onboarding.step1Confirmation")}
      </p>
      <dl className="mt-2 grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
        <dt className="text-emerald-800/70">Bot username</dt>
        <dd className="font-mono text-emerald-900">@{props.bot.username}</dd>

        <dt className="text-emerald-800/70">Token</dt>
        <dd className="font-mono text-emerald-900/80 text-xs">
          {props.bot.token
            ? `${props.bot.token.slice(0, 6)}…${props.bot.token.slice(-4)}`
            : "(saved)"}
        </dd>
      </dl>

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          onClick={props.onNext}
          className="btn btn-primary px-5 py-2.5"
        >
          {t("common.next")}
        </button>
        <button
          type="button"
          onClick={props.onReSet}
          className="text-sm text-ink-soft hover:text-sky-deep transition"
        >
          {t("common.edit")}
        </button>
      </div>
    </div>
  );
}