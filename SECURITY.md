# Security policy

Report suspected credential exposure, unauthorized access or execution privately
to the repository maintainer through an available private contact channel. Do
not include secrets in public Issues, screenshots or test fixtures. Only current
`main` is maintained; older releases do not receive compatibility backports.

## Current trust boundaries

- EVA peer HTTP endpoints assume a trusted private network, such as a Kubernetes
  bridge or a Tailscale-connected subnet. They do not authenticate requests or
  enforce per-peer tool permissions. Peer identity labels and membership are
  routing data. Do not expose the service to an untrusted network or the public
  internet. The deployment operator controls network reachability and trust.
- Shell process lifecycle operations use records of locally provisioned children;
  a remote contact is not a locally owned child.
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
  a public-key fingerprint is a stable peer label, not caller authentication.

[Architecture](docs/architecture.md) describes the implemented process and message
boundaries. OS sandboxing, credential storage guarantees and secure remote
multi-user hosting must not be inferred from successful local demo tests.
