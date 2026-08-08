"""Shell command execution.

Three tools the LLM uses together:

  - :class:`magi.tools.shell.run.BashRunTool`
  - :class:`magi.tools.shell.output.BashOutputTool`
  - :class:`magi.tools.shell.kill.BashKillTool`

All three share :class:`magi.tools.shell._manager._BackgroundShellManager`
— a per-process singleton that owns the live
``asyncio.subprocess.Process`` handles and the monitor
tasks that drain their output into per-shell ring buffers.

There is only one :class:`~magi.tools.worker.ToolsWorker`
per MAGI process, so no cross-worker contention exists.
Everything stays in-process.
"""
