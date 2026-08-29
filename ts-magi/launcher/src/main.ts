import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { launchPlayground } from "./launcher.js";

const suppliedWorkspace = process.argv[2];
const workspace = suppliedWorkspace ?? (await mkdtemp(join(tmpdir(), "ts-magi-")));
try {
  const runtime = await launchPlayground(workspace);
  const jobId = await runtime.caller.ask("hello");
  await runtime.provider.serveNext();
  const result = await runtime.reader.read(jobId);
  console.log(`caller -> provider -> reader: ${result?.output?.text}`);
  await runtime.shutdown();
} finally {
  if (!suppliedWorkspace) await rm(workspace, { recursive: true, force: true });
}
