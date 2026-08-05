"""Show cancellation and an interrupted prefix without a real provider."""

from __future__ import annotations

import asyncio

from lessons.module_01_provider_contract.implementation.contracts import (
    Deadline,
    InputMessage,
    MessageRole,
    ModelRequest,
    ModelStreamInterrupted,
    ModelTransportError,
    ProviderPayload,
    ResponseStarted,
    TextDelta,
)
from lessons.module_01_streaming_cancellation.implementation.execution import (
    StreamingRuntime,
)
from lessons.module_01_streaming_cancellation.implementation.testing import (
    AttemptScript,
    BlockUntil,
    ControlledStreamingClient,
    Emit,
    Fail,
    ManualTime,
)


PROVIDER = "demo-llm"
MODEL = "demo-model"


def raw(kind: str) -> ProviderPayload:
    return ProviderPayload(PROVIDER, kind, b"{}")


def request(request_id: str) -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        model=MODEL,
        messages=(InputMessage(MessageRole.USER, "Нужен ли зонт?"),),
    )


async def cancellation_trace() -> None:
    model_request = request("cancel-request")
    read_started = asyncio.Event()
    never_release = asyncio.Event()
    client = ControlledStreamingClient(
        (
            AttemptScript(
                (
                    Emit(
                        ResponseStarted(
                            sequence=1,
                            provider_payload=raw("response.started"),
                            request_id=model_request.request_id,
                            response_id="response-cancel",
                            model=MODEL,
                        )
                    ),
                    BlockUntil(never_release, read_started),
                )
            ),
        )
    )
    task = asyncio.create_task(
        StreamingRuntime(time=ManualTime()).collect(
            client,
            model_request,
            deadline=Deadline(30),
        )
    )
    await read_started.wait()

    task.cancel("пользователь закрыл запрос")

    try:
        await task
    except asyncio.CancelledError as error:
        print("1. Отмена пользователя")
        print(f"   наружу передана отмена: {error}")
        print(f"   закрытые попытки: {client.closed_attempts}")
        print("   завершённого ответа нет\n")


async def interrupted_trace() -> None:
    model_request = request("interrupted-request")
    started = ResponseStarted(
        sequence=1,
        provider_payload=raw("response.started"),
        request_id=model_request.request_id,
        response_id="response-interrupted",
        model=MODEL,
    )
    delta = TextDelta(
        sequence=2,
        provider_payload=raw("text.delta"),
        output_index=0,
        delta="Возьмите зон",
    )
    failure = ModelTransportError(
        "соединение оборвалось",
        received_bytes=True,
    )
    client = ControlledStreamingClient(
        (AttemptScript((Emit(started), Emit(delta), Fail(failure))),)
    )

    try:
        await StreamingRuntime(time=ManualTime()).collect(
            client,
            model_request,
            deadline=Deadline(30),
        )
    except ModelStreamInterrupted as error:
        print("2. Обрыв после части ответа")
        print(
            "   сохранённые события: "
            + ", ".join(type(event).__name__ for event in error.events)
        )
        print(f"   видимый черновик: {delta.delta!r}")
        print(f"   причина: {error.cause}")
        print("   завершённого ответа нет; повтор не выполнялся")


async def main() -> None:
    await cancellation_trace()
    await interrupted_trace()


if __name__ == "__main__":
    asyncio.run(main())
