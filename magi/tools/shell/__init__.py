"""Shell command execution.

Three tools the LLM uses together:

  - :class:`magi.tools.shell.run.BashRunTool`
  - :class:`magi.tools.shell.output.BashOutputTool`
  - :class:`magi.tools.shell.kill.BashKillTool`

All three share :class:`magi.tools.shell._manager._BackgroundShellManager`
— a process-asyncio-task singleton that owns the
in-flight background shells and the monitor tasks that
drain their output into the per-shell ring buffers.
"""