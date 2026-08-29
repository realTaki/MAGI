import ConsoleCard from "../ConsoleCard";

/** MAGIS administrators are shared identities, not local Contact records. */
export function SettingsWebuiAccessCard() {
  return (
    <ConsoleCard title="MAGIS administrators">
      <p className="text-sm text-ink-3">
        Administrator management is available after IM two-factor verification is enabled.
        Local Contact records never grant MAGIS administrator authority.
      </p>
    </ConsoleCard>
  );
}
