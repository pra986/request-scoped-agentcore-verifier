# request-scoped-agentcore-verifier

> **Project 39 / X8: Supplement / Production — AWS Bedrock AgentCore Tools & Code Interpreter**

AWS Bedrock AgentCore and Strands agent runtime featuring **strictly request-scoped Todo state** (zero cross-request global leaks) gated on **managed Python Code Interpreter verification proof**, full observability traces, and clean error envelopes on model timeout.

---

## Architecture

Traditional prototype agents often store task/todo lists in module-level globals (`TODOS = []`), causing cross-request contamination under concurrent invocations. `request-scoped-agentcore-verifier` eliminates global mutable state by binding every `TodoItem` and `CodeInterpreterProof` to a dedicated `AgentCoreSessionState` keyed by `request_id` (and `session_id`), wrapped in an `InvocationTrace`.

```
Client Request (request_id, session_id, prompt)
        │
        ▼
[1. Request-Scoped State Init]  ──► AgentCoreSessionState(request_id)
        │
        ▼
[2. Strands Agent Planning]     ──► TodoItem(requires_code_proof=True/False)
        │
        ▼
[3. Managed Code Interpreter]   ──► Python sandbox execution ─► CodeInterpreterProof
        │                                  │
        │                          (Failure surfaced in InvocationTrace)
        ▼
[4. Completion Gate]            ──► Calculation Todo blocked unless proof.passed == True
        │
        ▼
[5. Timeout & Error Envelope]   ──► Clean structured error envelope on model timeout
        │
        ▼
[6. Observability Trace Export] ──► InvocationTrace (spans, proofs, errors, status)
```

---

## 6-Stage Workflow

1. **Request-Scoped State Initialization**: Creates an isolated `AgentCoreSessionState` instance per `request_id`. No module-level lists or shared mutable caches are used.
2. **Todo Planning**: Registers `TodoItem` entries for the request, flagging calculation/quantitative tasks with `requires_code_proof=True`.
3. **Managed Python Code Interpreter Execution**: Executes verification scripts in the managed Bedrock AgentCore Python Code Interpreter and records stdout, stderr, exit code, and `passed` status in `CodeInterpreterProof`.
4. **Proof-Gated Todo Completion**: Any `TodoItem` with `requires_code_proof=True` cannot transition to `completed` unless a passing `CodeInterpreterProof` (`passed=True`) is attached.
5. **Model Timeout Handling**: If the underlying Bedrock/Strands model invocation exceeds the configured timeout, the engine halts cleanly and returns a structured error envelope (`status="timeout_error"`) without leaking partial state.
6. **Observability Trace Emission**: Emits an `InvocationTrace` capturing request metadata, stage timings, todo states, interpreter proof outcomes (including failures), and error envelopes.

---

## Data Contract

| Model | Key Fields | Purpose |
| :--- | :--- | :--- |
| `TodoItem` | `todo_id`, `title`, `requires_code_proof`, `status`, `proof_id` | Request-scoped work item; calculation items require proof |
| `CodeInterpreterProof` | `proof_id`, `code`, `stdout`, `stderr`, `exit_code`, `passed`, `duration_ms` | Verifiable execution record from managed Python Code Interpreter |
| `AgentCoreSessionState` | `request_id`, `session_id`, `todos`, `proofs` | Isolated per-request state container (zero global state) |
| `InvocationTrace` | `request_id`, `status`, `stages`, `proof_failures`, `error_envelope`, `latency_ms` | End-to-end observability & audit trace |

---

## Production Controls

- **Zero Cross-Request Leaks**: Concurrent invocations operate on independent `AgentCoreSessionState` objects; verified via concurrent `pytest` stress tests.
- **Strict Proof Gate**: Calculation todos raise `ProofRequiredError` / remain blocked if no passing `CodeInterpreterProof` exists.
- **Failure Transparency**: Interpreter syntax errors, runtime exceptions, and non-zero exit codes are captured in `InvocationTrace.proof_failures` rather than silently ignored.
- **Clean Timeout Envelopes**: Model timeouts return `{ "status": "error", "error_type": "ModelTimeoutError", "request_id": "...", "retryable": true }` instead of raw stack traces.

---

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run unit & concurrency tests
pytest -v

# Run CLI demo
python -m agentcore_verifier.cli
```

---

## Attribution

Pinned attribution: Inspired by and adapted from `ed-donner/production` (`finale/README.md`) — AWS Bedrock AgentCore tools, Strands agents, and managed Python Code Interpreter patterns.
