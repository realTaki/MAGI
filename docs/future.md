# Where MAGI is heading

The mechanisms currently implemented are described in [Architecture](architecture.md).
The project is not aiming for one final configuration that every user keeps.
It is aiming for a better starting point for many independently evolving MAGIs.
These directions should be read as questions to investigate, not a release
schedule or a promise of backward compatibility.

## The direction

- **Personal software, not only personal answers.** Can a user's agent turn a
  recurring need into a useful change to the tools or interface, then maintain
  it as the surrounding system changes?
- **More of the system participating in its own maintenance.** “Everything is
  an agent” includes the ambition for interfaces and custom applications to be
  agent-maintained. Today the repository has editable UI/runtime source and
  activation mechanisms, not a separate autonomous agent for every component.
- **Less manual configuration.** Eventually, a dedicated settings page may no
  longer be the primary way to manage the system. Settings and per-node provider
  forms still exist today. Removing them before their responsibilities have a
  working replacement would not achieve the goal.
- **Independent forks that can learn from elsewhere.** Agents should help
  inspect and adapt useful ideas, implementations and updates. Current
  fast-forward source sync is not that general maintenance capability.

## What still needs evidence or implementation

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
  desktop, peer channels, CLI, Telegram, tasks and MCP workers require implementation and
  verification. Native tools are not an OS sandbox.

Historical Python release notes describe older runtime generations, not the
current TypeScript worker composition. Working action notes and the continuing
subconscious conversation are now described in [Architecture](architecture.md).
Their operation is tested; broad gains in proactive usefulness still need user
evaluation. Proposals should state a falsifiable outcome, a bounded change and
the evidence needed to adopt it.
