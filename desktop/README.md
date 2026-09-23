# Desktop app

The Electron app is a bootstrap shell for a local MAGI installation. It starts
ASP on `127.0.0.1:42069`; ASP starts MAGI processes. The operator WebUI lives
in `ui/` and is loaded from the local source checkout. Electron user data is
separate from MAGI and ASP state.

On first launch, the packaged shell uses its bundled Git, Python, Node.js,
npm, and uv tools to clone the complete repository into `~/.magi/MAGI`, prepare
the ignored Python environments and UI dependencies, build the WebUI, and start
ASP. The startup page shows the current stage and offers Retry if preparation
fails. Later launches reuse the same Git working tree; they do not overwrite
local changes or automatically pull upstream. Preparation currently runs again
on each packaged launch.

## What local edits affect

| Source in `~/.magi/MAGI` | How the change takes effect |
| --- | --- |
| `py-magi/` | Restart the affected MAGI process. |
| `magi-asp/` | Restart local ASP. |
| `desktop/ui/` | Rebuild the UI. The shell notices a change to `dist/index.html` and asks whether to reload. |
| `desktop/shell/`, packaged tools and build configuration | Rebuild and reinstall the Electron app. The running shell is loaded from the installed app, not the checkout. |

This gives each user an editable Git working tree for the running MAGI system.
A coding agent can modify it and merge upstream changes. Automatic revision
validation, process handoff, and rollback for self-modification are future RSI
work; they are not provided by the bootstrap shell today.

The shell's bundled tools are used for MAGI child processes only; they do not
change the system-wide `PATH`. There is no separate deployment script for the
desktop app.
