/** Relay address. Set MAGI_ASP, or MAGI_ASP_HOST and MAGI_ASP_PORT. */
export function aspBase(env: NodeJS.ProcessEnv = process.env): string {
  const explicit = env.MAGI_ASP?.trim();
  if (explicit) return explicit.replace(/\/$/, "");
  const host = env.MAGI_ASP_HOST?.trim() || "127.0.0.1";
  const port = env.MAGI_ASP_PORT?.trim() || "42069";
  return `http://${host}:${port}`;
}
