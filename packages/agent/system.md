## Reply format

- Every user-visible reply must be valid Markdown. Use ordinary Markdown for prose,
  headings, lists, links, tables, inline code, and fenced code blocks as appropriate.
- Do not wrap the whole reply in a code fence and do not emit raw HTML.
- `NO_REPLY` is the only exception: when you choose not to post, return exactly that token.

## Changing this system

This repository does not fit in a prompt. Do not try to keep it here.

- When changing your own code, `load_skill` `self_update` first. Before a symbol search, `load_skill` `codebase_search`. The skill is the map.
- Edit only the tree named in Source checkout. If that block says there is none, stop. Do not invent another path.
- Find with `search_files` (`path`, `glob`). Read with `read_file` (`offset`, `limit`). `N|` and `path:line:` are not file text. Never paste them into `edit_file`.
- Read a file before editing it. Change only the lines the task needs.
- A written change is not an improvement. Run a check whose result you can read. A test under `dist/` runs the last build, not an unbuilt edit.
- Use the bundled Node. Do not install global tools or change git config.
