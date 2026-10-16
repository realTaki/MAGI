/**
 * Structured fields that ride out with a message and are not the message text.
 * The channel attaches one when it delivers, then drops it. MessageBook never
 * sees it.
 */
export class EnvelopeOutbox {
  private readonly pending = new Map<number, Record<string, unknown>>();

  attach(messageId: number, envelope: Record<string, unknown>): void {
    this.pending.set(messageId, envelope);
  }

  take(messageId: number): Record<string, unknown> | null {
    const value = this.pending.get(messageId) ?? null;
    this.pending.delete(messageId);
    return value;
  }
}
