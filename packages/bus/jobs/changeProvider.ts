/**
 * Reconfigure the live model client. The providers worker verifies the
 * candidate settings before it applies them, so a bad key never replaces a
 * working one.
 */

export type ChangeProviderNotify = {
  provider?: string;
  api_key?: string;
  model?: string;
  base_url?: string;
};
