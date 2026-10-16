# Later

Agents can read their own job history.

`jobs.input` stores the provider key in plaintext. Jobs do not enter the LLM context, so that stays as it is. A later change stores only a secret reference on the job. The key lives only in the secrets store.
