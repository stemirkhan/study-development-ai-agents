"""Deterministic time and stream controls for fault experiments."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import TypeAlias

from lessons.module_01_provider_contract.implementation.contracts import (
    ModelAttempt,
    ModelClientError,
    ModelEvent,
    ModelProtocolError,
    ModelRequest,
    ResponseCompleted,
    ResponseStarted,
    UnexpectedModelCall,
)


class ManualTime:
    """A monotonic clock advanced explicitly by a test or demo."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = _finite_time(start, label="start")
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    def now(self) -> float:
        return self._now

    @property
    def pending_deadlines(self) -> tuple[float, ...]:
        return tuple(
            sorted(when for when, future in self._waiters if not future.done())
        )

    async def wait_until(self, when: float) -> None:
        target = _finite_time(when, label="wait target")
        if target <= self._now:
            return

        future = asyncio.get_running_loop().create_future()
        waiter = (target, future)
        self._waiters.append(waiter)
        try:
            await future
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def advance_to(self, when: float) -> None:
        target = _finite_time(when, label="advance target")
        if target < self._now:
            raise ValueError("manual time cannot move backwards")
        self._now = target
        for deadline, future in tuple(self._waiters):
            if deadline <= target and not future.done():
                future.set_result(None)


@dataclass(frozen=True, slots=True)
class Emit:
    event: ModelEvent


@dataclass(frozen=True, slots=True)
class BlockUntil:
    release: asyncio.Event
    entered: asyncio.Event


@dataclass(frozen=True, slots=True)
class Fail:
    error: ModelClientError


StreamAction: TypeAlias = Emit | BlockUntil | Fail


@dataclass(frozen=True, slots=True)
class AttemptScript:
    actions: tuple[StreamAction, ...]

    def __post_init__(self) -> None:
        actions = tuple(self.actions)
        if not all(isinstance(action, (Emit, BlockUntil, Fail)) for action in actions):
            raise TypeError("attempt script contains an unsupported action")
        object.__setattr__(self, "actions", actions)


@dataclass(frozen=True, slots=True)
class RecordedStreamingCall:
    request: ModelRequest
    attempt: ModelAttempt


class ControlledStreamingClient:
    """Replay explicit events, waits, and failures without a network."""

    def __init__(self, scripts: Iterable[AttemptScript]) -> None:
        self._scripts = deque(scripts)
        self._calls: list[RecordedStreamingCall] = []
        self._closed_attempts: list[int] = []

    @property
    def calls(self) -> tuple[RecordedStreamingCall, ...]:
        return tuple(self._calls)

    @property
    def closed_attempts(self) -> tuple[int, ...]:
        return tuple(self._closed_attempts)

    def stream(
        self,
        request: ModelRequest,
        *,
        attempt: ModelAttempt,
    ) -> AsyncIterator[ModelEvent]:
        self._calls.append(RecordedStreamingCall(request, attempt))
        if not self._scripts:
            raise UnexpectedModelCall("no controlled stream attempt remains")
        script = self._scripts.popleft()
        return self._replay(request, attempt, script)

    async def _replay(
        self,
        request: ModelRequest,
        attempt: ModelAttempt,
        script: AttemptScript,
    ) -> AsyncIterator[ModelEvent]:
        try:
            for action in script.actions:
                if isinstance(action, Emit):
                    self._validate_event(request, action.event)
                    yield action.event
                elif isinstance(action, BlockUntil):
                    action.entered.set()
                    await action.release.wait()
                else:
                    raise action.error
        finally:
            self._closed_attempts.append(attempt.number)

    @staticmethod
    def _validate_event(request: ModelRequest, event: ModelEvent) -> None:
        if isinstance(event, ResponseStarted):
            if event.request_id != request.request_id:
                raise ModelProtocolError(
                    "stream request_id does not match the request",
                    application_request_id=request.request_id,
                    provider=event.provider_payload.provider,
                    provider_payloads=(event.provider_payload,),
                )
        elif isinstance(event, ResponseCompleted):
            if event.response.request_id != request.request_id:
                raise ModelProtocolError(
                    "terminal request_id does not match the request",
                    application_request_id=request.request_id,
                    provider=event.provider_payload.provider,
                    provider_payloads=(event.provider_payload,),
                )


def _finite_time(value: float, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{label} must be a finite number")
    return float(value)
