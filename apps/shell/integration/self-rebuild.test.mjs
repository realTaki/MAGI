import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, mkdir, readFile, writeFile, cp, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { WebSocketServer } from "ws";
import Database from "better-sqlite3";
import dugite from "dugite";
import { Bus, MAGI_CONTACT_ID } from "@magi/bus";
import { createSupervisor } from "../process.mjs";
import { createAppRebuilder } from "../rebuild.mjs";
import { prependRuntimePath } from "../runtime-environment.mjs";
import { createProcessOwner } from "../process-owner.mjs";
import { profilePaths } from "../profile-layout.mjs";

const repository = path.resolve(import.meta.dirname, "../../..");
const node = process.execPath;
const npm = path.join(repository, "apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js");
const git = dugite.resolveGitBinary();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, message, timeout = 300_000) {
  const deadline = Date.now() + timeout;
  while (!await check()) { if (Date.now() > deadline) throw new Error(message); await sleep(20); }
}

// This test is deliberately outside npm test's glob: candidate validation runs
// the ordinary root suite, without recursively running its own activation test.
test("EVA self-edits through model tools, asks Shell over IPC, and serves durable work after replacement", { timeout: 1_500_000 }, async (t) => {
  const temporary = await mkdtemp(path.join(tmpdir(), "magi-self-loop-"));
  const home = path.join(temporary, "home");
  const layout = profilePaths(home);
  const workspace = path.join(layout.dataRoot, "eva");
  const checkout = path.join(workspace, "MAGI");
  await mkdir(workspace, { recursive: true });
  const handle = "@self-loop.magi";
  const gitEnvironment = dugite.setupEnvironment({}).env;
  const env = { ...prependRuntimePath(gitEnvironment, [path.dirname(node)]), MAGI_GIT: git, npm_config_cache: path.join(temporary, "npm-cache") };
  // Node's outer test-runner context is not an application environment. Leaking
  // it into nested node --test can report failures without a failing exit code.
  delete env.NODE_TEST_CONTEXT;
  const commandLog = [], events = [], starts = [], exits = [], processEvents = [], sockets = [], deliveries = [], acknowledgements = [], modelRequests = [], modelFailures = [];
  const owner = createProcessOwner({ spawnProcess: (file, args, options) => {
    const child = spawn(file, args, options);
    if (args[0]?.endsWith("apps/eva/dist/eva.js") || args[0]?.endsWith("apps\\eva\\dist\\eva.js")) {
      starts.push(child.pid); processEvents.push(["spawn", child.pid]);
      child.once("exit", () => { exits.push(child.pid); processEvents.push(["exit", child.pid]); });
      child.on("message", (message) => { if (message?.type === "stopping") stoppingSeen = true; });
    }
    return child;
  } });
  const command = async (file, args, options) => {
    commandLog.push({ file, args, cwd: options.cwd });
    return await owner.command(file, args, { ...options, env: options.env ?? env });
  };
  let releaseDrain = null, heldDrain = false, successfulRequest = false, stoppingSeen = false;
  let server, websocket, supervisor;
  t.after(async () => {
    releaseDrain?.();
    if (supervisor) await supervisor.close(); else await owner.close();
    for (const socket of sockets) socket.terminate();
    websocket?.close(); server?.close(); server?.closeAllConnections();
    await rm(temporary, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  if (process.platform === "win32") {
    assert.match(await command("powershell.exe", ["-NoProfile", "-Command", "Write-Output 'lifecycle-shell-ready'"], { cwd: repository }), /lifecycle-shell-ready/, "the real child's environment retains the OS shell search path");
  }
  await command(git, ["clone", "--no-local", repository, checkout], { cwd: temporary });
  await command(git, ["checkout", "-B", "magi/self-loop"], { cwd: checkout });
  // Overlay this round's source while running before its commit. CI uses the
  // identical tracked files. No original checkout output is overwritten.
  for (const file of ["apps/eva/eva.ts", "apps/user/user.ts", "apps/user/host.ts", "packages/webapp/api.ts", "packages/webapp/magi-runtime.ts", "packages/webapp/clone.ts", "packages/built-in-tools/rebuild.ts", "packages/manager/worker.ts", "apps/eva/test/agent.test.ts", "apps/eva/test/manager.test.ts", "apps/shell/process.mjs", "apps/shell/process-owner.mjs", "apps/shell/profile-layout.mjs", "apps/shell/clone.mjs", "apps/shell/test/profile-layout.test.mjs", "apps/shell/test/process-table.test.mjs", "apps/shell/installation.mjs", "apps/shell/rebuild.mjs", "apps/shell/dependency-state.mjs", "apps/shell/runtime-environment.mjs", "apps/shell/test/rebuild.test.mjs", "apps/shell/test/runtime-environment.test.mjs", "apps/shell/test/shutdown.test.mjs"]) {
    await cp(path.join(repository, file), path.join(checkout, file));
  }
  await symlink(path.join(repository, "apps/shell/runtime"), path.join(checkout, "apps/shell/runtime"), "junction");
  await command(node, [npm, "ci", "--ignore-scripts"], { cwd: checkout });
  await command(node, [npm, "run", "build"], { cwd: checkout });
  const original = await readFile(path.join(checkout, "packages/agent/checkout.ts"), "utf8");
  const changed = original.replace("This is the only tree you may edit.", "This is the only tree you may edit.\\nloaded-self-version: candidate-v2");
  assert.notEqual(changed, original);
  const originalTools = await readFile(path.join(checkout, "packages/built-in-tools/worker.ts"), "utf8");
  const changedTools = originalTools.replace("...rebuildTools(bus.handle),", `...rebuildTools(bus.handle),
    { name: "self_version_probe", description: "Return the candidate code's square calculation", input_schema: { type: "object", properties: { value: { type: "number" } }, required: ["value"] }, async run(args) { return JSON.stringify({ version: "candidate-v2", result: Number(args.value) ** 2 }); } },`);
  assert.notEqual(changedTools, originalTools);
  const passingTest = `import test from 'node:test'; import assert from 'node:assert/strict'; import path from 'node:path'; import {mkdtemp,rm} from 'node:fs/promises'; import {tmpdir} from 'node:os'; import {Bus} from '@magi/bus'; import {builtinTools} from '@magi/built-in-tools/worker.js'; import {sourceCheckout} from '@magi/agent/checkout.js'; test('candidate source contributes its new behavior',async()=>{assert.match(sourceCheckout('@self-loop.magi',path.dirname(process.cwd())),/candidate-v2/);const workspace=await mkdtemp(path.join(tmpdir(),'candidate-tool-'));const bus=new Bus('@candidate.magi',workspace);try{const tool=builtinTools(bus).find(tool=>tool.name==='self_version_probe');assert.ok(tool);assert.deepEqual(JSON.parse(await tool.run({value:7})),{version:'candidate-v2',result:49});}finally{bus.close();await rm(workspace,{recursive:true,force:true});}});`;
  const quotedNode = `'${node.replaceAll("'", process.platform === "win32" ? "''" : "'\\''")}'`;
  const quotedNpm = `'${npm.replaceAll("'", process.platform === "win32" ? "''" : "'\\''")}'`;
  const candidateTestCommand = process.platform === "win32"
    ? `$env:PATH='${path.dirname(node).replaceAll("'", "''")};'+$env:PATH; & ${quotedNode} ${quotedNpm} --prefix MAGI test; exit $LASTEXITCODE`
    : `PATH='${path.dirname(node).replaceAll("'", "'\\''")}':"$PATH" ${quotedNode} ${quotedNpm} --prefix MAGI test`;
  // The branch/workspace declaration remains in the original source, including
  // its no-checkout refusal. Only a visible behavior marker changes.
  const plans = {
    "reject-managed": [{ name: "request_rebuild", arguments: { target: "self" } }],
    "reject-tests": [{ name: "write_file", arguments: { path: "MAGI/apps/eva/test/self-loop.test.ts", content: "import test from 'node:test'; import assert from 'node:assert/strict'; test('intentional gate failure',()=>assert.fail('candidate rejected by tests'));" } }, { name: "request_rebuild", arguments: { target: "self" } }],
    "reject-compile": [{ name: "write_file", arguments: { path: "MAGI/apps/eva/test/self-loop.test.ts", content: "export {};" } }, { name: "write_file", arguments: { path: "MAGI/packages/agent/checkout.ts", content: "export function invalid(: syntax" } }, { name: "request_rebuild", arguments: { target: "self" } }],
    "activate": [{ name: "write_file", arguments: { path: "MAGI/packages/agent/checkout.ts", content: changed } }, { name: "write_file", arguments: { path: "MAGI/packages/built-in-tools/worker.ts", content: changedTools } }, { name: "write_file", arguments: { path: "MAGI/apps/eva/test/self-loop.test.ts", content: passingTest } }, { name: "bash", arguments: { command: candidateTestCommand, timeout: 600 } }, { name: "request_rebuild", arguments: { target: "self" } }, { name: "request_rebuild", arguments: { target: "self" } }],
  };
  const steps = new Map();
  const probes = new Set();
  function complete(response, delta, finish = "stop") {
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    response.end(`data: ${JSON.stringify({ id: "local-fixture", object: "chat.completion.chunk", created: 1, model: "fixture", choices: [{ index: 0, delta: { role: "assistant", ...delta }, finish_reason: null }] })}\n\ndata: ${JSON.stringify({ id: "local-fixture", object: "chat.completion.chunk", created: 1, model: "fixture", choices: [{ index: 0, delta: {}, finish_reason: finish }] })}\n\ndata: [DONE]\n\n`);
  }
  server = createServer(async (request, response) => {
    try {
      const chunks = []; for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString() || "{}");
      if (request.url.endsWith("/messages")) { deliveries.push(body.content); response.writeHead(200); response.end("{}"); return; }
      if (request.url !== "/v1/chat/completions") { response.writeHead(200); response.end("{}"); return; }
      modelRequests.push(body);
      const text = (content) => typeof content === "string" ? content : Array.isArray(content) ? content.map((part) => part.text ?? "").join("\n") : "";
      const latest = text([...body.messages].reverse().find((message) => message.role === "user")?.content);
      const mode = Object.keys(plans).find((name) => latest.endsWith(name));
      const system = body.messages.filter((message) => ["system", "developer"].includes(message.role)).map((message) => text(message.content)).join("\n");
      assert.match(system, /@self-loop.magi/); assert.match(system, /durable self-loop memory/);
      if (!mode) {
        const hasProbe = body.tools?.some((tool) => tool.function?.name === "self_version_probe");
        if (!hasProbe) { complete(response, { content: "baseline served" }); return; }
        assert.match(system, /candidate-v2/, "the new source module also loaded");
        if (!probes.has(latest)) {
          probes.add(latest);
          complete(response, { tool_calls: [{ index: 0, id: `probe-${modelRequests.length}`, type: "function", function: { name: "self_version_probe", arguments: '{"value":7}' } }] }, "tool_calls");
        } else {
          const result = [...body.messages].reverse().find((message) => message.role === "tool");
          assert.deepEqual(JSON.parse(text(result?.content)), { version: "candidate-v2", result: 49 }, "the real new BUS tool executed candidate arithmetic code");
          complete(response, { content: "candidate-v2 served" });
        }
        return;
      }
      const step = steps.get(mode) ?? 0; steps.set(mode, step + 1);
      const tool = plans[mode][step];
      if (tool) {
        complete(response, { tool_calls: [{ index: 0, id: `${mode}-${step}`, type: "function", function: { name: tool.name, arguments: JSON.stringify(tool.arguments) } }] }, "tool_calls");
      } else if (mode === "activate") {
        const outcomes = body.messages.filter((message) => message.role === "tool");
        assert.ok(outcomes.some((message) => text(message.content).includes("exit_code: 0")), "EVA actually ran candidate tests");
        assert.ok(outcomes.some((message) => text(message.content).includes("Shell accepted")));
        assert.ok(outcomes.some((message) => text(message.content).includes("still running")), "duplicate request was rejected");
        heldDrain = true; releaseDrain = () => { releaseDrain = null; complete(response, { content: "activation caller drained" }); };
      } else {
        if (mode === "reject-managed") assert.ok(body.messages.some((message) => message.role === "tool" && text(message.content).includes("not managed")));
        complete(response, { content: `${mode} proposal submitted` });
      }
    } catch (error) { modelFailures.push(String(error)); response.writeHead(500); response.end(String(error)); }
  });
  websocket = new WebSocketServer({ server, path: "/connect" });
  websocket.on("connection", (socket) => { sockets.push(socket); socket.on("message", (message) => acknowledgements.push(JSON.parse(String(message)))); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const persisted = new Bus(handle, workspace);
  persisted.settings.setMany({ "provider.name": "custom", "provider.model": "fixture", "provider.api_key": "local-fixture-only", "provider.base_url": `${base}/v1`, "self-loop.state": "preserved" });
  persisted.memoryBook.save({ topic: "restart evidence", detail: "durable self-loop memory", kind: "long_term" });
  persisted.contacts.update(MAGI_CONTACT_ID, { nickname: "durable nickname" }); persisted.close();
  supervisor = createSupervisor({ node, env, owner, readyTimeoutMs: 30_000, stopTimeoutMs: 30_000 });
  let managed = true;
  const rebuild = createAppRebuilder({ supervisor, tools: { node, npm, git, env }, command, managed: () => managed, pause: () => sleep(100), emit: (event) => { events.push(event); console.log(`lifecycle: ${event.phase}${event.failedPhase ? ` (${event.failedPhase})` : ""}`); if (event.phase === "restarting") successfulRequest = true; }, onUser: async () => { throw new Error("User must not be replaced"); } });
  const child = await supervisor.start({ entry: path.join(checkout, "apps/eva/dist/eva.js"), cwd: path.join(checkout, "apps/eva"), args: [handle, base, "local-asp-only", "--workspace", workspace] });
  await child.whenReady(); await until(() => sockets.length === 1, "initial ASP connection missing");
  let sequence = 0;
  const send = (content) => { const n = ++sequence; sockets.at(-1).send(JSON.stringify({ type: "chat.message", chat_id: "self-loop", event_id: `event-${n}`, sequence: n, payload: { sender: "@user.magi", content, mentions: [handle] } })); return n; };
  send("baseline"); await until(() => deliveries.includes("baseline served"), "baseline behavior was not served");
  managed = false;
  const commandsBeforeDenied = commandLog.length;
  send("reject-managed"); await until(() => deliveries.includes("reject-managed proposal submitted"), "unmanaged request did not return its denial through the real tool channel");
  assert.equal(commandLog.length, commandsBeforeDenied); assert.equal(starts.length, 1);
  managed = true;
  for (const mode of ["reject-tests", "reject-compile"]) {
    const failures = events.filter((event) => event.phase === "failed").length;
    send(mode);
    await until(() => events.filter((event) => event.phase === "failed").length > failures, `${mode} did not fail validation`);
    assert.equal(events.at(-1).failedPhase, "testing");
    assert.match(events.at(-1).message, mode === "reject-tests" ? /candidate rejected by tests/ : /TS1138|TS1005/, "the expected real candidate validation failure was observed");
    assert.equal(starts.length, 1); assert.deepEqual(exits, []);
    send(`baseline ${mode} proof`); await until(() => deliveries.filter((value) => value === "baseline served").length >= (mode === "reject-tests" ? 2 : 3), "old loaded behavior did not survive failed candidate");
  }
  const activationStart = events.length;
  send("activate");
  await until(() => {
    if (modelFailures.length) throw new Error(`Model fixture assertion failed: ${modelFailures.join("; ")}`);
    const rejected = events.slice(activationStart).find((event) => event.phase === "failed");
    if (rejected) throw new Error(`Valid candidate activation was rejected: ${rejected.message}`);
    return successfulRequest && heldDrain && stoppingSeen;
  }, "Shell never drained the real caller (see fixture activation flags)").catch((error) => { throw new Error(`${error.message}: ${JSON.stringify({ successfulRequest, heldDrain, stoppingSeen, starts, exits, activationRequests: modelRequests.filter((body) => JSON.stringify(body.messages).includes("activate")).length, modelFailures })}`, { cause: error }); });
  const pendingEvent = send("pending during retirement");
  await until(() => acknowledgements.some((ack) => ack.event_id === `event-${pendingEvent}`), "pending message was not durably ingested");
  const jobs = new Database(path.join(workspace, "logs/jobs.db"), { readonly: true });
  const pending = jobs.prepare("SELECT id,status FROM jobs WHERE type='MessageDeliveryJob' AND publisher='asp' ORDER BY id DESC LIMIT 1").get();
  jobs.close(); assert.equal(pending.status, "pending", "message remained unfinished while the old agent drained");
  releaseDrain();
  await until(() => rebuild.status()[0]?.phase === "ready", "replacement never became ready");
  await until(() => sockets.length === 2 && deliveries.includes("candidate-v2 served"), "replacement did not reconnect and process pending work");
  assert.equal(starts.length, 2); assert.notEqual(starts[0], starts[1]); assert.deepEqual(exits, [starts[0]]);
  assert.deepEqual(processEvents, [["spawn", starts[0]], ["exit", starts[0]], ["spawn", starts[1]]], "old process exited before its replacement spawned");
  assert.equal(supervisor.snapshots().length, 1); assert.equal(supervisor.snapshots()[0].id, child.id);
  // Replay an acknowledged event: durable ASP read position must avoid a duplicate.
  sockets.at(-1).send(JSON.stringify({ type: "chat.message", chat_id: "self-loop", event_id: `event-${pendingEvent}`, sequence: pendingEvent, payload: { sender: "@user.magi", content: "pending during retirement" } }));
  send("after replacement"); await until(() => deliveries.filter((text) => text === "candidate-v2 served").length === 2, "new behavior did not serve follow-up work");
  supervisor.stopAll(); await until(() => supervisor.snapshots().length === 0, "replacement did not stop");
  const restored = new Bus(handle, workspace);
  assert.equal(restored.settings.get("self-loop.state"), "preserved"); assert.equal(restored.contacts.get(MAGI_CONTACT_ID).nickname, "durable nickname");
  assert.equal(restored.board("MessageDeliveryJob").result(pending.id).status, "completed");
  assert.equal(restored.chats.read("asp", "self-loop"), sequence); restored.close();
  assert.ok(commandLog.some((call) => call.args[1] === "test" && call.cwd === checkout));
  assert.ok(commandLog.some((call) => call.args[1] === "run" && call.args[2] === "build" && call.cwd === checkout));
  assert.equal(commandLog.filter((call) => call.args[1] === "ci").length, 1, "only the initial preparation installs dependencies; live rebuilds preserve native modules");
  t.diagnostic(JSON.stringify({ profile: "independent magi/self-loop clone", simulated: "loopback OpenAI SSE responses and ASP HTTP/WebSocket", real: "EVA CLI entry, BUS, file/bash/rebuild tools, own clone npm tests/compilation, private Shell IPC, supervisor child replacement and reconnection", starts, processEvents, pendingJob: pending.id, phases: events.map((event) => event.phase), modelCalls: modelRequests.length, unverified: "Electron window/OS deep-link, crash rollback and improvement quality" }));
});
