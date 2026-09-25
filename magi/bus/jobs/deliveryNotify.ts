/** Send this text back out on the chat's channel. */

export type DeliveryNotify = {
  chat_id: number;
  text: string;
  channel?: string;
  address?: string;
};
