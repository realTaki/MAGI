# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in MAGI, **please do not file a public Issue**.

Email the details to the maintainer directly. You should receive a
response within 48 hours. We take all reports seriously and will work
to verify and address them promptly.

## Supported versions

Only the latest `main` branch is supported. We do not backport
security fixes to older releases.

## Scope

Security concerns include but are not limited to:

- Exposure of API keys or credentials
- Unauthenticated access to admin endpoints
- Path traversal in file tools
- Injection vulnerabilities

The `api_key` field on contacts and magis rows is treated as a secret —
endpoints must never return it in plain text (only `api_key_set` +
`api_key_last4`).

## Security design

- **Cookie-based auth**: `magi_session` carries a contact's primary key,
  verified against the database on each gated request.
- **Per-contact LLM keys**: Each contact (or Magi, post-F1) routes to a
  dedicated provider key — no shared system key leaks.
- **File tool containment**: File read/write tools are scoped to the
  workspace directory. C8 hardening will add symlink protection.
- **admin_gate**: All mutation endpoints require `role='admin'`.
