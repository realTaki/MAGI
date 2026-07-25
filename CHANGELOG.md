# Changelog

## [Unreleased]

### Added
- Unified `contacts` table (merges `employees` + `contact_entries` + `user_im_bindings`)
- `magics` + `magis` tables replacing the old `departments` tree
- Full CRUD APIs for MAGIC teams and Magi agents
- Knowledge → Contacts pane with dual-mode view (directory + notes)
- Multi-language landing page (zh / en / ja)
- Icon set for MAGIC teams, Magis, Contacts

### Changed
- "Organization" tab → "智群" (Swarm) tab with MAGI teams + Magis management
- `Employee` → `Contact` across the entire codebase
- `departments` concept fully removed
- All route imports now use `auth_gates` instead of `departments`

### Removed
- `departments` table and all related code
- `user_im_bindings` table
- `contact_entries` table
- `EmployeesPane` and `DepartmentsPane` frontend components

---

## v0.1.0 (Initial)

- C0–C2: WebUI + Telegram channel, SQLite ORM, session memory
- Agent loop with interrupt-aware message handling
- Auto-compaction and FTS5 search
- Contact and memory subsystems with LLM-callable tools
- Proactive task scheduler (APScheduler)
- Multi-LLM provider support (Anthropic, OpenAI, Minimax)
