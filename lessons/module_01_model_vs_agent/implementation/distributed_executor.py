"""Deterministic single-attempt model of a distributed execution boundary."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from .contracts import (
    AmbiguousCompletion,
    AsyncOperation,
    ResultT,
    TaskDeadlineExceeded,
    Trace,
    WorkerUnavailable,
)


class _ExecutionDeadlineExpired(Exception):
    """Private signal that distinguishes executor timeout from task timeout."""


class DistributedExecutor:
    """Place preselected work without interpreting or retrying it."""

    def __init__(
        self,
        *,
        worker_available: bool = True,
        lose_ack_after_completion: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._worker_available = worker_available
        self._lose_ack_after_completion = lose_ack_after_completion
        self._clock = clock

    async def execute(
        self,
        *,
        task_id: str,
        operation: AsyncOperation[ResultT],
        trace: Trace,
        deadline: float | None = None,
    ) -> ResultT:
        """Execute once; deadline is absolute on the injected monotonic clock."""

        if not task_id:
            raise ValueError("task_id must not be empty")

        trace.record(
            "distributed_executor",
            "task_received",
            f"task_id={task_id}",
        )
        if not self._worker_available:
            trace.record(
                "distributed_executor",
                "worker_unavailable",
                f"task_id={task_id}",
            )
            raise WorkerUnavailable(f"no worker for task {task_id}")

        remaining = None if deadline is None else deadline - self._clock()
        if remaining is not None and remaining <= 0:
            trace.record(
                "distributed_executor",
                "deadline_exceeded",
                f"task_id={task_id},before_start=true",
            )
            raise TaskDeadlineExceeded(task_id)

        attempt = 1
        trace.record(
            "distributed_executor",
            "attempt_started",
            f"task_id={task_id},attempt={attempt}",
        )
        try:
            result = await self._run(operation, remaining)
        except asyncio.CancelledError:
            trace.record(
                "distributed_executor",
                "attempt_cancelled",
                f"task_id={task_id},attempt={attempt}",
            )
            raise
        except _ExecutionDeadlineExpired as exc:
            trace.record(
                "distributed_executor",
                "completion_ambiguous",
                f"task_id={task_id},attempt={attempt},reason=deadline",
            )
            raise AmbiguousCompletion(task_id, attempt) from exc
        except Exception as exc:
            trace.record(
                "distributed_executor",
                "attempt_failed",
                f"task_id={task_id},error={type(exc).__name__}",
            )
            raise

        if self._lose_ack_after_completion:
            trace.record(
                "distributed_executor",
                "completion_ambiguous",
                f"task_id={task_id},attempt={attempt}",
            )
            raise AmbiguousCompletion(task_id, attempt)

        trace.record(
            "distributed_executor",
            "attempt_completed",
            f"task_id={task_id},attempt={attempt}",
        )
        return result

    @staticmethod
    async def _run(
        operation: AsyncOperation[ResultT],
        remaining: float | None,
    ) -> ResultT:
        if remaining is None:
            return await operation()
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                result = await operation()
        except TimeoutError as exc:
            if timeout.expired():
                raise _ExecutionDeadlineExpired from exc
            raise
        if timeout.expired():
            raise _ExecutionDeadlineExpired
        return result
