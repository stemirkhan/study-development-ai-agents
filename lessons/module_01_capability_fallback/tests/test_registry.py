from __future__ import annotations

from dataclasses import replace

import pytest

from lessons.module_01_capability_fallback.implementation.registry import (
    CapabilityRegistry,
    ModelRoute,
)
from lessons.module_01_provider_contract.implementation.scripted_client import (
    ScriptedModelClient,
)

from .test_capabilities import compatible_profile


def route(
    route_id: str,
    *,
    model: str | None = None,
    priority: int = 100,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        provider="provider-a",
        model=model or f"model-{route_id}",
        client=ScriptedModelClient(),
        profile=compatible_profile(),
        priority=priority,
    )


def test_registry_resolves_routes_by_stable_id() -> None:
    primary = route("primary")
    fallback = route("fallback")
    registry = CapabilityRegistry(
        revision="2026-08-12.3",
        routes=(primary, fallback),
    )

    assert registry.get("primary") is primary
    assert tuple(registry) == (primary, fallback)


def test_registry_snapshot_does_not_follow_input_list_mutation() -> None:
    routes = [route("primary")]
    registry = CapabilityRegistry(
        revision="2026-08-12.3",
        routes=routes,  # type: ignore[arg-type]
    )

    routes.clear()

    assert [item.route_id for item in registry] == ["primary"]


def test_duplicate_route_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="route_id"):
        CapabilityRegistry(
            revision="2026-08-12.3",
            routes=(route("same"), route("same")),
        )


def test_same_model_can_have_distinct_api_configurations() -> None:
    first = route("responses-eu", model="shared-alias")
    second = replace(
        route("realtime-us", model="shared-alias"),
        provider="provider-b",
    )

    registry = CapabilityRegistry(
        revision="2026-08-12.3",
        routes=(first, second),
    )

    assert len(registry.routes) == 2
    assert registry.get("responses-eu").model == "shared-alias"
    assert registry.get("realtime-us").model == "shared-alias"


@pytest.mark.parametrize("priority", [-1, True])
def test_invalid_priority_is_rejected(priority: object) -> None:
    with pytest.raises(ValueError, match="priority"):
        route("invalid", priority=priority)  # type: ignore[arg-type]
