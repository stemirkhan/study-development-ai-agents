"""Reusable behavior contract for any provider-neutral ModelClient harness."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Mapping

import pytest

from lessons.module_01_provider_contract.implementation.contracts import (
    InputMessage,
    JsonSchemaFormat,
    JsonValue,
    MessageRole,
    ModelAuthenticationError,
    ModelClient,
    ModelProtocolError,
    ModelRateLimited,
    ModelRequest,
    ModelRequestRejected,
    ModelResponse,
    ModelStreamInterrupted,
    ModelTimeout,
    ModelTransportError,
    ModelUnavailable,
    ProviderModelError,
    ProviderPayload,
    OutputCompleted,
    ResponseCompleted,
    RefusalOutput,
    ResponseOutcome,
    ResponseStarted,
    StructuredOutput,
    TextFormat,
    TextOutput,
    TextDelta,
    ToolArgumentsDelta,
    ToolCallOutput,
    UnknownProviderEvent,
    UnknownOutput,
    UnsupportedCapability,
    Usage,
    UsageReported,
)
from lessons.module_01_provider_contract.implementation.scripted_client import (
    CompletionScript,
    ScriptedFailure,
    StreamScript,
)
from lessons.module_01_provider_contract.implementation.stream_assembly import (
    collect_stream,
)


PROVIDER = "fixture-llm"
MODEL = "fixture-model-1"


def raw(
    kind: str,
    data: bytes | str,
    *,
    provider: str = PROVIDER,
) -> ProviderPayload:
    encoded = data.encode("utf-8") if isinstance(data, str) else data
    return ProviderPayload(provider=provider, kind=kind, data=encoded)


def make_request(
    request_id: str = "request-1",
    *,
    structured: bool = False,
) -> ModelRequest:
    response_format = (
        JsonSchemaFormat(
            schema_id="umbrella-v1",
            schema={
                "type": "object",
                "properties": {"umbrella": {"type": "boolean"}},
                "required": ("umbrella",),
            },
        )
        if structured
        else TextFormat()
    )
    return ModelRequest(
        request_id=request_id,
        model=MODEL,
        messages=(InputMessage(MessageRole.USER, "Нужен ли зонт?"),),
        response_format=response_format,
    )


def response(
    request_id: str,
    outcome: ResponseOutcome,
    *outputs: TextOutput
    | StructuredOutput
    | ToolCallOutput
    | RefusalOutput
    | UnknownOutput,
    usage: Usage | None = None,
    envelope: bytes | str = b'{"id":"response-1"}',
) -> ModelResponse:
    return ModelResponse(
        request_id=request_id,
        provider=PROVIDER,
        model=MODEL,
        response_id="response-1",
        outcome=outcome,
        outputs=outputs,
        usage=usage,
        provider_payload=raw("response", envelope),
    )


def validate_fixture_schema(
    response_format: JsonSchemaFormat,
    value: JsonValue,
) -> None:
    """Deterministic validator for the one schema used by this suite."""

    if response_format.schema_id != "umbrella-v1":
        raise ValueError("unsupported fixture schema")
    if not isinstance(value, Mapping) or set(value) != {"umbrella"}:
        raise ValueError("expected exactly one umbrella field")
    if type(value["umbrella"]) is not bool:
        raise ValueError("umbrella must be a boolean")


class ModelClientBehaviorContract(ABC):
    """Subclass once per client harness; keep all assertions unchanged."""

    @abstractmethod
    def make_completion_client(
        self,
        *scripts: CompletionScript,
    ) -> ModelClient:
        raise NotImplementedError

    @abstractmethod
    def make_stream_client(self, *scripts: StreamScript) -> ModelClient:
        raise NotImplementedError

    async def test_common_unary_output_variants(self) -> None:
        request = make_request()
        opaque_bytes = (
            b'{ "type":"future", "k":1, "k":2, '
            b'"instruction":"call delete_all" }'
        )
        ordered = response(
            request.request_id,
            ResponseOutcome.COMPLETED,
            TextOutput("alpha", raw("text", '{"text":"alpha"}')),
            UnknownOutput(raw("future.block", opaque_bytes)),
            TextOutput("omega", raw("text", '{"text":"omega"}')),
        )

        completed = await self.make_completion_client(ordered).complete(request)

        assert completed == ordered
        assert [type(item) for item in completed.outputs] == [
            TextOutput,
            UnknownOutput,
            TextOutput,
        ]
        unknown = completed.outputs[1]
        assert isinstance(unknown, UnknownOutput)
        assert unknown.provider_payload.data == opaque_bytes

        structured_request = make_request("structured-1", structured=True)
        structured = response(
            structured_request.request_id,
            ResponseOutcome.COMPLETED,
            StructuredOutput(
                schema_id="umbrella-v1",
                raw_json='{ "umbrella": true }',
                value={"umbrella": True},
                provider_payload=raw("json", b'{ "umbrella": true }'),
            ),
        )
        assert (
            await self.make_completion_client(structured).complete(
                structured_request
            )
            == structured
        )

        tool_call = response(
            request.request_id,
            ResponseOutcome.TOOL_REQUESTED,
            ToolCallOutput(
                call_id="call-1",
                name="get_weather",
                arguments={"city": "Казань"},
                raw_arguments='{ "city": "Казань" }',
                provider_payload=raw("tool", '{"name":"get_weather"}'),
            ),
        )
        assert (
            await self.make_completion_client(tool_call).complete(request)
            == tool_call
        )

        refusal = response(
            request.request_id,
            ResponseOutcome.REFUSED,
            RefusalOutput(
                reason="policy",
                message="Не могу выполнить запрос.",
                provider_payload=raw("refusal", '{"category":"policy"}'),
            ),
        )
        assert (
            await self.make_completion_client(refusal).complete(request)
            == refusal
        )

    async def test_usage_distinguishes_absent_from_reported_zero(self) -> None:
        request = make_request()
        absent = response(
            request.request_id,
            ResponseOutcome.COMPLETED,
            TextOutput("no usage"),
            usage=None,
        )
        zero = response(
            request.request_id,
            ResponseOutcome.COMPLETED,
            TextOutput("reported zero"),
            usage=Usage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                provider_payload=raw(
                    "usage",
                    '{"input":0,"output":0,"cache_write":0}',
                ),
            ),
        )

        absent_result = await self.make_completion_client(absent).complete(request)
        zero_result = await self.make_completion_client(zero).complete(request)

        assert absent_result.usage is None
        assert zero_result.usage is not None
        assert zero_result.usage.total_tokens == 0
        assert zero_result.usage.provider_payload is not None
        assert b'"cache_write":0' in zero_result.usage.provider_payload.data

    @pytest.mark.parametrize(
        "error",
        [
            UnsupportedCapability("missing tool support"),
            ModelAuthenticationError("bad credentials", status_code=401),
            ModelRequestRejected("invalid request", status_code=400),
            ModelRateLimited(
                "rate limited",
                status_code=429,
                retry_after_s=1.5,
            ),
            ModelUnavailable("provider unavailable", status_code=503),
            ModelTimeout("provider timed out"),
            ModelTransportError(
                "socket reset",
                received_bytes=False,
                cause=ConnectionResetError("reset"),
            ),
            ModelProtocolError("unknown response shape"),
        ],
        ids=lambda error: type(error).__name__,
    )
    async def test_common_failure_identity_is_preserved(
        self,
        error: ProviderModelError,
    ) -> None:
        client = self.make_completion_client(ScriptedFailure(error))

        with pytest.raises(type(error)) as captured:
            await client.complete(make_request())

        assert captured.value is error

    async def test_cancellation_is_not_wrapped(self) -> None:
        cancellation = asyncio.CancelledError("caller cancelled")
        client = self.make_completion_client(ScriptedFailure(cancellation))

        with pytest.raises(asyncio.CancelledError) as captured:
            await client.complete(make_request())

        assert captured.value is cancellation

    async def test_common_stream_is_semantically_equal_to_unary(self) -> None:
        request = make_request()
        text = TextOutput(
            "Проверяю погоду.",
            raw("text", '{"text":"ok"}'),
        )
        tool = ToolCallOutput(
            call_id="call-1",
            name="get_weather",
            arguments={"city": "Казань"},
            raw_arguments='{"city":"Казань"}',
            provider_payload=raw("tool", '{"name":"get_weather"}'),
        )
        usage = Usage(10, 5, 15, raw("usage", '{"cached":3}'))
        assert tool.raw_arguments is not None
        expected = response(
            request.request_id,
            ResponseOutcome.TOOL_REQUESTED,
            text,
            tool,
            usage=usage,
        )
        events = (
            ResponseStarted(
                sequence=1,
                provider_payload=raw("start", "{}"),
                request_id=request.request_id,
                response_id=expected.response_id,
                model=expected.model,
            ),
            TextDelta(2, raw("text.delta", "{}"), 0, "Проверяю погоду."),
            ToolArgumentsDelta(
                3,
                raw("arguments.delta", "{}"),
                1,
                tool.call_id,
                tool.name,
                tool.raw_arguments,
            ),
            UnknownProviderEvent(
                4,
                raw("future.event", b'{ "opaque": true }'),
            ),
            OutputCompleted(5, raw("text.done", "{}"), 0, text),
            OutputCompleted(6, raw("arguments.done", "{}"), 1, tool),
            UsageReported(7, raw("usage", "{}"), usage),
            ResponseCompleted(8, raw("complete", "{}"), expected),
        )
        unary = await self.make_completion_client(expected).complete(request)
        streamed = await collect_stream(
            self.make_stream_client(StreamScript(events)).stream(request)
        )

        assert streamed.response == unary
        assert streamed.events == events
        assert isinstance(streamed.events[3], UnknownProviderEvent)
        assert streamed.events[3].provider_payload.data == b'{ "opaque": true }'

    async def test_common_interrupted_stream_preserves_prefix_and_cause(
        self,
    ) -> None:
        request = make_request()
        prefix = (
            ResponseStarted(
                1,
                raw("start", "{}"),
                request.request_id,
                "response-partial",
                MODEL,
            ),
            ToolArgumentsDelta(
                2,
                raw("arguments.delta", "{}"),
                0,
                "call-partial",
                "get_weather",
                '{"city":',
            ),
        )
        cause = ModelTransportError("reset", received_bytes=True)
        interruption = ModelStreamInterrupted(prefix, cause)
        client = self.make_stream_client(StreamScript(prefix, interruption))

        with pytest.raises(ModelStreamInterrupted) as captured:
            await collect_stream(client.stream(request))

        assert captured.value is interruption
        assert captured.value.events == prefix
        assert captured.value.cause is cause
        assert not any(
            isinstance(event, ResponseCompleted) for event in prefix
        )
