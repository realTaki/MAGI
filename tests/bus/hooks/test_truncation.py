"""Unit tests for size caps + truncation."""

from __future__ import annotations

from magi.bus.hooks.truncation import (
    MAX_ENVELOPE_BYTES,
    MAX_FIELD_BYTES,
    TruncationContext,
    apply_size_caps,
)


def test_constants_have_expected_values():
    """The defaults match spec §9."""
    assert MAX_ENVELOPE_BYTES == 256 * 1024
    assert MAX_FIELD_BYTES == 64 * 1024


def test_short_value_passes_through():
    ctx = TruncationContext()
    out = apply_size_caps("hello", ctx, path="x")
    assert out == "hello"
    assert ctx.total_bytes == 5
    assert ctx.truncated_fields == ()


def test_long_string_truncated():
    ctx = TruncationContext()
    long = "x" * (MAX_FIELD_BYTES + 100)
    out = apply_size_caps(long, ctx, path="x")
    assert isinstance(out, str)
    assert len(out) == MAX_FIELD_BYTES
    assert len(ctx.truncated_fields) == 1
    path, marker = ctx.truncated_fields[0]
    assert path == "x"
    assert marker.truncated is True
    assert marker.original_size == len(long)


def test_long_list_truncated():
    ctx = TruncationContext()
    long_list = list(range(100_000))
    out = apply_size_caps(long_list, ctx, path="list")
    assert isinstance(out, list)
    assert len(out) < len(long_list)
    assert len(ctx.truncated_fields) == 1


def test_long_dict_truncated():
    ctx = TruncationContext()
    long_dict = {str(i): i for i in range(100_000)}
    out = apply_size_caps(long_dict, ctx, path="d")
    assert isinstance(out, dict)
    assert len(out) < len(long_dict)
    assert len(ctx.truncated_fields) == 1


def test_envelope_budget_overrun_replaces_with_stub():
    """Once the envelope budget is filled, subsequent fields
    get a metadata-only stub instead of the value."""
    ctx = TruncationContext()
    # Fill the envelope budget (256 KiB) with multiple field-size chunks.
    for i in range((MAX_ENVELOPE_BYTES // MAX_FIELD_BYTES) + 1):
        apply_size_caps("a" * MAX_FIELD_BYTES, ctx, path=f"fill_{i}")
    # Subsequent field is over the envelope cap → metadata stub.
    out = apply_size_caps("b" * 1000, ctx, path="over")
    assert isinstance(out, dict)
    assert out.get("envelope_budget_exceeded") is True


def test_truncation_context_finalize_envelope_no_truncation():
    ctx = TruncationContext()
    marker, meta = ctx.finalize_envelope()
    assert marker is None
    assert meta == {}


def test_truncation_context_finalize_envelope_with_truncation():
    ctx = TruncationContext()
    apply_size_caps("x" * (MAX_FIELD_BYTES + 1), ctx, path="x")
    marker, meta = ctx.finalize_envelope()
    assert marker is not None
    assert meta.get("truncated") is True
    assert len(meta["truncated_fields"]) == 1
    assert meta["truncated_fields"][0]["path"] == "x"


def test_nested_walk_handles_dicts():
    ctx = TruncationContext()
    out = apply_size_caps({"a": {"b": "ok"}}, ctx, path="root")
    assert out == {"a": {"b": "ok"}}


def test_nested_walk_handles_lists():
    ctx = TruncationContext()
    out = apply_size_caps({"items": [1, 2, 3]}, ctx, path="root")
    assert out == {"items": [1, 2, 3]}
