/**
 * Shared type for the data collected by the onboarding wizard and
 * consumed by the dashboard. Keep this small — anything we add here
 * becomes part of the App-level "completed onboarding" contract.
 */
export interface OnboardingData {
  bot: { token: string; username: string };
  superAdmins: Array<{ telegramId: string; displayName: string | null }>;
}