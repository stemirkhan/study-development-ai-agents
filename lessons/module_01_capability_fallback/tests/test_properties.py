from __future__ import annotations

from dataclasses import replace
from itertools import product

from lessons.module_01_capability_fallback.implementation.capabilities import (
    CapabilityProfile,
    Maturity,
    Modality,
    ProfileStatus,
    SchemaMode,
    StreamMode,
    check_compatibility,
)
from lessons.module_01_capability_fallback.implementation.registry import (
    CapabilityRegistry,
)
from lessons.module_01_capability_fallback.implementation.routing import (
    CapabilityRouter,
)

from .test_capabilities import compatible_profile
from .test_routing import route, routing_request


DIMENSIONS = (
    "verified",
    "tools",
    "input_modalities",
    "output_modalities",
    "schema_mode",
    "schema_dialect",
    "schema_keywords",
    "input_limit",
    "output_limit",
    "total_limit",
    "stream_mode",
    "experimental",
    "maturity",
)


def test_property_incompatible_fallback_is_never_selected() -> None:
    """Exhaust every yes/no capability combination without a live API."""

    request = routing_request(stream_mode=StreamMode.COMPLETE)
    control = route("compatible-control", priority=1_000)

    for flags in product((False, True), repeat=len(DIMENSIONS)):
        enabled = dict(zip(DIMENSIONS, flags, strict=True))
        candidate = route(
            "candidate",
            priority=0,
            profile=_profile(enabled),
        )
        report = check_compatibility(
            candidate.route_id,
            candidate.profile,
            request.requirements,
        )
        router = CapabilityRouter(
            CapabilityRegistry(
                revision="property-snapshot/v1",
                routes=(candidate, control),
            )
        )

        plan = router.plan(request)

        assert report.compatible is all(flags), enabled
        expected = "candidate" if all(flags) else "compatible-control"
        assert plan.selected.route_id == expected, enabled
        assert candidate.client.completion_calls == ()  # type: ignore[attr-defined]
        assert control.client.completion_calls == ()  # type: ignore[attr-defined]


def _profile(enabled: dict[str, bool]) -> CapabilityProfile:
    base = compatible_profile()
    return replace(
        base,
        status=(
            ProfileStatus.VERIFIED
            if enabled["verified"]
            else ProfileStatus.UNKNOWN
        ),
        available_tools=(
            base.available_tools
            if enabled["tools"]
            else frozenset({"search_evidence"})
        ),
        input_modalities=(
            base.input_modalities
            if enabled["input_modalities"]
            else frozenset({Modality.TEXT})
        ),
        output_modalities=(
            base.output_modalities
            if enabled["output_modalities"]
            else frozenset({Modality.AUDIO})
        ),
        schema_modes=(
            base.schema_modes
            if enabled["schema_mode"]
            else frozenset({SchemaMode.TEXT})
        ),
        schema_dialects=(
            base.schema_dialects
            if enabled["schema_dialect"]
            else frozenset({"draft-07"})
        ),
        schema_keywords=(
            base.schema_keywords
            if enabled["schema_keywords"]
            else frozenset({"type", "required"})
        ),
        max_input_tokens=(64_000 if enabled["input_limit"] else 51_999),
        max_output_tokens=(8_000 if enabled["output_limit"] else 3_999),
        max_total_tokens=(64_000 if enabled["total_limit"] else 55_999),
        stream_modes=(
            base.stream_modes
            if enabled["stream_mode"]
            else frozenset({StreamMode.STREAM})
        ),
        experimental_features=(
            base.experimental_features
            if enabled["experimental"]
            else frozenset()
        ),
        maturity=(Maturity.STABLE if enabled["maturity"] else Maturity.PREVIEW),
    )
