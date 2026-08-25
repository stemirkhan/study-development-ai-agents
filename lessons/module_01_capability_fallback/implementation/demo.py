"""Observable primary-to-fallback trace with one incompatible candidate."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from lessons.module_01_provider_contract.implementation.contracts import (
    Deadline,
    InputMessage,
    JsonSchemaFormat,
    MessageRole,
    ModelResponse,
    ModelUnavailable,
    ProviderPayload,
    ResponseOutcome,
    StructuredOutput,
    ToolSpec,
)
from lessons.module_01_provider_contract.implementation.scripted_client import (
    ScriptedFailure,
    ScriptedModelClient,
)

from .capabilities import (
    CapabilityProfile,
    CapabilityRequirements,
    Maturity,
    Modality,
    ProfileStatus,
    SchemaMode,
    SchemaRequirements,
    StreamMode,
)
from .registry import CapabilityRegistry, ModelRoute
from .routing import (
    CapabilityRouter,
    EffectFreeEvidence,
    EffectFreeReason,
    EffectFreeRouteFailure,
    FallbackExecutor,
    ModelRequestTemplate,
    RoutedResponse,
    RoutingRequest,
)


@dataclass(frozen=True, slots=True)
class DemoResult:
    routed: RoutedResponse
    primary_calls: int
    fallback_calls: int
    incompatible_calls: int
    deadline_preserved: bool


async def run_demo() -> DemoResult:
    requirements = _requirements()
    template = _template()
    request = RoutingRequest(template, requirements)

    primary_client = ScriptedModelClient(
        completions=(
            ScriptedFailure(
                EffectFreeRouteFailure(
                    ModelUnavailable("health gate rejected the primary route"),
                    evidence=EffectFreeEvidence(
                        reason=EffectFreeReason.BEFORE_ATTEMPT,
                        proof_ref="health-gate:primary-a:not-started",
                        accepted_events=0,
                        provider_interaction_observed=False,
                    ),
                )
            ),
        )
    )
    fallback_client = ScriptedModelClient(
        completions=(_response(),),
        structured_validator=lambda _format, _value: None,
    )
    incompatible_client = ScriptedModelClient()
    profile = _compatible_profile()
    registry = CapabilityRegistry(
        revision="2026-08-12.3",
        routes=(
            ModelRoute(
                route_id="primary-a",
                provider="provider-a",
                model="model-primary",
                client=primary_client,
                profile=profile,
                priority=0,
            ),
            ModelRoute(
                route_id="cheap-c",
                provider="provider-c",
                model="model-cheap",
                client=incompatible_client,
                profile=replace(
                    profile,
                    available_tools=frozenset({"search_evidence"}),
                    input_modalities=frozenset({Modality.TEXT}),
                    schema_modes=frozenset({SchemaMode.TEXT}),
                    experimental_features=frozenset(),
                ),
                priority=1,
            ),
            ModelRoute(
                route_id="fallback-b",
                provider="provider-b",
                model="model-fallback",
                client=fallback_client,
                profile=profile,
                priority=10,
            ),
        ),
    )
    router = CapabilityRouter(registry)

    initial = router.plan(request)
    print(
        f"registry_revision={initial.registry_revision} "
        f"requirements_revision={initial.requirements_revision}"
    )
    for report in initial.reports:
        gaps = ",".join(gap.code.value for gap in report.gaps) or "none"
        print(
            f"candidate={report.route_id} "
            f"compatible={str(report.compatible).lower()} gaps={gaps}"
        )
    print(f"initial={initial.selected.route_id}")

    deadline = Deadline(asyncio.get_running_loop().time() + 30.0)
    routed = await FallbackExecutor(router).complete(
        request,
        deadline=deadline,
    )
    for attempt in routed.attempts:
        reason = f" reason={attempt.reason}" if attempt.reason else ""
        print(
            f"attempt={attempt.number} route={attempt.route_id} "
            f"outcome={attempt.outcome.value}{reason}"
        )
    print(f"selected={routed.route_id} response={routed.response.outcome.value}")

    attempts = primary_client.completion_calls + fallback_client.completion_calls
    deadline_preserved = all(
        call.attempt is not None and call.attempt.deadline is deadline
        for call in attempts
    )
    print(
        f"request_preserved=true deadline_preserved="
        f"{str(deadline_preserved).lower()} incompatible_calls="
        f"{len(incompatible_client.completion_calls)}"
    )
    return DemoResult(
        routed=routed,
        primary_calls=len(primary_client.completion_calls),
        fallback_calls=len(fallback_client.completion_calls),
        incompatible_calls=len(incompatible_client.completion_calls),
        deadline_preserved=deadline_preserved,
    )


def _requirements() -> CapabilityRequirements:
    return CapabilityRequirements(
        revision="change-plan/v1",
        tools=frozenset({"search_evidence", "propose_patch"}),
        input_modalities=frozenset({Modality.TEXT, Modality.IMAGE}),
        output_modalities=frozenset({Modality.TEXT}),
        schema=SchemaRequirements(
            mode=SchemaMode.STRICT,
            dialect="https://json-schema.org/draft/2020-12/schema",
            keywords=frozenset(
                {"$schema", "type", "required", "additionalProperties"}
            ),
        ),
        tool_schema=SchemaRequirements(
            mode=SchemaMode.STRICT,
            dialect="https://json-schema.org/draft/2020-12/schema",
            keywords=frozenset({"$schema", "type"}),
        ),
        min_input_tokens=52_000,
        min_output_tokens=4_000,
        stream_mode=StreamMode.COMPLETE,
        experimental_features=frozenset({"reasoning_budget:v1"}),
        allowed_maturity=frozenset({Maturity.STABLE}),
    )


def _compatible_profile() -> CapabilityProfile:
    return CapabilityProfile(
        available_tools=frozenset({"search_evidence", "propose_patch"}),
        input_modalities=frozenset({Modality.TEXT, Modality.IMAGE}),
        output_modalities=frozenset({Modality.TEXT}),
        schema_modes=frozenset({SchemaMode.TEXT, SchemaMode.STRICT}),
        schema_dialects=frozenset(
            {"https://json-schema.org/draft/2020-12/schema"}
        ),
        schema_keywords=frozenset(
            {"$schema", "type", "required", "additionalProperties"}
        ),
        max_input_tokens=64_000,
        max_output_tokens=8_000,
        max_total_tokens=64_000,
        stream_modes=frozenset({StreamMode.COMPLETE, StreamMode.STREAM}),
        experimental_features=frozenset({"reasoning_budget:v1"}),
        maturity=Maturity.STABLE,
        status=ProfileStatus.VERIFIED,
        source="provider docs + controlled probe",
        verified_at="2026-08-12T10:00:00Z",
    )


def _template() -> ModelRequestTemplate:
    return ModelRequestTemplate(
        request_id="change-plan-42",
        messages=(
            InputMessage(
                MessageRole.USER,
                "Build a change plan from the supplied screenshot",
            ),
        ),
        tools=(
            ToolSpec(
                "search_evidence",
                "Search evidence",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                },
            ),
            ToolSpec(
                "propose_patch",
                "Propose patch",
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


def _response() -> ModelResponse:
    return ModelResponse(
        request_id="change-plan-42",
        provider="provider-b",
        model="model-fallback",
        outcome=ResponseOutcome.COMPLETED,
        outputs=(
            StructuredOutput(
                schema_id="change-plan/v1",
                raw_json='{"plan":["inspect","patch"]}',
                value={"plan": ("inspect", "patch")},
            ),
        ),
        provider_payload=ProviderPayload(
            provider="provider-b",
            kind="response",
            data=b"{}",
        ),
    )


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
