from types import SimpleNamespace

from app.services.follow_up_suggestions import (
    FollowUpSuggestionSet,
    generate_follow_up_suggestions,
)


class _FakeAdapter:
    def __init__(self, suggestions: list[str]) -> None:
        self.suggestions = suggestions
        self.calls: list[dict[str, object]] = []

    def parse_structured(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_parsed=FollowUpSuggestionSet(suggestions=self.suggestions)
        )


def test_follow_up_suggestions_are_contextualized_and_normalized() -> None:
    adapter = _FakeAdapter(
        [
            "  为这个结论举一个具体例子  ",
            "进一步解释它和前一节的联系",
            "进一步解释它和前一节的联系",
        ]
    )

    suggestions = generate_follow_up_suggestions(
        adapter=adapter,
        user_message="这段是什么意思？",
        assistant_message="这段说明了两个概念之间的因果关系。",
        board_state="non_empty",
        workflow_state="conversation",
    )

    assert suggestions == [
        "为这个结论举一个具体例子",
        "进一步解释它和前一节的联系",
    ]
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["schema"] is FollowUpSuggestionSet


def test_follow_up_suggestion_failure_does_not_replace_the_reply() -> None:
    class FailingAdapter:
        def parse_structured(self, **_kwargs):
            raise RuntimeError("suggestion model unavailable")

    assert generate_follow_up_suggestions(
        adapter=FailingAdapter(),
        user_message="继续",
        assistant_message="这是本轮已经完成的回复。",
        board_state="non_empty",
        workflow_state="conversation",
    ) == []
