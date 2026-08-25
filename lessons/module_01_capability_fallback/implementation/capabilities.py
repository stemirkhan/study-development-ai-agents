"""Typed capability requirements, profiles, and fail-closed compatibility."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar


class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class SchemaMode(str, Enum):
    TEXT = "text"
    JSON = "json"
    STRICT = "strict"


class StreamMode(str, Enum):
    COMPLETE = "complete"
    STREAM = "stream"


class Maturity(str, Enum):
    STABLE = "stable"
    PREVIEW = "preview"


class ProfileStatus(str, Enum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"


class GapCode(str, Enum):
    PROFILE_UNKNOWN = "profile_unknown"
    MISSING_TOOLS = "missing_tools"
    INPUT_MODALITIES = "input_modalities"
    OUTPUT_MODALITIES = "output_modalities"
    SCHEMA_MODE = "schema_mode"
    SCHEMA_DIALECT = "schema_dialect"
    SCHEMA_KEYWORDS = "schema_keywords"
    INPUT_CONTEXT = "input_context"
    OUTPUT_CONTEXT = "output_context"
    TOTAL_CONTEXT = "total_context"
    STREAM_MODE = "stream_mode"
    EXPERIMENTAL_FEATURES = "experimental_features"
    MATURITY = "maturity"


_EnumT = TypeVar("_EnumT", bound=Enum)


def _require_non_empty(value: str, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _require_non_negative_int(value: object, *, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _require_positive_int(value: object, *, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")


def _freeze_strings(
    values: Iterable[object],
    *,
    label: str,
) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{label} must be an iterable of strings")
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise TypeError(f"{label} must be an iterable of strings") from exc
    frozen: set[str] = set()
    for value in iterator:
        if not isinstance(value, str):
            raise TypeError(f"{label} must contain only strings")
        _require_non_empty(value, label=label)
        frozen.add(value)
    return frozenset(frozen)


def _freeze_enum_values(
    values: Iterable[object],
    enum_type: type[_EnumT],
    *,
    label: str,
) -> frozenset[_EnumT]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{label} must be an iterable")
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise TypeError(f"{label} must be an iterable") from exc
    frozen: set[_EnumT] = set()
    for value in iterator:
        if not isinstance(value, enum_type):
            raise TypeError(f"{label} must contain only {enum_type.__name__}")
        frozen.add(value)
    return frozenset(frozen)


@dataclass(frozen=True, slots=True)
class SchemaRequirements:
    mode: SchemaMode = SchemaMode.TEXT
    dialect: str | None = None
    keywords: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SchemaMode):
            raise TypeError("schema mode must be a SchemaMode")
        keywords = _freeze_strings(self.keywords, label="schema keyword")
        object.__setattr__(self, "keywords", keywords)
        if self.mode is SchemaMode.TEXT:
            if self.dialect is not None or keywords:
                raise ValueError("text output cannot require a schema dialect")
            return
        if self.dialect is not None:
            _require_non_empty(self.dialect, label="schema dialect")
        if self.dialect is None:
            raise ValueError("JSON schema output requires a dialect")


@dataclass(frozen=True, slots=True)
class CapabilityRequirements:
    revision: str
    tools: frozenset[str] = frozenset()
    input_modalities: frozenset[Modality] = frozenset({Modality.TEXT})
    output_modalities: frozenset[Modality] = frozenset({Modality.TEXT})
    schema: SchemaRequirements = SchemaRequirements()
    tool_schema: SchemaRequirements | None = None
    min_input_tokens: int = 0
    min_output_tokens: int = 0
    stream_mode: StreamMode = StreamMode.COMPLETE
    experimental_features: frozenset[str] = frozenset()
    allowed_maturity: frozenset[Maturity] = frozenset({Maturity.STABLE})

    def __post_init__(self) -> None:
        _require_non_empty(self.revision, label="requirements revision")
        object.__setattr__(
            self,
            "tools",
            _freeze_strings(self.tools, label="required tool"),
        )
        object.__setattr__(
            self,
            "input_modalities",
            _freeze_enum_values(
                self.input_modalities,
                Modality,
                label="required input modality",
            ),
        )
        object.__setattr__(
            self,
            "output_modalities",
            _freeze_enum_values(
                self.output_modalities,
                Modality,
                label="required output modality",
            ),
        )
        if not self.input_modalities or not self.output_modalities:
            raise ValueError("input and output modalities must be non-empty")
        if not isinstance(self.schema, SchemaRequirements):
            raise TypeError("schema must be SchemaRequirements")
        if self.tool_schema is not None and not isinstance(
            self.tool_schema,
            SchemaRequirements,
        ):
            raise TypeError("tool_schema must be SchemaRequirements or None")
        if self.tools and self.tool_schema is None:
            raise ValueError("tools require explicit tool_schema capabilities")
        if not self.tools and self.tool_schema is not None:
            raise ValueError("tool_schema cannot be declared without tools")
        if (
            self.tool_schema is not None
            and self.tool_schema.mode is SchemaMode.TEXT
        ):
            raise ValueError("tool schemas must use JSON or strict mode")
        _require_non_negative_int(
            self.min_input_tokens,
            label="minimum input tokens",
        )
        _require_non_negative_int(
            self.min_output_tokens,
            label="minimum output tokens",
        )
        if not isinstance(self.stream_mode, StreamMode):
            raise TypeError("stream mode must be a StreamMode")
        object.__setattr__(
            self,
            "experimental_features",
            _freeze_strings(
                self.experimental_features,
                label="required experimental feature",
            ),
        )
        maturity = _freeze_enum_values(
            self.allowed_maturity,
            Maturity,
            label="allowed maturity",
        )
        if not maturity:
            raise ValueError("at least one maturity must be allowed")
        object.__setattr__(self, "allowed_maturity", maturity)


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    available_tools: frozenset[str]
    input_modalities: frozenset[Modality]
    output_modalities: frozenset[Modality]
    schema_modes: frozenset[SchemaMode]
    schema_dialects: frozenset[str]
    schema_keywords: frozenset[str]
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int | None
    stream_modes: frozenset[StreamMode]
    experimental_features: frozenset[str]
    maturity: Maturity
    status: ProfileStatus
    source: str
    verified_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "available_tools",
            _freeze_strings(self.available_tools, label="available tool"),
        )
        for name in ("input_modalities", "output_modalities"):
            frozen = _freeze_enum_values(
                getattr(self, name),
                Modality,
                label=name,
            )
            if not frozen:
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, frozen)
        schema_modes = _freeze_enum_values(
            self.schema_modes,
            SchemaMode,
            label="schema modes",
        )
        if not schema_modes:
            raise ValueError("schema_modes must be non-empty")
        object.__setattr__(self, "schema_modes", schema_modes)
        object.__setattr__(
            self,
            "schema_dialects",
            _freeze_strings(self.schema_dialects, label="schema dialect"),
        )
        object.__setattr__(
            self,
            "schema_keywords",
            _freeze_strings(self.schema_keywords, label="schema keyword"),
        )
        _require_positive_int(self.max_input_tokens, label="max input tokens")
        _require_positive_int(self.max_output_tokens, label="max output tokens")
        if self.max_total_tokens is not None:
            _require_positive_int(
                self.max_total_tokens,
                label="max total tokens",
            )
        stream_modes = _freeze_enum_values(
            self.stream_modes,
            StreamMode,
            label="stream modes",
        )
        if not stream_modes:
            raise ValueError("stream_modes must be non-empty")
        object.__setattr__(self, "stream_modes", stream_modes)
        object.__setattr__(
            self,
            "experimental_features",
            _freeze_strings(
                self.experimental_features,
                label="experimental feature",
            ),
        )
        if not isinstance(self.maturity, Maturity):
            raise TypeError("maturity must be a Maturity")
        if not isinstance(self.status, ProfileStatus):
            raise TypeError("status must be a ProfileStatus")
        _require_non_empty(self.source, label="profile source")
        _require_non_empty(self.verified_at, label="profile verification time")


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    code: GapCode
    required: tuple[str, ...]
    available: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, GapCode):
            raise TypeError("gap code must be a GapCode")
        object.__setattr__(self, "required", tuple(self.required))
        object.__setattr__(self, "available", tuple(self.available))


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    route_id: str
    requirements_revision: str
    gaps: tuple[CapabilityGap, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.route_id, label="route_id")
        _require_non_empty(
            self.requirements_revision,
            label="requirements revision",
        )
        object.__setattr__(self, "gaps", tuple(self.gaps))

    @property
    def compatible(self) -> bool:
        return not self.gaps


def check_compatibility(
    route_id: str,
    profile: CapabilityProfile,
    requirements: CapabilityRequirements,
) -> CompatibilityReport:
    """Return every known mismatch; unknown metadata never passes."""

    gaps: list[CapabilityGap] = []
    if profile.status is ProfileStatus.UNKNOWN:
        gaps.append(
            CapabilityGap(
                GapCode.PROFILE_UNKNOWN,
                (ProfileStatus.VERIFIED.value,),
                (ProfileStatus.UNKNOWN.value,),
            )
        )

    missing_tools = requirements.tools - profile.available_tools
    if missing_tools:
        gaps.append(
            CapabilityGap(
                GapCode.MISSING_TOOLS,
                tuple(sorted(missing_tools)),
                tuple(sorted(profile.available_tools)),
            )
        )

    missing_input = requirements.input_modalities - profile.input_modalities
    if missing_input:
        gaps.append(
            CapabilityGap(
                GapCode.INPUT_MODALITIES,
                _enum_values(missing_input),
                _enum_values(profile.input_modalities),
            )
        )

    missing_output = requirements.output_modalities - profile.output_modalities
    if missing_output:
        gaps.append(
            CapabilityGap(
                GapCode.OUTPUT_MODALITIES,
                _enum_values(missing_output),
                _enum_values(profile.output_modalities),
            )
        )

    schema_requirements = [requirements.schema]
    if requirements.tool_schema is not None:
        schema_requirements.append(requirements.tool_schema)
    required_schema_modes = frozenset(
        requirement.mode for requirement in schema_requirements
    )
    missing_schema_modes = required_schema_modes - profile.schema_modes
    if missing_schema_modes:
        gaps.append(
            CapabilityGap(
                GapCode.SCHEMA_MODE,
                _enum_values(missing_schema_modes),
                _enum_values(profile.schema_modes),
            )
        )
    required_dialects = frozenset(
        requirement.dialect
        for requirement in schema_requirements
        if requirement.dialect is not None
    )
    missing_dialects = required_dialects - profile.schema_dialects
    if missing_dialects:
        gaps.append(
            CapabilityGap(
                GapCode.SCHEMA_DIALECT,
                tuple(sorted(missing_dialects)),
                tuple(sorted(profile.schema_dialects)),
            )
        )
    required_keywords = frozenset().union(
        *(requirement.keywords for requirement in schema_requirements)
    )
    missing_keywords = required_keywords - profile.schema_keywords
    if missing_keywords:
        gaps.append(
            CapabilityGap(
                GapCode.SCHEMA_KEYWORDS,
                tuple(sorted(missing_keywords)),
                tuple(sorted(profile.schema_keywords)),
            )
        )

    if requirements.min_input_tokens > profile.max_input_tokens:
        gaps.append(
            CapabilityGap(
                GapCode.INPUT_CONTEXT,
                (str(requirements.min_input_tokens),),
                (str(profile.max_input_tokens),),
            )
        )
    if requirements.min_output_tokens > profile.max_output_tokens:
        gaps.append(
            CapabilityGap(
                GapCode.OUTPUT_CONTEXT,
                (str(requirements.min_output_tokens),),
                (str(profile.max_output_tokens),),
            )
        )
    required_total = (
        requirements.min_input_tokens + requirements.min_output_tokens
    )
    if (
        profile.max_total_tokens is not None
        and required_total > profile.max_total_tokens
    ):
        gaps.append(
            CapabilityGap(
                GapCode.TOTAL_CONTEXT,
                (str(required_total),),
                (str(profile.max_total_tokens),),
            )
        )

    if requirements.stream_mode not in profile.stream_modes:
        gaps.append(
            CapabilityGap(
                GapCode.STREAM_MODE,
                (requirements.stream_mode.value,),
                _enum_values(profile.stream_modes),
            )
        )

    missing_experimental = (
        requirements.experimental_features - profile.experimental_features
    )
    if missing_experimental:
        gaps.append(
            CapabilityGap(
                GapCode.EXPERIMENTAL_FEATURES,
                tuple(sorted(missing_experimental)),
                tuple(sorted(profile.experimental_features)),
            )
        )

    if profile.maturity not in requirements.allowed_maturity:
        gaps.append(
            CapabilityGap(
                GapCode.MATURITY,
                _enum_values(requirements.allowed_maturity),
                (profile.maturity.value,),
            )
        )

    return CompatibilityReport(
        route_id=route_id,
        requirements_revision=requirements.revision,
        gaps=tuple(gaps),
    )


def _enum_values(values: Iterable[Enum]) -> tuple[str, ...]:
    return tuple(sorted(str(value.value) for value in values))
