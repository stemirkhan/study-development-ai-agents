from __future__ import annotations

import asyncio

import pytest

from lessons.module_01_provider_contract.implementation.contracts import (
    Deadline,
    ModelClient,
    ModelAttempt,
    ModelProtocolError,
    ModelRequest,
    ModelStreamInterrupted,
    OutputCompleted,
    ResponseCompleted,
    ResponseOutcome,
    ResponseStarted,
    StructuredOutput,
    TextOutput,
    UnexpectedModelCall,
)
from lessons.module_01_provider_contract.implementation.scripted_client import (
    CompletionScript,
    ScriptedFailure,
    ScriptedModelClient,
    StreamScript,
)
from lessons.module_01_provider_contract.implementation.stream_assembly import (
    collect_stream,
)

from .contract_suite import (
    ModelClientBehaviorContract,
    make_request,
    raw,
    response,
    validate_fixture_schema,
)


class TestScriptedModelClientContract(ModelClientBehaviorContract):
    def make_completion_client(
        self,
        *scripts: CompletionScript,
    ) -> ModelClient:
        return ScriptedModelClient(
            completions=scripts,
            structured_validator=validate_fixture_schema,
        )

    def make_stream_client(self, *scripts: StreamScript) -> ModelClient:
        return ScriptedModelClient(streams=scripts)


async def test_each_call_consumes_one_script_and_records_request_order() -> None:
    first_request = make_request("request-1")
    second_request = make_request("request-2")
    first = response(
        first_request.request_id,
        ResponseOutcome.COMPLETED,
        TextOutput("first"),
    )
    second = response(
        second_request.request_id,
        ResponseOutcome.COMPLETED,
        TextOutput("second"),
    )
    client = ScriptedModelClient(completions=(first, second))

    assert await client.complete(first_request) == first
    assert await client.complete(second_request) == second
    assert client.completion_requests == (first_request, second_request)
    with pytest.raises(UnexpectedModelCall):
        await client.complete(make_request("request-3"))
    assert client.completion_requests[-1].request_id == "request-3"


async def test_runtime_attempt_is_recorded_without_breaking_request_log() -> None:
    request = make_request("request-1")
    expected = response(
        request.request_id,
        ResponseOutcome.COMPLETED,
        TextOutput("done"),
    )
    attempt = ModelAttempt(number=2, deadline=Deadline(10))
    client = ScriptedModelClient(
        completions=(expected,),
        streams=(StreamScript(()),),
    )

    assert await client.complete(request, attempt=attempt) is expected
    client.stream(request, attempt=attempt)

    assert client.completion_requests == (request,)
    assert client.stream_requests == (request,)
    assert client.completion_calls[0].attempt is attempt
    assert client.stream_calls[0].attempt is attempt


async def test_request_id_mismatch_is_a_protocol_error() -> None:
    client = ScriptedModelClient(
        completions=(
            response(
                "other-tenant-request",
                ResponseOutcome.COMPLETED,
                TextOutput("wrong tenant"),
            ),
        )
    )

    with pytest.raises(ModelProtocolError, match="request_id") as captured:
        await client.complete(make_request("request-1"))

    assert captured.value.application_request_id == "request-1"


@pytest.mark.parametrize(
    "model_request",
    [make_request("request-1"), make_request("request-1", structured=True)],
)
async def test_structured_output_requires_the_requested_schema_id(
    model_request: ModelRequest,
) -> None:
    scripted = response(
        model_request.request_id,
        ResponseOutcome.COMPLETED,
        StructuredOutput(
            schema_id="other-schema",
            raw_json='{"ok":true}',
            value={"ok": True},
        ),
    )
    client = ScriptedModelClient(completions=(scripted,))

    with pytest.raises(ModelProtocolError):
        await client.complete(model_request)


async def test_same_schema_id_still_requires_value_validation() -> None:
    model_request = make_request("request-1", structured=True)
    scripted = response(
        model_request.request_id,
        ResponseOutcome.COMPLETED,
        StructuredOutput(
            schema_id="umbrella-v1",
            raw_json='{"umbrella":"yes"}',
            value={"umbrella": "yes"},
        ),
    )
    client = ScriptedModelClient(
        completions=(scripted,),
        structured_validator=validate_fixture_schema,
    )

    with pytest.raises(ModelProtocolError, match="violates") as captured:
        await client.complete(model_request)

    assert isinstance(captured.value.cause, ValueError)


async def test_structured_output_is_rejected_without_a_validator() -> None:
    model_request = make_request("request-1", structured=True)
    scripted = response(
        model_request.request_id,
        ResponseOutcome.COMPLETED,
        StructuredOutput(
            schema_id="umbrella-v1",
            raw_json='{"umbrella":true}',
            value={"umbrella": True},
        ),
    )

    with pytest.raises(ModelProtocolError, match="no configured"):
        await ScriptedModelClient(completions=(scripted,)).complete(model_request)


async def test_completed_structured_request_cannot_fall_back_to_text() -> None:
    model_request = make_request("request-1", structured=True)
    json_like_text = response(
        model_request.request_id,
        ResponseOutcome.COMPLETED,
        TextOutput('{"umbrella":true}'),
    )
    client = ScriptedModelClient(
        completions=(json_like_text,),
        structured_validator=validate_fixture_schema,
    )

    with pytest.raises(ModelProtocolError, match="no StructuredOutput"):
        await client.complete(model_request)


async def test_structured_stream_rejects_text_only_terminal_response() -> None:
    model_request = make_request("request-1", structured=True)
    json_like_text = response(
        model_request.request_id,
        ResponseOutcome.COMPLETED,
        TextOutput('{"umbrella":true}'),
    )
    prefix = (
        ResponseStarted(
            1,
            raw("start", "{}"),
            model_request.request_id,
            json_like_text.response_id,
            json_like_text.model,
        ),
        OutputCompleted(
            2,
            raw("text.done", "{}"),
            0,
            json_like_text.outputs[0],
        ),
    )
    events = (
        *prefix,
        ResponseCompleted(3, raw("complete", "{}"), json_like_text),
    )
    client = ScriptedModelClient(
        streams=(StreamScript(events),),
        structured_validator=validate_fixture_schema,
    )

    with pytest.raises(ModelStreamInterrupted) as captured:
        await collect_stream(client.stream(model_request))

    assert captured.value.events == prefix
    assert isinstance(captured.value.cause, ModelProtocolError)
    assert "no StructuredOutput" in str(captured.value.cause)


async def test_scripted_error_is_raised_without_hidden_retry() -> None:
    error = ModelProtocolError("scripted failure")
    success = response(
        "request-2",
        ResponseOutcome.COMPLETED,
        TextOutput("second call"),
    )
    client = ScriptedModelClient(
        completions=(ScriptedFailure(error), success)
    )

    with pytest.raises(ModelProtocolError) as captured:
        await client.complete(make_request("request-1"))

    assert captured.value is error
    assert await client.complete(make_request("request-2")) == success
    assert len(client.completion_requests) == 2


async def test_stream_scripts_are_reserved_when_stream_is_created() -> None:
    first = StreamScript(events=())
    cancellation = asyncio.CancelledError("second")
    second = StreamScript(events=(), failure=cancellation)
    client = ScriptedModelClient(streams=(first, second))

    first_iterator = client.stream(make_request("request-1"))
    second_iterator = client.stream(make_request("request-2"))

    assert [request.request_id for request in client.stream_requests] == [
        "request-1",
        "request-2",
    ]
    assert [event async for event in first_iterator] == []
    with pytest.raises(asyncio.CancelledError) as captured:
        await anext(second_iterator)
    assert captured.value is cancellation
    with pytest.raises(UnexpectedModelCall):
        client.stream(make_request("request-3"))


async def test_stream_rejects_another_requests_events_before_leaking_them() -> None:
    wrong_request = make_request("other-tenant")
    wrong_response = response(
        wrong_request.request_id,
        ResponseOutcome.COMPLETED,
        TextOutput("private"),
    )
    events = (
        ResponseStarted(
            1,
            raw("start", '{"tenant":"other"}'),
            wrong_request.request_id,
            wrong_response.response_id,
            wrong_response.model,
        ),
        ResponseCompleted(2, raw("complete", "{}"), wrong_response),
    )
    client = ScriptedModelClient(streams=(StreamScript(events),))
    iterator = client.stream(make_request("request-1"))

    with pytest.raises(ModelProtocolError, match="request_id"):
        await anext(iterator)


def test_partial_model_failure_must_explicitly_preserve_its_prefix() -> None:
    event = ResponseStarted(
        1,
        raw("start", "{}"),
        "request-1",
        "response-1",
        None,
    )

    with pytest.raises(ValueError, match="ModelStreamInterrupted"):
        StreamScript((event,), ModelProtocolError("bad next event"))

    interruption = ModelStreamInterrupted(
        (event,),
        ModelProtocolError("bad next event"),
    )
    assert StreamScript((event,), interruption).failure is interruption
