"""One provider exchange, with no Agent loop and no tool execution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from .contracts import (
    FinalAnswer,
    ModelClientError,
    ModelOutput,
    ModelProtocolError,
    ModelRequest,
    ModelTimeout,
    ModelTransportError,
    ProviderTransport,
    ToolCall,
    Trace,
)


class ModelClient:
    """Normalize exactly one provider response per call."""

    def __init__(
        self,
        transport: ProviderTransport,
        *,
        timeout_s: float | None = None,
    ) -> None:
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._transport = transport
        self._timeout_s = timeout_s

    async def complete(
        self,
        request: ModelRequest,
        *,
        trace: Trace,
    ) -> ModelOutput:
        trace.record(
            "model_client",
            "request_started",
            f"messages={len(request.messages)}",
        )
        try:
            payload = await self._send(request)
            output = self._decode(payload)
        except asyncio.CancelledError:
            trace.record("model_client", "request_cancelled")
            raise
        except TimeoutError as exc:
            error = ModelTimeout("model request exceeded its deadline")
            trace.record("model_client", "request_failed", type(error).__name__)
            raise error from exc
        except ModelClientError as exc:
            trace.record("model_client", "request_failed", type(exc).__name__)
            raise
        except Exception as exc:
            error = ModelTransportError(exc)
            trace.record("model_client", "request_failed", type(error).__name__)
            raise error from exc

        trace.record(
            "model_client",
            "response_received",
            type(output).__name__,
        )
        return output

    async def _send(self, request: ModelRequest) -> Mapping[str, object]:
        if self._timeout_s is None:
            return await self._transport.send(request)
        async with asyncio.timeout(self._timeout_s):
            return await self._transport.send(request)

    @staticmethod
    def _decode(payload: Mapping[str, object]) -> ModelOutput:
        response_type = payload.get("type")
        if response_type == "final":
            text = payload.get("text")
            if not isinstance(text, str):
                raise ModelProtocolError("final response must contain text")
            return FinalAnswer(text=text)

        if response_type == "tool_call":
            call_id = payload.get("id")
            name = payload.get("name")
            arguments = payload.get("arguments")
            if not isinstance(call_id, str) or not call_id:
                raise ModelProtocolError("tool call must contain a non-empty id")
            if not isinstance(name, str) or not name:
                raise ModelProtocolError(
                    "tool call must contain a non-empty name"
                )
            if not isinstance(arguments, Mapping) or not all(
                isinstance(key, str) for key in arguments
            ):
                raise ModelProtocolError(
                    "tool call arguments must be a string-keyed object"
                )
            return ToolCall(
                call_id=call_id,
                name=name,
                arguments=arguments,
            )

        raise ModelProtocolError(
            f"unsupported provider response type: {response_type!r}"
        )
