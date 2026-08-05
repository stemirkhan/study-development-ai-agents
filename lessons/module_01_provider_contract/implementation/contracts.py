"""Provider-neutral immutable data models for one LLM exchange."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Protocol, TypeAlias


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = (
    JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)


def freeze_json(value: object) -> JsonValue:
    """Validate and recursively freeze one JSON-compatible value."""

    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            frozen[key] = freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def _freeze_object(value: Mapping[str, object]) -> Mapping[str, JsonValue]:
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - typing guard
        raise TypeError("expected a JSON object")
    return frozen


def _parse_json(raw: str, *, label: str) -> JsonValue:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{label} must contain valid JSON") from exc
    return freeze_json(decoded)


def _json_equal(left: JsonValue, right: JsonValue) -> bool:
    """Compare JSON without Python's surprising ``True == 1`` rule."""

    if isinstance(left, Mapping) or isinstance(right, Mapping):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return False
        return set(left) == set(right) and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, tuple) or isinstance(right, tuple):
        if not isinstance(left, tuple) or not isinstance(right, tuple):
            return False
        return len(left) == len(right) and all(
            _json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _require_non_empty(value: str, *, label: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} must be non-empty")


def _require_non_negative_int(value: object, *, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ProviderPayload:
    """Exact provider bytes plus enough identity to interpret them later."""

    provider: str
    kind: str
    data: bytes = field(repr=False)
    media_type: str = "application/json"

    def __post_init__(self) -> None:
        _require_non_empty(self.provider, label="provider")
        _require_non_empty(self.kind, label="provider payload kind")
        _require_non_empty(self.media_type, label="media_type")
        if not isinstance(self.data, bytes):
            raise TypeError("provider payload data must be bytes")


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class InputMessage:
    role: MessageRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("message content must be a string")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        _require_non_empty(self.name, label="tool name")
        object.__setattr__(self, "input_schema", _freeze_object(self.input_schema))


@dataclass(frozen=True, slots=True)
class TextFormat:
    pass


@dataclass(frozen=True, slots=True)
class JsonSchemaFormat:
    schema_id: str
    schema: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        _require_non_empty(self.schema_id, label="schema_id")
        object.__setattr__(self, "schema", _freeze_object(self.schema))


ResponseFormat: TypeAlias = TextFormat | JsonSchemaFormat


class StructuredOutputValidator(Protocol):
    def __call__(
        self,
        response_format: JsonSchemaFormat,
        value: JsonValue,
    ) -> None:
        """Raise when a parsed value violates the requested schema."""


@dataclass(frozen=True, slots=True)
class ModelRequest:
    request_id: str
    model: str
    messages: tuple[InputMessage, ...]
    tools: tuple[ToolSpec, ...] = ()
    response_format: ResponseFormat = field(default_factory=TextFormat)

    def __post_init__(self) -> None:
        _require_non_empty(self.request_id, label="request_id")
        _require_non_empty(self.model, label="model")
        messages = tuple(self.messages)
        tools = tuple(self.tools)
        if not messages:
            raise ValueError("a model request must contain at least one message")
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique within a request")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "tools", tools)


@dataclass(frozen=True, slots=True)
class Deadline:
    """Absolute deadline measured by one monotonic clock."""

    at: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.at, bool)
            or not isinstance(self.at, (int, float))
            or not math.isfinite(self.at)
        ):
            raise ValueError("deadline must be a finite number")
        object.__setattr__(self, "at", float(self.at))


@dataclass(frozen=True, slots=True)
class ModelAttempt:
    """Runtime controls for exactly one provider attempt."""

    number: int
    deadline: Deadline

    def __post_init__(self) -> None:
        if (
            not isinstance(self.number, int)
            or isinstance(self.number, bool)
            or self.number < 1
        ):
            raise ValueError("attempt number must be a positive integer")
        if not isinstance(self.deadline, Deadline):
            raise TypeError("attempt deadline must be a Deadline")


@dataclass(frozen=True, slots=True)
class TextOutput:
    text: str
    provider_payload: ProviderPayload | None = field(
        default=None,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text output must be a string")


@dataclass(frozen=True, slots=True)
class StructuredOutput:
    schema_id: str
    raw_json: str
    value: JsonValue
    provider_payload: ProviderPayload | None = field(
        default=None,
        compare=False,
    )

    def __post_init__(self) -> None:
        _require_non_empty(self.schema_id, label="schema_id")
        frozen = freeze_json(self.value)
        parsed = _parse_json(self.raw_json, label="raw_json")
        if not _json_equal(parsed, frozen):
            raise ValueError("raw_json and structured value disagree")
        object.__setattr__(self, "value", frozen)


@dataclass(frozen=True, slots=True)
class ToolCallOutput:
    call_id: str | None
    name: str
    arguments: Mapping[str, JsonValue]
    raw_arguments: str | None
    provider_payload: ProviderPayload | None = field(
        default=None,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.call_id is not None:
            _require_non_empty(self.call_id, label="call_id")
        _require_non_empty(self.name, label="tool name")
        frozen = _freeze_object(self.arguments)
        if self.raw_arguments is not None:
            parsed = _parse_json(self.raw_arguments, label="raw_arguments")
            if not isinstance(parsed, Mapping):
                raise ValueError("tool arguments must be a JSON object")
            if not _json_equal(parsed, frozen):
                raise ValueError("raw_arguments and parsed arguments disagree")
        object.__setattr__(self, "arguments", frozen)


@dataclass(frozen=True, slots=True)
class RefusalOutput:
    reason: str
    message: str | None = None
    provider_payload: ProviderPayload | None = field(
        default=None,
        compare=False,
    )

    def __post_init__(self) -> None:
        _require_non_empty(self.reason, label="refusal reason")


@dataclass(frozen=True, slots=True)
class UnknownOutput:
    provider_payload: ProviderPayload


ModelOutput: TypeAlias = (
    TextOutput
    | StructuredOutput
    | ToolCallOutput
    | RefusalOutput
    | UnknownOutput
)


class ResponseOutcome(str, Enum):
    COMPLETED = "completed"
    TOOL_REQUESTED = "tool_requested"
    REFUSED = "refused"
    TRUNCATED = "truncated"
    BLOCKED = "blocked"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider_payload: ProviderPayload | None = field(
        default=None,
        compare=False,
    )

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None:
                _require_non_negative_int(value, label=name)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    request_id: str
    provider: str
    outcome: ResponseOutcome
    outputs: tuple[ModelOutput, ...]
    provider_payload: ProviderPayload = field(compare=False)
    model: str | None = None
    response_id: str | None = None
    usage: Usage | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.request_id, label="request_id")
        _require_non_empty(self.provider, label="provider")
        if self.model is not None:
            _require_non_empty(self.model, label="model")
        if self.response_id is not None:
            _require_non_empty(self.response_id, label="response_id")
        outputs = tuple(self.outputs)
        object.__setattr__(self, "outputs", outputs)
        if self.provider_payload.provider != self.provider:
            raise ValueError("response and raw payload providers disagree")

        tool_calls = [item for item in outputs if isinstance(item, ToolCallOutput)]
        refusals = [item for item in outputs if isinstance(item, RefusalOutput)]
        if self.outcome is ResponseOutcome.TOOL_REQUESTED and not tool_calls:
            raise ValueError("tool_requested response must contain a tool call")
        if self.outcome is ResponseOutcome.REFUSED and not refusals:
            raise ValueError("refused response must contain a refusal")
        if tool_calls and self.outcome is not ResponseOutcome.TOOL_REQUESTED:
            raise ValueError("tool call output requires tool_requested outcome")
        if refusals and self.outcome is not ResponseOutcome.REFUSED:
            raise ValueError("refusal output requires refused outcome")
        if tool_calls and refusals:
            raise ValueError("one response cannot request a tool and refuse")

        call_ids = [item.call_id for item in tool_calls if item.call_id is not None]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("non-empty tool call IDs must be unique")

        payloads = [
            item.provider_payload
            for item in outputs
            if item.provider_payload is not None
        ]
        if self.usage is not None and self.usage.provider_payload is not None:
            payloads.append(self.usage.provider_payload)
        if any(payload.provider != self.provider for payload in payloads):
            raise ValueError("nested raw payload belongs to another provider")


@dataclass(frozen=True, slots=True)
class EventBase:
    sequence: int
    provider_payload: ProviderPayload

    def __post_init__(self) -> None:
        _require_non_negative_int(self.sequence, label="event sequence")
        if self.sequence == 0:
            raise ValueError("event sequence must be positive")


@dataclass(frozen=True, slots=True)
class ResponseStarted(EventBase):
    request_id: str
    response_id: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        EventBase.__post_init__(self)
        _require_non_empty(self.request_id, label="request_id")
        if self.response_id is not None:
            _require_non_empty(self.response_id, label="response_id")
        if self.model is not None:
            _require_non_empty(self.model, label="model")


@dataclass(frozen=True, slots=True)
class TextDelta(EventBase):
    output_index: int
    delta: str

    def __post_init__(self) -> None:
        EventBase.__post_init__(self)
        _require_non_negative_int(self.output_index, label="output_index")


@dataclass(frozen=True, slots=True)
class ToolArgumentsDelta(EventBase):
    output_index: int
    call_id: str | None
    name: str
    delta: str

    def __post_init__(self) -> None:
        EventBase.__post_init__(self)
        _require_non_negative_int(self.output_index, label="output_index")
        if self.call_id is not None:
            _require_non_empty(self.call_id, label="call_id")
        _require_non_empty(self.name, label="tool name")


@dataclass(frozen=True, slots=True)
class OutputCompleted(EventBase):
    output_index: int
    output: ModelOutput

    def __post_init__(self) -> None:
        EventBase.__post_init__(self)
        _require_non_negative_int(self.output_index, label="output_index")


@dataclass(frozen=True, slots=True)
class UsageReported(EventBase):
    usage: Usage


@dataclass(frozen=True, slots=True)
class UnknownProviderEvent(EventBase):
    """An event the common layer does not yet understand."""


@dataclass(frozen=True, slots=True)
class ResponseCompleted(EventBase):
    response: ModelResponse


ModelEvent: TypeAlias = (
    ResponseStarted
    | TextDelta
    | ToolArgumentsDelta
    | OutputCompleted
    | UsageReported
    | UnknownProviderEvent
    | ResponseCompleted
)


class ModelClientError(Exception):
    """Base failure at the boundary of one model exchange."""


class ProviderModelError(ModelClientError):
    def __init__(
        self,
        message: str,
        *,
        application_request_id: str | None = None,
        provider: str | None = None,
        provider_request_id: str | None = None,
        status_code: int | None = None,
        provider_payloads: tuple[ProviderPayload, ...] = (),
        cause: Exception | None = None,
    ) -> None:
        payloads = tuple(provider_payloads)
        payload_providers = {payload.provider for payload in payloads}
        if len(payload_providers) > 1:
            raise ValueError("error payloads belong to different providers")
        if application_request_id is not None:
            _require_non_empty(
                application_request_id,
                label="application_request_id",
            )
        if provider_request_id is not None:
            _require_non_empty(provider_request_id, label="provider_request_id")
        if provider is not None:
            _require_non_empty(provider, label="provider")
        if provider is not None and payload_providers - {provider}:
            raise ValueError("error payload belongs to another provider")
        if status_code is not None:
            _require_non_negative_int(status_code, label="status_code")
        self.application_request_id = application_request_id
        self.provider = provider or next(iter(payload_providers), None)
        self.provider_request_id = provider_request_id
        self.status_code = status_code
        self.provider_payloads = payloads
        self.cause = cause
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


class UnsupportedCapability(ProviderModelError):
    pass


class ModelAuthenticationError(ProviderModelError):
    pass


class ModelRequestRejected(ProviderModelError):
    pass


class ModelUnavailable(ProviderModelError):
    pass


class ModelRateLimited(ProviderModelError):
    def __init__(
        self,
        message: str,
        *,
        retry_after_s: float | None,
        application_request_id: str | None = None,
        provider: str | None = None,
        provider_request_id: str | None = None,
        status_code: int | None = None,
        provider_payloads: tuple[ProviderPayload, ...] = (),
        cause: Exception | None = None,
    ) -> None:
        if retry_after_s is not None and (
            isinstance(retry_after_s, bool)
            or not isinstance(retry_after_s, (int, float))
            or not math.isfinite(retry_after_s)
            or retry_after_s < 0
        ):
            raise ValueError("retry_after_s must be a finite non-negative number")
        self.retry_after_s = retry_after_s
        super().__init__(
            message,
            application_request_id=application_request_id,
            provider=provider,
            provider_request_id=provider_request_id,
            status_code=status_code,
            provider_payloads=provider_payloads,
            cause=cause,
        )


class TimeoutPhase(str, Enum):
    BEFORE_STREAM = "before_stream"
    STREAM_READ = "stream_read"
    RETRY_WAIT = "retry_wait"


class ModelTimeout(ProviderModelError):
    def __init__(
        self,
        message: str,
        *,
        phase: TimeoutPhase | None = None,
        application_request_id: str | None = None,
        provider: str | None = None,
        provider_request_id: str | None = None,
        status_code: int | None = None,
        provider_payloads: tuple[ProviderPayload, ...] = (),
        cause: Exception | None = None,
    ) -> None:
        if phase is not None and not isinstance(phase, TimeoutPhase):
            raise TypeError("timeout phase must be a TimeoutPhase")
        self.phase = phase
        super().__init__(
            message,
            application_request_id=application_request_id,
            provider=provider,
            provider_request_id=provider_request_id,
            status_code=status_code,
            provider_payloads=provider_payloads,
            cause=cause,
        )


class ModelTransportError(ProviderModelError):
    def __init__(
        self,
        message: str,
        *,
        received_bytes: bool,
        application_request_id: str | None = None,
        provider: str | None = None,
        provider_request_id: str | None = None,
        provider_payloads: tuple[ProviderPayload, ...] = (),
        cause: Exception | None = None,
    ) -> None:
        if not isinstance(received_bytes, bool):
            raise TypeError("received_bytes must be a boolean")
        self.received_bytes = received_bytes
        super().__init__(
            message,
            application_request_id=application_request_id,
            provider=provider,
            provider_request_id=provider_request_id,
            provider_payloads=provider_payloads,
            cause=cause,
        )


class ModelProtocolError(ProviderModelError):
    pass


class ModelStreamInterrupted(ModelClientError):
    def __init__(
        self,
        events: tuple[ModelEvent, ...],
        cause: ModelClientError,
    ) -> None:
        self.events = tuple(events)
        self.cause = cause
        super().__init__(
            f"model stream interrupted after {len(self.events)} event(s): {cause}"
        )
        self.__cause__ = cause


class UnexpectedModelCall(ModelClientError):
    pass


class ModelClient(Protocol):
    async def complete(
        self,
        request: ModelRequest,
        *,
        attempt: ModelAttempt | None = None,
    ) -> ModelResponse:
        """Perform exactly one non-streaming model exchange."""

    def stream(
        self,
        request: ModelRequest,
        *,
        attempt: ModelAttempt | None = None,
    ) -> AsyncIterator[ModelEvent]:
        """Perform exactly one streaming model exchange."""
