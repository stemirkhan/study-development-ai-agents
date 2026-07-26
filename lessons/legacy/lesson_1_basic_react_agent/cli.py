"""Командная строка архивного учебного ReAct-агента."""

from __future__ import annotations

import argparse
import os

from openai import OpenAI

from .agent import run_react_agent


def positive_int(value: str) -> int:
    parsed_value = int(value)
    if parsed_value < 1:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return parsed_value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Минимальный ReAct-агент")
    parser.add_argument("question", nargs="?", help="вопрос агенту")
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        help="модель OpenAI (по умолчанию: OPENAI_MODEL или gpt-5-mini)",
    )
    parser.add_argument(
        "--max-steps",
        type=positive_int,
        default=6,
        help="максимальное количество шагов ReAct-цикла",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    question = args.question or input("Ваш вопрос: ")
    answer = run_react_agent(
        question,
        client=OpenAI(),
        model=args.model,
        max_steps=args.max_steps,
    )
    print(f"\nФинальный ответ:\n{answer}")
