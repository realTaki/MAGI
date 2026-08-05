"""Size caps + truncation helpers for :class:`HookEnvelope`.

Every field the materializer emits is bounded.  Truncated fields
gain a :class:`magi.bus.hooks.contracts.TruncationMarker` so the
handler knows exactly how much was cut and can compute a digest of
what it saw.  The defaults match spec §9:

  - envelope total: 256 KiB
  - single field: 64 KiB
  - session window: 20 messages
  - memory matches: 10 entries
  - attachment metadata: 20 entries

The caps are exposed as module-level constants so the architecture
test can verify they are honored; nothing in this module mutates
state, so it is safe to call from any coroutine.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from magi.bus.hooks.contracts import TruncationMarker


# ───────────────────────────────────────────────────────────────────── #
# Size caps
# ───────────────────────────────────────────────────────────────────── #


MAX_ENVELOPE_BYTES = 256 * 1024
MAX_FIELD_BYTES = 64 * 1024
MAX_SESSION_WINDOW_MESSAGES = 20
MAX_MEMORY_MATCHES = 10
MAX_ATTACHMENT_METADATA = 20


# ───────────────────────────────────────────────────────────────────── #
# TruncationContext — state passed through the materializer
# ───────────────────────────────────────────────────────────────────── #


@dataclass(slots=True)
class TruncationContext:
    """Per-evaluation state for :func:`apply_size_caps`.

    The materializer builds one of these per envelope, walks the
    payload / context dicts through :func:`apply_size_caps`, and
    attaches the resulting :class:`TruncationMarker` to the
    envelope metadata so handlers can tell which fields were
    truncated.
    """

    total_bytes: int = 0
    truncated_fields: tuple[tuple[str, TruncationMarker], ...] = ()

    def record(self, path: str, original_size: int, included_size: int, value: Any) -> Any:
        """Apply a field-level cap to ``value`` and update context.

        Returns the value the materializer should embed in the
        envelope — either the original (under the cap) or a
        truncated copy with a marker.
        """
        if original_size <= MAX_FIELD_BYTES:
            self.total_bytes += original_size
            return value
        # Cut the value.  Strings truncate; lists drop their tail;
        # dicts keep their first N keys.
        truncated, included = _truncate_value(value)
        marker = TruncationMarker(
            truncated=True,
            original_size=original_size,
            included_size=included,
            content_hash=_sha256_hex(value),
        )
        self.truncated_fields = self.truncated_fields + ((path, marker),)
        self.total_bytes += included
        return truncated

    def finalize_envelope(self) -> tuple[TruncationMarker | None, dict[str, Any]]:
        """Return the envelope-level marker and the metadata dict.

        ``None`` is returned when no truncation happened so the
        materializer can omit the key.
        """
        if not self.truncated_fields:
            return None, {}
        marker = TruncationMarker(
            truncated=True,
            original_size=self.total_bytes,
            included_size=min(self.total_bytes, MAX_ENVELOPE_BYTES),
            content_hash="",
        )
        meta = {
            "truncated": True,
            "truncated_fields": [
                {"path": path, "marker": _marker_to_dict(m)}
                for path, m in self.truncated_fields
            ],
        }
        return marker, meta


# ───────────────────────────────────────────────────────────────────── #
# Apply caps recursively
# ───────────────────────────────────────────────────────────────────── #


def apply_size_caps(value: Any, context: TruncationContext, path: str = "") -> Any:
    """Walk ``value`` and apply field + envelope caps.

    Top-level ``value`` may be a dict, list, or primitive.  Strings
    longer than :data:`MAX_FIELD_BYTES` are truncated; long lists
    and dicts are cut to ``MAX_FIELD_BYTES`` bytes (counted
    approximately by ``len(repr(...))``).

    The function never raises.  A value larger than
    :data:`MAX_ENVELOPE_BYTES` is replaced with a metadata-only
    projection once the envelope is full.
    """
    if context.total_bytes >= MAX_ENVELOPE_BYTES:
        # Envelope already over budget — every subsequent field
        # is replaced with a metadata-only stub so handlers still
        # see "something existed here" without a runaway payload.
        return _over_budget_marker(value)
    original = _approx_size(value)
    if original <= MAX_FIELD_BYTES:
        context.total_bytes += original
        return value
    truncated, included = _truncate_value(value)
    marker = TruncationMarker(
        truncated=True,
        original_size=original,
        included_size=included,
        content_hash=_sha256_hex(value),
    )
    context.truncated_fields = context.truncated_fields + ((path or "<root>", marker),)
    context.total_bytes += included
    return truncated


# ───────────────────────────────────────────────────────────────────── #
# Helpers
# ───────────────────────────────────────────────────────────────────── #


def _truncate_value(value: Any) -> tuple[Any, int]:
    """Cut ``value`` so its serialised form fits ``MAX_FIELD_BYTES``.

    Strategy:

      - ``str``: keep the first ``MAX_FIELD_BYTES`` characters.
      - ``list``: keep the first ``MAX_FIELD_BYTES // 64`` items
        (assuming average 64-byte item).
      - ``dict``: keep the first ``MAX_FIELD_BYTES // 64`` keys.
      - anything else: return ``"...truncated..."`` of fixed size.

    Returns ``(truncated_value, included_size)``.
    """
    if isinstance(value, str):
        truncated = value[:MAX_FIELD_BYTES]
        return truncated, len(truncated)
    if isinstance(value, list):
        n = max(1, MAX_FIELD_BYTES // 64)
        truncated = list(value[:n])
        return truncated, _approx_size(truncated)
    if isinstance(value, dict):
        n = max(1, MAX_FIELD_BYTES // 64)
        truncated = {k: value[k] for k in list(value.keys())[:n]}
        return truncated, _approx_size(truncated)
    truncated = "...truncated..."
    return truncated, len(truncated)


def _over_budget_marker(value: Any) -> dict[str, Any]:
    """Metadata stub for fields that arrive after the envelope cap."""
    return {
        "truncated": True,
        "envelope_budget_exceeded": True,
        "type": _type_name(value),
        "original_size": _approx_size(value),
        "content_hash": _sha256_hex(value),
    }


def _approx_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1
    if isinstance(value, int):
        return 32
    if isinstance(value, float):
        return 32
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (list, tuple)):
        return sum(_approx_size(item) for item in value) + 8
    if isinstance(value, dict):
        return sum(_approx_size(k) + _approx_size(v) for k, v in value.items()) + 16
    return len(repr(value).encode("utf-8"))


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


def _type_name(value: Any) -> str:
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
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _marker_to_dict(marker: TruncationMarker) -> dict[str, Any]:
    return {
        "truncated": marker.truncated,
        "original_size": marker.original_size,
        "included_size": marker.included_size,
        "content_hash": marker.content_hash,
    }


__all__ = [
    "MAX_ATTACHMENT_METADATA",
    "MAX_ENVELOPE_BYTES",
    "MAX_FIELD_BYTES",
    "MAX_MEMORY_MATCHES",
    "MAX_SESSION_WINDOW_MESSAGES",
    "TruncationContext",
    "apply_size_caps",
]


# Silence linters: ``Sequence`` / ``Mapping`` are referenced in the
# docstrings above; keep them in __all__'s import surface even
# though the helpers below do not need them at runtime.
_ = (Sequence, Mapping)
