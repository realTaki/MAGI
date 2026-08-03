"""Deprecated import path; local memory persistence is BUS-owned."""

from magi.bus.models.local.memory import (
    ALL_KINDS,
    KIND_IMPORTANT,
    KIND_ONGOING,
    SOURCE_EVE,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
    MemoryEntry,
)

__all__ = [
    "ALL_KINDS", "KIND_IMPORTANT", "KIND_ONGOING", "SOURCE_EVE", "SOURCE_MANUAL",
    "SOURCE_SYSTEM", "MemoryEntry",
]
