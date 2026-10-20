export { Bus } from "./bus.js";
export {
  USER_HANDLE,
  SHELL_MAGI_HANDLE,
  SHELL_MAGI_NICKNAME,
  ASP_MAGI_HANDLE,
  ASP_MAGI_NICKNAME,
  EVA_MAGI_HANDLE,
  EVA_MAGI_NICKNAME,
  magiLocalName,
  magiHandle,
  magiNodeName,
  sequentialMagiHandle,
  nextMagiName,
  isInProcessMagiHandle,
} from "./identity.js";
export { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, validatePeerEndpoint, validatePeerPrincipal } from "./books/contactBook.js";
export { BaseWorker } from "./baseWorker.js";
export type { Chat } from "./books/chatBook.js";
export type { Message } from "./books/messageBook.js";
export type { Memory, MemoryKind } from "./books/memoryBook.js";
export type { Skill } from "./books/skillsBook.js";
export type { Task } from "./books/taskBook.js";
export type { Contact, ContactRole } from "./books/contactBook.js";
export type { ContactNote, NoteKind } from "./books/contactNoteBook.js";
export type { McpConnectionType, McpServerConfig } from "./books/mcpServerBook.js";
export type { PromptSection, PromptSource } from "./books/promptBook.js";
export type { Setting } from "./books/settingsBook.js";
export type { ExecutableTool, LLMTool, ToolRunContext, ToolSource } from "./books/toolBook.js";
// One file per job; `types.js` is only the board contract that binds them.
export type { MessageDeliveryJob } from "./jobs/messageDelivery.js";
export type { CallLLMJob, CallLLMResult, LLMMessage, LLMRequest } from "./jobs/callLlm.js";
export type { AgentTurnContext } from "./jobs/agentTurnCache.js";
export type { RunToolJob, RunToolResult, LLMToolCall } from "./jobs/runTool.js";
export type { ChangeProviderNotify } from "./jobs/changeProvider.js";
export type { ChangeMcpServerNotify } from "./jobs/changeMcpServer.js";
export type { ManageWorkerNotify } from "./jobs/manageWorker.js";
export type { ConfigureJob, ConfigureResult } from "./jobs/configure.js";
export { providerCard, PROVIDER_CHOICES, CARD_RESTART_ERROR } from "./jobs/card.js";
export { replaceFailedPath } from "./replaceMarker.js";
export type { CardField, CardJob, CardResult, CardSpec } from "./jobs/card.js";
export type { ReplaceSelfJob, ReplaceSelfResult } from "./jobs/replaceSelf.js";
export type { AspCommandJob, AspCommandResult } from "./jobs/aspCommand.js";
export type { InboundSecretJob, InboundSecretResult } from "./jobs/inboundSecret.js";
export type { Job, JobResult } from "./jobs/jobBoard.js";
export { waitForJob } from "./jobs/wait.js";
export { encryptFor, decryptWith, loadOrCreateIdentity } from "./identityKeys.js";
export type { Identity } from "./identityKeys.js";
// The message job also exports how its text is recorded and delivered.
export { messageDelivery } from "./jobs/messageDelivery.js";

export { chat } from "./chat.js";
export type { ChatInput, ChatDelivery, ChatReceipt, CardSubmission, ProviderEnvelope } from "./chat.js";

export type { PeerInbound, PeerReceipt } from "./jobs/messageDelivery.js";

export { groupPrincipal } from "./books/groupBook.js";
export type { Group, GroupMember, GroupMembership, GroupEvent, GroupMembershipEnvelope, GroupEventEnvelope, GroupReceipt } from "./books/groupBook.js";
