/**
 * Write this MAGI's own config. The manager is the only worker that claims it,
 * so a tool never writes SettingsBook or the secret store itself.
 *
 * `secrets` is plaintext already on this machine (a tool the operator filled).
 * `sealed` is ciphertext; the manager decrypts it straight into the secret store
 * and the job result names the keys without their values.
 */

export type ConfigureJob = {
  settings?: Record<string, string>;
  secrets?: Record<string, string>;
  sealed?: Record<string, string>;
  /** Workers that should be restarted so they read what was just written. */
  reload?: string[];
};

export type ConfigureResult = {
  secret_set: string[];
};
