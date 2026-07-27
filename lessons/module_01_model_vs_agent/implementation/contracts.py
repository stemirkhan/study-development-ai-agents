"""Shared data contracts and typed failures for the four runtime boundaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, TypeAlias, TypeVar


@dataclass(frozen=True, slots=True)
class UserMessage:
    content: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            MappingProxyType(dict(self.arguments)),
        )


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    call_id: str
    name: str
    content: str


ModelMessage: TypeAlias = UserMessage | ToolCall | ToolResultMessage


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    required_arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolSpec, ...]


@dataclass(frozen=True, slots=True)
class FinalAnswer:
    text: str


ModelOutput: TypeAlias = FinalAnswer | ToolCall


@dataclass(frozen=True, slots=True)
class TraceEvent:
    sequence: int
    component: str
    action: str
    detail: str = ""


@dataclass(slots=True)
class Trace:
    """A deterministic in-memory audit trail for the lesson."""

    _events: list[TraceEvent] = field(default_factory=list, repr=False)

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def record(self, component: str, action: str, detail: str = "") -> None:
        self._events.append(
            TraceEvent(
                sequence=len(self._events) + 1,
                component=component,
                action=action,
                detail=detail,
            )
        )


class ProviderTransport(Protocol):
    async def send(self, request: ModelRequest) -> Mapping[str, object]:
        """Return one provider-shaped response payload."""


ToolValidator: TypeAlias = Callable[
    [Mapping[str, object]],
    Mapping[str, object],
]
ToolHandler: TypeAlias = Callable[
    [Mapping[str, object]],
    Awaitable[str],
]


@dataclass(frozen=True, slots=True)
class ToolBinding:
    spec: ToolSpec
    validate: ToolValidator
    invoke: ToolHandler


@dataclass(frozen=True, slots=True)
class AgentResult:
    answer: str
    model_calls: int
    tool_calls: int
    context: tuple[ModelMessage, ...]


@dataclass(frozen=True, slots=True)
class UserRequest:
    request_id: str
    text: str


@dataclass(frozen=True, slots=True)
class UserResponse:
    request_id: str
    text: str
    model_calls: int
    tool_calls: int


ResultT = TypeVar("ResultT")
AsyncOperation: TypeAlias = Callable[[], Awaitable[ResultT]]


class LessonRuntimeError(Exception):
    """Base class for expected failures in this lesson."""


class ModelClientError(LessonRuntimeError):
    """Base class for the one-model-call boundary."""


class UnsupportedCapability(ModelClientError):
    pass


class ModelTimeout(ModelClientError):
    pass


class ModelProtocolError(ModelClientError):
    pass


class UnexpectedModelCall(ModelClientError):
    """The deterministic fake received more calls than scripted."""


class ModelTransportError(ModelClientError):
    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(f"model transport failed: {cause}")


class AgentError(LessonRuntimeError):
    """Base class for dynamic Agent-loop failures."""


class UnknownTool(AgentError):
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"tool is not allowed: {tool_name!r}")


class InvalidToolArguments(AgentError):
    def __init__(self, tool_name: str, reason: str) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"invalid arguments for {tool_name!r}: {reason}")


class DuplicateToolCall(AgentError):
    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        super().__init__(f"tool call id was already executed: {call_id!r}")


class ToolExecutionError(AgentError):
    def __init__(self, tool_name: str, cause: Exception) -> None:
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"tool {tool_name!r} failed: {cause}")


class AgentStepLimitExceeded(AgentError):
    def __init__(self, max_steps: int) -> None:
        self.max_steps = max_steps
        super().__init__(f"Agent exhausted its {max_steps} model-call budget")


class WorkflowError(LessonRuntimeError):
    """Base class for failures owned by the fixed process."""


class InvalidUserRequest(WorkflowError):
    pass


class WorkflowInvariantViolation(WorkflowError):
    pass


class WorkflowStepFailed(WorkflowError):
    def __init__(self, step: str, cause: Exception) -> None:
        self.step = step
        self.cause = cause
        super().__init__(f"workflow step {step!r} failed: {cause}")


class ExecutorError(LessonRuntimeError):
    """Base class for physical execution failures."""


class WorkerUnavailable(ExecutorError):
    pass


class TaskDeadlineExceeded(ExecutorError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"task deadline exceeded: {task_id}")


class AmbiguousCompletion(ExecutorError):
    def __init__(self, task_id: str, attempt: int) -> None:
        self.task_id = task_id
        self.attempt = attempt
        super().__init__(
            f"task may have completed before acknowledgement was lost: "
            f"{task_id}, attempt={attempt}"
        )
