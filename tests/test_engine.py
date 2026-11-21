from __future__ import annotations

import asyncio

import pytest

from agentcore_verifier.engine import (
    ProofRequiredError,
    RequestScopedAgentCoreVerifier,
)
from agentcore_verifier.models import CodeInterpreterProof, TodoItem


@pytest.mark.asyncio
async def test_concurrent_requests_never_leak_todo_items() -> None:
    verifier = RequestScopedAgentCoreVerifier()

    async def delayed_model(prompt: str, state: object) -> str:
        await asyncio.sleep(0.02)
        return f"ok:{prompt}"

    verifier._model_runner = delayed_model

    trace_1, trace_2 = await asyncio.gather(
        verifier.invoke(
            request_id="req-1",
            prompt="Analyze portfolio A",
            todos=[
                TodoItem(todo_id="t1-a", title="Calc A", requires_code_proof=True),
                TodoItem(todo_id="t1-b", title="Summarize A", requires_code_proof=False),
            ],
            calculation_code="print(2 + 2)",
        ),
        verifier.invoke(
            request_id="req-2",
            prompt="Analyze portfolio B",
            todos=[
                TodoItem(todo_id="t2-a", title="Calc B", requires_code_proof=True),
            ],
            calculation_code="print(10 * 5)",
        ),
    )

    assert [t.todo_id for t in trace_1.todos] == ["t1-a", "t1-b"]
    assert [t.todo_id for t in trace_2.todos] == ["t2-a"]
    assert [t.todo_id for t in verifier.get_session("req-1").todos] == ["t1-a", "t1-b"]
    assert [t.todo_id for t in verifier.get_session("req-2").todos] == ["t2-a"]

    fresh_verifier = RequestScopedAgentCoreVerifier()
    with pytest.raises(KeyError):
        fresh_verifier.get_session("req-1")


@pytest.mark.asyncio
async def test_calculation_todo_cannot_complete_without_code_interpreter_proof() -> None:
    verifier = RequestScopedAgentCoreVerifier()
    verifier.create_session("req-proof")
    todo = verifier.add_todo(
        request_id="req-proof",
        title="Verify IRR calculation",
        todo_id="calc-irr",
        requires_code_proof=True,
    )

    with pytest.raises(ProofRequiredError):
        verifier.complete_todo("req-proof", todo.todo_id)
    assert verifier.get_session("req-proof").todos[0].status == "blocked"

    failed_proof = await verifier.verify_with_code_interpreter("req-proof", "x = 1 / 0")
    assert failed_proof.passed is False
    with pytest.raises(ProofRequiredError):
        verifier.complete_todo("req-proof", todo.todo_id, proof_id=failed_proof.proof_id)

    passing_proof = await verifier.verify_with_code_interpreter("req-proof", "print(round(0.142, 3))")
    assert passing_proof.passed is True
    completed = verifier.complete_todo(
        "req-proof",
        todo.todo_id,
        proof_id=passing_proof.proof_id,
        result_summary=passing_proof.stdout.strip(),
    )
    assert completed.status == "completed"
    assert completed.proof_id == passing_proof.proof_id


@pytest.mark.asyncio
async def test_interpreter_failure_surfaces_in_trace() -> None:
    async def failing_interpreter(request_id: str, code: str) -> CodeInterpreterProof:
        return CodeInterpreterProof(
            proof_id="proof-fail-1",
            code=code,
            stdout="",
            stderr="ZeroDivisionError: division by zero",
            exit_code=1,
            passed=False,
            duration_ms=1.2,
        )

    verifier = RequestScopedAgentCoreVerifier(code_interpreter=failing_interpreter)
    trace = await verifier.invoke(
        request_id="req-trace-fail",
        prompt="Compute ratio",
        todos=[TodoItem(todo_id="calc-1", title="Compute ratio", requires_code_proof=True)],
        calculation_code="print(1 / 0)",
    )

    assert trace.status == "blocked"
    assert "code_interpreter_failed" in trace.stages
    assert len(trace.proof_failures) == 1
    assert trace.proof_failures[0].proof_id == "proof-fail-1"
    assert trace.proof_failures[0].exit_code == 1
    assert "ZeroDivisionError" in trace.proof_failures[0].stderr
    assert trace.todos[0].status == "blocked"
    assert trace.error_envelope is not None
    assert trace.error_envelope["error_type"] == "CodeInterpreterVerificationError"


@pytest.mark.asyncio
async def test_model_timeout_returns_clean_error_envelope() -> None:
    async def slow_model(_prompt: str, _state: object) -> str:
        await asyncio.sleep(0.5)
        return "too late"

    verifier = RequestScopedAgentCoreVerifier(model_runner=slow_model, default_timeout_s=0.02)
    trace = await verifier.invoke(
        request_id="req-timeout",
        session_id="sess-timeout",
        prompt="Run slow analysis",
        todos=[TodoItem(todo_id="t-1", title="Wait for model", requires_code_proof=False)],
    )

    assert trace.status == "timeout_error"
    assert trace.output is None
    assert "model_timeout" in trace.stages
    assert trace.error_envelope is not None
    assert trace.error_envelope["error_type"] == "ModelTimeoutError"
    assert trace.error_envelope["request_id"] == "req-timeout"
    assert trace.error_envelope["session_id"] == "sess-timeout"
    assert trace.error_envelope["retryable"] is True
