from __future__ import annotations

import pytest

from lessons.module_01_provider_contract.implementation.contracts import (
    ModelStreamInterrupted,
    ModelTransportError,
    ResponseCompleted,
    ToolArgumentsDelta,
    UnknownProviderEvent,
)
from lessons.module_01_provider_contract.implementation.demo import run_demo


async def test_demo_runs_success_and_interrupted_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = await run_demo()
    output = capsys.readouterr().out

    assert result.stream.response == result.unary_response
    assert len(result.stream.events) == 10
    assert isinstance(result.stream.events[3], UnknownProviderEvent)
    assert isinstance(result.stream.events[-1], ResponseCompleted)
    assert isinstance(result.interruption, ModelStreamInterrupted)
    assert isinstance(result.interruption.cause, ModelTransportError)
    assert len(result.interruption.events) == 2
    assert isinstance(result.interruption.events[-1], ToolArgumentsDelta)
    assert "SUCCESS\n01 ResponseStarted" in output
    assert "RESULT semantic_equal=True" in output
    assert "ERROR\n01 ResponseStarted" in output
    assert "received_events=2 terminal=False" in output
