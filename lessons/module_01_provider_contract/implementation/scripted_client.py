"""A deterministic LLM imitation that replays typed responses and events."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import TypeAlias

from .contracts import (
    JsonSchemaFormat,
    ModelClientError,
    ModelEvent,
    ModelProtocolError,
    ModelRequest,
    ModelResponse,
    ModelStreamInterrupted,
    ResponseCompleted,
    ResponseOutcome,
    ResponseStarted,
    StructuredOutput,
    StructuredOutputValidator,
    UnexpectedModelCall,
)


ScriptedError: TypeAlias = ModelClientError | asyncio.CancelledError


@dataclass(frozen=True, slots=True)
class ScriptedFailure:
    error: ScriptedError

    def __post_init__(self) -> None:
        if not isinstance(self.error, (ModelClientError, asyncio.CancelledError)):
            raise TypeError("scripted failures must use a model error or cancellation")


CompletionScript: TypeAlias = ModelResponse | ScriptedFailure


@dataclass(frozen=True, slots=True)
class StreamScript:
    events: tuple[ModelEvent, ...]
    failure: ScriptedError | None = None

    def __post_init__(self) -> None:
        events = tuple(self.events)
        object.__setattr__(self, "events", events)
        if self.failure is not None and not isinstance(
            self.failure,
            (ModelClientError, asyncio.CancelledError),
        ):
            raise TypeError("stream failure must be a model error or cancellation")
        if isinstance(self.failure, ModelStreamInterrupted):
            if not _same_event_objects(self.failure.events, events):
                raise ValueError(
                    "ModelStreamInterrupted must preserve exactly the scripted prefix"
                )
        elif events and isinstance(self.failure, ModelClientError):
            raise ValueError(
                "a model failure after events must be ModelStreamInterrupted"
            )


class ScriptedModelClient:
    """Consume one predeclared script per call, without retries or tools."""

    def __init__(
        self,
        *,
        completions: Iterable[CompletionScript] = (),
        streams: Iterable[StreamScript] = (),
        structured_validator: StructuredOutputValidator | None = None,
    ) -> None:
        self._completions = deque(completions)
        self._streams = deque(streams)
        self._completion_requests: list[ModelRequest] = []
        self._stream_requests: list[ModelRequest] = []
        self._structured_validator = structured_validator

    @property
    def completion_requests(self) -> tuple[ModelRequest, ...]:
        return tuple(self._completion_requests)

    @property
    def stream_requests(self) -> tuple[ModelRequest, ...]:
        return tuple(self._stream_requests)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self._completion_requests.append(request)
        if not self._completions:
            raise UnexpectedModelCall("no scripted completion remains")
        scripted = self._completions.popleft()
        if isinstance(scripted, ScriptedFailure):
            raise scripted.error
        self._validate_response(request, scripted)
        return scripted

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self._stream_requests.append(request)
        if not self._streams:
            raise UnexpectedModelCall("no scripted stream remains")
        script = self._streams.popleft()
        return self._replay(request, script)

    async def _replay(
        self,
        request: ModelRequest,
        script: StreamScript,
    ) -> AsyncIterator[ModelEvent]:
        emitted: list[ModelEvent] = []
        for event in script.events:
            try:
                self._validate_stream_event(request, event)
            except ModelClientError as exc:
                if emitted:
                    raise ModelStreamInterrupted(tuple(emitted), exc) from exc
                raise
            yield event
            emitted.append(event)
        if script.failure is not None:
            raise script.failure

    def _validate_stream_event(
        self,
        request: ModelRequest,
        event: ModelEvent,
    ) -> None:
        if isinstance(event, ResponseStarted):
            if event.request_id != request.request_id:
                raise ModelProtocolError(
                    "stream request_id does not match the request",
                    application_request_id=request.request_id,
                    provider=event.provider_payload.provider,
                    provider_payloads=(event.provider_payload,),
                )
        elif isinstance(event, ResponseCompleted):
            self._validate_response(request, event.response)

    def _validate_response(
        self,
        request: ModelRequest,
        response: ModelResponse,
    ) -> None:
        if response.request_id != request.request_id:
            raise ModelProtocolError(
                "response request_id does not match the request",
                application_request_id=request.request_id,
                provider=response.provider,
                provider_payloads=(response.provider_payload,),
            )
        structured = [
            output
            for output in response.outputs
            if isinstance(output, StructuredOutput)
        ]
        if not structured:
            if (
                isinstance(request.response_format, JsonSchemaFormat)
                and response.outcome is ResponseOutcome.COMPLETED
            ):
                raise ModelProtocolError(
                    "completed structured request has no StructuredOutput",
                    application_request_id=request.request_id,
                    provider=response.provider,
                    provider_payloads=(response.provider_payload,),
                )
            return
        if not isinstance(request.response_format, JsonSchemaFormat):
            raise ModelProtocolError(
                "structured output was not requested",
                application_request_id=request.request_id,
                provider=response.provider,
                provider_payloads=(response.provider_payload,),
            )
        if any(
            output.schema_id != request.response_format.schema_id
            for output in structured
        ):
            raise ModelProtocolError(
                "structured output schema_id does not match the request",
                application_request_id=request.request_id,
                provider=response.provider,
                provider_payloads=(response.provider_payload,),
            )
        if self._structured_validator is None:
            raise ModelProtocolError(
                "structured output has no configured schema validator",
                application_request_id=request.request_id,
                provider=response.provider,
                provider_payloads=(response.provider_payload,),
            )
        for output in structured:
            try:
                self._structured_validator(request.response_format, output.value)
            except Exception as exc:
                raise ModelProtocolError(
                    "structured output violates the requested schema",
                    application_request_id=request.request_id,
                    provider=response.provider,
                    provider_payloads=(response.provider_payload,),
                    cause=exc,
                ) from exc


def _same_event_objects(
    left: tuple[ModelEvent, ...],
    right: tuple[ModelEvent, ...],
) -> bool:
    return len(left) == len(right) and all(
        left_event is right_event
        for left_event, right_event in zip(left, right, strict=True)
    )
