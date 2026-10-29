# Bot Memory

Independent native DSH Host bundle, `@magi/memory`. The retired BUS memory worker
has been deleted. Existing BUS databases are preserved, not read or migrated.

## Incremental single-file storage work (not activated)

The next design uses one plain Markdown document per Bot at
`DSH_HOME/memory/MEMORY.md`, with no long/medium/short-term categories.
[document-store.js](<document-store.js>) implements only that storage foundation:
`openMemoryDocument(home)` exposes `read()`, `save({content, expected_revision})`
and literal `render()`. The revision is the SHA-256 of the complete content;
stale writes fail instead of overwriting another Session's committed changes.
An empty string clears the document. Content is limited to 32 KiB of UTF-8, plus
fixed guidance when rendered. Each read observes disk state; there is no cache,
automatic extraction, database import, or classification.

Writes use a cooperative exclusive lock, a private temporary file, file sync,
atomic rename and (on POSIX) directory sync. Readers see an entire old or new
file. Manual editors do not participate in the lock: edits observed before the
save are detected by revision, but a simultaneous external editor can still
race the write. A crash may leave a lock or temporary file; recovery is manual,
never automatic lock stealing. A directory-sync failure after rename can mean
the content changed despite an error; reread before retrying. Symlink/hardlink
files, noncanonical directories, and non-private POSIX permissions are rejected.

This module is **not yet connected to the plugin entry or tools, and is not
shipped in the bundle**. It has only been exercised in temporary test homes;
opening an existing Bot home is not part of this step. The running plugin and
existing SQLite data are unchanged. The sections below describe that existing
implementation, not the new target design.

The intended next integration is a dedicated memory message after the system
prompt and before history, replaced only on content changes. Current DSH basic
compaction selects from the first non-system surface node, with no supported
pin/exclude option: memory retention across automatic, manual and overflow
compaction must be solved before that layout is activated. Do not silently
substitute a system-prompt section or an append-at-tail snapshot for this target.

## Scope and automatic context

Mount one root per Host. All Agent conversations on that Host receive the same
Bot-local active facts, including newly created, resumed, forked, DM and scheduled
conversations when their next native model request is admitted. No Persona or
preset edits are required. A global **required, literal runtime context** survives
complete/custom system prompts and optional-context suppression. The native loop
records the accepted context in Session history and restores it when needed after
compaction. The plugin never rewrites model requests or runs a background Agent.

Facts belong to `profileContext.home` (this Bot's `DSH_HOME`), not the working
directory, current project, credentials store or individual Session. Profiles
sharing one Bot home intentionally share this memory. Different EVA homes are
isolated. EVA mounts the installed package as a separate child Host root; source
changes do not retrofit an already running child process.

Storage: `DSH_HOME/memory/memories.sqlite`, using Node 24's built-in SQLite,
transactions and version checks. The directory is private; symlink storage paths
are rejected. Committed changes are read on the next context refresh, including
changes made by another profile sharing this Bot home. Unloading/reinstalling the
plugin does not delete memory. This is plaintext local storage, not encryption.
Back it up with the Host stopped or a SQLite-aware backup method.

Unchanged combined runtime context is deduplicated by DSH; changed snapshots can
append new history. Archiving/deleting a fact excludes it from future snapshots,
**not from existing conversation history or backups**. Compaction is native DSH
behavior, not an independent memory-maintenance process.

## Plugin-owned skill

The plugin optionally registers native `magi-bot-memory`, with [operational guidance](<skills/bot-memory/SKILL.md>) for fact selection, duplicate reconciliation, version conflicts, archive/restore and authorized deletion. Its resources ship inside this package and resolve from the installed module, not the working directory. The injected skill scope owns teardown and re-registration; absence or reload of `skills` does not interrupt the memory context or tools. This guidance describes the **active SQLite tools**, not activation of the Markdown backend. It changes no tool permission and does not update an already running Bot.

## Management tools

- `list_memory({query?,include_archived?,offset?,limit?})`: paginated literal search,
  defaults to active records, offset 0, page size 20, maximum 50.
- `get_memory({memory_id})`: read one complete record, including archived facts.
- `save_memory({topic,detail,kind?,archived?})`: create. For updates supply
  `memory_id`, `expected_version` and only the fields to change. Creation must not
  include `expected_version`. Set `archived:false` to restore a supported fact.
- `archive_memory({memory_id,expected_version})`: hide from automatic context.
- `delete_memory({memory_id,expected_version,confirm:true})`: permanent storage
  deletion, only after user authorization; prefer archive when uncertain.

Records have a UUID, topic, detail, kind (`temporary`, `short_term`, `long_term`),
archive flag, version, timestamps and creation Session provenance. Kinds are labels,
not automatic expiry. Concurrent/stale updates fail; reread and reconcile rather
than blindly retrying. Native tool policy still applies; the plugin does not
bypass guards or approvals, and restricted presets may hide management tools.

Topic is limited to 160 Unicode characters and detail to 4096. The complete active
snapshot must fit **32 KiB UTF-8**, including guidance and metadata; writes that
exceed it fail atomically instead of silently omitting facts. Total stored records
(active plus archived) are limited to 2000. Capacity errors require reconciliation,
archiving or explicit deletion, not automatic loss of memory.

Store reusable facts and preferences with sources and uncertainty. Do not save
credentials, quoted instructions as authorization, or unfinished tasks as facts.
There is no automatic extraction, vector search, TTL, subconscious maintenance,
cross-Bot sharing, legacy import, or memory UI in this first version.

## Installation and testing

Install this package directory through native Plugin Manager `install_bundle`, or
install EVA, which integrates it. Do not install both root rows in the same Host.
Existing running EVA children need a separately authorized restart to load changed
Host composition; their profile files are not rewritten.

Run with the MAGI-owned Node runtime:

```sh
runtime/bin/node --test packages/memory/test/*.test.js
```

The original SVG icon is created for this package; no third-party artwork is used.
