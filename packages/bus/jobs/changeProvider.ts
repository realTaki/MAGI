/**
 * Reconfigure the live model client. The providers worker verifies the
 * candidate settings before it applies them, so a bad key never replaces a
 * working one.
 */

export type ChangeProviderNotify = {
  /** Opaque local secret reference, never an OAuth token. */
  oauth_ref?: string;
  provider?: string;
  api_key?: string;
  model?: string;
  base_url?: string;
};
