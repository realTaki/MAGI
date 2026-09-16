# desktop

Electron operator app. UI lives in `ui/`. Local sqlite schema is `schema.sql`
(file in Electron userData). Talks to `magi-asp` and to MAGI runtimes; it
does not serve a website.

There are **no deploy scripts** here. Run the app; the UI only requests.
ASP starts MAGI (local process, or a k8s Pod when ASP itself is on Kubernetes).

