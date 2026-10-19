# Security policy

Report suspected credential exposure, unauthorized access or execution privately
to the repository maintainer through an available private contact channel. Do
not include secrets in public Issues, screenshots or test fixtures. Only current
`main` is maintained; older releases do not receive compatibility backports.

## Current trust boundaries

- ASP authenticates registered handles with bearer tokens. The local
  `/operator` endpoint supplies the operator credential; this is a trusted local
  deployment model, not an internet-facing multi-user login system. The old
  Cookie/session and `admin_gate` descriptions do not apply to this runtime.
- Provider keys are encrypted to the receiving node's public key in card/sync
  transport. Each node stores its own secrets in `secrets.json`; this is a
  plaintext file with restrictive POSIX permissions where supported, not an
  encrypted-at-rest vault. Windows permissions are not established by POSIX mode
  bits. See [SecretStore](packages/bus/secrets.ts).
- Some trusted internal provider/configuration job inputs contain plaintext
  credentials. Do not export a workspace or jobs database assuming it is
  sanitized. Replacing such payloads with secret references remains
  [future work](docs/future.md).
- Native file and shell tools execute with the node process's OS privileges.
  Relative paths resolve against its workspace, but the file worker explicitly
  does not implement a filesystem sandbox. A working-directory convention or
  self-rebuild source check is not OS containment.
- Source checks validate a local checkout's structure and branch, not its
  provenance. Tests and source in an editable candidate are not an immutable
  security policy. Tool/job authority and repository authorization still matter.
- Handles, names and public keys have different purposes. Names are not identity;
  the chat protocol currently has no immutable registration-generation ID.

[Architecture](docs/architecture.md) describes the implemented process and message
boundaries. OS sandboxing, credential storage guarantees and secure remote
multi-user hosting must not be inferred from successful local demo tests.
