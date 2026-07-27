"""Run the deterministic weather request through all four components."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import cast

from .agent_loop import AgentLoop
from .contracts import (
    InvalidToolArguments,
    ModelRequest,
    ToolBinding,
    ToolSpec,
    Trace,
    UnexpectedModelCall,
    UserRequest,
)
from .distributed_executor import DistributedExecutor
from .model_client import ModelClient
from .workflow import Workflow


class ScriptedTransport:
    """A deterministic fake provider with provider-shaped responses."""

    def __init__(
        self,
        responses: Iterable[Mapping[str, object]],
    ) -> None:
        self._responses = deque(dict(response) for response in responses)
        self.requests: list[ModelRequest] = []

    async def send(self, request: ModelRequest) -> Mapping[str, object]:
        self.requests.append(request)
        if not self._responses:
            raise UnexpectedModelCall("scripted provider has no next response")
        return self._responses.popleft()


def validate_weather_arguments(
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    if set(arguments) != {"city"}:
        raise InvalidToolArguments(
            "get_weather",
            "expected exactly one field: city",
        )
    city = arguments["city"]
    if not isinstance(city, str) or not city.strip():
        raise InvalidToolArguments(
            "get_weather",
            "city must be a non-empty string",
        )
    return MappingProxyType({"city": city.strip()})


async def get_weather(arguments: Mapping[str, object]) -> str:
    city = cast(str, arguments["city"])
    observations = {
        "Казань": "дождь, +12 °C",
        "Москва": "облачно, +15 °C",
    }
    observation = observations.get(city, "данные отсутствуют")
    return f"{city}: {observation}"


def build_weather_tool() -> ToolBinding:
    return ToolBinding(
        spec=ToolSpec(
            name="get_weather",
            description="Return deterministic weather for one city",
            required_arguments=("city",),
        ),
        validate=validate_weather_arguments,
        invoke=get_weather,
    )


def build_demo() -> tuple[Workflow, ScriptedTransport]:
    transport = ScriptedTransport(
        responses=(
            {
                "type": "tool_call",
                "id": "weather-call-1",
                "name": "get_weather",
                "arguments": {"city": "Казань"},
            },
            {
                "type": "final",
                "text": "В Казани ожидается дождь; возьмите зонт.",
            },
        )
    )
    model_client = ModelClient(transport)
    weather = build_weather_tool()
    agent_loop = AgentLoop(
        model_client,
        tools={weather.spec.name: weather},
        max_steps=3,
    )
    executor = DistributedExecutor()
    return Workflow(agent_loop, executor), transport


async def run_demo() -> None:
    trace = Trace()
    workflow, _ = build_demo()
    response = await workflow.run(
        UserRequest(
            request_id="weather-001",
            text="Нужен ли сегодня зонт в Казани?",
        ),
        trace=trace,
    )

    for event in trace.events:
        detail = f" {event.detail}" if event.detail else ""
        print(
            f"{event.sequence:02d} "
            f"{event.component}.{event.action}{detail}"
        )
    print(f"RESULT {response.text}")
    print(
        f"METRICS model_calls={response.model_calls} "
        f"tool_calls={response.tool_calls}"
    )


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
