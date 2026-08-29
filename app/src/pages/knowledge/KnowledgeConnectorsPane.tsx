
import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';

export function KnowledgeConnectorsPane() {
  const t = useT();
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={t("settings.knowledgeConnectorsHint")} /></div>
      <ConsoleCard title={t("settings.knowledgeConnectorsHeading")}>
        <p className="text-sm text-ink-3">{t("settings.knowledgeConnectorsHint")}</p>
      </ConsoleCard>
    </div>
  );
}
