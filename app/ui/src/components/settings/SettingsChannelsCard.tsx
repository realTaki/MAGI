/**
 * SettingsChannelsCard — channel on/off toggles.
 *
 * Reads from GET /api/channels, toggles via POST /api/channels.
 * Each row: name + status badge + toggle switch.
 * - WebUI: always on, switch disabled (control plane).
 * - TG: toggleable; disabled when no bot token is saved.
 * - WeChat / Lark / Teams: disabled, "coming soon".
 */
import { Fragment, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import Toggle from "../Toggle";
import { useT } from "../../i18n/index";
import { apiFetch } from "../../lib/queryClient";
import { BotTokenField } from "./BotTokenField";

// -- types ----------------------------------------------------------------

type ChannelInfo = {
  name: string;
  label: string;
  implemented: boolean;
  has_credentials: boolean;
  enabled: boolean;
  running: boolean;
};

type ChannelsResponse = {
  enabled: string[];
  available: ChannelInfo[];
};

// -- status badge ---------------------------------------------------------

function Badge({ status }: { status: "on" | "off" | "coming" | "no-creds" }) {
  const t = useT();
  switch (status) {
    case "on":
      return <span className="status-pill status-pill--connected text-[10px]">{t("settings.statusConnected")}</span>;
    case "off":
      return <span className="status-pill status-pill--disconnected text-[10px]">{t("settings.statusDisconnected")}</span>;
    case "coming":
      return <span className="text-[10px] text-ink-soft bg-sky-soft border border-sky-soft rounded px-1.5 py-0.5">{t("settings.statusComingSoon")}</span>;
    case "no-creds":
      return <span className="text-[10px] text-warning bg-warning-soft border border-warning-soft rounded px-1.5 py-0.5">{t("settings.channelNoCredentials")}</span>;
  }
}

// -- main card -------------------------------------------------------------

export function SettingsChannelsCard(props: {
  onBotUpdated: (newBot: { token: string; username: string }) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [editingToken, setEditingToken] = useState(false);

  const query = useQuery({
    queryKey: ["channels"] as const,
    queryFn: () => apiFetch<ChannelsResponse>("/api/channels"),
    staleTime: 30_000,
  });

  const data = query.data;
  const loadError = query.error
    ? (query.error as Error).message
    : null;

  async function toggle(ch: ChannelInfo) {
    if (!data) return;
    const next = ch.enabled
      ? data.enabled.filter((c) => c !== ch.name)
      : [...data.enabled, ch.name];
    // Optimistic update
    qc.setQueryData(["channels"], {
      ...data,
      enabled: next,
      available: data.available.map((c) =>
        c.name === ch.name ? { ...c, enabled: !ch.enabled } : c,
      ),
    });
    await apiFetch<ChannelsResponse>("/api/channels", {
      method: "POST",
      body: { enabled: next },
    });
    void qc.invalidateQueries({ queryKey: ["channels"] });
  }

  return (
    <ConsoleCard
      title={t("settings.channels")}
      headerRight={<InfoTip text={t("settings.channelsDesc")} />}
    >
      {loadError && (
        <p className="form-error mb-3">✗ {loadError}</p>
      )}

      {!data && !loadError && (
        <p className="text-sm text-ink-soft">{t("common.loading")}</p>
      )}

      {data && (
        <table className="w-full text-sm">
          <tbody>
            {data.available.map((ch) => {
              const canToggle = ch.implemented && ch.name !== "webui";
              const showTokenField = ch.name === "tg" && ch.enabled && !ch.has_credentials;

              return (
                <Fragment key={ch.name}>
                  <tr key={ch.name} className="border-b border-border-2 last:border-0">
                    <td className="py-2.5 pr-3">
                      <span className="font-medium text-ink">{ch.label}</span>
                    </td>
                    <td className="py-2.5 pr-3">
                      {ch.implemented
                        ? ch.enabled
                          ? <Badge status="on" />
                          : ch.has_credentials
                            ? <Badge status="off" />
                            : <Badge status="no-creds" />
                        : <Badge status="coming" />
                      }
                    </td>
                    <td className="py-2.5 text-right">
                      {ch.implemented && ch.name === "tg" && ch.has_credentials && (
                        <button
                          type="button"
                          onClick={() => setEditingToken((v) => !v)}
                          className="text-xs text-accent hover:text-accent-ink transition mr-3"
                        >
                          {editingToken ? t("common.cancel") : t("settings.btnReSet")}
                        </button>
                      )}
                      {/* Toggle switch */}
                      <Toggle
                        checked={ch.enabled}
                        disabled={!canToggle}
                        onChange={() => toggle(ch)}
                        ariaLabel={ch.label}
                      />
                    </td>
                  </tr>

                  {/* Bot token field — shown when TG is enabled but no token */}
                  {showTokenField && (
                    <tr key={`${ch.name}-token`} className="border-b border-border-2 bg-warning-soft">
                      <td colSpan={3} className="p-3">
                        <p className="text-xs text-warning mb-2">
                          {t("settings.channelTgNoToken")}
                        </p>
                        <BotTokenField
                          runtimeTarget
                          onSaved={(token, username) => {
                            props.onBotUpdated({ token, username });
                            void qc.invalidateQueries({ queryKey: ["channels"] });
                          }}
                          onCancel={() => {}}
                        />
                      </td>
                    </tr>
                  )}

                  {/* Bot token re-set field */}
                  {editingToken && ch.name === "tg" && ch.has_credentials && (
                    <tr key={`${ch.name}-edit`} className="border-b border-border-2 bg-sky-soft">
                      <td colSpan={3} className="p-3">
                        <BotTokenField
                          runtimeTarget
                          onSaved={(token, username) => {
                            props.onBotUpdated({ token, username });
                            setEditingToken(false);
                            void qc.invalidateQueries({ queryKey: ["channels"] });
                          }}
                          onCancel={() => setEditingToken(false)}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </ConsoleCard>
  );
}
