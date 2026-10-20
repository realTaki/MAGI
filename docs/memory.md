# Shared memory maintenance

`MemoryWorker` owns the memory tool catalog, live prompt context, and execution
of memory `RunToolJob` calls. Both foreground conversations and the private
subconscious use this same BUS path and the same `MemoryBook`. The subconscious
can use `list_memory`, `save_memory`, and `complete_memory`, but not permanent
`delete_memory`. Stopping MemoryWorker removes its tools and context, prevents
new claims, and drains calls it has already accepted.

Memory stores supported facts, preferences, and reusable context. Open commitments
belong in action items, and timed work belongs in tasks. Before creating a memory,
read existing entries, including archived entries when reconciling, and update a
matching fact instead of duplicating it. Put relevant sources and uncertainty in
the detail. A remembered quotation or observation is evidence, not fresh authority
to act. Archive obsolete facts; do not revive them without current evidence.
Credentials and secrets do not belong in memory.

## Versioned tool contract

- `list_memory({ include_archived?: boolean })` returns `{ memories }`. Each
  memory includes `id`, `topic`, `detail`, `kind`, `archived`, `created_at`, and
  its positive integer `version`.
- `save_memory` without `memory_id` creates a memory and requires nonempty
  `topic` and `detail`. Optional fields are `kind` (`temporary`, `short_term`,
  or `long_term`) and `archived`. Creation must not supply `expected_version`.
- Updating with `save_memory` requires `memory_id` and `expected_version`,
  followed by the fields to change. Omitted fields stay unchanged.
- `complete_memory({ memory_id, expected_version })` archives the memory.
- Successful updates and archives return `{ memory }` with an incremented
  version. A stale version fails without changing the record. Read the new
  state and reconcile the evidence before retrying.

Migration `0020_memory_versions` preserves existing rows, IDs, timestamps and
archive state, and initializes their version to 1. New entries also start at 1.
The migration makes IDs non-recycling, so deleting a fact and creating another
cannot cause a stale ID/version pair to refer to the new fact.
Creation remains compatible with the previous tool contract. Existing callers
that update or archive must now supply the version from `list_memory` (or
`MemoryBook.get/list`); the book API uses `expectedVersion`. Omitting the version
fails explicitly rather than falling back to last-write-wins. Invalid supplied
IDs, kinds, or field types fail rather than becoming omitted values or creates.
The legacy foreground-only permanent-delete contract is unchanged.

Live module guidance and versioned active entries appear in each new turn's
context even when its agent prompt was customized. The implementation does not
overwrite customized prompt files, introduce another scheduler, or change the
private conversation's external-communication boundary.
