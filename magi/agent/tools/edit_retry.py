"""Per-path tracker for ``edit_file`` retries.

Why this exists
---------------

``edit_file`` requires the LLM's ``old_str`` to match the
**current** file content exactly. After several
tool iterations (or after another tool modified the file
mid-chain), the LLM often tries ``edit_file`` with a stale
``old_str`` it remembered from a prior ``read_file`` and
gets ``is_error=True`` back. The LLM can usually self-
correct, but it sometimes retries the same stale string
twice — burning a turn budget — before it remembers to
re-read the file.

This tracker nudges the LLM back on track when it has
failed on the **same path** ``N`` times in a row. After
the threshold is hit, the next ``tool_result`` block the
agent loop returns to the LLM gets a short hint appended:

    "[agent hint] edit_file has failed 2 times in a row on
     'foo.py'. Call read_file first to refresh your view
     of the file before retrying."

The hint is **only** appended to the result content — it
is not a separate channel of feedback, so the LLM sees it
right next to the failure message it needs to act on. We
deliberately don't change ``is_error`` (it's still the
tool's own verdict) and don't fire on the first failure
(the LLM can usually fix one-off whitespace mistakes on
its own).

Counter lifecycle
-----------------

- One counter per ``(tool_name, path)`` pair. The path is
  taken from the ``input`` dict's ``path`` field; tools
  other than ``edit_file`` (and ``edit_file`` calls
  without a ``path``) share a single "any" bucket so a
  repeated failure on different paths also gets the hint.
- A **successful** call for the same ``(tool_name,
  path)`` resets the counter. A failure on a different
  tool does NOT reset ``edit_file``'s counter — the
  relevant question is "did the LLM successfully edit
  this file", and intervening tool calls don't count.
- When the threshold is hit we fire once; subsequent
  failures keep the counter climbing but we only inject
  the hint on every failure (the LLM may genuinely need
  the repeated reminder).
- ``reset()`` is exposed for tests; the agent loop
  instantiates a fresh tracker per chat turn so a long-
  running session doesn't accumulate state across
  unrelated tasks.

Why per-path instead of a single counter
----------------------------------------

A model working on two files in the same turn shouldn't
have its hint budget spent by a failure on file A while
file B's edit succeeded. The path keying keeps the hint
focused on the actual problem.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


#: How many consecutive ``edit_file`` failures on the
#: same path are tolerated before the hint fires. 2 is
#: enough to filter out one-off whitespace slips (the LLM
#: can self-correct) while still catching the "LLM is
#: looping on a stale ``old_str``" case before the budget
#: is exhausted.
FAILURE_THRESHOLD = 2

#: The hint template. ``{count}`` is the current failure
#: count for the path; ``{path}`` is the file path (or
#: ``"<no path>"`` if the LLM forgot to send one). Kept
#: as a module constant so tests can pin the wording
#: without copy-pasting it.
_HINT_TEMPLATE = (
    "[agent hint] edit_file has failed {count} times in "
    "a row on '{path}'. Call read_file on this file first "
    "to refresh your view of the current contents "
    "(whitespace, indentation, recent edits) before "
    "retrying. The first ~5 lines of the file are usually "
    "enough to construct a unique old_str."
)


class EditRetryTracker:
    """Count consecutive ``edit_file`` failures per path.

    The instance is short-lived: the agent loop creates
    one per chat turn and discards it when the turn
    finishes. Process-lifetime tracking is unnecessary
    (and would cause stale counters to leak across
    unrelated chats).

    Thread-safe-ish: the agent loop is single-threaded
    within one ``handle_message`` call (asyncio serialises
    the tool calls). No lock is needed for the current
    usage; if a future change ever runs tool calls in
    parallel from one turn, add a ``Lock`` around
    ``record`` / ``reset``.
    """

    def __init__(self, *, threshold: int = FAILURE_THRESHOLD) -> None:
        self._threshold = threshold
        # path → consecutive failure count. Default-dict
        # so we don't need to pre-create entries on
        # ``record``; ``__missing__`` returns 0 which is
        # what we want for the "first failure" case.
        self._failures: dict[str, int] = defaultdict(int)

    def record(
        self,
        *,
        tool_name: str,
        input: dict[str, Any] | None,
        is_error: bool,
    ) -> str | None:
        """Update the tracker after a tool call.

        Returns the hint string to append to the
        ``tool_result`` content when the failure threshold
        has been crossed for the path; ``None`` when no
        hint should be appended.

        ``tool_name`` is the LLM-called name (``"edit_file"``
        is the only tool we track today; any other name
        resets the path's counter on success but doesn't
        increment it on failure).

        ``input`` is the dict the LLM sent to the tool.
        For ``edit_file`` we key on the ``path`` field;
        for any other tool we key on the tool name
        itself so a non-edit tool that errored on file
        X doesn't poison the edit_file budget for X.
        """
        if tool_name != "edit_file":
            # Non-edit tools don't affect edit_file's
            # budget. If the other tool happened to be
            # the LLM's own recovery move (e.g.
            # ``read_file`` on the same path), we still
            # don't reset edit_file's counter — that's
            # intentional. The counter only resets on a
            # *successful edit* on that path.
            return None

        path = self._path_from_input(input)
        key = path or "<no path>"

        if not is_error:
            # A successful edit on this path → reset the
            # streak. The LLM's old_str was correct, the
            # state is consistent, the hint is no longer
            # needed for this path.
            self._failures.pop(key, None)
            return None

        self._failures[key] += 1
        if self._failures[key] < self._threshold:
            # First failure is the tool's own error
            # message; we don't add a hint yet. Second
            # failure is when the hint first fires.
            return None

        return _HINT_TEMPLATE.format(
            count=self._failures[key],
            path=key,
        )

    @staticmethod
    def _path_from_input(input: dict[str, Any] | None) -> str:
        """Extract the path field from a tool input dict.

        Returns ``""`` when the LLM didn't send a path
        (malformed call) — the tracker still keys on it
        so two consecutive malformed edits get the hint,
        just under a generic label.
        """
        if not isinstance(input, dict):
            return ""
        path = input.get("path")
        return path if isinstance(path, str) else ""

    def reset(self) -> None:
        """Drop all counters. Test-only helper."""
        self._failures.clear()


__all__ = ["EditRetryTracker", "FAILURE_THRESHOLD"]