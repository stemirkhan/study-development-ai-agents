"""Оркестратор цикла Reasoning -> Action -> Observation."""

from __future__ import annotations

import json

from openai import OpenAI

from .prompts import SYSTEM_PROMPT
from .protocol import AgentProtocolError, FinalAnswer, parse_llm_response
from .tools import execute_tool


def run_react_agent(
    question: str,
    *,
    client: OpenAI,
    model: str,
    max_steps: int = 6,
    verbose: bool = True,
) -> str:
    """Запускает ReAct-цикл с жёстким лимитом количества шагов."""
    if not question.strip():
        raise ValueError("question должен быть непустой строкой")
    if max_steps < 1:
        raise ValueError("max_steps должен быть положительным числом")

    messages: list[dict[str, str]] = [{"role": "user", "content": question}]

    for step in range(1, max_steps + 1):
        response = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=messages,
        )
        raw_response = response.output_text

        if verbose:
            print(f"\n[Шаг {step}/{max_steps}] LLM:\n{raw_response}")

        messages.append({"role": "assistant", "content": raw_response})

        try:
            action = parse_llm_response(raw_response)
        except AgentProtocolError as error:
            observation = (
                f"Ошибка протокола: {error}. Верни корректный JSON по заданной схеме."
            )
            messages.append(
                {"role": "user", "content": f"Observation: {observation}"}
            )
            if verbose:
                print(f"Observation: {observation}")
            continue

        if isinstance(action, FinalAnswer):
            return action.answer

        observation = execute_tool(action)
        observation_message = json.dumps(
            {
                "tool_name": action.name,
                "result": observation,
            },
            ensure_ascii=False,
        )
        messages.append(
            {"role": "user", "content": f"Observation: {observation_message}"}
        )

        if verbose:
            print(f"Observation: {observation_message}")

    return (
        f"Агент остановлен после {max_steps} шагов: "
        "модель не сформировала финальный ответ."
    )
