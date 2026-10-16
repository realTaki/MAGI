/**
 * Work only the ASP channel can do: open a chat with the operator, or send
 * this MAGI's provider to the others. The tool publishes the job and does not
 * call the relay itself.
 */

export type AspCommandJob = { name: "open_owner_chat" } | { name: "sync_provider" };

export type AspCommandResult = { chat_id: number } | { synced: string[] };
