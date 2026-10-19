/** Explicit owner-chat lifecycle. A contact entry never grants process ownership. */
import { setTimeout as sleep } from "node:timers/promises";
import { createServer } from "node:net";
import { mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { createHash, createPublicKey, randomUUID } from "node:crypto";
import { discoverPeer, PeerClient } from "@magi/channel-peer/client.js";
import { loadOrCreateIdentity, messageDelivery, MAGI_CONTACT_ID, type Bus, type ExecutableTool } from "@magi/bus";

async function freePort(): Promise<number> {
  const server = createServer();
  return await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") return server.close(() => reject(new Error("No local port")));
      server.close(error => error ? reject(error) : resolve(address.port));
    });
  });
}

export function localEvaManagement(bus: Bus, runtime): ExecutableTool[] {
  const inFlight = new Set<string>();
  async function create({ name }: Record<string, unknown>) {
    if (typeof name !== "string" || !/^[a-z0-9][a-z0-9-]{0,62}$/.test(name) || ["shell", "asp", "user"].includes(name)) throw new Error("Choose a unique lowercase EVA name (letters, digits, hyphens)");
    const handle = `@${name}.magi`;
    const workspace = join(dirname(bus.workspace), name);
    if (existsSync(workspace) || inFlight.has(name)) throw new Error("That workspace already exists; nothing was overwritten");
    inFlight.add(name);
    try {
      mkdirSync(workspace, { recursive: true });
      const identity = loadOrCreateIdentity(workspace);
      const principal = createHash("sha256").update(createPublicKey(identity.publicKey).export({ type: "spki", format: "der" })).digest("hex");
      const port = await freePort();
      const url = `http://127.0.0.1:${port}`;
      try {
        await runtime.startLocal({ handle, local: true, port, url });
        let discovered: { principal?: string; publicKey?: string; public_key?: string; handle?: string } | undefined;
        const deadline = Date.now() + 30_000;
        while (Date.now() < deadline) {
          if (!runtime.running().includes(handle)) throw new Error("New EVA exited before peer readiness");
          try {
            const response = await fetch(`${url}/.well-known/magi-peer.json`, { signal: AbortSignal.timeout(1_000) });
            if (response.ok) { discovered = await response.json(); break; }
          } catch { /* Bind and startup are asynchronous. */ }
          await sleep(100);
        }
        if (!discovered) throw new Error("New EVA did not become ready");
        const publicKey = discovered.publicKey ?? discovered.public_key;
        if (!publicKey || createHash("sha256").update(createPublicKey(publicKey).export({ type: "spki", format: "der" })).digest("hex") !== principal) throw new Error("New EVA identity does not match its workspace");
        const contact = bus.contacts.forPeer({ principal, endpoint: url, name: handle });
        bus.contacts.acceptPeer(contact.id, url);
        bus.settings.set(`local.eva.${contact.id}`, JSON.stringify({ handle, port, url }));
        return JSON.stringify({ contact_id: contact.id, handle, url, ready: true });
      } catch (error) {
        await runtime.stopLocal(handle).catch(() => runtime.stop(handle));
        // Keep failed workspace and its credentials for inspection; never erase user state.
        throw error;
      }
    } finally { inFlight.delete(name); }
  }
  async function lifecycle({ contact_id, action }: Record<string, unknown>) {
    const stored = bus.settings.get(`local.eva.${Number(contact_id)}`);
    if (!stored) throw new Error("This contact is not a locally provisioned EVA");
    const local = JSON.parse(stored);
    if (action === "stop") return JSON.stringify({ stopped: await runtime.stopLocal(local.handle) });
    if (action === "start") return JSON.stringify(await runtime.startLocal({ ...local, local: true }));
    throw new Error("Action must be start or stop");
  }
  async function connect({ url }: Record<string, unknown>) {
    if (typeof url !== "string") throw new Error("A peer URL is required");
    const ownURL = bus.settings.get("peer.reachableURL");
    if (!ownURL) throw new Error("Shell peer server is not ready");
    const discovered = await discoverPeer(url);
    if (discovered.principal === bus.settings.get("peer.principal")) throw new Error("This is the Shell EVA itself");
    const contact = bus.contacts.forPeer({ principal: discovered.principal, endpoint: url, name: discovered.handle });
    if (contact.peer_trust === "blocked") throw new Error("This peer is blocked");
    const conversation = bus.chats.forPeer(discovered.principal);
    const hello = `Hello from ${bus.handle}. My EVA URL is ${ownURL}. Please decide whether to accept my contact using your own contact tool.`;
    await new PeerClient(bus.identity).send(url, discovered.principal, {
      messageId: randomUUID(), conversationUUID: conversation.conversation_uuid!,
      content: hello,
      reachableURL: ownURL, handle: bus.handle,
    });
    messageDelivery.record(bus, conversation.id, hello, MAGI_CONTACT_ID);
    bus.contacts.acceptPeer(contact.id, url);
    return JSON.stringify({ contact_id: contact.id, handle: contact.name, url, hello_sent: true, remote_acceptance: "pending" });
  }
  return [
    { name: "connect_peer", description: "Owner requested a remote EVA URL: discover its pinned identity, send a signed hello containing this EVA URL, and add the local contact. The remote EVA independently decides whether to accept.", input_schema: { type: "object", properties: { url: { type: "string" } }, required: ["url"], additionalProperties: false }, run: connect },
    { name: "create_local_eva", description: "Create and start a separate local EVA workspace and direct peer server, then add its contact. Owner chat only.", input_schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"], additionalProperties: false }, run: create },
    { name: "local_eva_lifecycle", description: "Start or stop an explicitly provisioned local EVA. Remote contacts never grant process control. Owner chat only.", input_schema: { type: "object", properties: { contact_id: { type: "integer" }, action: { enum: ["start", "stop"] } }, required: ["contact_id", "action"], additionalProperties: false }, run: lifecycle },
  ];
}
