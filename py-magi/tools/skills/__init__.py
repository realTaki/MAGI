"""Skill tools.

One tool that lets the LLM fetch a skill's full markdown body on
demand, complementing the frontmatter summary the system prompt
already advertises:

  - :class:`magi.tools.skills.load_skill.LoadSkillTool`

Lives next to the other file-backed tools (``tools/filesystem``,
``tools/shell``, etc.) because it is, structurally, a special
file-read tool for SKILL.md format — it knows about the
``--- name / description / version ---`` frontmatter, rewrites
relative paths to absolute ones (Progressive Disclosure Level 3),
and prepends the "Skill Root Directory" hint so the LLM can follow
``scripts/foo.py``-style references via :func:`magi.tools.filesystem.read_file`.

The actual registry lives on the bus as ``bus.skills_book``;
see :mod:`magi.bus.firmwares.books.file.skillsBook`.
"""
