/** Tool authority is derived from durable triggering messages, never model arguments.
 * These checks run at job claim and at privileged workers before invoking any tool.
 */
import type { Bus } from "./bus.js";
import type { RunToolJob } from "./jobs/runTool.js";
import { SYSTEM_CONTACT_ID } from "./books/contactBook.js";

const OWNER_ONLY = new Set([
  "provider_settings", "sync_provider", "mcp_server", "request_replace", "set_home_chat", "show_card",
  "set_self_profile", "set_peer_contact_policy", "set_peer_tool_policy", "block_peer_contact",
  "accept_group_invitation", "create_group", "update_group_members", "create_eva", "stop_eva", "restart_eva",
  "connect_peer", "create_local_eva", "local_eva_lifecycle", "manage_workers", "search_contact_messages", "schedule_task",
]);
const PRIVILEGED_WITHOUT_CONTEXT = new Set(["bash", "bash_output", "bash_kill", "write_file", "edit_file", "read_file", "list_files", "search_files"]);
export function peerToolPolicyKey(principal: string): string { return `peer.tools.${principal}`; }
export function ownerOnlyTool(name: string): boolean {
  return OWNER_ONLY.has(name) || /^(?:configure_|manage_|provision_|create_eva|stop_eva|restart_eva|remove_eva|delete_eva)/.test(name);
}

export function authorizeToolCall(bus: Bus, input: RunToolJob): void {
  const { name, arguments: args } = input.call;
  const message = input.message_id === undefined ? undefined : bus.messages.get(input.message_id);
  const target = input.chat_id === undefined ? null : bus.chats.get(input.chat_id);
  if (input.message_id !== undefined && (!message || message.chat_id !== input.chat_id)) throw new Error("tool denied: invalid triggering message provenance");
  const contact = message ? bus.contacts.get(message.contact_id) : null;
  const peerChat = target?.channel === "peer" || target?.channel === "group";
  const owner = message?.contact_id === SYSTEM_CONTACT_ID && !peerChat;
  if (owner) return;
  if (ownerOnlyTool(name)) throw new Error(`tool denied: ${name} requires an owner-authored triggering message`);
  if (!peerChat && !contact?.peer_id) {
    // Internal/legacy ordinary channels retain their existing tool behavior. Missing
    // provenance can never grant process, filesystem, MCP, or administration rights.
    if (!message && (PRIVILEGED_WITHOUT_CONTEXT.has(name) || name.includes("__"))) throw new Error("tool denied: privileged call requires triggering message provenance");
    return;
  }
  if (!message || !target || !contact?.peer_principal || !contact.peer_id || contact.peer_principal !== contact.peer_id) throw new Error("tool denied: authenticated peer provenance missing");
  const journal = bus.deliveries.get(message.id);
  const expected = target.channel === "peer" ? `peer:${contact.peer_id}:` : `group:${target.delivery_address}:${contact.peer_id}:`;
  if (!journal?.delivery_key.startsWith(expected) || (target.channel === "peer" && target.peer_id !== contact.peer_id)) throw new Error("tool denied: peer delivery provenance mismatch");
  if (contact.peer_trust === "blocked") throw new Error("tool denied: peer is blocked");
  if (contact.peer_trust === "pending") {
    if (name === "accept_peer_contact" && target.channel === "peer" && args.contact_id === contact.id && args.endpoint === undefined && bus.settings.get("peer.contact_policy") === "agent-message-only") return;
    throw new Error("tool denied: pending peer may only request policy-gated hello acceptance");
  }
  if (target.channel === "group" && !bus.groups.allows(target.delivery_address, contact.peer_id)) throw new Error("tool denied: sender is no longer a current group member");
  // Ordinary accepted contacts may inspect only the conversation they are in.
  if (name === "search_chat_messages" && args.chat_id === target.id) return;
  // These tools carry destinations. Even an exact tool grant must not cross chats.
  if ((name === "search_chat_messages" || name === "send_message") && args.chat_id !== target.id) throw new Error("tool denied: peer cannot access another conversation");
  let allowed: unknown;
  try { allowed = JSON.parse(bus.settings.get(peerToolPolicyKey(contact.peer_id)) ?? "[]"); } catch { allowed = []; }
  if (!Array.isArray(allowed) || !allowed.every(item => typeof item === "string") || !allowed.includes(name)) throw new Error(`tool denied: owner has not delegated ${name} to this peer`);
}
