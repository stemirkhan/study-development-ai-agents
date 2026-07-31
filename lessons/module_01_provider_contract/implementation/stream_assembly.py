"""Validate a common event lifecycle and assemble its terminal response."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass

from .contracts import (
    ModelClientError,
    ModelEvent,
    ModelOutput,
    ModelProtocolError,
    ModelResponse,
    ModelStreamInterrupted,
    OutputCompleted,
    ResponseCompleted,
    ResponseStarted,
    StructuredOutput,
    TextDelta,
    TextOutput,
    ToolArgumentsDelta,
    ToolCallOutput,
    UnknownProviderEvent,
    Usage,
    UsageReported,
)


@dataclass(frozen=True, slots=True)
class CollectedStream:
    response: ModelResponse
    events: tuple[ModelEvent, ...]


@dataclass(slots=True)
class _ToolAccumulator:
    call_id: str | None
    name: str
    chunks: list[str]


async def collect_stream(source: AsyncIterable[ModelEvent]) -> CollectedStream:
    """Collect one stream only after proving its common lifecycle."""

    events: list[ModelEvent] = []
    started: ResponseStarted | None = None
    terminal: ResponseCompleted | None = None
    outputs: dict[int, ModelOutput] = {}
    text_parts: dict[int, list[str]] = {}
    tool_parts: dict[int, _ToolAccumulator] = {}
    reported_usage: Usage | None = None

    iterator = aiter(source)
    while True:
        event = await _next_event(iterator, events)
        if event is None:
            break
        expected_sequence = len(events) + 1
        if event.sequence != expected_sequence:
            raise _protocol_error(
                f"expected event sequence {expected_sequence}, "
                f"got {event.sequence}",
                event=event,
            )
        if terminal is not None:
            raise _protocol_error(
                "an event followed the terminal response",
                event=event,
            )
        if started is None and not isinstance(event, ResponseStarted):
            raise _protocol_error(
                "the first event must start the response",
                event=event,
            )
        if started is not None:
            if event.provider_payload.provider != started.provider_payload.provider:
                raise _protocol_error(
                    "stream event belongs to another provider",
                    event=event,
                )

        if isinstance(event, ResponseStarted):
            if started is not None:
                raise _protocol_error("response started twice", event=event)
            started = event
        elif isinstance(event, TextDelta):
            _require_open_output(event.output_index, outputs, event)
            if event.output_index in tool_parts:
                raise _protocol_error(
                    "one output mixed text and tool argument deltas",
                    event=event,
                )
            text_parts.setdefault(event.output_index, []).append(event.delta)
        elif isinstance(event, ToolArgumentsDelta):
            _require_open_output(event.output_index, outputs, event)
            if event.output_index in text_parts:
                raise _protocol_error(
                    "one output mixed text and tool argument deltas",
                    event=event,
                )
            current = tool_parts.setdefault(
                event.output_index,
                _ToolAccumulator(event.call_id, event.name, []),
            )
            if current.name != event.name:
                raise _protocol_error(
                    "tool argument deltas changed call identity",
                    event=event,
                )
            if (
                current.call_id is not None
                and event.call_id is not None
                and current.call_id != event.call_id
            ):
                raise _protocol_error(
                    "tool argument deltas changed call identity",
                    event=event,
                )
            if current.call_id is None and event.call_id is not None:
                current.call_id = event.call_id
            current.chunks.append(event.delta)
        elif isinstance(event, OutputCompleted):
            if event.output_index in outputs:
                raise _protocol_error("output completed twice", event=event)
            output_payload = event.output.provider_payload
            if (
                output_payload is not None
                and output_payload.provider != event.provider_payload.provider
            ):
                raise _protocol_error(
                    "completed output belongs to another provider",
                    event=event,
                )
            _validate_completed_output(event, text_parts, tool_parts)
            outputs[event.output_index] = event.output
        elif isinstance(event, UsageReported):
            if reported_usage is not None:
                raise _protocol_error("usage was reported twice", event=event)
            if (
                event.usage.provider_payload is not None
                and event.usage.provider_payload.provider
                != event.provider_payload.provider
            ):
                raise _protocol_error(
                    "reported usage belongs to another provider",
                    event=event,
                )
            reported_usage = event.usage
        elif isinstance(event, UnknownProviderEvent):
            pass
        elif isinstance(event, ResponseCompleted):
            assert started is not None
            _validate_terminal(
                started,
                event,
                outputs,
                text_parts,
                tool_parts,
                reported_usage,
            )
            terminal = event
        events.append(event)

    if terminal is None:
        if events:
            last_event = events[-1]
            cause = ModelProtocolError(
                "stream ended without a terminal response",
                provider=last_event.provider_payload.provider,
                provider_payloads=(last_event.provider_payload,),
            )
            raise ModelStreamInterrupted(tuple(events), cause) from cause
        raise ModelProtocolError("stream ended without a terminal response")
    return CollectedStream(response=terminal.response, events=tuple(events))


async def _next_event(
    iterator: AsyncIterator[ModelEvent],
    observed: list[ModelEvent],
) -> ModelEvent | None:
    try:
        return await anext(iterator)
    except StopAsyncIteration:
        return None
    except asyncio.CancelledError:
        raise
    except ModelStreamInterrupted as exc:
        if not _same_event_objects(exc.events, observed):
            raise ModelProtocolError(
                "stream interruption did not preserve the observed prefix"
            ) from exc
        raise
    except ModelClientError as exc:
        if observed:
            raise ModelStreamInterrupted(tuple(observed), exc) from exc
        raise


def _same_event_objects(
    expected: tuple[ModelEvent, ...],
    observed: list[ModelEvent],
) -> bool:
    return len(expected) == len(observed) and all(
        expected_event is observed_event
        for expected_event, observed_event in zip(
            expected,
            observed,
            strict=True,
        )
    )


def _require_open_output(
    output_index: int,
    outputs: dict[int, ModelOutput],
    event: ModelEvent,
) -> None:
    if output_index in outputs:
        raise _protocol_error("delta followed output completion", event=event)


def _validate_completed_output(
    event: OutputCompleted,
    text_parts: dict[int, list[str]],
    tool_parts: dict[int, _ToolAccumulator],
) -> None:
    output = event.output
    if event.output_index in text_parts and not isinstance(
        output,
        (TextOutput, StructuredOutput),
    ):
        raise _protocol_error(
            "text deltas ended as an incompatible output type",
            event=event,
        )
    if event.output_index in tool_parts and not isinstance(
        output,
        ToolCallOutput,
    ):
        raise _protocol_error(
            "tool argument deltas ended as an incompatible output type",
            event=event,
        )
    if isinstance(output, TextOutput) and event.output_index in text_parts:
        if "".join(text_parts[event.output_index]) != output.text:
            raise _protocol_error(
                "text deltas disagree with completed output",
                event=event,
            )
    if isinstance(output, StructuredOutput) and event.output_index in text_parts:
        if "".join(text_parts[event.output_index]) != output.raw_json:
            raise _protocol_error(
                "structured deltas disagree with completed raw_json",
                event=event,
            )
    if isinstance(output, ToolCallOutput) and event.output_index in tool_parts:
        accumulator = tool_parts[event.output_index]
        if accumulator.name != output.name or (
            accumulator.call_id is not None
            and accumulator.call_id != output.call_id
        ):
            raise _protocol_error(
                "tool deltas disagree with completed call identity",
                event=event,
            )
        if output.raw_arguments is None:
            raise _protocol_error(
                "streamed tool arguments lost their complete JSON text",
                event=event,
            )
        if "".join(accumulator.chunks) != output.raw_arguments:
            raise _protocol_error(
                "tool deltas disagree with completed arguments",
                event=event,
            )


def _validate_terminal(
    started: ResponseStarted,
    terminal: ResponseCompleted,
    outputs: dict[int, ModelOutput],
    text_parts: dict[int, list[str]],
    tool_parts: dict[int, _ToolAccumulator],
    reported_usage: Usage | None,
) -> None:
    response = terminal.response
    if response.request_id != started.request_id:
        raise _protocol_error("terminal request_id changed", event=terminal)
    if (
        started.response_id is not None
        and response.response_id != started.response_id
    ):
        raise _protocol_error("terminal response_id changed", event=terminal)
    if started.model is not None and response.model != started.model:
        raise _protocol_error("terminal model changed", event=terminal)
    if response.provider != started.provider_payload.provider:
        raise _protocol_error("terminal provider changed", event=terminal)

    open_indexes = (set(text_parts) | set(tool_parts)) - set(outputs)
    if open_indexes:
        raise _protocol_error(
            f"output indexes never completed: {sorted(open_indexes)}",
            event=terminal,
        )
    expected_indexes = list(range(len(response.outputs)))
    if sorted(outputs) != expected_indexes:
        raise _protocol_error(
            "completed output indexes are not contiguous",
            event=terminal,
        )
    assembled_outputs = tuple(outputs[index] for index in expected_indexes)
    if assembled_outputs != response.outputs:
        raise _protocol_error(
            "completed outputs disagree with terminal response",
            event=terminal,
        )
    if reported_usage is not None and reported_usage != response.usage:
        raise _protocol_error(
            "reported usage disagrees with terminal response",
            event=terminal,
        )


def _protocol_error(message: str, *, event: ModelEvent) -> ModelProtocolError:
    return ModelProtocolError(
        message,
        provider=event.provider_payload.provider,
        provider_payloads=(event.provider_payload,),
    )
