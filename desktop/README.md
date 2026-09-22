# desktop

Electron operator app. UI lives in `ui/`. Local sqlite schema is `schema.sql`
(file in Electron userData). Talks to `magi-asp` and to MAGI runtimes; it
does not serve a website.

Release builds carry the editable Python sources for `magi-asp` and `py-magi`.
On the first local start, MAGI atomically installs them at `~/.magi/MAGI`,
initializes a Git repository with the bundled revision as its first commit, and
runs the local ASP from that checkout. Later starts preserve that working tree
and its local changes.

There are **no deploy scripts** here. Run the app; the UI only requests.
ASP starts MAGI as a local process.
