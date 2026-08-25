from __future__ import annotations

import pytest

from lessons.module_01_capability_fallback.implementation.demo import run_demo


async def test_demo_routes_only_to_compatible_candidates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = await run_demo()
    output = capsys.readouterr().out

    assert result.routed.route_id == "fallback-b"
    assert result.primary_calls == 1
    assert result.fallback_calls == 1
    assert result.incompatible_calls == 0
    assert result.deadline_preserved
    assert "candidate=primary-a compatible=true gaps=none" in output
    assert "candidate=cheap-c compatible=false" in output
    assert "initial=primary-a" in output
    assert "route=primary-a outcome=effect_free_failure" in output
    assert "selected=fallback-b response=completed" in output
    assert "deadline_preserved=true incompatible_calls=0" in output
