from __future__ import annotations

import argparse
import asyncio
import json

from agentcore_verifier.engine import RequestScopedAgentCoreVerifier
from agentcore_verifier.models import TodoItem


async def run_demo() -> dict[str, object]:
    verifier = RequestScopedAgentCoreVerifier(default_timeout_s=2.0)

    # 1. Concurrent request-scoped invocations with isolated Todo state
    trace_a, trace_b = await asyncio.gather(
        verifier.invoke(
            request_id="req-alpha",
            prompt="Verify portfolio Sharpe ratio",
            todos=[
                TodoItem(todo_id="a-1", title="Compute Sharpe ratio", requires_code_proof=True),
                TodoItem(todo_id="a-2", title="Summarize findings", requires_code_proof=False),
            ],
            calculation_code="sharpe = (0.12 - 0.04) / 0.16\nprint(f'sharpe={sharpe:.2f}')",
        ),
        verifier.invoke(
            request_id="req-beta",
            prompt="Verify VaR threshold",
            todos=[
                TodoItem(todo_id="b-1", title="Compute 95% VaR", requires_code_proof=True),
            ],
            calculation_code="var_95 = 1_000_000 * 0.032\nprint(f'var_95={var_95:.2f}')",
        ),
    )

    # 2. Interpreter failure surfaced in observability trace
    failed_trace = await verifier.invoke(
        request_id="req-fail",
        prompt="Run invalid division check",
        todos=[
            TodoItem(todo_id="f-1", title="Divide by zero check", requires_code_proof=True),
        ],
        calculation_code="result = 100 / 0\nprint(result)",
    )

    # 3. Model timeout returns clean error envelope
    async def slow_model(_prompt: str, _state: object) -> str:
        await asyncio.sleep(1.0)
        return "unreachable"

    timeout_verifier = RequestScopedAgentCoreVerifier(model_runner=slow_model, default_timeout_s=0.05)
    timeout_trace = await timeout_verifier.invoke(
        request_id="req-timeout",
        prompt="Slow model call",
        todos=[TodoItem(todo_id="t-1", title="Wait for model", requires_code_proof=False)],
    )

    return {
        "concurrent_a": trace_a.model_dump(mode="json"),
        "concurrent_b": trace_b.model_dump(mode="json"),
        "interpreter_failure": failed_trace.model_dump(mode="json"),
        "timeout_envelope": timeout_trace.model_dump(mode="json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentcore-verifier",
        description="Run the RequestScopedAgentCoreVerifier demo scenarios.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args(argv)
    summary = asyncio.run(run_demo())
    indent = 2 if args.pretty else None
    print(json.dumps(summary, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
