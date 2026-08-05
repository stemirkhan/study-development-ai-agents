from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

import pytest

from lessons.module_01_provider_contract.implementation.contracts import (
    Deadline,
    InputMessage,
    JsonValue,
    JsonSchemaFormat,
    MessageRole,
    ModelRateLimited,
    ModelAttempt,
    ModelRequest,
    ModelResponse,
    ProviderPayload,
    RefusalOutput,
    ResponseOutcome,
    StructuredOutput,
    TextOutput,
    ToolCallOutput,
    ToolSpec,
    UnknownOutput,
    Usage,
    freeze_json,
)

from .contract_suite import MODEL, PROVIDER, raw


def test_request_and_json_values_are_deeply_immutable() -> None:
    nested_schema: dict[str, JsonValue] = {
        "properties": {"tags": {"enum": ("safe", "fast")}}
    }
    tool = ToolSpec("search", "Search", nested_schema)
    request = ModelRequest(
        request_id="request-1",
        model=MODEL,
        messages=(InputMessage(MessageRole.USER, "find"),),
        tools=(tool,),
        response_format=JsonSchemaFormat("result-v1", nested_schema),
    )

    nested_schema["properties"] = {"injected": True}

    tool_properties = request.tools[0].input_schema["properties"]
    assert isinstance(tool_properties, MappingProxyType)
    assert "tags" in tool_properties
    format_schema = request.response_format
    assert isinstance(format_schema, JsonSchemaFormat)
    format_properties = format_schema.schema["properties"]
    assert isinstance(format_properties, Mapping)
    assert "tags" in format_properties
    with pytest.raises(TypeError):
        tool_properties["new"] = True  # type: ignore[index]


def test_deadline_and_attempt_are_validated_runtime_controls() -> None:
    deadline = Deadline(10)
    attempt = ModelAttempt(number=2, deadline=deadline)

    assert deadline.at == 10.0
    assert attempt.deadline is deadline
    assert attempt.number == 2

    for invalid in (True, float("nan"), float("inf"), "10"):
        with pytest.raises(ValueError):
            Deadline(invalid)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive integer"):
        ModelAttempt(number=0, deadline=deadline)
    with pytest.raises(TypeError, match="Deadline"):
        ModelAttempt(number=1, deadline=10)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "bad"}])
def test_non_json_values_are_rejected(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        freeze_json(value)


def test_structured_output_requires_raw_and_value_to_agree() -> None:
    with pytest.raises(ValueError, match="disagree"):
        StructuredOutput(
            schema_id="result-v1",
            raw_json='{"ok":false}',
            value={"ok": True},
        )
    with pytest.raises(ValueError, match="disagree"):
        StructuredOutput(
            schema_id="result-v1",
            raw_json='{"value":1}',
            value={"value": True},
        )


def test_tool_call_requires_complete_json_object_that_matches_arguments() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        ToolCallOutput(
            call_id=None,
            name="search",
            arguments={"query": "x"},
            raw_arguments='{"query":',
        )
    with pytest.raises(ValueError, match="JSON object"):
        ToolCallOutput(
            call_id=None,
            name="search",
            arguments={"query": "x"},
            raw_arguments='["x"]',
        )


def test_native_provider_tool_arguments_do_not_require_invented_raw_json() -> None:
    output = ToolCallOutput(
        call_id=None,
        name="search",
        arguments={"query": "x"},
        raw_arguments=None,
        provider_payload=raw("native.function", b'{"args":{"query":"x"}}'),
    )

    assert output.arguments == {"query": "x"}
    assert output.raw_arguments is None
    assert output.provider_payload is not None


@pytest.mark.parametrize(
    ("outcome", "outputs", "message"),
    [
        (ResponseOutcome.TOOL_REQUESTED, (TextOutput("x"),), "tool call"),
        (ResponseOutcome.REFUSED, (TextOutput("x"),), "refusal"),
        (
            ResponseOutcome.COMPLETED,
            (
                RefusalOutput("policy"),
            ),
            "requires refused",
        ),
    ],
)
def test_response_outcome_must_match_output_variants(
    outcome: ResponseOutcome,
    outputs: tuple[TextOutput | RefusalOutput, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ModelResponse(
            request_id="request-1",
            provider=PROVIDER,
            model=MODEL,
            response_id="response-1",
            outcome=outcome,
            outputs=outputs,
            provider_payload=raw("response", "{}"),
        )


def test_missing_provider_identifiers_and_usage_are_not_invented() -> None:
    tool_call = ToolCallOutput(
        call_id=None,
        name="search",
        arguments={"query": "x"},
        raw_arguments='{"query":"x"}',
    )
    response = ModelResponse(
        request_id="request-1",
        provider=PROVIDER,
        model=None,
        response_id=None,
        outcome=ResponseOutcome.TOOL_REQUESTED,
        outputs=(tool_call,),
        usage=None,
        provider_payload=raw("response", "{}"),
    )

    assert response.model is None
    assert response.response_id is None
    assert response.usage is None
    assert tool_call.call_id is None


def test_provider_bytes_are_exact_and_hidden_from_repr() -> None:
    secret = b'{ "duplicate":1, "duplicate":2, "secret":"token" }\n'
    payload = ProviderPayload(PROVIDER, "future.block", secret)
    output = UnknownOutput(payload)

    assert output.provider_payload.data == secret
    assert secret.decode() not in repr(payload)


@pytest.mark.parametrize(
    ("field", "build"),
    [
        ("input_tokens", lambda: Usage(input_tokens=-1)),
        ("output_tokens", lambda: Usage(output_tokens=1.5)),  # type: ignore[arg-type]
        ("total_tokens", lambda: Usage(total_tokens=True)),
    ],
)
def test_usage_rejects_invalid_known_counters(
    field: str,
    build: Callable[[], Usage],
) -> None:
    with pytest.raises(ValueError, match=field):
        build()


def test_semantic_response_equality_ignores_wire_representation() -> None:
    first = ModelResponse(
        request_id="request-1",
        provider=PROVIDER,
        model=MODEL,
        response_id="response-1",
        outcome=ResponseOutcome.COMPLETED,
        outputs=(TextOutput("same", raw("text", b'{"text":"same"}')),),
        provider_payload=raw("response", b'{"output":"same"}'),
    )
    second = ModelResponse(
        request_id="request-1",
        provider=PROVIDER,
        model=MODEL,
        response_id="response-1",
        outcome=ResponseOutcome.COMPLETED,
        outputs=(
            TextOutput("same", raw("delta.done", b'{ "text": "same" }')),
        ),
        provider_payload=raw("stream.assembled", b'{"events":3}'),
    )

    assert first == second
    assert first.provider_payload.data != second.provider_payload.data


def test_provider_error_keeps_both_request_ids_and_all_raw_blocks() -> None:
    cause = ConnectionError("quota service unavailable")
    body = raw("error.body", b'{"code":"rate_limit"}')
    headers = raw("http.headers", b'retry-after: 1.5\nx-request-id: prv-7')
    error = ModelRateLimited(
        "rate limited",
        application_request_id="app-42",
        provider=PROVIDER,
        provider_request_id="prv-7",
        status_code=429,
        retry_after_s=1.5,
        provider_payloads=(headers, body),
        cause=cause,
    )

    assert error.application_request_id == "app-42"
    assert error.provider_request_id == "prv-7"
    assert error.provider == PROVIDER
    assert error.status_code == 429
    assert error.retry_after_s == 1.5
    assert error.provider_payloads == (headers, body)
    assert error.cause is cause
