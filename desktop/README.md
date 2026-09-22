# desktop

Electron operator app. UI lives in `ui/`. Local sqlite schema is `schema.sql`
(file in Electron userData). Talks to `magi-asp` and to MAGI runtimes; it
does not serve a website.

Release builds are a bootstrap shell with their own Git, Python, Node.js, npm,
and uv. On the first local start, the shell clones the complete public MAGI
repository into `~/.magi/MAGI`, prepares each project's ignored `.venv` or
`node_modules`, builds the operator UI, and runs the local ASP from that
checkout. Later starts preserve and run the same ordinary Git working tree, so
a coding agent can modify MAGI locally and merge future upstream changes.

There are **no deploy scripts** here. Run the app; the UI only requests.
ASP starts MAGI as a local process.
