# py-magi

Python implementation of the MAGI service. Packages live at this project root
(`magi`, `bus`, `agent`, …). The `magi` package owns one BUS, its workers,
and channel adapters such as `channels.asp`.

The operator UI lives in [`../desktop/`](../desktop/). MAGI attaches to the
sibling [`../magi-asp/`](../magi-asp/) over ASP (`/sessions`, `/connect`).
