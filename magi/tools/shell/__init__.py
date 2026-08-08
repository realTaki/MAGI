"""Shell command execution.

Three tools the LLM uses together:

  - :class:`magi.tools.shell.run.BashRunTool`
  - :class:`magi.tools.shell.output.BashOutputTool`
  - :class:`magi.tools.shell.kill.BashKillTool`

All three share :class:`magi.tools.shell._manager._BackgroundShellManager`
— a per-process singleton that owns the live
``asyncio.subprocess.Process`` handles and the monitor
tasks that drain their output into the per-shell ring
buffers.

Cross-worker visibility (the :class:`magi.new_bus`
:mod:`magi.new_bus.library.local.shellBook` tables) lets
``bash_output`` / ``bash_kill`` run on any worker
process — not just the one that spawned the shell. The
process handle itself stays local (it can't be
serialised); the row in ``background_shells`` is the
rendezvous point.

When the bus isn't wired (tests / boot probes with
``ctx.bus is None``) the manager degrades to single-worker
mode — same in-memory behaviour as v0, no DB writes.
"""