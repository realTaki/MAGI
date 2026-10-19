/** A backend revision can move the UI file; reloading the old URL is insufficient. */
export async function reloadUpdatedInterface(
  desktop: { reloadInterface?: () => Promise<unknown> } | undefined,
  confirm: () => boolean,
): Promise<void> {
  if (!desktop?.reloadInterface || !confirm()) return;
  await desktop.reloadInterface();
}
