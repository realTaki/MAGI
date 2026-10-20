import { test } from "node:test";
import assert from "node:assert/strict";
import { CodexOAuthVault, CodexDeviceSessions, CODEX_VERIFICATION_URI, type OAuthUpdate } from "../oauth.js";
import type { OAuthAuth, OAuthCredential, ProviderAuthInteraction } from "@earendil-works/pi-ai";
const token = (): OAuthCredential => ({ type: "oauth", access: "mock-access-SECRET", refresh: "mock-refresh-SECRET", expires: Date.now() + 120_000 });
function fixture(login?: OAuthAuth["login"], refresh?: OAuthAuth["refresh"]) {
  const data = new Map<string, string>();
  const driver: OAuthAuth = { name: "mock", login: login ?? (async interaction => {
    assert.equal(await interaction.prompt({ type: "select", message: "pick", options: [{ id: "device_code", label: "device" }] }), "device_code");
    interaction.notify({ type: "device_code", verificationUri: CODEX_VERIFICATION_URI, userCode: "ABCD-1234", expiresInSeconds: 900 });
    return token();
  }), refresh: refresh ?? (async () => token()), toAuth: async c => ({ apiKey: c.access }) };
  return { data, vault: new CodexOAuthVault({ get: k => data.get(k) ?? null, set: (k,v) => { if(v) data.set(k,v); else data.delete(k); } }, driver) };
}
const binding = { instance: "instance-A", owner: 1, chat: 10, attempt: "attempt-A-12345" };
test("device login returns only safe fields; activation receives an opaque staged ref", async () => {
  const f = fixture(); const updates: OAuthUpdate[] = []; let active = "";
  const sessions = new CodexDeviceSessions(f.vault, async ref => { active = ref; assert.equal(await f.vault.access(ref), token().access); }, (_, update) => updates.push(update));
  sessions.start(binding, "test-model"); await sessions.settled();
  assert.equal(updates.at(-1)?.state, "saved"); assert.ok(f.data.has(active));
  assert.ok(updates.some(u => u.device?.verification_uri === CODEX_VERIFICATION_URI));
  assert.ok(!JSON.stringify(updates).includes("SECRET"));
  const count = f.data.size; sessions.start(binding, "test-model"); assert.equal(f.data.size, count);
});
test("failure discards candidate and leaves previous provider credential intact", async () => {
  const f = fixture(); const previous = f.vault.stage(token()); const updates: OAuthUpdate[] = [];
  const sessions = new CodexDeviceSessions(f.vault, async () => { throw new Error("mock-access-SECRET"); }, (_, u) => updates.push(u));
  sessions.start(binding, "test-model"); await sessions.settled();
  assert.equal(updates.at(-1)?.state, "failed"); assert.deepEqual([...f.data.keys()], [previous]); assert.ok(!JSON.stringify(updates).includes("SECRET"));
});
test("attempt owner/chat/instance mismatch cannot cancel; cancellation ignores late credential", async () => {
  let release!: (value: OAuthCredential) => void;
  const f = fixture(() => new Promise(resolve => { release = resolve; })); let activated = false;
  const sessions = new CodexDeviceSessions(f.vault, async () => { activated = true; }, () => {});
  sessions.start(binding, "model");
  for (const changed of [{owner:2}, {chat:11}, {instance:"B"}, {attempt:"other-123"}]) assert.throws(() => sessions.cancel({...binding,...changed}));
  assert.throws(() => sessions.start({...binding,attempt:"another-123"}, "model"), /busy/);
  sessions.cancel(binding); await sessions.settled(); release(token()); await Promise.resolve();
  assert.equal(activated, false); assert.equal(f.data.size,0);
});
test("unsupported prompts/URLs fail closed instead of falling back to browser auth", async () => {
  for (const login of [async (i: ProviderAuthInteraction) => { await i.prompt({type:"manual_code",message:"paste token"}); return token(); },
    async (i: ProviderAuthInteraction) => { i.notify({type:"device_code",userCode:"AAAA",verificationUri:"https://evil.example/"}); return token(); }]) {
    const f=fixture(login); const updates: OAuthUpdate[]=[];
    const sessions=new CodexDeviceSessions(f.vault,async()=>assert.fail("must not activate"),(_,u)=>updates.push(u));
    sessions.start(binding,"model"); await sessions.settled(); assert.equal(updates.at(-1)?.state,"failed"); assert.equal(f.data.size,0);
  }
});
test("refresh is single-flight, persists rotation, and never resurrects discarded sessions", async () => {
  let calls=0; let finish!: (value: OAuthCredential)=>void;
  const f=fixture(undefined, async()=>{calls++; return new Promise(r=>{finish=r;});});
  const ref=f.vault.stage({...token(),expires:0}); const a=f.vault.access(ref); const b=f.vault.access(ref);
  finish({...token(),access:"rotated"}); assert.deepEqual(await Promise.all([a,b]),["rotated","rotated"]); assert.equal(calls,1);
  const ref2=f.vault.stage({...token(),expires:0}); const c=f.vault.access(ref2); f.vault.discard(ref2); finish(token()); await assert.rejects(c,/OAuthError:authentication/); assert.equal(f.data.has(ref2),false);
});
test("timeout terminates pending login without credentials or activation", async () => {
  const f=fixture(()=>new Promise(()=>{})); const updates: OAuthUpdate[]=[];
  const sessions=new CodexDeviceSessions(f.vault,async()=>assert.fail(),(_,u)=>updates.push(u),5);
  sessions.start(binding,"model"); await sessions.settled(); assert.equal(updates.at(-1)?.state,"failed"); assert.equal(f.data.size,0);
});
test("successful refresh rotation survives caller cancellation after response", async () => {
  let finish!: (value: OAuthCredential) => void;
  const f = fixture(undefined, async () => new Promise(resolve => { finish = resolve; }));
  const ref = f.vault.stage({ ...token(), expires: 0 });
  const controller = new AbortController();
  const access = f.vault.access(ref, controller.signal);
  controller.abort();
  const rotated = { ...token(), access: "new-access", refresh: "new-refresh" };
  finish(rotated);
  await assert.rejects(access, /OAuthError:authentication/);
  assert.deepEqual(JSON.parse(f.data.get(ref)!), rotated);
  assert.equal(await f.vault.access(ref), "new-access");
});
test("verification clears active OAuth for both API-key and non-Codex candidates", async () => {
  const { PiAiClient } = await import("../client.js");
  for (const provider of ["openai-codex", "openai"]) {
    const client = new PiAiClient({ provider: "openai-codex", model: "old-model", oauth_ref: "old-ref" });
    let candidate: import("../client.js").ProviderSettings | undefined;
    // Capture the exact settings delivered to the request boundary, without network.
    (client as unknown as { request: (_job: unknown, settings: import("../client.js").ProviderSettings) => Promise<void> }).request = async (_job, settings) => { candidate = settings; };
    await client.verify({ provider, model: "candidate-model", api_key: "new-key" });
    assert.equal(candidate?.oauth_ref, undefined);
    assert.equal(candidate?.api_key, "new-key");
    assert.equal(candidate?.provider, provider);
    await client.complete({ messages: [], tools: [] });
    assert.equal(candidate?.oauth_ref, "old-ref", "verification must not mutate active provider");
  }
});
test("OAuth credential resolution remains the fourth client argument beside rich attachment workspace", async () => {
  const { PiAiClient } = await import("../client.js");
  let called = 0;
  const client = new PiAiClient({ provider: "openai-codex", oauth_ref: "fake-ref" },
    async () => { throw new Error("unexpected network"); }, "/fake/attachment/workspace",
    async ref => { called++; assert.equal(ref, "fake-ref"); throw new Error("fake denied credential"); });
  await assert.rejects(client.complete({ messages: [], tools: [] }), /ProviderError \[authentication\]/);
  assert.equal(called, 1);
});
