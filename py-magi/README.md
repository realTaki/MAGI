# py-magi

Python implementation of the MAGI service. Packages live at this project root
(`magi`, `bus`, `agent`, …). The `magi` package owns the FastAPI service,
its BUS, and its attached workers.

The repository-level overview is in [`../README.md`](../README.md); the operator
The browser UI is the sibling [`../webapp/`](../webapp/) project; the Electron
shell that consumes it is [`../desktop/`](../desktop/).
