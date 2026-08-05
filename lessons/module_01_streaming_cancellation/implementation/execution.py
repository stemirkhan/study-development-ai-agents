"""Deadline-aware stream collection with explicit rate-limit retries."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from typing import Protocol

from lessons.module_01_provider_contract.implementation.contracts import (
    Deadline,
    ModelAttempt,
    ModelEvent,
    ModelRateLimited,
    ModelRequest,
    ModelTimeout,
    TimeoutPhase,
)
from lessons.module_01_provider_contract.implementation.stream_assembly import (
    CollectedStream,
    collect_stream,
)


class MonotonicTime(Protocol):
    """One monotonic time domain shared by waits and absolute deadlines."""

    def now(self) -> float:
        """Return the current monotonic timestamp."""

    async def wait_until(self, when: float) -> None:
        """Wait until ``when`` or propagate caller cancellation."""


class EventLoopTime:
    """Production clock backed by the active asyncio event loop."""

    def now(self) -> float:
        return asyncio.get_running_loop().time()

    async def wait_until(self, when: float) -> None:
        await asyncio.sleep(max(0.0, when - self.now()))


class StreamingAttemptClient(Protocol):
    """A model adapter that performs exactly one visible attempt."""

    def stream(
        self,
        request: ModelRequest,
        *,
        attempt: ModelAttempt,
    ) -> AsyncIterator[ModelEvent]:
        """Start one stream without an internal retry."""


@dataclass(frozen=True, slots=True)
class RateLimitRetryPolicy:
    """Bound only rate-limit retries; other failures are never retried here."""

    max_attempts: int = 2
    fallback_delay_s: float = 1.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_attempts, int)
            or isinstance(self.max_attempts, bool)
            or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be a positive integer")
        if (
            isinstance(self.fallback_delay_s, bool)
            or not isinstance(self.fallback_delay_s, (int, float))
            or not math.isfinite(self.fallback_delay_s)
            or self.fallback_delay_s < 0
        ):
            raise ValueError("fallback_delay_s must be finite and non-negative")
        object.__setattr__(self, "fallback_delay_s", float(self.fallback_delay_s))

    def delay_for(
        self,
        error: ModelRateLimited,
        *,
        attempt_number: int,
    ) -> float:
        if error.retry_after_s is not None:
            return float(error.retry_after_s)
        return self.fallback_delay_s * (2 ** (attempt_number - 1))


class StreamingRuntime:
    """Collect a terminal response under one deadline and an explicit policy."""

    def __init__(
        self,
        *,
        time: MonotonicTime | None = None,
        retry_policy: RateLimitRetryPolicy = RateLimitRetryPolicy(),
    ) -> None:
        self._time = time if time is not None else EventLoopTime()
        self._retry_policy = retry_policy

    async def collect(
        self,
        client: StreamingAttemptClient,
        request: ModelRequest,
        *,
        deadline: Deadline,
    ) -> CollectedStream:
        attempt_number = 1

        while True:
            if self._time.now() >= deadline.at:
                raise _timeout(
                    request,
                    phase=TimeoutPhase.BEFORE_STREAM,
                )

            attempt = ModelAttempt(
                number=attempt_number,
                deadline=deadline,
            )
            try:
                source = client.stream(request, attempt=attempt)
                bounded_source = _events_before_deadline(
                    source,
                    request=request,
                    attempt=attempt,
                    time=self._time,
                )
                return await collect_stream(bounded_source)
            except ModelRateLimited as error:
                if attempt_number >= self._retry_policy.max_attempts:
                    raise

                delay = self._retry_policy.delay_for(
                    error,
                    attempt_number=attempt_number,
                )
                retry_at = self._time.now() + delay
                if retry_at >= deadline.at:
                    raise _timeout(
                        request,
                        phase=TimeoutPhase.RETRY_WAIT,
                        cause=error,
                    ) from error

                await self._time.wait_until(retry_at)
                if self._time.now() >= deadline.at:
                    raise _timeout(
                        request,
                        phase=TimeoutPhase.RETRY_WAIT,
                        cause=error,
                    ) from error
                attempt_number += 1


async def _events_before_deadline(
    source: AsyncIterator[ModelEvent],
    *,
    request: ModelRequest,
    attempt: ModelAttempt,
    time: MonotonicTime,
) -> AsyncIterator[ModelEvent]:
    iterator = aiter(source)
    try:
        while True:
            try:
                event = await _next_before_deadline(
                    iterator,
                    request=request,
                    attempt=attempt,
                    time=time,
                )
            except StopAsyncIteration:
                return
            yield event
    finally:
        await _close_iterator_before_deadline(
            iterator,
            request=request,
            attempt=attempt,
            time=time,
        )


async def _next_before_deadline(
    iterator: AsyncIterator[ModelEvent],
    *,
    request: ModelRequest,
    attempt: ModelAttempt,
    time: MonotonicTime,
) -> ModelEvent:
    if time.now() >= attempt.deadline.at:
        raise _timeout(request, phase=TimeoutPhase.STREAM_READ)

    event_task = asyncio.create_task(_read_next(iterator))
    deadline_task = asyncio.create_task(time.wait_until(attempt.deadline.at))
    try:
        done, _ = await asyncio.wait(
            (event_task, deadline_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        await _cancel_and_wait(event_task, deadline_task)
        raise

    if deadline_task in done:
        await _cancel_and_wait(event_task, deadline_task)
        raise _timeout(request, phase=TimeoutPhase.STREAM_READ)

    if event_task in done:
        deadline_task.cancel()
        await _settle(deadline_task)
        return event_task.result()

    raise AssertionError("deadline race completed without a winner")


async def _cancel_and_wait(*tasks: asyncio.Task[object]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _settle(task: asyncio.Task[object]) -> None:
    await asyncio.gather(task, return_exceptions=True)


async def _read_next(iterator: AsyncIterator[ModelEvent]) -> ModelEvent:
    return await anext(iterator)


async def _close_iterator_before_deadline(
    iterator: AsyncIterator[ModelEvent],
    *,
    request: ModelRequest,
    attempt: ModelAttempt,
    time: MonotonicTime,
) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return

    close_task = asyncio.create_task(_await_cleanup(close()))
    deadline_task = asyncio.create_task(time.wait_until(attempt.deadline.at))
    try:
        done, _ = await asyncio.wait(
            (close_task, deadline_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        await _cancel_and_wait(close_task, deadline_task)
        raise

    if close_task in done:
        deadline_task.cancel()
        await _settle(deadline_task)
        close_task.result()
        return

    await _cancel_and_wait(close_task, deadline_task)
    raise _timeout(request, phase=TimeoutPhase.STREAM_READ)


async def _await_cleanup(operation: Awaitable[object]) -> None:
    await operation


def _timeout(
    request: ModelRequest,
    *,
    phase: TimeoutPhase,
    cause: Exception | None = None,
) -> ModelTimeout:
    return ModelTimeout(
        f"model stream deadline expired during {phase.value}",
        phase=phase,
        application_request_id=request.request_id,
        cause=cause or TimeoutError("absolute deadline expired"),
    )
