# Architecture Overview

## 1. Request-Scoped Isolation (`AgentCoreSessionState`)

Every call to `RequestScopedAgentCoreVerifier.run()` instantiates a fresh `AgentCoreSessionState(request_id=..., session_id=...)` scoped strictly to that invocation.
There are **no** module-level mutable lists (`TODOS = []`) or shared global registries. Concurrent asyncio/threaded requests cannot observe or mutate another request's `TodoItem` list or `CodeInterpreterProof` records.

## 2. Managed Python Code Interpreter Proof Gate

When a `TodoItem` has `requires_code_proof=True` (for example, financial, statistical, or numerical calculations):
- The engine invokes the configured Python Code Interpreter runner (Bedrock AgentCore managed Code Interpreter in production, or an injectable sandbox runner in tests).
- The execution produces a `CodeInterpreterProof` containing `stdout`, `stderr`, `exit_code`, and `passed`.
- `complete_todo()` enforces that `proof is not None and proof.passed is True` before transitioning `status` to `"completed"`.

## 3. Observability & Failure Surfacing (`InvocationTrace`)

Every invocation returns an `InvocationTrace` containing:
- `request_id` and `session_id`
- `stages`: ordered execution stage log
- `proof_failures`: explicit list of failed `CodeInterpreterProof` records (syntax error, runtime exception, non-zero exit code)
- `error_envelope`: structured dictionary when a model timeout or fatal error occurs
- `latency_ms`: end-to-end execution duration

## 4. Clean Timeout Error Envelope

If the model invocation raises `TimeoutError` or `asyncio.TimeoutError`, the engine catches it, records `"timeout_error"` in the trace, and returns:

```json
{
  "status": "error",
  "error_type": "ModelTimeoutError",
  "message": "Model invocation timed out",
  "request_id": "<request_id>",
  "retryable": true
}
```
