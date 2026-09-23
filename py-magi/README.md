# py-magi

One MAGI. Start it with one command:

```bash
python -m magi <handle> <base> <token>
```

(`magi` on PATH is the same.) `magi.py` at this project root is the composition root; the packages (`bus`, `agent`, `channels`, …) sit next to it. MAGI attaches to [`../magi-asp/`](../magi-asp/) over ASP (`/sessions`, `/connect`). The operator UI is [`../desktop/`](../desktop/).

