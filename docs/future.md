# Research goals and unfinished work

The mechanisms currently implemented are described in [Architecture](architecture.md).
The following items are goals or limitations, not shipped guarantees:

- **Measured recursive improvement:** evaluate whether repeated self-edit,
  validation and adoption improve subsequent performance. A successful revision
  switch alone does not establish RSI.
- **Coordination and governance:** shared task/decision protocols and explicit
  authority, accountability and resource limits beyond chat transport. Chat
  between MAGIs already exists; a general collaboration-governance system does not.
- **Credential minimization:** replace plaintext provider/configuration job
  payloads with secret references or sealed data. See `ConfigureJob` and
  `ChangeProviderNotify`; local job history must currently be treated as sensitive.
- **Identity generations:** establish stable identity/registration semantics
  beyond display names and reused handle strings before claiming cross-generation
  equivalence.
- **Activation guarantees:** broader evaluation of crash/interruption recovery,
  dependency changes, data-schema changes and external side effects. Current
  process rollback does not prove an atomic whole-system rollback.
- **Interactive launcher stdin:** the non-host launcher currently gives its
  child ignored stdin even though the child has a terminal handler. Verify and
  implement forwarding before claiming an interactive CLI through that entry.
- **Additional integrations and isolation:** capabilities outside the current
  desktop, ASP, CLI, Telegram, tasks and MCP workers require implementation and
  verification. Native tools are not an OS sandbox.

Material in `not_implemented/` and historical Python release notes is design
history or unfinished work; its presence does not enable a feature in the
TypeScript worker composition. Proposals should state a falsifiable outcome,
a bounded change and the evidence needed to adopt it.
