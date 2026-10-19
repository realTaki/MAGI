/** Stable, safe diagnostics across job and UI boundaries. Never persist provider response bodies. */
export const PROVIDER_FAILURES = {
  connection: "Cannot connect to the model provider. Check network access, DNS, proxy and the selected region, then retry.",
  authentication: "The provider rejected the credentials or account permission. Check the API key and region, then retry.",
  model: "The selected model is unsupported or unavailable to this account. Choose a supported model, then retry.",
  configuration: "The provider rejected the request configuration. Check the model and endpoint.",
  rate_limit: "The provider reported a rate or quota limit. Check the account quota and retry later.",
  timeout: "The provider request timed out. Check connectivity and retry.",
  unconfigured: "Configure and save a model provider using the configuration button before sending a message.",
  unknown: "The provider request failed. Check the configuration and retry.",
} as const;
export type ProviderFailureCode = keyof typeof PROVIDER_FAILURES;
export function providerFailure(code: ProviderFailureCode): Error {
  return new Error(`ProviderError [${code}]: ${PROVIDER_FAILURES[code]}`);
}
export function providerFailureCode(error: unknown): ProviderFailureCode {
  const value = error instanceof Error ? error.message : String(error);
  const code = /^ProviderError \[([a-z_]+)\]:/.exec(value)?.[1];
  return code && Object.hasOwn(PROVIDER_FAILURES, code) ? code as ProviderFailureCode : "unknown";
}
