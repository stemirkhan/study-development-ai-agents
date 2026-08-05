from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import FrozenInstanceError

import pytest

from lessons.module_01_provider_contract.implementation.contracts import (
    Deadline,
    InputMessage,
    MessageRole,
    ModelAttempt,
    ModelEvent,
    ModelRateLimited,
    ModelRequest,
    ModelResponse,
    ModelStreamInterrupted,
    ModelTimeout,
    ModelTransportError,
    OutputCompleted,
    ProviderPayload,
    ResponseCompleted,
    ResponseOutcome,
    ResponseStarted,
    TextDelta,
    TextOutput,
    TimeoutPhase,
    Usage,
    UsageReported,
)
from lessons.module_01_streaming_cancellation.implementation.execution import (
    RateLimitRetryPolicy,
    StreamingRuntime,
)
from lessons.module_01_streaming_cancellation.implementation.testing import (
    AttemptScript,
    BlockUntil,
    ControlledStreamingClient,
    Emit,
    Fail,
    ManualTime,
)


PROVIDER = "fixture-llm"
MODEL = "fixture-model"


class FutureBackedStream:
    """Exercise the full AsyncIterator contract, not only async generators."""

    def __init__(self, events: tuple[ModelEvent, ...]) -> None:
        self._events = deque(events)
        self.closed = False

    def __aiter__(self) -> FutureBackedStream:
        return self

    def __anext__(self) -> asyncio.Future[ModelEvent]:
        result = asyncio.get_running_loop().create_future()
        if self._events:
            result.set_result(self._events.popleft())
        else:
            result.set_exception(StopAsyncIteration())
        return result

    async def aclose(self) -> None:
        self.closed = True


class FailingCloseStream(FutureBackedStream):
    def __init__(
        self,
        events: tuple[ModelEvent, ...],
        close_error: Exception,
    ) -> None:
        super().__init__(events)
        self._close_error = close_error

    async def aclose(self) -> None:
        self.closed = True
        raise self._close_error


class BlockingCloseStream(FutureBackedStream):
    def __init__(
        self,
        events: tuple[ModelEvent, ...],
        *,
        close_entered: asyncio.Event,
        close_release: asyncio.Event,
    ) -> None:
        super().__init__(events)
        self._close_entered = close_entered
        self._close_release = close_release

    async def aclose(self) -> None:
        self._close_entered.set()
        try:
            await self._close_release.wait()
        finally:
            self.closed = True


class SingleStreamClient:
    def __init__(self, source: AsyncIterator[ModelEvent]) -> None:
        self._source = source
        self.calls = 0

    def stream(
        self,
        request: ModelRequest,
        *,
        attempt: ModelAttempt,
    ) -> AsyncIterator[ModelEvent]:
        del request, attempt
        self.calls += 1
        return self._source


def raw(kind: str, data: str = "{}") -> ProviderPayload:
    return ProviderPayload(
        provider=PROVIDER,
        kind=kind,
        data=data.encode("utf-8"),
    )


def make_request(request_id: str = "request-1") -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        model=MODEL,
        messages=(InputMessage(MessageRole.USER, "Нужен ли зонт?"),),
    )


def make_response(
    request: ModelRequest,
    *,
    outcome: ResponseOutcome = ResponseOutcome.COMPLETED,
    text: str = "Возьмите зонт.",
    usage: Usage | None = None,
) -> ModelResponse:
    return ModelResponse(
        request_id=request.request_id,
        provider=PROVIDER,
        model=MODEL,
        response_id="response-1",
        outcome=outcome,
        outputs=(TextOutput(text),),
        usage=usage,
        provider_payload=raw("response"),
    )


def completed_actions(
    request: ModelRequest,
    response: ModelResponse,
) -> tuple[Emit, ...]:
    output = response.outputs[0]
    assert isinstance(output, TextOutput)
    events: list[ModelEvent] = [
        ResponseStarted(
            sequence=1,
            provider_payload=raw("response.started"),
            request_id=request.request_id,
            response_id=response.response_id,
            model=response.model,
        ),
        TextDelta(
            sequence=2,
            provider_payload=raw("text.delta"),
            output_index=0,
            delta=output.text,
        ),
        OutputCompleted(
            sequence=3,
            provider_payload=raw("output.completed"),
            output_index=0,
            output=output,
        ),
    ]
    if response.usage is not None:
        events.append(
            UsageReported(
                sequence=4,
                provider_payload=raw("usage"),
                usage=response.usage,
            )
        )
    events.append(
        ResponseCompleted(
            sequence=len(events) + 1,
            provider_payload=raw("response.completed"),
            response=response,
        )
    )
    return tuple(Emit(event) for event in events)


def completed_events(
    request: ModelRequest,
    response: ModelResponse,
) -> tuple[ModelEvent, ...]:
    return tuple(action.event for action in completed_actions(request, response))


async def wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


async def test_transport_break_after_prefix_preserves_exact_events() -> None:
    request = make_request()
    started = ResponseStarted(1, raw("start"), request.request_id, "r-1", MODEL)
    delta = TextDelta(2, raw("delta"), 0, "Возьмите зон")
    error = ModelTransportError("connection reset", received_bytes=True)
    client = ControlledStreamingClient(
        (AttemptScript((Emit(started), Emit(delta), Fail(error))),)
    )
    clock = ManualTime()
    runtime = StreamingRuntime(time=clock)

    with pytest.raises(ModelStreamInterrupted) as captured:
        await runtime.collect(client, request, deadline=Deadline(10))

    assert len(captured.value.events) == 2
    assert captured.value.events[0] is started
    assert captured.value.events[1] is delta
    assert captured.value.cause is error
    assert not any(
        isinstance(event, (OutputCompleted, ResponseCompleted))
        for event in captured.value.events
    )
    assert [call.attempt.number for call in client.calls] == [1]
    assert client.closed_attempts == (1,)
    assert clock.pending_deadlines == ()


async def test_caller_cancellation_reaches_blocked_source() -> None:
    request = make_request()
    entered = asyncio.Event()
    release = asyncio.Event()
    started = ResponseStarted(1, raw("start"), request.request_id, "r-1", MODEL)
    client = ControlledStreamingClient(
        (AttemptScript((Emit(started), BlockUntil(release, entered))),)
    )
    clock = ManualTime()
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=Deadline(10),
        )
    )
    await entered.wait()

    task.cancel("caller stopped")

    with pytest.raises(asyncio.CancelledError, match="caller stopped"):
        await task
    assert len(client.calls) == 1
    assert client.closed_attempts == (1,)
    assert clock.pending_deadlines == ()


async def test_deadline_before_first_event_is_plain_timeout() -> None:
    request = make_request()
    entered = asyncio.Event()
    release = asyncio.Event()
    client = ControlledStreamingClient(
        (AttemptScript((BlockUntil(release, entered),)),)
    )
    clock = ManualTime()
    deadline = Deadline(10)
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=deadline,
        )
    )
    await entered.wait()

    clock.advance_to(deadline.at)

    with pytest.raises(ModelTimeout) as captured:
        await task
    assert captured.value.phase is TimeoutPhase.STREAM_READ
    assert captured.value.application_request_id == request.request_id
    assert client.calls[0].attempt.deadline is deadline
    assert client.closed_attempts == (1,)
    assert clock.pending_deadlines == ()


async def test_expired_deadline_never_opens_the_stream() -> None:
    request = make_request()
    client = ControlledStreamingClient((AttemptScript(()),))
    clock = ManualTime(start=10)

    with pytest.raises(ModelTimeout) as captured:
        await StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=Deadline(10),
        )

    assert captured.value.phase is TimeoutPhase.BEFORE_STREAM
    assert client.calls == ()


async def test_deadline_after_prefix_is_an_interruption() -> None:
    request = make_request()
    entered = asyncio.Event()
    release = asyncio.Event()
    started = ResponseStarted(1, raw("start"), request.request_id, "r-1", MODEL)
    delta = TextDelta(2, raw("delta"), 0, "Возьмите зон")
    client = ControlledStreamingClient(
        (
            AttemptScript(
                (Emit(started), Emit(delta), BlockUntil(release, entered))
            ),
        )
    )
    clock = ManualTime()
    deadline = Deadline(10)
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=deadline,
        )
    )
    await entered.wait()

    clock.advance_to(deadline.at)

    with pytest.raises(ModelStreamInterrupted) as captured:
        await task
    assert captured.value.events[0] is started
    assert captured.value.events[1] is delta
    assert isinstance(captured.value.cause, ModelTimeout)
    assert captured.value.cause.phase is TimeoutPhase.STREAM_READ
    assert len(client.calls) == 1
    assert client.closed_attempts == (1,)


async def test_deadline_wins_when_terminal_is_released_at_the_same_instant() -> None:
    request = make_request()
    response = make_response(request)
    actions = completed_actions(request, response)
    entered = asyncio.Event()
    release = asyncio.Event()
    client = ControlledStreamingClient(
        (
            AttemptScript(
                (
                    *actions[:-1],
                    BlockUntil(release, entered),
                    actions[-1],
                )
            ),
        )
    )
    clock = ManualTime()
    deadline = Deadline(10)
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=deadline,
        )
    )
    await entered.wait()

    release.set()
    clock.advance_to(deadline.at)

    with pytest.raises(ModelStreamInterrupted) as captured:
        await task
    assert isinstance(captured.value.cause, ModelTimeout)
    assert captured.value.events == tuple(
        action.event for action in actions[:-1]
    )
    assert not any(
        isinstance(event, ResponseCompleted) for event in captured.value.events
    )
    assert client.closed_attempts == (1,)


async def test_429_waits_then_retries_with_the_same_deadline() -> None:
    request = make_request()
    rate_limit = ModelRateLimited(
        "too many requests",
        status_code=429,
        retry_after_s=2,
    )
    response = make_response(request)
    client = ControlledStreamingClient(
        (
            AttemptScript((Fail(rate_limit),)),
            AttemptScript(completed_actions(request, response)),
        )
    )
    clock = ManualTime()
    deadline = Deadline(10)
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=deadline,
        )
    )
    await wait_until(lambda: clock.pending_deadlines == (2.0,))

    assert [call.attempt.number for call in client.calls] == [1]
    clock.advance_to(2)
    result = await task

    assert result.response is response
    assert [call.attempt.number for call in client.calls] == [1, 2]
    assert all(call.attempt.deadline is deadline for call in client.calls)
    assert client.closed_attempts == (1, 2)
    assert clock.pending_deadlines == ()


async def test_missing_retry_after_uses_the_explicit_fallback_delay() -> None:
    request = make_request()
    rate_limit = ModelRateLimited(
        "too many requests",
        status_code=429,
        retry_after_s=None,
    )
    response = make_response(request)
    client = ControlledStreamingClient(
        (
            AttemptScript((Fail(rate_limit),)),
            AttemptScript(completed_actions(request, response)),
        )
    )
    clock = ManualTime()
    runtime = StreamingRuntime(
        time=clock,
        retry_policy=RateLimitRetryPolicy(
            max_attempts=2,
            fallback_delay_s=0.5,
        ),
    )
    task = asyncio.create_task(
        runtime.collect(client, request, deadline=Deadline(10))
    )
    await wait_until(lambda: clock.pending_deadlines == (0.5,))

    clock.advance_to(0.5)
    result = await task

    assert result.response is response
    assert [call.attempt.number for call in client.calls] == [1, 2]


async def test_attempt_limit_returns_the_last_rate_limit_without_a_third_call() -> None:
    request = make_request()
    first = ModelRateLimited("first 429", status_code=429, retry_after_s=1)
    second = ModelRateLimited("second 429", status_code=429, retry_after_s=1)
    client = ControlledStreamingClient(
        (
            AttemptScript((Fail(first),)),
            AttemptScript((Fail(second),)),
        )
    )
    clock = ManualTime()
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=Deadline(10),
        )
    )
    await wait_until(lambda: clock.pending_deadlines == (1.0,))
    clock.advance_to(1)

    with pytest.raises(ModelRateLimited) as captured:
        await task

    assert captured.value is second
    assert [call.attempt.number for call in client.calls] == [1, 2]
    assert clock.pending_deadlines == ()


async def test_retry_after_must_fit_the_remaining_deadline() -> None:
    request = make_request()
    rate_limit = ModelRateLimited(
        "too many requests",
        status_code=429,
        retry_after_s=3,
    )
    client = ControlledStreamingClient(
        (AttemptScript((Fail(rate_limit),)),)
    )
    clock = ManualTime()

    with pytest.raises(ModelTimeout) as captured:
        await StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=Deadline(2),
        )

    assert captured.value.phase is TimeoutPhase.RETRY_WAIT
    assert captured.value.cause is rate_limit
    assert len(client.calls) == 1
    assert client.closed_attempts == (1,)
    assert clock.pending_deadlines == ()


async def test_rate_limit_after_prefix_is_not_retried() -> None:
    request = make_request()
    started = ResponseStarted(1, raw("start"), request.request_id, "r-1", MODEL)
    rate_limit = ModelRateLimited(
        "late rate limit",
        status_code=429,
        retry_after_s=1,
    )
    client = ControlledStreamingClient(
        (AttemptScript((Emit(started), Fail(rate_limit))),)
    )
    clock = ManualTime()

    with pytest.raises(ModelStreamInterrupted) as captured:
        await StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=Deadline(10),
        )

    assert captured.value.events == (started,)
    assert captured.value.cause is rate_limit
    assert len(client.calls) == 1
    assert clock.pending_deadlines == ()


async def test_truncated_response_is_terminal_not_a_retryable_failure() -> None:
    request = make_request()
    response = make_response(
        request,
        outcome=ResponseOutcome.TRUNCATED,
        text="Ответ оборвался по лимиту",
        usage=Usage(input_tokens=8, output_tokens=4, total_tokens=12),
    )
    client = ControlledStreamingClient(
        (AttemptScript(completed_actions(request, response)),)
    )

    result = await StreamingRuntime(time=ManualTime()).collect(
        client,
        request,
        deadline=Deadline(10),
    )

    assert result.response is response
    assert result.response.outcome is ResponseOutcome.TRUNCATED
    assert isinstance(result.events[-1], ResponseCompleted)
    assert len(client.calls) == 1


async def test_terminal_response_without_usage_preserves_none() -> None:
    request = make_request()
    response = make_response(request, usage=None)
    client = ControlledStreamingClient(
        (AttemptScript(completed_actions(request, response)),)
    )

    result = await StreamingRuntime(time=ManualTime()).collect(
        client,
        request,
        deadline=Deadline(10),
    )

    assert result.response is response
    assert result.response.usage is None
    assert not any(isinstance(event, UsageReported) for event in result.events)
    assert len(client.calls) == 1


async def test_future_backed_async_iterator_is_supported() -> None:
    request = make_request()
    response = make_response(request)
    source = FutureBackedStream(completed_events(request, response))
    client = SingleStreamClient(source)

    result = await StreamingRuntime(time=ManualTime()).collect(
        client,
        request,
        deadline=Deadline(10),
    )

    assert result.response is response
    assert result.cleanup_error is None
    assert source.closed is True
    assert client.calls == 1


async def test_cleanup_failure_cannot_retry_a_committed_response() -> None:
    request = make_request()
    response = make_response(request)
    close_error = ModelRateLimited(
        "close failed",
        status_code=429,
        retry_after_s=1,
    )
    source = FailingCloseStream(
        completed_events(request, response),
        close_error,
    )
    client = SingleStreamClient(source)

    result = await StreamingRuntime(time=ManualTime()).collect(
        client,
        request,
        deadline=Deadline(10),
    )

    assert result.response is response
    assert result.cleanup_error is close_error
    assert source.closed is True
    assert client.calls == 1


async def test_cancellation_during_retry_wait_stops_the_operation() -> None:
    request = make_request()
    rate_limit = ModelRateLimited(
        "too many requests",
        status_code=429,
        retry_after_s=5,
    )
    response = make_response(request)
    client = ControlledStreamingClient(
        (
            AttemptScript((Fail(rate_limit),)),
            AttemptScript(completed_actions(request, response)),
        )
    )
    clock = ManualTime()
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=Deadline(10),
        )
    )
    await wait_until(lambda: clock.pending_deadlines == (5.0,))

    task.cancel("cancel retry wait")

    with pytest.raises(asyncio.CancelledError, match="cancel retry wait"):
        await task
    assert len(client.calls) == 1
    assert client.closed_attempts == (1,)
    assert clock.pending_deadlines == ()


async def test_terminal_response_wins_if_deadline_expires_during_cleanup() -> None:
    request = make_request()
    response = make_response(request)
    close_entered = asyncio.Event()
    close_release = asyncio.Event()
    source = BlockingCloseStream(
        completed_events(request, response),
        close_entered=close_entered,
        close_release=close_release,
    )
    client = SingleStreamClient(source)
    clock = ManualTime()
    deadline = Deadline(10)
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=deadline,
        )
    )
    await close_entered.wait()

    clock.advance_to(deadline.at)
    result = await task

    assert result.response is response
    assert isinstance(result.events[-1], ResponseCompleted)
    assert isinstance(result.cleanup_error, ModelTimeout)
    assert source.closed is True
    assert client.calls == 1
    assert clock.pending_deadlines == ()


async def test_terminal_response_wins_cancellation_during_cleanup() -> None:
    request = make_request()
    response = make_response(request)
    close_entered = asyncio.Event()
    close_release = asyncio.Event()
    source = BlockingCloseStream(
        completed_events(request, response),
        close_entered=close_entered,
        close_release=close_release,
    )
    client = SingleStreamClient(source)
    clock = ManualTime()
    task = asyncio.create_task(
        StreamingRuntime(time=clock).collect(
            client,
            request,
            deadline=Deadline(10),
        )
    )
    await close_entered.wait()

    task.cancel("terminal already committed")
    result = await task

    assert result.response is response
    assert isinstance(result.cleanup_error, asyncio.CancelledError)
    assert result.cleanup_error.args == ("terminal already committed",)
    assert source.closed is True
    assert client.calls == 1
    assert clock.pending_deadlines == ()


def test_retry_policy_is_immutable() -> None:
    policy = RateLimitRetryPolicy(max_attempts=2, fallback_delay_s=0.5)

    with pytest.raises(FrozenInstanceError):
        policy.max_attempts = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_attempts", 0),
        ("max_attempts", True),
        ("fallback_delay_s", -1),
        ("fallback_delay_s", float("inf")),
    ],
)
def test_retry_policy_rejects_invalid_limits(field: str, value: object) -> None:
    arguments: dict[str, object] = {
        "max_attempts": 2,
        "fallback_delay_s": 0.5,
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        RateLimitRetryPolicy(**arguments)  # type: ignore[arg-type]
