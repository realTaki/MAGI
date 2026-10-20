/** Retain correlation while delivery/events are delayed or uncertain. Only an
 * authenticated terminal event permits replacing a submitted attempt. */
export class CodexAttempt {
  id?: string;
  pending = false;
  begin(id: string): boolean {
    if (this.pending) return false;
    this.id = id; this.pending = true; return true;
  }
  observe(id: string, state: string): boolean {
    if (this.id && (this.id !== id || !this.pending)) return false;
    if (!["pending", "verifying", "saved", "cancelled", "failed"].includes(state)) return false;
    this.id = id; this.pending = state === "pending" || state === "verifying";
    return true;
  }
}
