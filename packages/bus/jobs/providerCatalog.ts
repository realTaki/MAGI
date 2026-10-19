/** Curated against pi-ai 0.87.1 and official docs on 2026-10-19.
 * See docs/provider-catalog.md. This browser-safe data contains no credentials. */
export const PROVIDER_CATALOG = [
  { id: "openai", label: "OpenAI", baseUrl: "https://api.openai.com/v1", api: "openai-responses", models: [
    { id: "gpt-6-sol", contextWindow: 272000 }, { id: "gpt-6-luna", contextWindow: 272000 }, { id: "gpt-6-astra", contextWindow: 272000 },
  ] },
  { id: "anthropic", label: "Anthropic", baseUrl: "https://api.anthropic.com", api: "anthropic-messages", models: [
    { id: "claude-opus-5-5", contextWindow: 1000000 }, { id: "claude-fable-5-1", contextWindow: 1000000 }, 
  ] },
  { id: "minimax-cn", label: "MiniMax", baseUrl: "https://api.minimaxi.com/anthropic", api: "anthropic-messages", models: [{ id: "MiniMax-M3", contextWindow: 1048576 }] },
  { id: "minimax-global", label: "MiniMax", baseUrl: "https://api.minimax.io/anthropic", api: "anthropic-messages", models: [{ id: "MiniMax-M3", contextWindow: 1048576 }] },
  { id: "deepseek", label: "DeepSeek", baseUrl: "https://api.deepseek.com", api: "openai-completions", models: [
    { id: "deepseek-flash", contextWindow: 1000000 }, { id: "deepseek-v4-pro", contextWindow: 1000000 },
  ] },
] as const;

export function providerPreset(id: string) { return PROVIDER_CATALOG.find((item) => item.id === id); }
