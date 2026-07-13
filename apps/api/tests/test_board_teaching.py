from app.services.board_teaching import _build_section_plan


def test_section_teaching_plan_keeps_only_board_derived_context() -> None:
    plan = _build_section_plan(
        index=0,
        heading="任意小节",
        excerpt="结论成立时需要满足这个条件。",
    )

    assert plan.core_points == ["结论成立时需要满足这个条件。"]
    assert plan.teaching_steps == []
    assert plan.teaching_method == ""
    assert plan.example_or_analogy == ""
    assert plan.common_pitfalls == []
    assert plan.check_question == ""
    assert plan.transition_to_next == ""
