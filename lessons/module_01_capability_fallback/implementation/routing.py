"""Filter-before-rank routing with explicit, effect-free unary fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from lessons.module_01_provider_contract.implementation.contracts import (
    Deadline,
    InputMessage,
    JsonSchemaFormat,
    ModelAttempt,
    ModelClientError,
    ModelProtocolError,
    ModelRequest,
    ModelResponse,
    ModelTimeout,
    ModelTransportError,
    ModelStreamInterrupted,
    ProviderModelError,
    ResponseFormat,
    TextFormat,
    ToolSpec,
)

from .capabilities import (
    CapabilityRequirements,
    CompatibilityReport,
    SchemaMode,
    SchemaRequirements,
    StreamMode,
    check_compatibility,
)
from .registry import CapabilityRegistry, ModelRoute


def _require_non_empty(value: str, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ModelRequestTemplate:
    """A provider-independent request waiting for the selected model ID."""

    request_id: str
    messages: tuple[InputMessage, ...]
    tools: tuple[ToolSpec, ...] = ()
    response_format: ResponseFormat = field(default_factory=TextFormat)

    def __post_init__(self) -> None:
        _require_non_empty(self.request_id, label="request_id")
        messages = tuple(self.messages)
        tools = tuple(self.tools)
        if not messages:
            raise ValueError("a request template must contain at least one message")
        if any(not isinstance(message, InputMessage) for message in messages):
            raise TypeError("template messages must be InputMessage instances")
        if any(not isinstance(tool, ToolSpec) for tool in tools):
            raise TypeError("template tools must be ToolSpec instances")
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique within a template")
        if not isinstance(self.response_format, (TextFormat, JsonSchemaFormat)):
            raise TypeError("unsupported response format")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "tools", tools)

    def bind(self, model: str) -> ModelRequest:
        """Change only the route-owned model field."""

        return ModelRequest(
            request_id=self.request_id,
            model=model,
            messages=self.messages,
            tools=self.tools,
            response_format=self.response_format,
        )


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    template: ModelRequestTemplate
    requirements: CapabilityRequirements

    def __post_init__(self) -> None:
        if not isinstance(self.template, ModelRequestTemplate):
            raise TypeError("template must be a ModelRequestTemplate")
        if not isinstance(self.requirements, CapabilityRequirements):
            raise TypeError("requirements must be CapabilityRequirements")

        declared_tools = frozenset(tool.name for tool in self.template.tools)
        if declared_tools != self.requirements.tools:
            raise ValueError(
                "template tools and required tools must match exactly"
            )

        if isinstance(self.template.response_format, JsonSchemaFormat):
            if self.requirements.schema.mode not in {
                SchemaMode.JSON,
                SchemaMode.STRICT,
            }:
                raise ValueError(
                    "JsonSchemaFormat requires explicit JSON or strict schema capability"
                )
            _validate_schema_declaration(
                self.template.response_format.schema,
                self.requirements.schema,
                label="response schema",
            )
        elif self.requirements.schema.mode is not SchemaMode.TEXT:
            raise ValueError("TextFormat cannot declare a schema capability")

        if self.template.tools:
            tool_schema = self.requirements.tool_schema
            if tool_schema is None:  # pragma: no cover - requirements guard
                raise ValueError("tools require explicit tool schema capabilities")
            for tool in self.template.tools:
                _validate_schema_declaration(
                    tool.input_schema,
                    tool_schema,
                    label=f"tool schema {tool.name}",
                )


_SCHEMA_MAP_CONTAINERS = frozenset(
    {
        "$defs",
        "definitions",
        "dependencies",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
)
_SCHEMA_SINGLE_CONTAINERS = frozenset(
    {
        "additionalProperties",
        "additionalItems",
        "contains",
        "contentSchema",
        "else",
        "if",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_SCHEMA_SEQUENCE_CONTAINERS = frozenset(
    {"allOf", "anyOf", "oneOf", "prefixItems"}
)
_SCHEMA_FLEXIBLE_CONTAINERS = frozenset({"extends", "items"})
_SUPPORTED_SCHEMA_DIALECTS = frozenset(
    {
        "http://json-schema.org/draft-07/schema#",
        "https://json-schema.org/draft/2019-09/schema",
        "https://json-schema.org/draft/2020-12/schema",
    }
)


def _validate_schema_declaration(
    schema: Mapping[str, object],
    requirements: SchemaRequirements,
    *,
    label: str,
) -> None:
    declared_dialect = schema.get("$schema")
    if not isinstance(declared_dialect, str):
        raise ValueError(f"{label} must declare a string $schema")
    if requirements.dialect not in _SUPPORTED_SCHEMA_DIALECTS:
        raise ValueError(
            f"{label} uses a dialect without a local preflight implementation"
        )
    if declared_dialect != requirements.dialect:
        raise ValueError(
            f"{label} $schema and required dialect must match exactly"
        )
    actual_keywords = _collect_schema_keywords(schema)
    undeclared = actual_keywords - requirements.keywords
    if undeclared:
        names = ", ".join(sorted(undeclared))
        raise ValueError(
            f"{label} uses undeclared capability keyword(s): {names}"
        )


def _collect_schema_keywords(schema: Mapping[str, object]) -> frozenset[str]:
    """Collect keywords while ignoring user-defined property and definition names."""

    found: set[str] = set()

    def visit(node: Mapping[str, object]) -> None:
        for keyword, value in node.items():
            found.add(keyword)
            if keyword in _SCHEMA_MAP_CONTAINERS and isinstance(value, Mapping):
                for nested in value.values():
                    if isinstance(nested, Mapping):
                        visit(nested)
            elif keyword in _SCHEMA_SINGLE_CONTAINERS and isinstance(value, Mapping):
                visit(value)
            elif keyword in _SCHEMA_SEQUENCE_CONTAINERS and isinstance(
                value,
                (list, tuple),
            ):
                for nested in value:
                    if isinstance(nested, Mapping):
                        visit(nested)
            elif keyword in _SCHEMA_FLEXIBLE_CONTAINERS:
                if isinstance(value, Mapping):
                    visit(value)
                elif isinstance(value, (list, tuple)):
                    for nested in value:
                        if isinstance(nested, Mapping):
                            visit(nested)

    visit(schema)
    return frozenset(found)


class RankingPolicy(Protocol):
    def rank(self, routes: tuple[ModelRoute, ...]) -> tuple[ModelRoute, ...]:
        """Order an already compatible, non-empty candidate set."""


@dataclass(frozen=True, slots=True)
class PriorityRanking:
    """Lower declared priority wins; route ID makes ties deterministic."""

    def rank(self, routes: tuple[ModelRoute, ...]) -> tuple[ModelRoute, ...]:
        return tuple(sorted(routes, key=lambda route: (route.priority, route.route_id)))


@dataclass(frozen=True, slots=True)
class RoutePlan:
    registry_revision: str
    requirements_revision: str
    reports: tuple[CompatibilityReport, ...]
    excluded_route_ids: frozenset[str]
    ordered_routes: tuple[ModelRoute, ...]

    def __post_init__(self) -> None:
        if not self.ordered_routes:
            raise ValueError("a route plan must contain an eligible route")
        object.__setattr__(self, "reports", tuple(self.reports))
        object.__setattr__(
            self,
            "excluded_route_ids",
            frozenset(self.excluded_route_ids),
        )
        object.__setattr__(self, "ordered_routes", tuple(self.ordered_routes))

    @property
    def selected(self) -> ModelRoute:
        return self.ordered_routes[0]


class NoCompatibleRoute(ModelClientError):
    def __init__(
        self,
        *,
        registry_revision: str,
        reports: tuple[CompatibilityReport, ...],
        excluded_route_ids: frozenset[str] = frozenset(),
        attempts: tuple[RouteAttemptRecord, ...] = (),
    ) -> None:
        self.registry_revision = registry_revision
        self.reports = tuple(reports)
        self.excluded_route_ids = frozenset(excluded_route_ids)
        self.attempts = tuple(attempts)
        compatible = sum(report.compatible for report in self.reports)
        super().__init__(
            "no eligible route: "
            f"{compatible} compatible, {len(self.excluded_route_ids)} excluded, "
            f"{len(self.attempts)} attempted"
        )


class CapabilityRouter:
    """Check every profile, then rank only compatible eligible routes."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        ranking: RankingPolicy = PriorityRanking(),
    ) -> None:
        self._registry = registry
        self._ranking = ranking

    @property
    def registry_revision(self) -> str:
        return self._registry.revision

    def plan(
        self,
        request: RoutingRequest,
    ) -> RoutePlan:
        return self._plan_excluding(request, frozenset())

    def _plan_excluding(
        self,
        request: RoutingRequest,
        excluded: frozenset[str],
    ) -> RoutePlan:
        known_ids = frozenset(route.route_id for route in self._registry)
        unknown_exclusions = excluded - known_ids
        if unknown_exclusions:
            unknown = ", ".join(sorted(unknown_exclusions))
            raise ValueError(f"cannot exclude unknown route(s): {unknown}")

        reports = tuple(
            check_compatibility(
                route.route_id,
                route.profile,
                request.requirements,
            )
            for route in self._registry
        )
        compatible_by_id = {
            report.route_id: report.compatible for report in reports
        }
        candidates = tuple(
            route
            for route in self._registry
            if compatible_by_id[route.route_id]
            and route.route_id not in excluded
        )
        if not candidates:
            raise NoCompatibleRoute(
                registry_revision=self._registry.revision,
                reports=reports,
                excluded_route_ids=excluded,
            )

        ranked = tuple(self._ranking.rank(candidates))
        _validate_ranking(candidates, ranked)
        return RoutePlan(
            registry_revision=self._registry.revision,
            requirements_revision=request.requirements.revision,
            reports=reports,
            excluded_route_ids=excluded,
            ordered_routes=ranked,
        )


def _validate_ranking(
    candidates: tuple[ModelRoute, ...],
    ranked: tuple[ModelRoute, ...],
) -> None:
    expected = {route.route_id: route for route in candidates}
    actual_ids = [route.route_id for route in ranked]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
        raise ValueError("ranking must return every candidate exactly once")
    if any(route is not expected[route.route_id] for route in ranked):
        raise ValueError("ranking must not replace registry route objects")


class EffectFreeReason(str, Enum):
    BEFORE_ATTEMPT = "before_attempt"
    BEFORE_FIRST_EVENT_NO_SERVER_EFFECT = (
        "before_first_event_no_server_effect"
    )
    RECONCILED_ABSENT = "reconciled_absent"


@dataclass(frozen=True, slots=True)
class EffectFreeEvidence:
    """Auditable evidence supplied by the failure-classification boundary."""

    reason: EffectFreeReason
    proof_ref: str
    accepted_events: int
    provider_interaction_observed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.reason, EffectFreeReason):
            raise TypeError("effect-free reason must be EffectFreeReason")
        _require_non_empty(self.proof_ref, label="effect-free proof reference")
        if (
            not isinstance(self.accepted_events, int)
            or isinstance(self.accepted_events, bool)
            or self.accepted_events != 0
        ):
            raise ValueError("fallback requires exactly zero accepted events")
        if not isinstance(self.provider_interaction_observed, bool):
            raise TypeError("provider interaction flag must be a boolean")
        if (
            self.reason is EffectFreeReason.BEFORE_ATTEMPT
            and self.provider_interaction_observed
        ):
            raise ValueError("before-attempt evidence cannot report provider interaction")


class EffectFreeRouteFailure(ModelClientError):
    """An explicit proof boundary authorizing a different model route."""

    def __init__(
        self,
        cause: ModelClientError,
        *,
        evidence: EffectFreeEvidence,
    ) -> None:
        if not isinstance(cause, ModelClientError):
            raise TypeError("effect-free failure cause must be a model error")
        if not isinstance(evidence, EffectFreeEvidence):
            raise TypeError("effect-free failure requires EffectFreeEvidence")
        if isinstance(cause, ModelStreamInterrupted):
            raise ValueError("a stream interruption cannot authorize unary fallback")
        interaction_in_cause = _cause_has_provider_interaction(cause)
        if interaction_in_cause and not evidence.provider_interaction_observed:
            raise ValueError(
                "failure evidence contradicts observed provider interaction; "
                "reconciliation or an effect-free contract is required"
            )
        if (
            evidence.reason is EffectFreeReason.BEFORE_ATTEMPT
            and interaction_in_cause
        ):
            raise ValueError("before-attempt failure contains provider interaction")
        self.cause = cause
        self.evidence = evidence
        self.reason = evidence.reason
        super().__init__(
            f"effect-free route failure ({evidence.reason.value}, "
            f"proof={evidence.proof_ref}): {cause}"
        )
        self.__cause__ = cause


def _cause_has_provider_interaction(cause: ModelClientError) -> bool:
    if isinstance(cause, ModelTransportError) and cause.received_bytes:
        return True
    if isinstance(cause, ProviderModelError):
        return bool(
            cause.provider_request_id is not None
            or cause.status_code is not None
            or cause.provider_payloads
        )
    return False


class AttemptOutcome(str, Enum):
    EFFECT_FREE_FAILURE = "effect_free_failure"
    SUCCEEDED = "succeeded"


@dataclass(frozen=True, slots=True)
class RouteAttemptRecord:
    number: int
    route_id: str
    model: str
    outcome: AttemptOutcome
    reason: str | None = None
    proof_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.number, int)
            or isinstance(self.number, bool)
            or self.number < 1
        ):
            raise ValueError("attempt number must be a positive integer")
        _require_non_empty(self.route_id, label="attempt route_id")
        _require_non_empty(self.model, label="attempt model")
        if not isinstance(self.outcome, AttemptOutcome):
            raise TypeError("attempt outcome must be an AttemptOutcome")
        if self.outcome is AttemptOutcome.EFFECT_FREE_FAILURE:
            if self.reason is None or self.proof_ref is None:
                raise ValueError("effect-free failure requires reason and proof")
            _require_non_empty(self.reason, label="attempt reason")
            _require_non_empty(self.proof_ref, label="attempt proof reference")
        elif self.reason is not None or self.proof_ref is not None:
            raise ValueError("successful attempt cannot have failure evidence")


@dataclass(frozen=True, slots=True)
class RoutedResponse:
    response: ModelResponse = field(repr=False)
    route_id: str
    registry_revision: str
    requirements_revision: str
    attempts: tuple[RouteAttemptRecord, ...]
    compatibility_reports: tuple[CompatibilityReport, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.response, ModelResponse):
            raise TypeError("routed response must contain a ModelResponse")
        _require_non_empty(self.route_id, label="selected route_id")
        _require_non_empty(self.registry_revision, label="registry revision")
        _require_non_empty(
            self.requirements_revision,
            label="requirements revision",
        )
        attempts = tuple(self.attempts)
        if not attempts:
            raise ValueError("routed response must contain at least one attempt")
        if attempts[-1].outcome is not AttemptOutcome.SUCCEEDED:
            raise ValueError("last routed attempt must be successful")
        if attempts[-1].route_id != self.route_id:
            raise ValueError("selected route must match the successful attempt")
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(
            self,
            "compatibility_reports",
            tuple(self.compatibility_reports),
        )


class MonotonicClock(Protocol):
    def now(self) -> float:
        """Return time in the same monotonic domain as Deadline."""

    async def wait_until(self, when: float) -> None:
        """Wait until ``when`` or propagate caller cancellation."""


class EventLoopClock:
    def now(self) -> float:
        return asyncio.get_running_loop().time()

    async def wait_until(self, when: float) -> None:
        await asyncio.sleep(max(0.0, when - self.now()))


class FallbackExecutor:
    """Unary execution; only an explicit effect-free signal permits fallback."""

    def __init__(
        self,
        router: CapabilityRouter,
        *,
        clock: MonotonicClock | None = None,
    ) -> None:
        self._router = router
        self._clock = clock if clock is not None else EventLoopClock()

    async def complete(
        self,
        request: RoutingRequest,
        *,
        deadline: Deadline,
    ) -> RoutedResponse:
        if request.requirements.stream_mode is not StreamMode.COMPLETE:
            raise ValueError("FallbackExecutor supports only complete mode")

        excluded: set[str] = set()
        attempts: list[RouteAttemptRecord] = []
        reports: tuple[CompatibilityReport, ...] = ()

        while True:
            self._raise_if_expired(request, deadline)
            try:
                plan = self._router._plan_excluding(request, frozenset(excluded))
            except NoCompatibleRoute as error:
                if not attempts:
                    raise
                raise NoCompatibleRoute(
                    registry_revision=error.registry_revision,
                    reports=error.reports,
                    excluded_route_ids=error.excluded_route_ids,
                    attempts=tuple(attempts),
                ) from error
            self._raise_if_expired(request, deadline)
            reports = plan.reports
            route = plan.selected
            number = len(attempts) + 1
            model_request = request.template.bind(route.model)
            try:
                response = await _complete_before_deadline(
                    route,
                    model_request,
                    attempt=ModelAttempt(number=number, deadline=deadline),
                    clock=self._clock,
                )
            except EffectFreeRouteFailure as error:
                attempts.append(
                    RouteAttemptRecord(
                        number=number,
                        route_id=route.route_id,
                        model=route.model,
                        outcome=AttemptOutcome.EFFECT_FREE_FAILURE,
                        reason=error.reason.value,
                        proof_ref=error.evidence.proof_ref,
                    )
                )
                excluded.add(route.route_id)
                continue

            _validate_route_response(route, model_request, response)

            attempts.append(
                RouteAttemptRecord(
                    number=number,
                    route_id=route.route_id,
                    model=route.model,
                    outcome=AttemptOutcome.SUCCEEDED,
                )
            )
            return RoutedResponse(
                response=response,
                route_id=route.route_id,
                registry_revision=plan.registry_revision,
                requirements_revision=plan.requirements_revision,
                attempts=tuple(attempts),
                compatibility_reports=reports,
            )

    def _raise_if_expired(
        self,
        request: RoutingRequest,
        deadline: Deadline,
    ) -> None:
        if self._clock.now() >= deadline.at:
            raise ModelTimeout(
                "routing deadline expired before the next route",
                application_request_id=request.template.request_id,
            )


async def _complete_before_deadline(
    route: ModelRoute,
    request: ModelRequest,
    *,
    attempt: ModelAttempt,
    clock: MonotonicClock,
) -> ModelResponse:
    completion_task = asyncio.create_task(
        route.client.complete(request, attempt=attempt)
    )
    deadline_task = asyncio.create_task(clock.wait_until(attempt.deadline.at))
    try:
        done, _ = await asyncio.wait(
            (completion_task, deadline_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        await _cancel_and_wait(completion_task, deadline_task)
        raise

    if deadline_task in done:
        await _cancel_and_wait(completion_task, deadline_task)
        raise ModelTimeout(
            "routing deadline expired during model completion",
            application_request_id=request.request_id,
        )

    deadline_task.cancel()
    await asyncio.gather(deadline_task, return_exceptions=True)
    return completion_task.result()


async def _cancel_and_wait(*tasks: asyncio.Task[object]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _validate_route_response(
    route: ModelRoute,
    request: ModelRequest,
    response: ModelResponse,
) -> None:
    if response.request_id != request.request_id:
        raise ModelProtocolError(
            "response request_id does not match the routed request",
            application_request_id=request.request_id,
            provider=response.provider,
            provider_payloads=(response.provider_payload,),
        )
    if response.provider != route.provider:
        raise ModelProtocolError(
            "response provider does not match the selected route",
            application_request_id=response.request_id,
            provider=response.provider,
            provider_payloads=(response.provider_payload,),
        )
    if (
        response.model is not None
        and response.model not in route.response_model_ids
    ):
        raise ModelProtocolError(
            "response model does not match the selected route",
            application_request_id=response.request_id,
            provider=response.provider,
            provider_payloads=(response.provider_payload,),
        )
