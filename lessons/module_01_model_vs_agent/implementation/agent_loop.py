"""Dynamic model/tool loop with explicit trust and step-budget boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from .contracts import (
    AgentResult,
    AgentStepLimitExceeded,
    DuplicateToolCall,
    FinalAnswer,
    InvalidToolArguments,
    ModelRequest,
    ToolBinding,
    ToolCall,
    ToolExecutionError,
    ToolResultMessage,
    Trace,
    UnknownTool,
    UserMessage,
)
from .model_client import ModelClient


class AgentLoop:
    """Ask for a next action, validate it, and stop within a fixed budget."""

    def __init__(
        self,
        model_client: ModelClient,
        tools: Mapping[str, ToolBinding],
        *,
        max_steps: int = 4,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least one")
        copied_tools = dict(tools)
        if any(name != binding.spec.name for name, binding in copied_tools.items()):
            raise ValueError("tool registry keys must match ToolSpec.name")

        self._model_client = model_client
        self._tools = copied_tools
        self._tool_specs = tuple(
            copied_tools[name].spec for name in sorted(copied_tools)
        )
        self._max_steps = max_steps

    async def run(self, user_input: str, *, trace: Trace) -> AgentResult:
        context = [UserMessage(content=user_input)]
        tool_calls = 0
        seen_call_ids: set[str] = set()
        trace.record(
            "agent_loop",
            "agent_started",
            f"max_steps={self._max_steps}",
        )

        for step in range(1, self._max_steps + 1):
            trace.record(
                "agent_loop",
                "model_requested",
                f"step={step}",
            )
            try:
                output = await self._model_client.complete(
                    ModelRequest(
                        messages=tuple(context),
                        tools=self._tool_specs,
                    ),
                    trace=trace,
                )
            except asyncio.CancelledError:
                trace.record("agent_loop", "agent_cancelled", f"step={step}")
                raise

            if isinstance(output, FinalAnswer):
                trace.record(
                    "agent_loop",
                    "agent_completed",
                    f"step={step}",
                )
                return AgentResult(
                    answer=output.text,
                    model_calls=step,
                    tool_calls=tool_calls,
                    context=tuple(context),
                )

            trace.record(
                "agent_loop",
                "tool_call_received",
                f"step={step},name={output.name}",
            )
            if step == self._max_steps:
                trace.record(
                    "agent_loop",
                    "step_budget_exhausted",
                    f"step={step}",
                )
                raise AgentStepLimitExceeded(self._max_steps)

            binding = self._tools.get(output.name)
            if binding is None:
                trace.record(
                    "agent_loop",
                    "tool_rejected",
                    f"name={output.name}",
                )
                raise UnknownTool(output.name)

            if output.call_id in seen_call_ids:
                trace.record(
                    "agent_loop",
                    "tool_rejected",
                    f"call_id={output.call_id},reason=duplicate",
                )
                raise DuplicateToolCall(output.call_id)

            arguments = self._validate_arguments(binding, output, trace)
            seen_call_ids.add(output.call_id)
            try:
                tool_result = await binding.invoke(arguments)
            except asyncio.CancelledError:
                trace.record(
                    "agent_loop",
                    "tool_cancelled",
                    f"name={output.name}",
                )
                raise
            except Exception as exc:
                trace.record(
                    "agent_loop",
                    "tool_failed",
                    f"name={output.name}",
                )
                raise ToolExecutionError(output.name, exc) from exc

            if not isinstance(tool_result, str):
                cause = TypeError("tool result must be a string")
                raise ToolExecutionError(output.name, cause) from cause

            tool_calls += 1
            trace.record(
                "agent_loop",
                "tool_completed",
                f"name={output.name}",
            )
            context.extend(
                (
                    output,
                    ToolResultMessage(
                        call_id=output.call_id,
                        name=output.name,
                        content=tool_result,
                    ),
                )
            )

        raise AssertionError("step loop must return or raise")

    @staticmethod
    def _validate_arguments(
        binding: ToolBinding,
        tool_call: ToolCall,
        trace: Trace,
    ) -> Mapping[str, object]:
        try:
            arguments = binding.validate(tool_call.arguments)
        except InvalidToolArguments as exc:
            trace.record(
                "agent_loop",
                "tool_rejected",
                f"name={tool_call.name},reason={exc.reason}",
            )
            raise

        trace.record(
            "agent_loop",
            "tool_validated",
            f"name={tool_call.name}",
        )
        return arguments
