"""Immutable, versioned registry of concrete model routes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from lessons.module_01_provider_contract.implementation.contracts import ModelClient

from .capabilities import CapabilityProfile


def _require_non_empty(value: str, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """One adapter configuration and its verified capability profile."""

    route_id: str
    provider: str
    model: str
    client: ModelClient = field(repr=False, compare=False)
    profile: CapabilityProfile
    priority: int = 100
    accepted_response_models: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _require_non_empty(self.route_id, label="route_id")
        _require_non_empty(self.provider, label="provider")
        _require_non_empty(self.model, label="model")
        if not isinstance(self.profile, CapabilityProfile):
            raise TypeError("route profile must be a CapabilityProfile")
        if (
            not isinstance(self.priority, int)
            or isinstance(self.priority, bool)
            or self.priority < 0
        ):
            raise ValueError("route priority must be a non-negative integer")
        if isinstance(self.accepted_response_models, (str, bytes)):
            raise TypeError("accepted response models must be an iterable")
        try:
            accepted = frozenset(self.accepted_response_models)
        except TypeError as exc:
            raise TypeError("accepted response models must be an iterable") from exc
        for response_model in accepted:
            _require_non_empty(response_model, label="accepted response model")
        object.__setattr__(self, "accepted_response_models", accepted)

    @property
    def response_model_ids(self) -> frozenset[str]:
        return self.accepted_response_models | frozenset({self.model})


@dataclass(frozen=True, slots=True)
class CapabilityRegistry:
    """A stable snapshot; updates create a new revision and object."""

    revision: str
    routes: tuple[ModelRoute, ...]
    _by_id: Mapping[str, ModelRoute] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _require_non_empty(self.revision, label="registry revision")
        routes = tuple(self.routes)
        if not routes:
            raise ValueError("registry must contain at least one route")
        if any(not isinstance(route, ModelRoute) for route in routes):
            raise TypeError("registry routes must be ModelRoute instances")
        by_id = {route.route_id: route for route in routes}
        if len(by_id) != len(routes):
            raise ValueError("route_id values must be unique within a registry")
        object.__setattr__(self, "routes", routes)
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))

    def __iter__(self) -> Iterator[ModelRoute]:
        return iter(self.routes)

    def get(self, route_id: str) -> ModelRoute:
        return self._by_id[route_id]
