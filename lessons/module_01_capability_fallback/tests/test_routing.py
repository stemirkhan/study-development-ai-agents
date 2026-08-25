from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from lessons.module_01_capability_fallback.implementation.capabilities import (
    GapCode,
    Modality,
    SchemaMode,
    SchemaRequirements,
    StreamMode,
)
from lessons.module_01_capability_fallback.implementation.registry import (
    CapabilityRegistry,
    ModelRoute,
)
from lessons.module_01_capability_fallback.implementation.routing import (
    AttemptOutcome,
    CapabilityRouter,
    EffectFreeEvidence,
    EffectFreeReason,
    EffectFreeRouteFailure,
    FallbackExecutor,
    ModelRequestTemplate,
    NoCompatibleRoute,
    RouteAttemptRecord,
    RoutedResponse,
    RoutingRequest,
)
from lessons.module_01_provider_contract.implementation.contracts import (
    Deadline,
    InputMessage,
    JsonSchemaFormat,
    MessageRole,
    ModelProtocolError,
    ModelResponse,
    ModelStreamInterrupted,
    ModelTransportError,
    ModelTimeout,
    ModelUnavailable,
    ProviderPayload,
    ResponseStarted,
    ResponseOutcome,
    StructuredOutput,
    ToolSpec,
)
from lessons.module_01_provider_contract.implementation.scripted_client import (
    ScriptedFailure,
    ScriptedModelClient,
)

from .test_capabilities import compatible_profile, compatible_requirements


def template() -> ModelRequestTemplate:
    return ModelRequestTemplate(
        request_id="req-secret",
        messages=(InputMessage(MessageRole.USER, "confidential screenshot"),),
        tools=(
            ToolSpec(
                "search_evidence",
                "Search",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                },
            ),
            ToolSpec(
                "propose_patch",
                "Propose",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                },
            ),
        ),
        response_format=JsonSchemaFormat(
            schema_id="change-plan/v1",
            schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
            },
        ),
    )


def routing_request(*, stream_mode: StreamMode = StreamMode.COMPLETE) -> RoutingRequest:
    requirements = replace(
        compatible_requirements(),
        stream_mode=stream_mode,
    )
    return RoutingRequest(template(), requirements)


def _before_attempt_evidence(route_id: str) -> EffectFreeEvidence:
    return EffectFreeEvidence(
        reason=EffectFreeReason.BEFORE_ATTEMPT,
        proof_ref=f"health-gate:{route_id}:not-started",
        accepted_events=0,
        provider_interaction_observed=False,
    )


def response(
    *,
    model: str,
    provider: str = "provider",
    request_id: str = "req-secret",
) -> ModelResponse:
    return ModelResponse(
        request_id=request_id,
        provider=provider,
        model=model,
        outcome=ResponseOutcome.COMPLETED,
        outputs=(
            StructuredOutput(
                schema_id="change-plan/v1",
                raw_json='{"plan":[]}',
                value={"plan": []},
            ),
        ),
        provider_payload=ProviderPayload(
            provider=provider,
            kind="response",
            data=b"{}",
        ),
    )


def route(
    route_id: str,
    *,
    client: ScriptedModelClient | None = None,
    priority: int = 100,
    profile=None,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        provider=f"provider-{route_id}",
        model=f"model-{route_id}",
        client=client or ScriptedModelClient(),
        profile=profile or compatible_profile(),
        priority=priority,
    )


class RecordingRanking:
    def __init__(self) -> None:
        self.seen: tuple[str, ...] = ()

    def rank(self, routes: tuple[ModelRoute, ...]) -> tuple[ModelRoute, ...]:
        self.seen = tuple(route.route_id for route in routes)
        return routes


def test_incompatible_route_is_filtered_before_ranking() -> None:
    incompatible = route(
        "cheap",
        priority=0,
        profile=replace(
            compatible_profile(),
            available_tools=frozenset({"search_evidence"}),
        ),
    )
    fallback = route("fallback", priority=100)
    ranking = RecordingRanking()
    router = CapabilityRouter(
        CapabilityRegistry("registry/v1", (incompatible, fallback)),
        ranking=ranking,
    )

    plan = router.plan(routing_request())

    assert plan.selected is fallback
    assert ranking.seen == ("fallback",)
    cheap_report = next(
        report for report in plan.reports if report.route_id == "cheap"
    )
    assert [gap.code for gap in cheap_report.gaps] == [GapCode.MISSING_TOOLS]


def test_priority_orders_only_compatible_routes() -> None:
    later = route("later", priority=20)
    first = route("first", priority=10)
    router = CapabilityRouter(
        CapabilityRegistry("registry/v1", (later, first))
    )

    plan = router.plan(routing_request())

    assert [item.route_id for item in plan.ordered_routes] == ["first", "later"]


def test_no_compatible_route_contains_every_report_and_calls_no_client() -> None:
    first_client = ScriptedModelClient()
    second_client = ScriptedModelClient()
    first = route(
        "first",
        client=first_client,
        profile=replace(
            compatible_profile(),
            available_tools=frozenset(),
        ),
    )
    second = route(
        "second",
        client=second_client,
        profile=replace(
            compatible_profile(),
            schema_modes=frozenset({SchemaMode.TEXT}),
        ),
    )
    router = CapabilityRouter(
        CapabilityRegistry("registry/v1", (first, second))
    )

    with pytest.raises(NoCompatibleRoute) as captured:
        router.plan(routing_request())

    assert [report.route_id for report in captured.value.reports] == [
        "first",
        "second",
    ]
    assert first_client.completion_calls == ()
    assert second_client.completion_calls == ()


def test_template_binding_changes_only_model() -> None:
    original = template()

    bound = original.bind("fallback-model")

    assert bound.model == "fallback-model"
    assert bound.request_id == original.request_id
    assert bound.messages is original.messages
    assert bound.tools is original.tools
    assert bound.response_format is original.response_format


async def test_effect_free_failure_uses_compatible_fallback_and_same_deadline() -> None:
    primary_client = ScriptedModelClient(
        completions=(
            ScriptedFailure(
                EffectFreeRouteFailure(
                    ModelUnavailable("primary unavailable"),
                    evidence=_before_attempt_evidence("primary"),
                )
            ),
        )
    )
    fallback_client = ScriptedModelClient(
        completions=(
            response(model="model-fallback", provider="provider-fallback"),
        ),
        structured_validator=lambda _format, _value: None,
    )
    incompatible_client = ScriptedModelClient()
    primary = route("primary", client=primary_client, priority=0)
    fallback = route("fallback", client=fallback_client, priority=10)
    incompatible = route(
        "cheap",
        client=incompatible_client,
        priority=1,
        profile=replace(
            compatible_profile(),
            experimental_features=frozenset(),
        ),
    )
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (primary, incompatible, fallback),
        )
    )
    deadline = Deadline(asyncio.get_running_loop().time() + 100.0)

    result = await FallbackExecutor(router).complete(
        routing_request(),
        deadline=deadline,
    )

    assert result.route_id == "fallback"
    assert [attempt.route_id for attempt in result.attempts] == [
        "primary",
        "fallback",
    ]
    assert primary_client.completion_calls[0].attempt is not None
    assert fallback_client.completion_calls[0].attempt is not None
    assert primary_client.completion_calls[0].attempt.deadline is deadline
    assert fallback_client.completion_calls[0].attempt.deadline is deadline
    assert primary_client.completion_requests[0].model == "model-primary"
    assert fallback_client.completion_requests[0].model == "model-fallback"
    assert incompatible_client.completion_calls == ()


async def test_incompatible_fallback_is_never_selected_or_called() -> None:
    primary_client = ScriptedModelClient(
        completions=(
            ScriptedFailure(
                EffectFreeRouteFailure(
                    ModelUnavailable("primary unavailable"),
                    evidence=_before_attempt_evidence("primary"),
                )
            ),
        )
    )
    incompatible_client = ScriptedModelClient()
    primary = route("primary", client=primary_client, priority=0)
    incompatible = route(
        "fallback-with-gap",
        client=incompatible_client,
        priority=1,
        profile=replace(
            compatible_profile(),
            available_tools=frozenset({"search_evidence"}),
        ),
    )
    router = CapabilityRouter(
        CapabilityRegistry("registry/v1", (primary, incompatible))
    )

    with pytest.raises(NoCompatibleRoute) as captured:
        await FallbackExecutor(router).complete(
            routing_request(),
            deadline=Deadline(asyncio.get_running_loop().time() + 100.0),
        )

    assert captured.value.excluded_route_ids == frozenset({"primary"})
    assert len(captured.value.attempts) == 1
    assert captured.value.attempts[0].proof_ref == (
        "health-gate:primary:not-started"
    )
    assert primary_client.completion_calls[0].request.model == "model-primary"
    assert incompatible_client.completion_calls == ()


async def test_plain_model_error_does_not_authorize_fallback() -> None:
    primary_client = ScriptedModelClient(
        completions=(ScriptedFailure(ModelUnavailable("ambiguous 503")),)
    )
    fallback_client = ScriptedModelClient(
        completions=(response(model="model-fallback"),)
    )
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (
                route("primary", client=primary_client, priority=0),
                route("fallback", client=fallback_client, priority=10),
            ),
        )
    )

    with pytest.raises(ModelUnavailable, match="ambiguous"):
        await FallbackExecutor(router).complete(
            routing_request(),
            deadline=Deadline(asyncio.get_running_loop().time() + 100.0),
        )

    assert fallback_client.completion_calls == ()


async def test_expired_deadline_calls_no_route() -> None:
    client = ScriptedModelClient()
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (route("primary", client=client),),
        )
    )
    now = asyncio.get_running_loop().time()

    with pytest.raises(ModelTimeout, match="before the next route"):
        await FallbackExecutor(router).complete(
            routing_request(),
            deadline=Deadline(now),
        )

    assert client.completion_calls == ()


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def now(self) -> float:
        return self.value

    async def wait_until(self, when: float) -> None:
        self.value = when
        await asyncio.sleep(0)


class ExpiringRanking:
    def __init__(self, clock: MutableClock) -> None:
        self.clock = clock

    def rank(self, routes: tuple[ModelRoute, ...]) -> tuple[ModelRoute, ...]:
        self.clock.value = 1.0
        return routes


async def test_deadline_is_rechecked_after_ranking_before_client_call() -> None:
    clock = MutableClock(0.0)
    client = ScriptedModelClient()
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (route("primary", client=client),),
        ),
        ranking=ExpiringRanking(clock),
    )

    with pytest.raises(ModelTimeout, match="before the next route"):
        await FallbackExecutor(router, clock=clock).complete(
            routing_request(),
            deadline=Deadline(1.0),
        )

    assert client.completion_calls == ()


class SlowIgnoringClient:
    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False

    async def complete(self, request, *, attempt=None) -> ModelResponse:
        self.calls += 1
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("deadline coordinator did not cancel the client")


async def test_executor_enforces_deadline_when_client_ignores_attempt() -> None:
    client = SlowIgnoringClient()
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (route("primary", client=client),),  # type: ignore[arg-type]
        )
    )

    with pytest.raises(ModelTimeout, match="during model completion"):
        await FallbackExecutor(router).complete(
            routing_request(),
            deadline=Deadline(asyncio.get_running_loop().time() + 0.005),
        )

    assert client.calls == 1
    assert client.cancelled


async def test_response_identity_must_match_selected_route() -> None:
    client = ScriptedModelClient(
        completions=(response(model="model-primary", provider="other-provider"),),
        structured_validator=lambda _format, _value: None,
    )
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (route("primary", client=client),),
        )
    )

    with pytest.raises(ModelProtocolError, match="provider"):
        await FallbackExecutor(router).complete(
            routing_request(),
            deadline=Deadline(asyncio.get_running_loop().time() + 100.0),
        )


async def test_declared_canonical_response_model_is_accepted_for_alias() -> None:
    canonical = "model-primary-2026-08-12"
    client = ScriptedModelClient(
        completions=(
            response(model=canonical, provider="provider-primary"),
        ),
        structured_validator=lambda _format, _value: None,
    )
    alias_route = replace(
        route("primary", client=client),
        accepted_response_models=frozenset({canonical}),
    )
    router = CapabilityRouter(
        CapabilityRegistry("registry/v1", (alias_route,))
    )

    result = await FallbackExecutor(router).complete(
        routing_request(),
        deadline=Deadline(asyncio.get_running_loop().time() + 100.0),
    )

    assert result.route_id == "primary"
    assert result.response.model == canonical


class StaticResponseClient:
    def __init__(self, scripted: ModelResponse) -> None:
        self.scripted = scripted
        self.calls = 0

    async def complete(self, request, *, attempt=None) -> ModelResponse:
        self.calls += 1
        return self.scripted


async def test_response_request_id_must_match_routed_request() -> None:
    client = StaticResponseClient(
        response(
            model="model-primary",
            provider="provider-primary",
            request_id="another-request",
        )
    )
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (route("primary", client=client),),  # type: ignore[arg-type]
        )
    )

    with pytest.raises(ModelProtocolError, match="request_id"):
        await FallbackExecutor(router).complete(
            routing_request(),
            deadline=Deadline(asyncio.get_running_loop().time() + 100.0),
        )

    assert client.calls == 1


async def test_partial_stream_failure_does_not_authorize_fallback() -> None:
    event = ResponseStarted(
        sequence=1,
        provider_payload=ProviderPayload(
            provider="provider-primary",
            kind="response.started",
            data=b"{}",
        ),
        request_id="req-secret",
    )
    interruption = ModelStreamInterrupted(
        (event,),
        ModelTransportError("broken", received_bytes=True),
    )
    primary_client = ScriptedModelClient(
        completions=(ScriptedFailure(interruption),)
    )
    fallback_client = ScriptedModelClient(
        completions=(response(model="model-fallback"),)
    )
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (
                route("primary", client=primary_client, priority=0),
                route("fallback", client=fallback_client, priority=10),
            ),
        )
    )

    with pytest.raises(ModelStreamInterrupted) as captured:
        await FallbackExecutor(router).complete(
            routing_request(),
            deadline=Deadline(asyncio.get_running_loop().time() + 100.0),
        )

    assert captured.value is interruption
    assert fallback_client.completion_calls == ()


def test_effect_free_signal_cannot_wrap_a_stream_interruption() -> None:
    interruption = ModelStreamInterrupted(
        (),
        ModelTransportError("broken", received_bytes=True),
    )

    with pytest.raises(ValueError, match="stream interruption"):
        EffectFreeRouteFailure(
            interruption,
            evidence=EffectFreeEvidence(
                reason=EffectFreeReason.RECONCILED_ABSENT,
                proof_ref="reconciliation:operation-1:absent",
                accepted_events=0,
                provider_interaction_observed=True,
            ),
        )


def test_received_bytes_require_reconciliation_before_fallback() -> None:
    transport = ModelTransportError("broken", received_bytes=True)

    with pytest.raises(ValueError, match="reconciliation"):
        EffectFreeRouteFailure(
            transport,
            evidence=EffectFreeEvidence(
                reason=EffectFreeReason.BEFORE_FIRST_EVENT_NO_SERVER_EFFECT,
                proof_ref="route-contract:v1:no-server-tools",
                accepted_events=0,
                provider_interaction_observed=False,
            ),
        )

    allowed = EffectFreeRouteFailure(
        transport,
        evidence=EffectFreeEvidence(
            reason=EffectFreeReason.RECONCILED_ABSENT,
            proof_ref="reconciliation:operation-1:absent",
            accepted_events=0,
            provider_interaction_observed=True,
        ),
    )
    assert allowed.cause is transport


def test_before_attempt_evidence_rejects_provider_response_metadata() -> None:
    payload = ProviderPayload(
        provider="provider-primary",
        kind="error",
        data=b"{}",
    )
    cause = ModelUnavailable(
        "ambiguous 503",
        provider="provider-primary",
        provider_request_id="provider-request-1",
        status_code=503,
        provider_payloads=(payload,),
    )

    with pytest.raises(ValueError, match="provider interaction"):
        EffectFreeRouteFailure(
            cause,
            evidence=_before_attempt_evidence("primary"),
        )


async def test_cancellation_does_not_start_fallback() -> None:
    primary_client = ScriptedModelClient(
        completions=(ScriptedFailure(asyncio.CancelledError()),)
    )
    fallback_client = ScriptedModelClient(
        completions=(response(model="model-fallback"),)
    )
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (
                route("primary", client=primary_client, priority=0),
                route("fallback", client=fallback_client, priority=10),
            ),
        )
    )

    with pytest.raises(asyncio.CancelledError):
        await FallbackExecutor(router).complete(
            routing_request(),
            deadline=Deadline(asyncio.get_running_loop().time() + 100.0),
        )

    assert fallback_client.completion_calls == ()


def test_routing_request_rejects_undeclared_tool_capability() -> None:
    requirements = replace(
        compatible_requirements(),
        tools=frozenset({"search_evidence"}),
        stream_mode=StreamMode.COMPLETE,
    )

    with pytest.raises(ValueError, match="tools"):
        RoutingRequest(template(), requirements)


def test_routing_request_rejects_silent_schema_downgrade() -> None:
    requirements = replace(
        compatible_requirements(),
        schema=SchemaRequirements(mode=SchemaMode.TEXT),
        stream_mode=StreamMode.COMPLETE,
    )

    with pytest.raises(ValueError, match="strict schema"):
        RoutingRequest(template(), requirements)


def test_routing_request_rejects_undeclared_nested_schema_keyword() -> None:
    schema_template = replace(
        template(),
        response_format=JsonSchemaFormat(
            schema_id="change-plan/v1",
            schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "safe_name": {
                        "type": "string",
                        "pattern": "^safe$",
                    }
                },
            },
        ),
    )

    with pytest.raises(ValueError, match="pattern"):
        RoutingRequest(
            schema_template,
            replace(compatible_requirements(), stream_mode=StreamMode.COMPLETE),
        )


def test_routing_request_rejects_undeclared_tool_schema_keyword() -> None:
    original = template()
    tool_with_union = ToolSpec(
        "search_evidence",
        "Search",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "oneOf": (
                {"type": "object"},
                {"type": "string"},
            ),
        },
    )
    schema_template = replace(
        original,
        tools=(tool_with_union, original.tools[1]),
    )

    with pytest.raises(ValueError, match="oneOf"):
        RoutingRequest(
            schema_template,
            replace(compatible_requirements(), stream_mode=StreamMode.COMPLETE),
        )


def test_routing_request_rejects_schema_dialect_mismatch() -> None:
    requirements = replace(
        compatible_requirements(),
        schema=replace(
            compatible_requirements().schema,
            dialect="https://json-schema.org/draft/2019-09/schema",
        ),
        stream_mode=StreamMode.COMPLETE,
    )

    with pytest.raises(ValueError, match="dialect"):
        RoutingRequest(template(), requirements)


def test_json_schema_mode_is_reachable_without_claiming_strictness() -> None:
    requirements = replace(
        compatible_requirements(),
        schema=replace(
            compatible_requirements().schema,
            mode=SchemaMode.JSON,
        ),
        stream_mode=StreamMode.COMPLETE,
    )
    json_profile = replace(
        compatible_profile(),
        schema_modes=(
            compatible_profile().schema_modes | frozenset({SchemaMode.JSON})
        ),
    )
    router = CapabilityRouter(
        CapabilityRegistry(
            "registry/v1",
            (route("json", profile=json_profile),),
        )
    )

    assert router.plan(RoutingRequest(template(), requirements)).selected.route_id == (
        "json"
    )


def test_draft_07_dependency_subschema_is_preflighted() -> None:
    schema_template = replace(
        template(),
        response_format=JsonSchemaFormat(
            schema_id="change-plan/v1",
            schema={
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "dependencies": {
                    "name": {
                        "type": "string",
                        "pattern": "^safe$",
                    }
                },
            },
        ),
    )
    requirements = replace(
        compatible_requirements(),
        schema=SchemaRequirements(
            mode=SchemaMode.STRICT,
            dialect="http://json-schema.org/draft-07/schema#",
            keywords=frozenset({"$schema", "type", "dependencies"}),
        ),
        stream_mode=StreamMode.COMPLETE,
    )

    with pytest.raises(ValueError, match="pattern"):
        RoutingRequest(schema_template, requirements)


def test_unknown_schema_dialect_fails_closed_without_preflight() -> None:
    dialect = "https://example.test/custom-schema/v1"
    schema_template = replace(
        template(),
        response_format=JsonSchemaFormat(
            schema_id="change-plan/v1",
            schema={"$schema": dialect, "type": "object"},
        ),
    )
    requirements = replace(
        compatible_requirements(),
        schema=SchemaRequirements(
            mode=SchemaMode.STRICT,
            dialect=dialect,
            keywords=frozenset({"$schema", "type"}),
        ),
        stream_mode=StreamMode.COMPLETE,
    )

    with pytest.raises(ValueError, match="without a local preflight"):
        RoutingRequest(schema_template, requirements)


def test_audit_objects_do_not_contain_messages_or_tool_arguments() -> None:
    routed = RoutedResponse(
        response=response(model="model"),
        route_id="route",
        registry_revision="registry/v1",
        requirements_revision="change-plan/v1",
        attempts=(
            RouteAttemptRecord(
                number=1,
                route_id="route",
                model="model",
                outcome=AttemptOutcome.SUCCEEDED,
            ),
        ),
        compatibility_reports=(),
    )

    assert "confidential screenshot" not in repr(routed)
    assert "input_schema" not in repr(routed)


def test_streaming_request_is_planned_but_not_executed_by_unary_runtime() -> None:
    router = CapabilityRouter(
        CapabilityRegistry("registry/v1", (route("stream"),))
    )
    request = routing_request(stream_mode=StreamMode.STREAM)

    assert router.plan(request).selected.route_id == "stream"
    with pytest.raises(ValueError, match="only complete"):
        asyncio.run(
            FallbackExecutor(router).complete(
                request,
                deadline=Deadline(100.0),
            )
        )
