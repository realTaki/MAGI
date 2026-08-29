import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { launchDemo } from "./launcher.js";

const suppliedWorkspace = process.argv[2];
const workspace = suppliedWorkspace ?? (await mkdtemp(join(tmpdir(), "magi-ts-bus-demo-")));
try {
  const runtime = await launchDemo(workspace);
  console.log(`provider attached: ${runtime.provider.isAttached}, model: ${runtime.provider.model}`);
  await runtime.shutdown();
} finally {
  if (!suppliedWorkspace) await rm(workspace, { recursive: true, force: true });
}
