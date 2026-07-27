from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from lessons.module_01_model_vs_agent.implementation.agent_loop import (
    AgentLoop,
)
from lessons.module_01_model_vs_agent.implementation.contracts import (
    AgentStepLimitExceeded,
    AmbiguousCompletion,
    InvalidUserRequest,
    ModelRequest,
    ModelTimeout,
    TaskDeadlineExceeded,
    ToolBinding,
    ToolSpec,
    Trace,
    UserRequest,
    WorkerUnavailable,
    WorkflowInvariantViolation,
    WorkflowStepFailed,
)
from lessons.module_01_model_vs_agent.implementation.demo import (
    ScriptedTransport,
)
from lessons.module_01_model_vs_agent.implementation.distributed_executor import (
    DistributedExecutor,
)
from lessons.module_01_model_vs_agent.implementation.model_client import (
    ModelClient,
)
from lessons.module_01_model_vs_agent.implementation.workflow import Workflow


@pytest.mark.asyncio
async def test_expired_deadline_rejects_before_start() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "done"

    executor = DistributedExecutor(clock=lambda: 10.0)
    with pytest.raises(TaskDeadlineExceeded):
        await executor.execute(
            task_id="task-1",
            operation=operation,
            trace=Trace(),
            deadline=9.0,
        )

    assert calls == 0


@pytest.mark.asyncio
async def test_unavailable_worker_does_not_start_operation() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "done"

    executor = DistributedExecutor(worker_available=False)
    with pytest.raises(WorkerUnavailable):
        await executor.execute(
            task_id="task-1",
            operation=operation,
            trace=Trace(),
        )

    assert calls == 0


@pytest.mark.asyncio
async def test_acknowledgement_loss_is_ambiguous_and_not_retried() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "side effect completed"

    executor = DistributedExecutor(lose_ack_after_completion=True)
    with pytest.raises(AmbiguousCompletion) as captured:
        await executor.execute(
            task_id="task-1",
            operation=operation,
            trace=Trace(),
        )

    assert calls == 1
    assert captured.value.attempt == 1


@pytest.mark.asyncio
async def test_deadline_after_start_is_ambiguous_and_not_retried() -> None:
    side_effects = 0
    cancelled = asyncio.Event()

    async def operation() -> str:
        nonlocal side_effects
        side_effects += 1
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    with pytest.raises(AmbiguousCompletion) as captured:
        await DistributedExecutor(clock=lambda: 0.0).execute(
            task_id="task-1",
            operation=operation,
            trace=Trace(),
            deadline=0.01,
        )

    assert side_effects == 1
    assert cancelled.is_set()
    assert captured.value.attempt == 1


@pytest.mark.asyncio
async def test_swallowed_deadline_cancellation_cannot_become_success() -> None:
    async def operation() -> str:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "late success"

    with pytest.raises(AmbiguousCompletion):
        await DistributedExecutor(clock=lambda: 0.0).execute(
            task_id="task-1",
            operation=operation,
            trace=Trace(),
            deadline=0.01,
        )


@pytest.mark.asyncio
async def test_executor_preserves_operation_error() -> None:
    failure = AgentStepLimitExceeded(1)

    async def operation() -> str:
        raise failure

    with pytest.raises(AgentStepLimitExceeded) as captured:
        await DistributedExecutor().execute(
            task_id="task-1",
            operation=operation,
            trace=Trace(),
        )

    assert captured.value is failure


@pytest.mark.asyncio
async def test_executor_does_not_misclassify_task_timeout() -> None:
    failure = TimeoutError("task-owned timeout")

    async def operation() -> str:
        raise failure

    with pytest.raises(TimeoutError) as captured:
        await DistributedExecutor(clock=lambda: 0.0).execute(
            task_id="task-1",
            operation=operation,
            trace=Trace(),
            deadline=100.0,
        )

    assert captured.value is failure


@pytest.mark.asyncio
async def test_workflow_wraps_step_failure_and_preserves_cause() -> None:
    failure = ModelTimeout("provider timed out")

    class FailingTransport:
        async def send(
            self,
            request: ModelRequest,
        ) -> Mapping[str, object]:
            del request
            raise failure

    agent = AgentLoop(
        ModelClient(FailingTransport()),
        tools={},
        max_steps=1,
    )
    workflow = Workflow(agent, DistributedExecutor())

    with pytest.raises(WorkflowStepFailed) as captured:
        await workflow.run(
            UserRequest("request-1", "question"),
            trace=Trace(),
        )

    assert captured.value.step == "agent_recommendation"
    assert captured.value.cause is failure
    assert captured.value.__cause__ is failure


@pytest.mark.asyncio
async def test_workflow_owns_empty_answer_invariant() -> None:
    workflow = Workflow(
        AgentLoop(
            ModelClient(
                ScriptedTransport([{"type": "final", "text": "   "}])
            ),
            tools={},
            max_steps=1,
        ),
        DistributedExecutor(),
    )

    with pytest.raises(WorkflowInvariantViolation):
        await workflow.run(
            UserRequest("request-1", "question"),
            trace=Trace(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_request",
    [
        UserRequest("", "question"),
        UserRequest("request-1", "   "),
    ],
)
async def test_workflow_rejects_invalid_input_before_dispatch(
    user_request: UserRequest,
) -> None:
    transport = ScriptedTransport(
        [{"type": "final", "text": "must not be consumed"}]
    )
    trace = Trace()
    workflow = Workflow(
        AgentLoop(ModelClient(transport), tools={}, max_steps=1),
        DistributedExecutor(),
    )

    with pytest.raises(InvalidUserRequest):
        await workflow.run(user_request, trace=trace)

    assert transport.requests == []
    assert "task_received" not in [event.action for event in trace.events]


@pytest.mark.asyncio
async def test_workflow_does_not_mask_validator_bug() -> None:
    failure = RuntimeError("validator bug")

    def broken_validator(
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        del arguments
        raise failure

    async def tool(arguments: Mapping[str, object]) -> str:
        del arguments
        raise AssertionError("tool must not run")

    binding = ToolBinding(
        spec=ToolSpec("get_weather", "Weather", ("city",)),
        validate=broken_validator,
        invoke=tool,
    )
    transport = ScriptedTransport(
        [
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "get_weather",
                "arguments": {"city": "Казань"},
            }
        ]
    )
    workflow = Workflow(
        AgentLoop(
            ModelClient(transport),
            tools={"get_weather": binding},
            max_steps=2,
        ),
        DistributedExecutor(),
    )

    with pytest.raises(RuntimeError) as captured:
        await workflow.run(
            UserRequest("request-1", "question"),
            trace=Trace(),
        )

    assert captured.value is failure


@pytest.mark.asyncio
async def test_cancellation_crosses_all_boundaries_unchanged() -> None:
    started = asyncio.Event()
    never = asyncio.Event()

    class BlockingTransport:
        async def send(
            self,
            request: ModelRequest,
        ) -> Mapping[str, object]:
            del request
            started.set()
            await never.wait()
            raise AssertionError("unreachable")

    trace = Trace()
    workflow = Workflow(
        AgentLoop(
            ModelClient(BlockingTransport()),
            tools={},
            max_steps=1,
        ),
        DistributedExecutor(),
    )
    task = asyncio.create_task(
        workflow.run(
            UserRequest("request-1", "question"),
            trace=trace,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    actions = [event.action for event in trace.events]
    assert "request_cancelled" in actions
    assert "agent_cancelled" in actions
    assert "attempt_cancelled" in actions
    assert "workflow_cancelled" in actions
    assert "step_failed" not in actions
    assert actions.index("request_cancelled") < actions.index("agent_cancelled")
    assert actions.index("agent_cancelled") < actions.index("attempt_cancelled")
    assert actions.index("attempt_cancelled") < actions.index(
        "workflow_cancelled"
    )
    assert "agent_completed" not in actions
    assert "attempt_completed" not in actions
    assert "workflow_completed" not in actions
