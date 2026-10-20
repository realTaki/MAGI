import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Bus, chat } from "@magi/bus";
import { ConfigWorker } from "@magi/launcher/config.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import type { OAuthAuth } from "@magi/providers/oauth.js";
import { CODEX_VERIFICATION_URI } from "@magi/providers/oauth.js";
const attempt = "owner-attempt-12345";
function fixture() { const dir=mkdtempSync(join(tmpdir(),"magi-oauth-")); const bus=new Bus("@oauth-test.magi",dir); return {bus,dir,close(){bus.close();rmSync(dir,{recursive:true,force:true});}}; }
const driver: OAuthAuth = { name:"mock", async login(i) {
  assert.equal(await i.prompt({type:"select",message:"login",options:[{id:"device_code",label:"Device"}]}),"device_code");
  i.notify({type:"device_code",verificationUri:CODEX_VERIFICATION_URI,userCode:"TEST-1234",expiresInSeconds:600});
  return {type:"oauth",access:"fake-access-DO-NOT-LOG",refresh:"fake-refresh-DO-NOT-LOG",expires:Date.now()+120000};
}, async refresh(c){return c;}, async toAuth(c){return {apiKey:c.access};} };
test("only authenticated private owner chat can initiate OAuth; peer and groups denied",()=>{
 const f=fixture();try{
  const owner=f.bus.chats.forChannel("local-owner","owner");
  const input={content:"ignored",metadata:{provider_oauth:{action:"start" as const,attempt,model:"gpt-6-sol"}}};
  const result=chat(f.bus,owner.id,f.bus.userContactId,input); assert.equal(result.type,"InboundSecretJob");
  const peer=f.bus.contacts.forPeer({principal:"a".repeat(64),endpoint:"https://peer.example"});
  assert.throws(()=>chat(f.bus,owner.id,peer.id,input),/owner/);
  const remote=f.bus.chats.forPeer("a".repeat(64)); assert.throws(()=>chat(f.bus,remote.id,f.bus.userContactId,input),/owner/);
  assert.throws(()=>chat(f.bus,owner.id,f.bus.userContactId,{...input,metadata:{...input.metadata,card_reject:{id:"other"}}}),/invalid/);
  assert.equal(f.bus.messages.list(owner.id,50).at(-1)?.content,"provider connection requested");
 }finally{f.close();}
});
test("OAuth config verifies before activation, stores tokens only in secrets, and replay does not log in again",async()=>{
 const f=fixture();let logins=0;let configured=false;
 const config=new ConfigWorker(f.bus,{...driver,async login(i){logins++;return driver.login(i);}});
 const providers=new ProvidersWorker(f.bus,{async complete(){return {role:"assistant",content:"OK"};},async verify(s){assert.ok(s.oauth_ref?.startsWith("provider.oauth.codex."));assert.equal(s.provider,"openai-codex");},configure(){configured=true;}});
 try{
  const owner=f.bus.chats.forChannel("local-owner","owner");await providers.start();
  const input={content:"ignored",metadata:{provider_oauth:{action:"start" as const,attempt,model:"gpt-6-sol"}}};
  chat(f.bus,owner.id,f.bus.userContactId,input);
  for(let i=0;i<60 && !configured;i++){await config.poll();void providers.poll();await new Promise(r=>setTimeout(r,5));}
  assert.ok(configured);const ref=f.bus.settings.get("provider.oauth_ref");assert.ok(ref);assert.ok(f.bus.secrets.get(ref)?.includes("fake-access"));
  await config.poll();await new Promise(r=>setTimeout(r,30));
  chat(f.bus,owner.id,f.bus.userContactId,input);await config.poll();await new Promise(r=>setTimeout(r,5));assert.equal(logins,1);
  const all=JSON.stringify(f.bus.messages.list(owner.id,200));assert.ok(!all.includes("DO-NOT-LOG"));
  // SQLite jobs and settings must never contain the credential strings.
  for(const file of readdirSync(join(f.dir,"logs"))){const raw=readFileSync(join(f.dir,"logs",file));assert.ok(!raw.includes(Buffer.from("DO-NOT-LOG")),file);}
 }finally{config.interrupt();await config.stop();providers.interrupt();await providers.stop();f.close();}
});
test("failed OAuth verification preserves the active API-key provider and removes candidate tokens",async()=>{
 const f=fixture();f.bus.settings.set("provider.name","custom");f.bus.settings.set("provider.model","old-model");f.bus.secrets.set("provider.api_key","existing-key");
 const config=new ConfigWorker(f.bus,driver);
 const providers=new ProvidersWorker(f.bus,{async complete(){return {role:"assistant",content:"OK"};},async verify(){throw new Error("rejected");},configure(){}});
 try{
  const owner=f.bus.chats.forChannel("local-owner","owner");await providers.start();
  chat(f.bus,owner.id,f.bus.userContactId,{content:"",metadata:{provider_oauth:{action:"start",attempt,model:"gpt-6-sol"}}});
  for(let i=0;i<60;i++){await config.poll();void providers.poll();await new Promise(r=>setTimeout(r,5));}
  assert.equal(f.bus.settings.get("provider.name"),"custom");assert.equal(f.bus.settings.get("provider.model"),"old-model");assert.equal(f.bus.secrets.get("provider.api_key"),"existing-key");
  assert.equal(f.bus.settings.get("provider.oauth_ref"),null);assert.deepEqual(f.bus.secrets.names(),["provider.api_key"]);
 }finally{config.interrupt();await config.stop();providers.interrupt();await providers.stop();f.close();}
});
test("restart cannot replay a consumed device-login attempt",async()=>{
 const f=fixture();let logins=0;
 const owner=f.bus.chats.forChannel("local-owner","owner");
 const binding={instance:f.bus.identity.publicKey,owner:f.bus.userContactId,chat:owner.id,attempt};
 f.bus.settings.set(`provider.oauth.attempt.${attempt}`,JSON.stringify({binding,update:{attempt,instance:binding.instance,state:"pending"}}));
 const config=new ConfigWorker(f.bus,{...driver,async login(i){logins++;return driver.login(i);}});
 try{
  chat(f.bus,owner.id,f.bus.userContactId,{content:"",metadata:{provider_oauth:{action:"start",attempt,model:"gpt-6-sol"}}});
  await config.poll();await new Promise(r=>setTimeout(r,10));
  assert.equal(logins,0);assert.ok(f.bus.messages.list(owner.id,20).some(m=>m.content==="Provider connection: cancelled"));
 }finally{await config.stop();f.close();}
});
test("paused node refuses OAuth before login and stop during grant prevents activation", async () => {
 for (const stoppedDuringGrant of [false, true]) {
  const f = fixture(); let logins = 0;
  if (!stoppedDuringGrant) f.bus.settings.set("node.enabled", "false");
  const config = new ConfigWorker(f.bus, { ...driver, async login(i) {
    logins++; const value = await driver.login(i);
    f.bus.settings.set("node.enabled", "false"); return value;
  } });
  try {
   const owner = f.bus.chats.forChannel("local-owner", "owner");
   chat(f.bus, owner.id, f.bus.userContactId, { content: "", metadata: { provider_oauth: { action: "start", attempt, model: "gpt-6-sol" } } });
   await config.poll(); await new Promise(r => setTimeout(r, 20));
   assert.equal(logins, stoppedDuringGrant ? 1 : 0);
   assert.equal(f.bus.settings.get("provider.oauth_ref"), null);
   assert.deepEqual(f.bus.secrets.names(), []);
   assert.ok(f.bus.messages.list(owner.id, 30).some(m => m.content === "Provider connection: failed"));
  } finally { await config.stop(); f.close(); }
 }
});
