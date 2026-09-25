/** "Someone said something" — the job that opens one agent turn. */

export type ChatNotify = {
  text: string;
  contact_id?: number;
  chat_id?: number;
  channel?: string;
  delivery_address?: string;
};
