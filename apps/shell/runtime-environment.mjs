/** Preserve the OS search path and emit one PATH key for a Windows child. */
export function prependRuntimePath(environment, entries, platform = process.platform) {
  const isPath = (key) => platform === "win32" ? key.toUpperCase() === "PATH" : key === "PATH";
  const original = Object.keys(environment).find(isPath);
  const inherited = original ? environment[original] : "";
  const result = { ...environment };
  for (const key of Object.keys(result)) if (isPath(key)) delete result[key];
  result.PATH = [...entries, inherited ?? ""].filter(Boolean).join(platform === "win32" ? ";" : ":");
  return result;
}
