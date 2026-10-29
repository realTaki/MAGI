import type { Bus } from "@magi/bus";

/** Deterministic local setup, independent of whether an LLM is configured yet. */
export function seedSubconscious(bus: Bus, now = new Date()): void {
  const chat = bus.chats.forChannel("subconscious", "self");
  bus.actionItems.seedOnce("onboarding.provider.v1", "Onboarding: Check whether a model provider is configured. If not, leave setup pending for the next foreground conversation and direct the user to MAGI provider settings. Never request or store API keys in chat or action notes.");
  bus.actionItems.seedOnce("onboarding.preferences.v1", "Onboarding: Learn the user's priorities, timezone, and preferred level of proactive help from existing conversation. Keep unknown details pending; the foreground conversation may ask naturally. Do not invent preferences or repeatedly ask completed questions.");
  bus.actionItems.seedOnce("onboarding.first-task.v1", "Onboarding: Help the user choose one concrete first goal for MAGI and track its next step. Review existing action items first, avoid duplicate commitments, and let the user edit or dismiss onboarding notes.");
  bus.tasks.seedOnce("review.v1", {
    name: "Subconscious review", cron: "17 * * * *", chat_id: chat.id,
    prompt: "Privately review shared todo, idea, and goal action items and recent conversation for unfinished commitments, omissions in each category, and onboarding progress. Assess todo urgency and importance from evidence; refine or archive unprioritized ideas without turning them into commitments. Update goal background, success criteria, current evidence, and remaining gaps; a completed todo does not by itself complete or archive its related goal. Repair or consolidate notes through the shared action-item tools when supported by evidence; retain concrete next steps and archive completed or withdrawn work without silently deleting history. Take at most one bounded useful internal step. Do not directly send external messages, create external work, invent commitments, or repeatedly reschedule yourself. Use handoff_to_foreground only for a concrete finding worth attention; the foreground decides whether to speak. With nothing useful to do, finish quietly. Keep this review low traffic; this hourly task already provides the next wake.",
  }, now);
}
