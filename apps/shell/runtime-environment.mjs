/**
 * Preserve the OS search path and emit one PATH key for a Windows child.
 * @param {Readonly<NodeJS.ProcessEnv>} environment
 * @param {readonly string[]} entries
 * @param {NodeJS.Platform} [platform]
 * @returns {NodeJS.ProcessEnv & { PATH: string }}
 */
export function prependRuntimePath(environment, entries, platform = process.platform) {
  /** @param {string} key */
  const isPath = (key) => platform === "win32" ? key.toUpperCase() === "PATH" : key === "PATH";
  const original = Object.keys(environment).find(isPath);
  const inherited = original ? environment[original] : "";
  /** @type {NodeJS.ProcessEnv} */
  const result = { ...environment };
  for (const key of Object.keys(result)) if (isPath(key)) delete result[key];
  return { ...result, PATH: [...entries, inherited ?? ""].filter(Boolean).join(platform === "win32" ? ";" : ":") };
}
