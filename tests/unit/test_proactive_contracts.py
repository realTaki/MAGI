"""Tests for the intentionally side-effect-free proactive boundary."""

from __future__ import annotations

from datetime import datetime, timezone

from magi.proactive import ProactiveSignal, ProposedAction


def test_proactive_contracts_are_plain_data_without_runtime_side_effects() -> None:
    signal = ProactiveSignal("health_changed", "runtime", datetime.now(timezone.utc))
    action = ProposedAction("notify", "health policy matched")

    assert signal.payload == {}
    assert action.payload == {}
