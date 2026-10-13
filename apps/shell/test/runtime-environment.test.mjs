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
