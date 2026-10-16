/**
 * Write this MAGI's own config. The config worker is the only one that claims
 * it, so a tool never writes SettingsBook or the secret store itself.
 *
 * `secrets` is plaintext already on this machine (a provider change that was
 * verified). `sealed` is ciphertext; the config worker decrypts it straight
 * into the secret store and the job result names the keys without their values.
 */

export type ConfigureJob = {
  settings?: Record<string, string>;
  secrets?: Record<string, string>;
  sealed?: Record<string, string>;
};

export type ConfigureResult = {
  secret_set: string[];
};
