from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class CodeInterpreterProof(BaseModel):
    """Execution proof produced by the managed Python Code Interpreter."""

    proof_id: str = Field(default_factory=lambda: f"proof_{uuid4().hex[:10]}")
    code: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    passed: bool = False
    duration_ms: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TodoItem(BaseModel):
    """Request-scoped Todo item with optional Code Interpreter proof gate."""

    todo_id: str = Field(default_factory=lambda: f"todo_{uuid4().hex[:8]}")
    title: str
    requires_code_proof: bool = False
    status: Literal["pending", "in_progress", "completed", "blocked", "failed"] = "pending"
    proof_id: str | None = None
    result_summary: str | None = None


class AgentCoreSessionState(BaseModel):
    """Strictly request-scoped state container (zero module-level global state)."""

    request_id: str
    session_id: str
    todos: list[TodoItem] = Field(default_factory=list)
    proofs: dict[str, CodeInterpreterProof] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InvocationTrace(BaseModel):
    """Observability trace for a single AgentCore verifier invocation."""

    request_id: str
    session_id: str
    status: Literal["completed", "blocked", "failed", "timeout_error"] = "completed"
    stages: list[str] = Field(default_factory=list)
    todos: list[TodoItem] = Field(default_factory=list)
    proof_failures: list[CodeInterpreterProof] = Field(default_factory=list)
    error_envelope: dict[str, Any] | None = None
    output: str | None = None
    latency_ms: float = 0.0
