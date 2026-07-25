"""Инструменты ReAct-агента и их диспетчер."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

from .protocol import ToolCall


class CalculatorError(ValueError):
    """Калькулятор получил неподдерживаемое или небезопасное выражение."""


def web_search(query: str) -> str:
    """Имитирует поиск в интернете с помощью небольшой локальной базы."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query должен быть непустой строкой")

    normalized_query = query.casefold()
    simulated_pages = {
        "react": (
            "ReAct (Reasoning + Acting) — паттерн, в котором языковая модель "
            "чередует выбор действия с анализом наблюдения от внешнего инструмента."
        ),
        "python": (
            "Python — высокоуровневый язык программирования общего назначения."
        ),
        "openai": (
            "OpenAI разрабатывает модели и API для работы с искусственным интеллектом."
        ),
    }

    for keyword, page in simulated_pages.items():
        if keyword in normalized_query:
            return f"Симулированный результат поиска: {page}"

    return (
        "Симулированный результат поиска: релевантных документов не найдено "
        f"для запроса {query!r}."
    )


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MAX_EXPRESSION_LENGTH = 200
_MAX_ABSOLUTE_RESULT = 10**100
_MAX_EXPONENT = 100


def _check_number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalculatorError("разрешены только числа")
    if isinstance(value, float) and not math.isfinite(value):
        raise CalculatorError("результат должен быть конечным числом")
    if abs(value) > _MAX_ABSOLUTE_RESULT:
        raise CalculatorError("слишком большое абсолютное значение результата")
    return value


def _evaluate_expression(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate_expression(node.body)

    if isinstance(node, ast.Constant):
        return _check_number(node.value)

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        operand = _evaluate_expression(node.operand)
        return _check_number(_UNARY_OPERATORS[type(node.op)](operand))

    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate_expression(node.left)
        right = _evaluate_expression(node.right)

        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise CalculatorError(
                f"модуль показателя степени не должен превышать {_MAX_EXPONENT}"
            )

        try:
            result = _BINARY_OPERATORS[type(node.op)](left, right)
        except (ArithmeticError, OverflowError) as error:
            raise CalculatorError(f"ошибка вычисления: {error}") from error
        return _check_number(result)

    raise CalculatorError(
        "разрешены только числа, скобки и операции +, -, *, /, //, %, **"
    )


def calculator(expression: str) -> str:
    """Безопасно вычисляет арифметическое выражение без использования eval."""
    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorError("expression должен быть непустой строкой")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise CalculatorError(
            f"выражение не должно быть длиннее {_MAX_EXPRESSION_LENGTH} символов"
        )

    try:
        syntax_tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise CalculatorError("некорректное арифметическое выражение") from error

    return str(_evaluate_expression(syntax_tree))


Tool = Callable[..., str]

TOOLS: dict[str, Tool] = {
    "web_search": web_search,
    "calculator": calculator,
}


def execute_tool(tool_call: ToolCall) -> str:
    """Вызывает инструмент; его ошибка становится наблюдением для модели."""
    tool = TOOLS.get(tool_call.name)
    if tool is None:
        return (
            f"Ошибка: неизвестный инструмент {tool_call.name!r}. "
            f"Доступны: {', '.join(TOOLS)}."
        )

    try:
        return tool(**tool_call.arguments)
    except (TypeError, ValueError) as error:
        return f"Ошибка инструмента {tool_call.name}: {error}"
