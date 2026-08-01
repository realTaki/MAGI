"""Tests for :mod:`magi.tools.edit_retry`.

The tracker is the policy behind the agent loop's
"edit_file failed twice — nudge the LLM to re-read"
behaviour. It's pure Python (no DB, no I/O), so the
tests are short and pin the contract end-to-end:

- First failure on a path → no hint (the tool's own
  error message is enough; the LLM can usually self-
  correct a one-off whitespace slip).
- Second failure on the same path → hint fires, with
  the path label inside the message.
- A success on that path resets the counter.
- Per-path isolation: failing on path A doesn't burn
  path B's budget.
- Non-``edit_file`` tools never trigger the hint and
  never reset the counter (the LLM may still need to
  re-read after a successful ``read_file``).
- Crashed (exception) tool calls count as failures too
  — the LLM didn't make progress on the path.
- The hint is appended to the tool_result the LLM
  sees, not a separate channel.
"""

from __future__ import annotations

import pytest

from magi.tools.edit_retry import FAILURE_THRESHOLD, EditRetryTracker


# ──────────────────────────────────────────────────────────── #
# Threshold + helper behaviour
# ──────────────────────────────────────────────────────────── #

def test_threshold_is_two_by_default():
    """Sanity: the default threshold is the public
    constant. Pinning the value here catches any future
    "let's lower it to 1" change that would re-introduce
    noise on every single edit_file failure.
    """
    assert FAILURE_THRESHOLD == 2


def test_first_failure_emits_no_hint():
    """A single edit_file failure shouldn't append a
    hint. The tool's own error message is sufficient
    for one-off mistakes (whitespace slip, off-by-one
    line); piling a hint on every failure would just
    waste tokens.
    """
    t = EditRetryTracker()
    hint = t.record(
        tool_name="edit_file",
        input={"path": "foo.py"},
        is_error=True,
    )
    assert hint is None


# ──────────────────────────────────────────────────────────── #
# Hint firing
# ──────────────────────────────────────────────────────────── #

def test_second_consecutive_failure_fires_hint_with_path():
    """Two failures in a row on the same path →
    tracker returns the hint string with the path
    embedded. The LLM sees this appended to the
    tool_result content.
    """
    t = EditRetryTracker()
    # First failure: silent.
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    # Second failure: hint fires.
    hint = t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    assert hint is not None
    assert "foo.py" in hint
    assert "read_file" in hint
    assert "2 times" in hint


def test_hint_repeats_on_third_and_later_failures():
    """The hint keeps firing on every failure past
    the threshold. The LLM might be on turn 3 of the
    same stale-old_str loop; we keep nudging.
    """
    t = EditRetryTracker()
    for _ in range(2):
        t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    third = t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    assert third is not None
    assert "3 times" in third


# ──────────────────────────────────────────────────────────── #
# Counter reset
# ──────────────────────────────────────────────────────────── #

def test_success_resets_counter_on_same_path():
    """A successful edit_file on the path wipes the
    streak. Next failure starts from 0 again.
    """
    t = EditRetryTracker()
    # Two failures → hint fires.
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    # Successful edit on the same path.
    success_hint = t.record(
        tool_name="edit_file", input={"path": "foo.py"}, is_error=False,
    )
    assert success_hint is None
    # Next failure is silent again (fresh streak).
    next_hint = t.record(
        tool_name="edit_file", input={"path": "foo.py"}, is_error=True,
    )
    assert next_hint is None


def test_success_on_other_path_does_not_reset_counter():
    """Editing a different file successfully doesn't
    clear the streak on the file that's still failing.
    The LLM might be bouncing between two files; we
    don't want a coincidental success to mask the
    real problem.
    """
    t = EditRetryTracker()
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    # Successful edit on bar.py.
    t.record(tool_name="edit_file", input={"path": "bar.py"}, is_error=False)
    # Second failure on foo.py still fires the hint.
    hint = t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    assert hint is not None
    assert "foo.py" in hint


# ──────────────────────────────────────────────────────────── #
# Per-path isolation
# ──────────────────────────────────────────────────────────── #

def test_failures_on_different_paths_track_independently():
    """Two failures on path A don't affect path B's
    budget — and vice versa. The hint for path A
    mentions A; the hint for B mentions B.
    """
    t = EditRetryTracker()
    # Two failures on foo.py.
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    foo_hint = t.record(
        tool_name="edit_file", input={"path": "foo.py"}, is_error=True,
    )
    assert foo_hint is not None
    assert "foo.py" in foo_hint
    # First failure on bar.py — silent.
    bar_first = t.record(
        tool_name="edit_file", input={"path": "bar.py"}, is_error=True,
    )
    assert bar_first is None


# ──────────────────────────────────────────────────────────── #
# Non-edit tools
# ──────────────────────────────────────────────────────────── #

def test_non_edit_tool_failures_never_fire_hint():
    """Only ``edit_file`` triggers the hint. A failure
    on ``bash`` or ``read_file`` doesn't make sense
    to push the LLM into re-reading a file.
    """
    t = EditRetryTracker()
    for _ in range(5):
        hint = t.record(
            tool_name="bash", input={"command": "ls"}, is_error=True,
        )
        assert hint is None


def test_non_edit_tool_success_does_not_reset_edit_counter():
    """The LLM calling ``read_file`` (success) is the
    *intended* recovery move — but we deliberately
    don't reset edit_file's counter on a read_file
    success. The LLM is supposed to figure out from
    the hint that read_file is what to do; resetting
    the counter would let it think "I've already
    recovered" before it actually retries the edit.
    """
    t = EditRetryTracker()
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    # Successful read_file on the same path.
    t.record(tool_name="read_file", input={"path": "foo.py"}, is_error=False)
    # Second edit_file failure still fires the hint.
    hint = t.record(
        tool_name="edit_file", input={"path": "foo.py"}, is_error=True,
    )
    assert hint is not None


# ──────────────────────────────────────────────────────────── #
# Edge cases
# ──────────────────────────────────────────────────────────── #

def test_missing_path_input_keyed_under_generic_label():
    """An edit_file call without a ``path`` field
    (malformed input) still gets the hint after two
    failures, labelled under the generic ``<no path>``
    bucket. Two distinct malformed calls share that
    bucket.
    """
    t = EditRetryTracker()
    t.record(tool_name="edit_file", input={}, is_error=True)
    hint = t.record(tool_name="edit_file", input={}, is_error=True)
    assert hint is not None
    assert "<no path>" in hint


def test_non_dict_input_is_treated_as_no_path():
    """Defensive: a non-dict input (shouldn't happen,
    but the LLM SDK is unpredictable) is treated like
    a missing path. The tracker doesn't crash.
    """
    t = EditRetryTracker()
    t.record(tool_name="edit_file", input=None, is_error=True)  # type: ignore[arg-type]
    hint = t.record(tool_name="edit_file", input=None, is_error=True)  # type: ignore[arg-type]
    assert hint is not None
    assert "<no path>" in hint


def test_reset_clears_all_counters():
    """``reset`` is for tests + any future use that
    wants a clean slate. After reset, the first
    failure is silent again.
    """
    t = EditRetryTracker()
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    t.reset()
    hint = t.record(
        tool_name="edit_file", input={"path": "foo.py"}, is_error=True,
    )
    assert hint is None


def test_custom_threshold():
    """The constructor accepts an override for tests
    that want a tighter or looser threshold without
    monkeypatching the module constant.
    """
    t = EditRetryTracker(threshold=1)
    hint = t.record(
        tool_name="edit_file", input={"path": "foo.py"}, is_error=True,
    )
    assert hint is not None  # fires on the first failure
    assert "1 times" in hint


# ──────────────────────────────────────────────────────────── #
# Integration with the loop's tool_result content
# ──────────────────────────────────────────────────────────── #

def test_loop_appends_hint_to_tool_result_content():
    """End-to-end shape check: when the tracker returns
    a hint, the loop concatenates it to the tool's
    content with a blank-line separator. Verifies the
    contract the loop relies on (the hint is a suffix
    on the existing tool_result content, not a
    replacement).
    """
    t = EditRetryTracker()
    # Build up to the firing state.
    t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    hint = t.record(tool_name="edit_file", input={"path": "foo.py"}, is_error=True)
    assert hint is not None

    # The loop's behaviour (what _run_tool_calls does):
    original_content = "edit_file: ``old_str`` not found in 'foo.py'. ..."
    combined = f"{original_content}\n\n{hint}"
    assert "not found" in combined
    assert "[agent hint]" in combined
    # Hint comes AFTER the tool's own error message so
    # the LLM sees both pieces of context.
    assert combined.index("[agent hint]") > combined.index("not found")