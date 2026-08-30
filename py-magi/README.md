# py-magi

Python implementation of the MAGI service. Packages live at this project root
(`magi`, `bus`, `agent`, …). The `magi` package owns one BUS, its workers,
and an ASP client onto `webapp/asp`.

The repository-level overview is in [`../README.md`](../README.md); the operator
The browser UI is the sibling [`../webapp/`](../webapp/) project; the Electron
shell that consumes it is [`../desktop/`](../desktop/).
