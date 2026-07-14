from __future__ import annotations

import time

import pytest

from app.services.ai_call_budget import (
    AICallBudget,
    AICallBudgetExceeded,
    AIOutputBudgetExceeded,
    bind_ai_call_budget,
    current_ai_call_budget,
)


def _budget(*, seconds: float = 5, chars: int = 1000) -> AICallBudget:
    return AICallBudget(
        deadline_monotonic=time.monotonic() + seconds,
        max_output_tokens=512,
        max_output_chars=chars,
    )


def test_budget_context_is_scoped_to_active_codex_call() -> None:
    budget = _budget()

    assert current_ai_call_budget() is None
    with bind_ai_call_budget(budget):
        assert current_ai_call_budget() is budget
    assert current_ai_call_budget() is None


def test_budget_rejects_expired_deadline() -> None:
    budget = _budget(seconds=-1)

    with pytest.raises(AICallBudgetExceeded, match="deadline exceeded"):
        budget.checkpoint()


def test_budget_rejects_oversized_output() -> None:
    budget = _budget(chars=8)

    with pytest.raises(AIOutputBudgetExceeded, match="8-character budget"):
        budget.validate_output("123456789")
