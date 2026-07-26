"""Архивный ReAct-цикл, выраженный как граф состояний LangGraph."""

from __future__ import annotations

import json
from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from openai import OpenAI

from lessons.legacy.lesson_1_basic_react_agent.prompts import SYSTEM_PROMPT
from lessons.legacy.lesson_1_basic_react_agent.protocol import (
    AgentProtocolError,
    FinalAnswer,
    ToolCall,
    parse_llm_response,
)
from lessons.legacy.lesson_1_basic_react_agent.tools import execute_tool

from .state import AgentState, AgentStateUpdate, message


Route = Literal["tool", "retry", "final", "step_limit"]


def route_after_llm(state: AgentState) -> Route:
    """Условное ребро: выбирает следующий Node по текущему действию."""
    action = state["current_action"]

    if isinstance(action, FinalAnswer):
        return "final"

    # После последнего разрешённого вызова LLM новый цикл не начинаем.
    if state["step_count"] >= state["max_steps"]:
        return "step_limit"

    if isinstance(action, ToolCall):
        return "tool"

    # current_action == None означает ошибку JSON-протокола: просим LLM
    # исправить ответ на следующем шаге.
    return "retry"


def final_node(state: AgentState) -> AgentStateUpdate:
    """Извлекает финальный ответ из текущего действия."""
    action = state["current_action"]
    if not isinstance(action, FinalAnswer):
        raise TypeError("final_node ожидает FinalAnswer")
    return {"final_answer": action.answer}


def step_limit_node(state: AgentState) -> AgentStateUpdate:
    """Завершает граф, если исчерпан лимит вызовов LLM."""
    answer = (
        f"Агент остановлен после {state['max_steps']} шагов: "
        "модель не сформировала финальный ответ."
    )
    return {
        "messages": [message("assistant", answer)],
        "current_action": FinalAnswer(answer=answer),
        "final_answer": answer,
    }


def build_react_graph(
    *,
    client: OpenAI,
    model: str,
    verbose: bool = True,
) -> Any:
    """Создаёт и компилирует граф ReAct-агента.

    client, model и verbose захватываются замыканиями узлов. Они являются
    зависимостями выполнения, а не частью сериализуемого состояния агента.
    """

    def llm_node(state: AgentState) -> AgentStateUpdate:
        """Node Reasoning: вызывает LLM и разбирает предложенное действие."""
        next_step = state["step_count"] + 1
        response = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=state["messages"],
        )
        raw_response = response.output_text
        new_messages = [message("assistant", raw_response)]

        if verbose:
            print(
                f"\n[Шаг {next_step}/{state['max_steps']}] "
                f"Node llm:\n{raw_response}"
            )

        try:
            action = parse_llm_response(raw_response)
        except AgentProtocolError as error:
            observation = (
                f"Ошибка протокола: {error}. Верни корректный JSON по заданной схеме."
            )
            new_messages.append(message("user", f"Observation: {observation}"))
            action = None

            if verbose:
                print(f"Observation: {observation}")

        # Возвращаем только новые сообщения. operator.add в AgentState
        # самостоятельно присоединит их к существующей истории.
        return {
            "messages": new_messages,
            "step_count": next_step,
            "current_action": action,
        }

    def tool_node(state: AgentState) -> AgentStateUpdate:
        """Node Acting: вызывает выбранный Python-инструмент."""
        action = state["current_action"]
        if not isinstance(action, ToolCall):
            raise TypeError("tool_node ожидает ToolCall")

        observation = execute_tool(action)
        observation_message = json.dumps(
            {
                "tool_name": action.name,
                "result": observation,
            },
            ensure_ascii=False,
        )

        if verbose:
            print(f"Node tool → Observation: {observation_message}")

        return {
            "messages": [
                message("user", f"Observation: {observation_message}")
            ],
            "current_action": None,
        }

    builder = StateGraph(AgentState)

    # Nodes — функции, которые читают State и возвращают частичное обновление.
    builder.add_node("llm", llm_node)
    builder.add_node("tool", tool_node)
    builder.add_node("final", final_node)
    builder.add_node("step_limit", step_limit_node)

    # Edges — маршруты выполнения между узлами.
    builder.add_edge(START, "llm")
    builder.add_conditional_edges(
        "llm",
        route_after_llm,
        {
            "tool": "tool",
            "retry": "llm",
            "final": "final",
            "step_limit": "step_limit",
        },
    )
    builder.add_edge("tool", "llm")
    builder.add_edge("final", END)
    builder.add_edge("step_limit", END)

    return builder.compile()


def run_langgraph_agent(
    question: str,
    *,
    client: OpenAI,
    model: str,
    max_steps: int = 6,
    verbose: bool = True,
) -> str:
    """Запускает скомпилированный StateGraph и возвращает финальный ответ."""
    if not question.strip():
        raise ValueError("question должен быть непустой строкой")
    if max_steps < 1:
        raise ValueError("max_steps должен быть положительным числом")

    graph = build_react_graph(client=client, model=model, verbose=verbose)
    initial_state: AgentState = {
        "messages": [message("user", question)],
        "step_count": 0,
        "current_action": None,
        "max_steps": max_steps,
        "final_answer": None,
    }

    result = cast(
        AgentState,
        graph.invoke(
            initial_state,
            # Дополнительная страховка LangGraph для циклических графов.
            {"recursion_limit": max_steps * 3 + 5},
        ),
    )

    final_answer = result["final_answer"]
    if final_answer is None:
        raise RuntimeError("граф завершился без final_answer")
    return final_answer
