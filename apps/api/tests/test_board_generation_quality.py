from __future__ import annotations

from app.models import LearningClarificationStatus
from app.services.board_document_editor import generate_from_requirements
from app.services.board_generation_quality import (
    BoardContentBlock,
    BoardSection,
    build_board_teaching_plan,
    generate_board_ai_input,
    validate_board_plan,
    validate_math_rendering,
)
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardDocumentEditResult, openai_course_ai


def test_board_generation_plan_splits_broad_calculus_preview() -> None:
    plan = build_board_teaching_plan(
        {
            "domain": "高等数学",
            "startingPoint": "高中生，刚高考完，高中数学基础扎实",
            "contentToLearn": "极限与连续",
            "learningContext": "预习高等数学",
            "granularity": "broad_topic",
        }
    )

    assert plan.scope_kind == "lesson_series"
    assert plan.course_series_plan is not None
    assert "极限的直观含义" in plan.course_series_plan.current_lesson
    assert "极限的直观含义" in plan.current_lesson.title
    assert plan.board_mode == "concept_explanation"
    assert plan.math_adapter_enabled is True
    first_lesson_text = "\n".join(section.title for section in plan.current_lesson.sections)
    assert "等价无穷小" not in first_lesson_text
    assert "介值定理" not in first_lesson_text
    assert "最值定理" not in first_lesson_text
    assert validate_board_plan(plan).passed is True


def test_validate_math_rendering_rejects_half_rendered_fragments() -> None:
    plan = build_board_teaching_plan({"domain": "数学", "contentToLearn": "公式检查"})
    bad_lesson = plan.current_lesson.model_copy(
        update={
            "sections": [
                BoardSection(
                    title="错误公式",
                    content_blocks=[
                        BoardContentBlock(type="paragraph", text="displaystyle lim x→0"),
                        BoardContentBlock(type="paragraph", text="begincases"),
                        BoardContentBlock(type="paragraph", text="sin xsim x"),
                    ],
                )
            ]
        }
    )
    bad_plan = plan.model_copy(update={"current_lesson": bad_lesson})

    result = validate_math_rendering(bad_plan)

    assert result.passed is False
    messages = "\n".join(issue.message for issue in result.issues)
    assert "displaystyle" in messages
    assert "begincases" in messages
    assert "sim" in messages


def test_field_map_plan_for_beginner_multi_part_system() -> None:
    content = "账户、交易、Gas、EVM、智能合约、DApp 前端如何协同工作"
    plan = build_board_teaching_plan(
        {
            "domain": "以太坊开发",
            "startingPoint": "完全新手",
            "contentToLearn": content,
            "learningContext": "想先看懂整体协同流程",
        }
    )
    payload = generate_board_ai_input(plan)
    payload_text = str(payload)

    assert plan.board_mode == "field_map"
    for term in ["账户", "交易", "Gas", "EVM", "智能合约", "DApp"]:
        assert term in payload_text
    assert "一个完整流程" in payload_text


def test_scenario_dialogue_plan_for_language_scene() -> None:
    plan = build_board_teaching_plan(
        {
            "domain": "法语",
            "contentToPractice": "咖啡厅点单",
            "currentLevel": "B1-B2，词汇量约3500",
            "targetScenario": "法国咖啡厅真实点单",
            "output_preference": "情景对话和替换练习",
        }
    )
    section_text = "\n".join(section.title for section in plan.current_lesson.sections)

    assert plan.board_mode == "scenario_dialogue"
    assert "替换练习" in section_text
    assert "用户输出任务" in section_text


def test_review_lesson_plan_for_advanced_recall_task() -> None:
    plan = build_board_teaching_plan(
        {
            "domain": "中级会计",
            "contentToPractice": "长期股权投资",
            "currentLevel": "基础懂，高级低频模块遗忘较多",
            "targetScenario": "工作中重新用起来",
            "output_preference": "复习和高级模块回顾",
        }
    )
    section_text = "\n".join(section.title for section in plan.current_lesson.sections)

    assert plan.board_mode == "review_lesson"
    assert "易忘点" in section_text
    assert "典型例子" in section_text
    assert "练习题" in section_text


def test_generate_from_requirements_retries_half_rendered_math(monkeypatch) -> None:
    lesson = create_empty_lesson("公式质量")
    requirements = lesson.learning_requirements
    assert requirements is not None
    requirements.theme = "数学"
    requirements.learning_goal = "理解一个公式的直觉"
    requirements.level = "有基础，想看清公式"
    requirements.board_scope = ["公式质量"]
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="ready",
        ready_for_board=True,
    )
    calls: list[dict[str, object]] = []

    def _fake_board_edit(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return BoardDocumentEditResult(
                operation="replace_document",
                title="公式质量",
                content_text=(
                    "# 公式质量\n\n"
                    "## 本节目标\n\n看懂公式。\n\n"
                    "## 核心直觉\n\n出现 displaystyle lim x→0 和 begincases。\n\n"
                    "## 例子\n\nsin xsim x。\n\n"
                    "## 课堂练习\n\n说出问题。\n\n"
                    "## 本节小结\n\n先修复公式。\n\n"
                    "## 下一步\n\n继续。"
                ),
                summary="坏公式。",
                chatbot_message="已生成。",
                section_titles=["本节目标", "核心直觉", "例子", "课堂练习", "本节小结", "下一步"],
            )
        return BoardDocumentEditResult(
            operation="replace_document",
            title="公式质量",
            content_text=(
                "# 公式质量\n\n"
                "## 本节目标\n\n看懂公式。\n\n"
                "## 核心直觉\n\n先理解公式描述的关系。\n\n"
                "## 例子\n\n使用标准公式：$\\lim_{x\\to 0}\\frac{\\sin x}{x}=1$。\n\n"
                "## 课堂练习\n\n解释这个极限表达了什么。\n\n"
                "## 本节小结\n\n公式要服务于直觉。\n\n"
                "## 下一步\n\n继续练习。"
            ),
            summary="已修复。",
            chatbot_message="已生成。",
            section_titles=["本节目标", "核心直觉", "例子", "课堂练习", "本节小结", "下一步"],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)

    outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=clarification,
        resource_summary="",
    )

    assert outcome.changed is True
    assert outcome.quality_repair_attempts == 1
    assert len(calls) == 2
    assert "board_generation_quality_pipeline" in calls[0]["learning_requirement_context"]
    repair_context = calls[1]["learning_requirement_context"]["document_quality_repair"]
    assert repair_context["board_generation_quality_validation"]["passed"] is False
    assert "displaystyle" not in outcome.new_document.content_text
    assert "begincases" not in outcome.new_document.content_text
