from __future__ import annotations

from lessons.module_01_model_vs_agent.implementation.agent_loop import (
    AgentLoop,
)
from lessons.module_01_model_vs_agent.implementation.contracts import (
    ToolCall,
    ToolResultMessage,
    Trace,
    UserRequest,
)
from lessons.module_01_model_vs_agent.implementation.demo import (
    ScriptedTransport,
    build_demo,
    get_weather,
    validate_weather_arguments,
)
from lessons.module_01_model_vs_agent.implementation.distributed_executor import (
    DistributedExecutor,
)
from lessons.module_01_model_vs_agent.implementation.model_client import (
    ModelClient,
)
from lessons.module_01_model_vs_agent.implementation.workflow import Workflow


async def test_weather_request_end_to_end() -> None:
    trace = Trace()
    workflow, transport = build_demo()

    response = await workflow.run(
        UserRequest(
            request_id="weather-001",
            text="Нужен ли сегодня зонт в Казани?",
        ),
        trace=trace,
    )

    assert response.text == "В Казани ожидается дождь; возьмите зонт."
    assert response.request_id == "weather-001"
    assert response.model_calls == 2
    assert response.tool_calls == 1
    assert len(transport.requests) == 2
    assert isinstance(transport.requests[1].messages[-2], ToolCall)
    assert isinstance(
        transport.requests[1].messages[-1],
        ToolResultMessage,
    )
    assert [event.action for event in trace.events] == [
        "workflow_started",
        "input_validated",
        "agent_step_selected",
        "task_received",
        "attempt_started",
        "agent_started",
        "model_requested",
        "request_started",
        "response_received",
        "tool_call_received",
        "tool_validated",
        "tool_completed",
        "model_requested",
        "request_started",
        "response_received",
        "agent_completed",
        "attempt_completed",
        "workflow_completed",
    ]
    assert [event.sequence for event in trace.events] == list(range(1, 19))


async def test_direct_answer_end_to_end_has_no_tool_path() -> None:
    transport = ScriptedTransport(
        [{"type": "final", "text": "Можно ответить без погоды."}]
    )
    workflow = Workflow(
        AgentLoop(ModelClient(transport), tools={}, max_steps=1),
        DistributedExecutor(),
    )

    response = await workflow.run(
        UserRequest("request-1", "Общий вопрос"),
        trace=Trace(),
    )

    assert response.model_calls == 1
    assert response.tool_calls == 0


async def test_weather_tool_is_deterministic_for_validated_input() -> None:
    arguments = validate_weather_arguments({"city": " Казань "})

    first = await get_weather(arguments)
    second = await get_weather(arguments)
    unknown = await get_weather(
        validate_weather_arguments({"city": "Иннополис"})
    )

    assert first == second == "Казань: дождь, +12 °C"
    assert unknown == "Иннополис: данные отсутствуют"
