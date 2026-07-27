"""A fixed process that contains one bounded Agent step."""

from __future__ import annotations

import asyncio

from .agent_loop import AgentLoop
from .contracts import (
    InvalidUserRequest,
    LessonRuntimeError,
    Trace,
    UserRequest,
    UserResponse,
    WorkflowInvariantViolation,
    WorkflowStepFailed,
)
from .distributed_executor import DistributedExecutor


class Workflow:
    """Validate input, run the preselected Agent step, validate output."""

    def __init__(
        self,
        agent_loop: AgentLoop,
        executor: DistributedExecutor,
    ) -> None:
        self._agent_loop = agent_loop
        self._executor = executor

    async def run(
        self,
        request: UserRequest,
        *,
        trace: Trace,
        deadline: float | None = None,
    ) -> UserResponse:
        trace.record(
            "workflow",
            "workflow_started",
            f"request_id={request.request_id}",
        )
        if not request.request_id:
            trace.record("workflow", "input_rejected", "empty request_id")
            raise InvalidUserRequest("request_id must not be empty")
        if not request.text.strip():
            trace.record("workflow", "input_rejected", "empty text")
            raise InvalidUserRequest("request text must not be empty")

        trace.record("workflow", "input_validated")
        trace.record("workflow", "agent_step_selected")
        try:
            agent_result = await self._executor.execute(
                task_id=f"{request.request_id}:agent-recommendation",
                operation=lambda: self._agent_loop.run(
                    request.text,
                    trace=trace,
                ),
                trace=trace,
                deadline=deadline,
            )
        except asyncio.CancelledError:
            trace.record("workflow", "workflow_cancelled")
            raise
        except LessonRuntimeError as exc:
            trace.record(
                "workflow",
                "step_failed",
                f"error={type(exc).__name__}",
            )
            raise WorkflowStepFailed("agent_recommendation", exc) from exc

        if not agent_result.answer.strip():
            trace.record("workflow", "invariant_violated", "empty answer")
            raise WorkflowInvariantViolation("Agent returned an empty answer")

        response = UserResponse(
            request_id=request.request_id,
            text=agent_result.answer,
            model_calls=agent_result.model_calls,
            tool_calls=agent_result.tool_calls,
        )
        trace.record(
            "workflow",
            "workflow_completed",
            f"request_id={request.request_id}",
        )
        return response
