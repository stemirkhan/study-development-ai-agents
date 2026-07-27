from __future__ import annotations

from collections.abc import Mapping
from itertools import product

import pytest

from lessons.module_01_model_vs_agent.implementation.agent_loop import (
    AgentLoop,
)
from lessons.module_01_model_vs_agent.implementation.contracts import (
    AgentStepLimitExceeded,
    DuplicateToolCall,
    InvalidToolArguments,
    ModelProtocolError,
    ToolBinding,
    ToolCall,
    ToolExecutionError,
    ToolResultMessage,
    ToolSpec,
    Trace,
    UnknownTool,
    UserMessage,
)
from lessons.module_01_model_vs_agent.implementation.demo import (
    ScriptedTransport,
    validate_weather_arguments,
)
from lessons.module_01_model_vs_agent.implementation.model_client import (
    ModelClient,
)


def _recording_weather_tool(
    calls: list[Mapping[str, object]],
) -> ToolBinding:
    async def invoke(arguments: Mapping[str, object]) -> str:
        calls.append(dict(arguments))
        return "Казань: дождь, +12 °C"

    return ToolBinding(
        spec=ToolSpec(
            name="get_weather",
            description="Weather by city",
            required_arguments=("city",),
        ),
        validate=validate_weather_arguments,
        invoke=invoke,
    )


def _agent(
    responses: list[Mapping[str, object]],
    tool: ToolBinding,
    *,
    max_steps: int = 3,
) -> tuple[AgentLoop, ScriptedTransport]:
    transport = ScriptedTransport(responses)
    return (
        AgentLoop(
            ModelClient(transport),
            tools={tool.spec.name: tool},
            max_steps=max_steps,
        ),
        transport,
    )


@pytest.mark.asyncio
async def test_direct_final_answer_skips_tool() -> None:
    calls: list[Mapping[str, object]] = []
    agent, transport = _agent(
        [{"type": "final", "text": "Ответ без tool."}],
        _recording_weather_tool(calls),
    )

    result = await agent.run("Привет", trace=Trace())

    assert result.answer == "Ответ без tool."
    assert result.model_calls == 1
    assert result.tool_calls == 0
    assert calls == []
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_tool_result_is_in_second_model_context() -> None:
    calls: list[Mapping[str, object]] = []
    agent, transport = _agent(
        [
            {
                "type": "tool_call",
                "id": "weather-1",
                "name": "get_weather",
                "arguments": {"city": "Казань"},
            },
            {"type": "final", "text": "Берите зонт."},
        ],
        _recording_weather_tool(calls),
    )

    result = await agent.run("Нужен ли зонт?", trace=Trace())

    assert result.model_calls == 2
    assert result.tool_calls == 1
    assert calls == [{"city": "Казань"}]
    assert len(transport.requests[0].messages) == 1
    assert transport.requests[0].messages == (
        UserMessage("Нужен ли зонт?"),
    )
    second_context = transport.requests[1].messages
    assert second_context == (
        UserMessage("Нужен ли зонт?"),
        ToolCall(
            call_id="weather-1",
            name="get_weather",
            arguments={"city": "Казань"},
        ),
        ToolResultMessage(
            call_id="weather-1",
            name="get_weather",
            content="Казань: дождь, +12 °C",
        ),
    )
    assert result.context == second_context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "expected_error"),
    [
        ("delete_account", UnknownTool),
        ("getWeather", UnknownTool),
        (" get_weather", UnknownTool),
        ("", ModelProtocolError),
    ],
)
async def test_untrusted_tool_name_never_reaches_handler(
    tool_name: str,
    expected_error: type[Exception],
) -> None:
    calls: list[Mapping[str, object]] = []
    agent, _ = _agent(
        [
            {
                "type": "tool_call",
                "id": "call-1",
                "name": tool_name,
                "arguments": {"city": "Казань"},
            }
        ],
        _recording_weather_tool(calls),
    )

    with pytest.raises(expected_error) as captured:
        await agent.run("request", trace=Trace())

    assert calls == []
    if isinstance(captured.value, UnknownTool):
        assert captured.value.tool_name == tool_name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"city": None},
        {"city": 42},
        {"city": []},
        {"city": ""},
        {"city": "Казань", "admin": True},
        {"city": "Казань", "tenant_id": "other"},
        {"location": "Казань"},
    ],
)
async def test_invalid_arguments_have_zero_side_effects(
    arguments: Mapping[str, object],
) -> None:
    calls: list[Mapping[str, object]] = []
    agent, _ = _agent(
        [
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "get_weather",
                "arguments": arguments,
            }
        ],
        _recording_weather_tool(calls),
    )

    with pytest.raises(InvalidToolArguments):
        await agent.run("request", trace=Trace())

    assert calls == []


@pytest.mark.asyncio
async def test_tool_failure_is_not_retried() -> None:
    calls = 0
    failure = RuntimeError("weather backend failed")

    async def fail(arguments: Mapping[str, object]) -> str:
        nonlocal calls
        del arguments
        calls += 1
        raise failure

    tool = ToolBinding(
        spec=ToolSpec("get_weather", "Weather", ("city",)),
        validate=validate_weather_arguments,
        invoke=fail,
    )
    agent, _ = _agent(
        [
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "get_weather",
                "arguments": {"city": "Казань"},
            }
        ],
        tool,
    )

    with pytest.raises(ToolExecutionError) as captured:
        await agent.run("request", trace=Trace())

    assert calls == 1
    assert captured.value.cause is failure
    assert captured.value.__cause__ is failure


@pytest.mark.asyncio
async def test_tool_call_on_last_step_is_not_executed() -> None:
    calls: list[Mapping[str, object]] = []
    agent, transport = _agent(
        [
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "get_weather",
                "arguments": {"city": "Казань"},
            }
        ],
        _recording_weather_tool(calls),
        max_steps=1,
    )

    with pytest.raises(AgentStepLimitExceeded):
        await agent.run("request", trace=Trace())

    assert len(transport.requests) == 1
    assert calls == []


@pytest.mark.asyncio
async def test_duplicate_call_id_is_not_executed_twice() -> None:
    calls: list[Mapping[str, object]] = []
    agent, transport = _agent(
        [
            {
                "type": "tool_call",
                "id": "same-call",
                "name": "get_weather",
                "arguments": {"city": "Казань"},
            },
            {
                "type": "tool_call",
                "id": "same-call",
                "name": "get_weather",
                "arguments": {"city": "Казань"},
            },
        ],
        _recording_weather_tool(calls),
        max_steps=3,
    )

    with pytest.raises(DuplicateToolCall):
        await agent.run("request", trace=Trace())

    assert len(transport.requests) == 2
    assert calls == [{"city": "Казань"}]


@pytest.mark.asyncio
async def test_property_model_and_tool_calls_never_exceed_budget() -> None:
    for max_steps in range(1, 5):
        for symbols in product(("final", "tool"), repeat=max_steps + 1):
            responses: list[Mapping[str, object]] = []
            for index, symbol in enumerate(symbols):
                if symbol == "final":
                    responses.append(
                        {"type": "final", "text": f"final-{index}"}
                    )
                else:
                    responses.append(
                        {
                            "type": "tool_call",
                            "id": f"call-{index}",
                            "name": "get_weather",
                            "arguments": {"city": "Казань"},
                        }
                    )

            calls: list[Mapping[str, object]] = []
            agent, transport = _agent(
                responses,
                _recording_weather_tool(calls),
                max_steps=max_steps,
            )
            first_final = next(
                (
                    index
                    for index, symbol in enumerate(symbols)
                    if symbol == "final"
                ),
                None,
            )

            if first_final is not None and first_final < max_steps:
                result = await agent.run("request", trace=Trace())
                assert result.model_calls == first_final + 1
                assert result.tool_calls == first_final
                assert len(transport.requests) == first_final + 1
                assert len(calls) == first_final
            else:
                with pytest.raises(AgentStepLimitExceeded):
                    await agent.run("request", trace=Trace())
                assert len(transport.requests) == max_steps
                assert len(calls) == max_steps - 1

            assert len(transport.requests) <= max_steps
            assert len(calls) <= max(0, max_steps - 1)
