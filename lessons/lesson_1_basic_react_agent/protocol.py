"""JSON-протокол между LLM и оркестратором агента."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class AgentProtocolError(ValueError):
    """Ответ модели не соответствует протоколу агента."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class FinalAnswer:
    answer: str


AgentAction = ToolCall | FinalAnswer


def parse_llm_response(raw_response: str) -> AgentAction:
    """Парсит JSON модели и извлекает имя инструмента с аргументами или ответ."""
    text = raw_response.strip()

    # Некоторые модели, несмотря на инструкцию, оборачивают JSON в Markdown.
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1])
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:].lstrip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise AgentProtocolError(f"невалидный JSON: {error.msg}") from error

    if not isinstance(payload, dict):
        raise AgentProtocolError("верхний уровень JSON должен быть объектом")

    action = payload.get("action")
    if action == "tool":
        tool_name = payload.get("tool_name")
        arguments = payload.get("arguments")
        if not isinstance(tool_name, str) or not tool_name:
            raise AgentProtocolError("tool_name должен быть непустой строкой")
        if not isinstance(arguments, dict):
            raise AgentProtocolError("arguments должен быть JSON-объектом")
        return ToolCall(name=tool_name, arguments=arguments)

    if action == "final":
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise AgentProtocolError("answer должен быть непустой строкой")
        return FinalAnswer(answer=answer)

    raise AgentProtocolError("action должен иметь значение 'tool' или 'final'")
