from __future__ import annotations

import asyncio
import contextlib
import io
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from agentcore_verifier.models import (
    AgentCoreSessionState,
    CodeInterpreterProof,
    InvocationTrace,
    TodoItem,
)


class ProofRequiredError(RuntimeError):
    """Raised when a calculation todo attempts completion without a passing Code Interpreter proof."""


CodeInterpreterRunner = Callable[[str, str], Awaitable[CodeInterpreterProof] | CodeInterpreterProof]
ModelRunner = Callable[[str, AgentCoreSessionState], Awaitable[str] | str]


def _default_code_interpreter(request_id: str, code: str) -> CodeInterpreterProof:
    """Isolated local Python execution fallback mimicking Bedrock AgentCore Code Interpreter."""
    start = time.perf_counter()
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0
    namespace: dict[str, Any] = {"__name__": "__main__", "request_id": request_id}

    try:
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            exec(compile(code, "<agentcore-code-interpreter>", "exec"), namespace, namespace)
    except Exception as exc:  # noqa: BLE001
        exit_code = 1
        stderr_buf.write(f"{type(exc).__name__}: {exc}\n")

    duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
    return CodeInterpreterProof(
        proof_id=f"proof-{uuid.uuid4().hex[:12]}",
        code=code,
        stdout=stdout_buf.getvalue(),
        stderr=stderr_buf.getvalue(),
        exit_code=exit_code,
        passed=exit_code == 0,
        duration_ms=duration_ms,
    )


async def _default_model_runner(prompt: str, state: AgentCoreSessionState) -> str:
    """Default Strands/Bedrock model runner stub for local verification and CLI demos."""
    await asyncio.sleep(0)
    return f"Verified response for request_id={state.request_id}: {prompt}"


class RequestScopedAgentCoreVerifier:
    """AgentCore & Strands verifier enforcing strictly request_id-isolated Todo state."""

    def __init__(
        self,
        code_interpreter: CodeInterpreterRunner | None = None,
        model_runner: ModelRunner | None = None,
        default_timeout_s: float = 10.0,
    ) -> None:
        self._code_interpreter: CodeInterpreterRunner = code_interpreter or _default_code_interpreter
        self._model_runner: ModelRunner = model_runner or _default_model_runner
        self.default_timeout_s = default_timeout_s
        # Strictly instance-bound, keyed by request_id; zero module-level global lists.
        self._sessions: dict[str, AgentCoreSessionState] = {}

    def create_session(
        self,
        request_id: str,
        session_id: str | None = None,
        todos: list[TodoItem] | None = None,
    ) -> AgentCoreSessionState:
        if not request_id:
            raise ValueError("request_id must be non-empty")
        resolved_session_id = session_id or f"sess-{request_id}"
        cloned_todos = [todo.model_copy(deep=True) for todo in (todos or [])]
        state = AgentCoreSessionState(
            request_id=request_id,
            session_id=resolved_session_id,
            todos=cloned_todos,
        )
        self._sessions[request_id] = state
        return state

    def get_session(self, request_id: str) -> AgentCoreSessionState:
        if request_id not in self._sessions:
            raise KeyError(f"No session state found for request_id={request_id}")
        return self._sessions[request_id]

    def add_todo(
        self,
        request_id: str,
        title: str,
        todo_id: str | None = None,
        requires_code_proof: bool = False,
    ) -> TodoItem:
        state = self._sessions.get(request_id) or self.create_session(request_id=request_id)
        item = TodoItem(
            todo_id=todo_id or f"todo-{uuid.uuid4().hex[:8]}",
            title=title,
            requires_code_proof=requires_code_proof,
        )
        state.todos.append(item)
        return item

    async def verify_with_code_interpreter(
        self,
        request_id: str,
        code: str,
    ) -> CodeInterpreterProof:
        state = self._sessions.get(request_id) or self.create_session(request_id=request_id)
        result = self._code_interpreter(request_id, code)
        proof = await result if asyncio.iscoroutine(result) else result
        state.proofs[proof.proof_id] = proof
        return proof

    def complete_todo(
        self,
        request_id: str,
        todo_id: str,
        proof_id: str | None = None,
        result_summary: str | None = None,
    ) -> TodoItem:
        state = self.get_session(request_id)
        target = next((t for t in state.todos if t.todo_id == todo_id), None)
        if target is None:
            raise KeyError(f"Todo {todo_id} not found in request_id={request_id}")

        if target.requires_code_proof:
            if not proof_id or proof_id not in state.proofs:
                target.status = "blocked"
                raise ProofRequiredError(
                    f"Todo '{target.todo_id}' requires a passing Code Interpreter proof."
                )
            proof = state.proofs[proof_id]
            if not proof.passed or proof.exit_code != 0:
                target.status = "blocked"
                raise ProofRequiredError(
                    f"Todo '{target.todo_id}' cannot complete: proof '{proof_id}' failed "
                    f"(exit_code={proof.exit_code})."
                )
            target.proof_id = proof_id

        target.status = "completed"
        target.result_summary = result_summary
        return target

    async def invoke(
        self,
        request_id: str,
        prompt: str,
        session_id: str | None = None,
        todos: list[TodoItem] | None = None,
        calculation_code: str | None = None,
        timeout_s: float | None = None,
    ) -> InvocationTrace:
        start = time.perf_counter()
        state = self.create_session(
            request_id=request_id,
            session_id=session_id,
            todos=todos,
        )
        stages: list[str] = ["session_initialized"]
        proof_failures: list[CodeInterpreterProof] = []
        effective_timeout = timeout_s if timeout_s is not None else self.default_timeout_s

        try:
            maybe_coro = self._model_runner(prompt, state)
            if asyncio.iscoroutine(maybe_coro):
                output = await asyncio.wait_for(maybe_coro, timeout=effective_timeout)
            else:
                output = maybe_coro
            stages.append("model_completed")
        except (asyncio.TimeoutError, TimeoutError) as exc:
            stages.append("model_timeout")
            latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
            return InvocationTrace(
                request_id=state.request_id,
                session_id=state.session_id,
                status="timeout_error",
                stages=stages,
                todos=[t.model_copy(deep=True) for t in state.todos],
                proof_failures=proof_failures,
                error_envelope={
                    "error_type": "ModelTimeoutError",
                    "message": f"Model invocation timed out after {effective_timeout}s: {exc}",
                    "request_id": state.request_id,
                    "session_id": state.session_id,
                    "timeout_s": effective_timeout,
                    "retryable": True,
                },
                output=None,
                latency_ms=latency_ms,
            )

        calc_todos = [t for t in state.todos if t.requires_code_proof]
        if calculation_code is not None or calc_todos:
            code_to_run = calculation_code or ""
            stages.append("code_interpreter_started")
            proof = await self.verify_with_code_interpreter(state.request_id, code_to_run)
            if not proof.passed or proof.exit_code != 0:
                proof_failures.append(proof)
                for todo in calc_todos:
                    todo.status = "blocked"
                stages.append("code_interpreter_failed")
                latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
                return InvocationTrace(
                    request_id=state.request_id,
                    session_id=state.session_id,
                    status="blocked",
                    stages=stages,
                    todos=[t.model_copy(deep=True) for t in state.todos],
                    proof_failures=proof_failures,
                    error_envelope={
                        "error_type": "CodeInterpreterVerificationError",
                        "message": proof.stderr.strip() or "Code Interpreter exited with non-zero status.",
                        "request_id": state.request_id,
                        "proof_id": proof.proof_id,
                        "exit_code": proof.exit_code,
                    },
                    output=output,
                    latency_ms=latency_ms,
                )
            stages.append("code_interpreter_passed")
            for todo in calc_todos:
                self.complete_todo(
                    state.request_id,
                    todo.todo_id,
                    proof_id=proof.proof_id,
                    result_summary=proof.stdout.strip() or "Verified via Code Interpreter",
                )

        for todo in state.todos:
            if not todo.requires_code_proof and todo.status in ("pending", "in_progress"):
                self.complete_todo(
                    state.request_id,
                    todo.todo_id,
                    result_summary="Completed",
                )

        stages.append("todos_verified")
        latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
        return InvocationTrace(
            request_id=state.request_id,
            session_id=state.session_id,
            status="completed",
            stages=stages,
            todos=[t.model_copy(deep=True) for t in state.todos],
            proof_failures=proof_failures,
            error_envelope=None,
            output=output,
            latency_ms=latency_ms,
        )
