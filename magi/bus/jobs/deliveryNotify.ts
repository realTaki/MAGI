/** Send this text back out on the conversation's channel. */

export type DeliveryNotify = {
  conversation_id: number;
  text: string;
  channel?: string;
  address?: string;
};
