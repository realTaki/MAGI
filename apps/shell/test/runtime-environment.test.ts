import test from "node:test";
import assert from "node:assert/strict";
import { prependRuntimePath } from "../runtime-environment.mjs";

test("Windows child PATH preserves a case-preserved OS Path without duplicate keys", () => {
  const original = { Path: String.raw`C:\Windows\System32;C:\Windows\System32\WindowsPowerShell\v1.0`, SystemRoot: String.raw`C:\Windows` };
  const result = prependRuntimePath(original, [String.raw`D:\MAGI\runtime\bin`], "win32");
  assert.equal(result.PATH, String.raw`D:\MAGI\runtime\bin;C:\Windows\System32;C:\Windows\System32\WindowsPowerShell\v1.0`);
  assert.deepEqual(Object.keys(result).filter((key) => key.toUpperCase() === "PATH"), ["PATH"]);
  assert.equal(result.SystemRoot, original.SystemRoot);
  assert.equal(Object.hasOwn(original, "PATH"), false, "the parent's environment is not mutated");
});

test("POSIX PATH remains distinct from an unrelated Path variable", () => {
  assert.deepEqual(prependRuntimePath({ PATH: "/usr/bin:/bin", Path: "unrelated" }, ["/magi/bin"], "linux"), { PATH: "/magi/bin:/usr/bin:/bin", Path: "unrelated" });
});

test("missing PATH and empty entries produce a usable string without mutating readonly inputs", () => {
  const environment: Readonly<NodeJS.ProcessEnv> = Object.freeze({ HOME: "/home/test", PATH: undefined });
  const entries: readonly string[] = Object.freeze(["", "/magi/bin", ""]);
  assert.deepEqual(prependRuntimePath(environment, entries, "linux"), { HOME: "/home/test", PATH: "/magi/bin" });
  assert.deepEqual(prependRuntimePath({}, [], "linux"), { PATH: "" });
  assert.equal(environment.PATH, undefined);
});

test("Windows collapses duplicate case variants to the first inherited PATH", () => {
  const environment = Object.freeze({ Path: "first", PATH: "second", path: "third" });
  assert.deepEqual(prependRuntimePath(environment, ["runtime"], "win32"), { PATH: "runtime;first" });
  assert.deepEqual(environment, { Path: "first", PATH: "second", path: "third" });
});
