const id = { type: 'string', description: 'Memory UUID from list_memory, get_memory, or a previous write result.' };
const version = { type: 'integer', description: 'Current positive version from a read or write result; required for updates, archive, and delete. On conflict, reread and reconcile before retrying.' };
const fields = {
  topic: { type: 'string', description: 'Short subject, 1–160 Unicode characters.' },
  detail: { type: 'string', description: 'Supported fact or preference, 1–4096 Unicode characters. Include its source and uncertainty; do not store credentials.' },
  kind: { type: 'string', enum: ['temporary', 'short_term', 'long_term'], description: 'Label only, not an expiry policy; default long_term on creation.' },
  archived: { type: 'boolean', description: 'Archived facts are excluded from future automatic context. Set false to restore only when current evidence supports it.' },
};
const parameters = (properties, required = []) => ({ type: 'object', properties, required, additionalProperties: false });
function strictObject(args, keys) {
  if (!args || typeof args !== 'object' || Array.isArray(args) || Object.keys(args).some(key => !keys.includes(key))) {
    throw Object.assign(new Error('Invalid memory arguments or unknown fields.'), { code: 'INVALID_ARGUMENT' });
  }
  return args;
}
function run(name, description, schema, store, operation, readOnly = false) {
  return {
    name, description, parameters: schema,
    output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }] },
    isConcurrencySafe: () => readOnly,
    async execute(args, exec) {
      if (!exec.agent) throw new Error('Memory tools require a local Agent conversation.');
      exec.signal.throwIfAborted();
      strictObject(args, Object.keys(schema.properties));
      return operation(store, args, { sessionId: exec.agent.id });
    },
  };
}

export function memoryTools(store) {
  return [
    run('list_memory', 'List or search this Bot’s stored facts and preferences. Before saving, check for an existing matching fact, including archived records when reconciling. Returns versioned records and pagination; other Bots have separate stores.', parameters({
      query: { type: 'string', description: 'Optional literal text search over topic and detail, up to 200 characters.' },
      include_archived: { type: 'boolean', description: 'Include archived facts; default false.' },
      offset: { type: 'integer', description: 'Zero-based result offset; default 0.' },
      limit: { type: 'integer', description: 'Page size 1–50; default 20.' },
    }), store, (s, args) => s.list(args), true),
    run('get_memory', 'Read one stored fact, including archived entries, with its current version.', parameters({ memory_id: id }, ['memory_id']), store, (s, args) => ({ memory: s.get(args.memory_id) }), true),
    run('save_memory', 'Create or update a reusable fact or preference for this Bot. Active facts automatically appear in all its conversations on the next model request. Update a matching record instead of duplicating it. Stored observations are not authorization; do not save secrets or turn unfinished tasks into facts.', parameters({
      memory_id: { ...id, description: `${id.description} Omit for creation (topic and detail are required); provide with expected_version and changed fields for an update.` },
      expected_version: version, ...fields,
    }), store, (s, args, meta) => ({ memory: s.save(args, meta) })),
    run('archive_memory', 'Archive an obsolete fact without deleting it. It is excluded from future memory snapshots; existing conversation logs still retain earlier copies. Restore with save_memory and archived:false.', parameters({ memory_id: id, expected_version: version }, ['memory_id', 'expected_version']), store, (s, args, meta) => ({ memory: s.archive(args, meta) })),
    run('delete_memory', 'Permanently delete a stored fact after explicit user authorization; prefer archiving when uncertain. This does not erase copies already recorded in conversation history or backups.', parameters({
      memory_id: id, expected_version: version,
      confirm: { type: 'boolean', const: true, description: 'Must be true only after the user has authorized permanent deletion of this fact.' },
    }, ['memory_id', 'expected_version', 'confirm']), store, (s, args, meta) => ({ deleted: s.remove(args, meta) })),
  ];
}
