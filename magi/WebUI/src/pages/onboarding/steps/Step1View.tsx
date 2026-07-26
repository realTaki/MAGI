import { useState } from 'react';
import { useT } from '../../../i18n/index';


type ChannelOption = { id: string; name: string; descriptionKey: string; available: boolean };

const channels: ChannelOption[] = [
  { id: 'telegram', name: 'Telegram', descriptionKey: 'onboarding.channelTelegramDesc', available: true },
  { id: 'slack', name: 'Slack', descriptionKey: 'onboarding.channelSlackDesc', available: false },
  { id: 'wechat', name: 'WeChat', descriptionKey: 'onboarding.channelWechatDesc', available: false },
];
export function Step1View(props: {
  step1Mode: "view" | "edit";
  existingBot: { token: string; username: string } | null;
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
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-ink">
        {t("onboarding.step1Title")}
      </h1>
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
          <BotTokenField onSaved={props.onSaved} />
        ))}
    </>
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
