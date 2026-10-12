import path from "node:path";

const HANDLE = /^@[A-Za-z0-9][A-Za-z0-9._-]{0,62}\.magi$/;

/** Recognize actual app entries, not an arbitrary file with the same basename. */
export function describeApp(spec, paths = path) {
  const { basename, dirname, resolve } = paths;
  const cwd = resolve(spec.cwd);
  const kind = basename(cwd);
  if (!["user", "asp", "eva"].includes(kind) || basename(dirname(cwd)) !== "apps") return null;
  if (resolve(cwd, spec.entry) !== resolve(cwd, "dist", `${kind}.js`)) return null;
  const handle = kind === "eva" ? spec.args?.[0] : `@${kind}.magi`;
  if (!HANDLE.test(handle ?? "") || (kind === "eva" && ["@user.magi", "@asp.magi"].includes(handle))) return null;
  return { kind, handle, root: resolve(cwd, "..", ".."), key: `${kind}:${handle}` };
}
