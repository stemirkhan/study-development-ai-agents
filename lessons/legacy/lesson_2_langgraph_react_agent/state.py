"""Строго типизированное состояние архивного LangGraph-агента."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from lessons.legacy.lesson_1_basic_react_agent.protocol import AgentAction


class ChatMessage(TypedDict):
    """Сообщение в формате, который принимает OpenAI Responses API."""

    role: Literal["user", "assistant"]
    content: str


class AgentState(TypedDict):
    """Общее состояние, доступное всем узлам графа.

    operator.add — reducer поля messages. Когда узел возвращает
    {"messages": [new_message]}, LangGraph складывает два списка:

        old_messages + [new_message]

    Без reducer новое значение полностью заменило бы историю.
    """

    messages: Annotated[list[ChatMessage], operator.add]
    step_count: int
    current_action: AgentAction | None
    max_steps: int
    final_answer: str | None


class AgentStateUpdate(TypedDict, total=False):
    """Частичное обновление State, которое может вернуть Node."""

    messages: list[ChatMessage]
    step_count: int
    current_action: AgentAction | None
    max_steps: int
    final_answer: str | None


def message(role: Literal["user", "assistant"], content: str) -> ChatMessage:
    """Создаёт типизированное сообщение."""
    return {"role": role, "content": content}
