from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from lessons.module_01_model_vs_agent.implementation.contracts import (
    FinalAnswer,
    ModelProtocolError,
    ModelRequest,
    ModelTimeout,
    ToolCall,
    ToolSpec,
    Trace,
    UnexpectedModelCall,
    UserMessage,
)
from lessons.module_01_model_vs_agent.implementation.demo import (
    ScriptedTransport,
)
from lessons.module_01_model_vs_agent.implementation.model_client import (
    ModelClient,
)


def _request() -> ModelRequest:
    return ModelRequest(
        messages=(UserMessage("Нужен ли зонт?"),),
        tools=(
            ToolSpec(
                name="get_weather",
                description="Weather by city",
                required_arguments=("city",),
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"type": "final", "text": "Возьмите зонт."}, FinalAnswer),
        (
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "get_weather",
                "arguments": {"city": "Казань"},
            },
            ToolCall,
        ),
    ],
)
async def test_contract_preserves_response_variant(
    payload: Mapping[str, object],
    expected_type: type[FinalAnswer] | type[ToolCall],
) -> None:
    request = _request()
    transport = ScriptedTransport([payload])
    output = await ModelClient(transport).complete(request, trace=Trace())

    assert isinstance(output, expected_type)
    assert transport.requests == [request]
    if isinstance(output, ToolCall):
        assert output.call_id == "call-1"
        assert output.name == "get_weather"
        assert output.arguments == {"city": "Казань"}


@pytest.mark.asyncio
async def test_each_call_consumes_exactly_one_scripted_response() -> None:
    transport = ScriptedTransport(
        [
            {"type": "final", "text": "first"},
            {"type": "final", "text": "second"},
        ]
    )
    client = ModelClient(transport)
    request = _request()

    first = await client.complete(request, trace=Trace())
    second = await client.complete(request, trace=Trace())

    assert first == FinalAnswer("first")
    assert second == FinalAnswer("second")
    assert transport.requests == [request, request]
    with pytest.raises(UnexpectedModelCall):
        await client.complete(request, trace=Trace())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "unknown"},
        {"type": "final", "text": 42},
        {
            "type": "tool_call",
            "id": "",
            "name": "get_weather",
            "arguments": {"city": "Казань"},
        },
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "get_weather",
            "arguments": ["Казань"],
        },
    ],
)
async def test_malformed_provider_payload_is_a_protocol_error(
    payload: Mapping[str, object],
) -> None:
    client = ModelClient(ScriptedTransport([payload]))

    with pytest.raises(ModelProtocolError):
        await client.complete(_request(), trace=Trace())


@pytest.mark.asyncio
async def test_transport_timeout_is_normalized() -> None:
    class TimeoutTransport:
        async def send(
            self,
            request: ModelRequest,
        ) -> Mapping[str, object]:
            del request
            raise TimeoutError("provider timeout")

    with pytest.raises(ModelTimeout) as captured:
        await ModelClient(TimeoutTransport()).complete(
            _request(),
            trace=Trace(),
        )

    assert isinstance(captured.value.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_configured_timeout_cancels_blocked_transport() -> None:
    class BlockingTransport:
        def __init__(self) -> None:
            self.cancelled = False

        async def send(
            self,
            request: ModelRequest,
        ) -> Mapping[str, object]:
            del request
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True
            raise AssertionError("unreachable")

    transport = BlockingTransport()
    with pytest.raises(ModelTimeout):
        await ModelClient(transport, timeout_s=0.01).complete(
            _request(),
            trace=Trace(),
        )

    assert transport.cancelled is True
