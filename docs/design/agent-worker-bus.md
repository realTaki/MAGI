# AgentWorker and BUS

`AgentWorker` is a sequential consumer of `Bus.agent_job_board`. It receives a
fully constructed `Bus` through constructor injection and never opens
persistence directly.

```text
ingress -> ChatJob -> agent_job_board -> AgentWorker
                                      |-> CallLLMJob -> llm_job_board
                                      |-> RunToolJob -> tool_job_board
                                      |-> SendA2AJob -> a2a_job_board
                                      `-> DeliveryJob -> delivery_job_board
```

Each Job Board is the durable authority for its workflow:
`publish -> claim -> submit_result`. A lease expiry makes unfinished work
eligible for recovery. The worker keeps only in-process coordination state;
recovery comes from the durable Board, not an additional turn store.

The Agent reads Books for sessions, messages, settings, tool definitions,
catalog state, token usage, and prompts. It publishes work to the LLM, tool,
A2A, and delivery boards rather than invoking those external effects directly.

`ChatJobResult` records whether the input was processed. Reply text travels in
`DeliveryJob.payload`; delivery status is represented by `DeliveryResult`.
Committed Board results are authoritative over streaming updates.

Cancellation uses a `ChatJob` with `kind="run.cancel"` and the target
conversation id. Additional user input for an active conversation is released
and later claimed as steering input. Neither behavior requires a second
persistence protocol.

BUS has no compatibility paths. Any schema evolution is an explicit migration;
the runtime neither dual-writes nor falls back to another implementation.
