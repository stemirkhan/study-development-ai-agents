from __future__ import annotations

from dataclasses import replace

import pytest

from lessons.module_01_capability_fallback.implementation.capabilities import (
    CapabilityProfile,
    CapabilityRequirements,
    GapCode,
    Maturity,
    Modality,
    ProfileStatus,
    SchemaMode,
    SchemaRequirements,
    StreamMode,
    check_compatibility,
)


def compatible_requirements() -> CapabilityRequirements:
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
        stream_mode=StreamMode.STREAM,
        experimental_features=frozenset({"reasoning_budget:v1"}),
        allowed_maturity=frozenset({Maturity.STABLE}),
    )


def compatible_profile() -> CapabilityProfile:
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
        source="provider docs + probe",
        verified_at="2026-08-12T10:00:00Z",
    )


def test_fully_compatible_profile_has_no_gaps() -> None:
    report = check_compatibility(
        "fallback-b",
        compatible_profile(),
        compatible_requirements(),
    )

    assert report.compatible
    assert report.gaps == ()


def test_every_mismatch_is_reported_in_stable_order() -> None:
    profile = CapabilityProfile(
        available_tools=frozenset({"search_evidence"}),
        input_modalities=frozenset({Modality.TEXT}),
        output_modalities=frozenset({Modality.AUDIO}),
        schema_modes=frozenset({SchemaMode.TEXT}),
        schema_dialects=frozenset({"draft-07"}),
        schema_keywords=frozenset({"type"}),
        max_input_tokens=32_000,
        max_output_tokens=2_000,
        max_total_tokens=34_000,
        stream_modes=frozenset({StreamMode.COMPLETE}),
        experimental_features=frozenset(),
        maturity=Maturity.PREVIEW,
        status=ProfileStatus.UNKNOWN,
        source="stale catalog",
        verified_at="2026-01-01T00:00:00Z",
    )

    report = check_compatibility(
        "cheap-c",
        profile,
        compatible_requirements(),
    )

    assert [gap.code for gap in report.gaps] == [
        GapCode.PROFILE_UNKNOWN,
        GapCode.MISSING_TOOLS,
        GapCode.INPUT_MODALITIES,
        GapCode.OUTPUT_MODALITIES,
        GapCode.SCHEMA_MODE,
        GapCode.SCHEMA_DIALECT,
        GapCode.SCHEMA_KEYWORDS,
        GapCode.INPUT_CONTEXT,
        GapCode.OUTPUT_CONTEXT,
        GapCode.TOTAL_CONTEXT,
        GapCode.STREAM_MODE,
        GapCode.EXPERIMENTAL_FEATURES,
        GapCode.MATURITY,
    ]
    assert not report.compatible


def test_exact_context_boundaries_are_compatible() -> None:
    requirements = replace(
        compatible_requirements(),
        min_input_tokens=60_000,
        min_output_tokens=4_000,
    )
    profile = replace(
        compatible_profile(),
        max_input_tokens=60_000,
        max_output_tokens=4_000,
        max_total_tokens=64_000,
    )

    assert check_compatibility("boundary", profile, requirements).compatible


def test_unknown_profile_fails_closed_even_when_values_match() -> None:
    profile = replace(compatible_profile(), status=ProfileStatus.UNKNOWN)

    report = check_compatibility(
        "unknown",
        profile,
        compatible_requirements(),
    )

    assert [gap.code for gap in report.gaps] == [GapCode.PROFILE_UNKNOWN]


def test_mutating_input_sets_cannot_change_a_snapshot() -> None:
    tools = {"search_evidence", "propose_patch"}
    profile = replace(compatible_profile(), available_tools=tools)  # type: ignore[arg-type]

    tools.clear()

    assert profile.available_tools == frozenset(
        {"search_evidence", "propose_patch"}
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_input_tokens", 0),
        ("max_output_tokens", True),
        ("max_total_tokens", -1),
        ("source", " "),
    ],
)
def test_invalid_profile_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(compatible_profile(), **{field: value})


def test_text_schema_cannot_smuggle_schema_requirements() -> None:
    with pytest.raises(ValueError, match="text output"):
        SchemaRequirements(
            mode=SchemaMode.TEXT,
            dialect="https://json-schema.org/draft/2020-12/schema",
        )
