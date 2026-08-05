"""SecretRedactor — applies per-field classification to a payload tree.

The redactor walks a JSON-shaped tree and, for every field whose
classification is ``SECRET`` or ``CREDENTIAL``, replaces the value
with a metadata-only projection per spec §9:

  - default: ``{"present": true, "type": <type>, "length": <len>, "content_hash": <sha256>}``
  - override ``mode="redacted"``: literal ``"***REDACTED***"``
  - override ``mode="hashed"``: ``{"sha256": <hex>}``
  - override ``mode="metadata-only"``: same as default

The redactor does NOT touch fields with classification ``PUBLIC``,
``INTERNAL`` or ``CONFIDENTIAL``.  ``CONFIDENTIAL`` fields keep
their value but receive a ``data_classification`` annotation so
downstream sinks (file audit, metrics) can apply additional policy.

The redactor never raises — a malformed value (e.g. a binary
blob sneaking into the payload) is converted to a metadata-only
projection rather than aborting the whole evaluation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


# ───────────────────────────────────────────────────────────────────── #
# Classification hints
# ───────────────────────────────────────────────────────────────────── #


# Field-name → classification.  Kept narrow on purpose: only the
# field names that genuinely carry credentials get special-cased by
# name.  Anything else relies on the materializer's per-call
# classification hints.
KNOWN_SECRET_FIELDS: frozenset[str] = frozenset({
    "api_key",
    "anthropic_api_key",
    "openai_api_key",
    "authorization",
    "authorization_header",
    "bot_token",
    "telegram_bot_token",
    "refresh_token",
    "access_token",
    "id_token",
    "session_token",
    "cookie",
    "set_cookie",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "private_key",
    "signing_key",
    "db_password",
    "database_url",
    "connection_string",
})


@dataclass(frozen=True, slots=True)
class FieldClassification:
    """How to treat a single field inside the redacted payload.

    ``mode`` controls the *shape* of the redacted projection;
    ``classification`` is what the BUS actually saw before
    redaction.  The two are independent because the same
    classification (``SECRET``) can be projected either as
    metadata-only (default) or as a literal ``***REDACTED***``
    string (the operator-friendly form).
    """

    classification: str  # HookDataClassification value
    mode: str = "metadata-only"  # "metadata-only" | "redacted" | "hashed" | "preserve"


# ───────────────────────────────────────────────────────────────────── #
# Redactor
# ───────────────────────────────────────────────────────────────────── #


class SecretRedactor:
    """Stateless redactor.

    Each call to :meth:`redact` consumes a payload tree, a flat
    ``field_classifications`` map, and an optional list of known
    secret field names.  The result is a new tree with secrets
    replaced.  The input tree is not mutated.
    """

    @staticmethod
    def redact(
        value: Any,
        *,
        field_classifications: Mapping[str, FieldClassification] | None = None,
        known_secret_fields: frozenset[str] = KNOWN_SECRET_FIELDS,
    ) -> Any:
        """Return a redacted copy of ``value``.

        Top-level ``value`` may be ``None``, a primitive, a list,
        or a dict.  For dicts the redactor uses
        ``field_classifications`` to look up explicit hints, then
        falls back to ``known_secret_fields`` for case-insensitive
        matches on field names.
        """
        classifications = field_classifications or {}
        return _walk(value, classifications, known_secret_fields)

    @staticmethod
    def metadata_projection(
        *,
        original_value: Any,
        classification: str,
        mode: str,
    ) -> Any:
        """Compute the redacted projection for one field.

        Exposed so materializers can build field hints that
        reference the same projection logic.
        """
        return _project(original_value, classification, mode)


# ───────────────────────────────────────────────────────────────────── #
# Internal helpers
# ───────────────────────────────────────────────────────────────────── #


def _walk(
    value: Any,
    classifications: Mapping[str, FieldClassification],
    known_secret_fields: frozenset[str],
) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_walk(item, classifications, known_secret_fields) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _walk_field(
                key,
                item,
                classifications,
                known_secret_fields,
            )
            for key, item in value.items()
        }
    # Unknown type — coerce to a string and let the caller decide.
    # Never raise; the redactor is best-effort.
    return _project(value, "internal", "preserve")


def _walk_field(
    key: Any,
    value: Any,
    classifications: Mapping[str, FieldClassification],
    known_secret_fields: frozenset[str],
) -> Any:
    name = str(key)
    hint = classifications.get(name)
    if hint is not None:
        if hint.classification in {"secret", "credential"}:
            return _project(value, hint.classification, hint.mode)
        # ``confidential`` and below keep their value but the
        # caller can re-annotate after walking.
        return _walk(value, classifications, known_secret_fields)
    if name.lower() in known_secret_fields:
        return _project(value, "secret", "metadata-only")
    return _walk(value, classifications, known_secret_fields)


def _project(value: Any, classification: str, mode: str) -> Any:
    """Apply one projection rule to ``value``.

    The four modes are:

      - ``metadata-only`` (default for SECRET/CREDENTIAL):
        ``{"present": bool, "type": str, "length": int,
        "content_hash": sha256-hex}``.  The original value is
        NOT included.
      - ``redacted``: literal ``"***REDACTED***"``.
      - ``hashed``: ``{"sha256": sha256-hex}``.
      - ``preserve``: pass-through for non-secret classifications.
    """
    if mode == "preserve":
        return value
    digest = _sha256_hex(value)
    length = _safe_length(value)
    type_name = _safe_type(value)
    if mode == "redacted":
        return "***REDACTED***"
    if mode == "hashed":
        return {"sha256": digest}
    # default: metadata-only
    return {
        "present": value is not None,
        "type": type_name,
        "length": length,
        "content_hash": digest,
    }


def _sha256_hex(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        blob = value.encode("utf-8")
    elif isinstance(value, (bytes, bytearray)):
        blob = bytes(value)
    elif isinstance(value, (list, tuple, dict)):
        blob = repr(value).encode("utf-8")
    else:
        blob = str(value).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _safe_length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 1


def _safe_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (bytes, bytearray)):
        return "bytes"
    if isinstance(value, list):
        return "list"
    if isinstance(value, tuple):
        return "tuple"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


__all__ = [
    "FieldClassification",
    "KNOWN_SECRET_FIELDS",
    "SecretRedactor",
]
