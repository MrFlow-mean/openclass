from __future__ import annotations

from app.models import LearningClarificationStatus
from app.services.board_document_editor import generate_from_requirements
from app.services.board_generation_quality import (
    BoardContentBlock,
    BoardSection,
    build_board_teaching_plan,
    generate_board_ai_input,
    validate_board_plan,
    validate_generated_board_text,
    validate_math_rendering,
)
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardDocumentEditResult, openai_course_ai


def _textbook_content(title: str, body: str = "") -> str:
    return (
        f"# {title}\n\n"
        "## 1.1 概念引入\n\n本节讨论该主题所刻画的基本对象与问题背景。\n\n"
        "## 1.2 正式定义\n\n定义：相关对象可用下列关系加以刻画。\n\n"
        f"{body}\n\n"
        "## 1.3 性质或结论\n\n性质：该关系用于描述局部变化趋势或结构约束。\n\n"
        "## 1.4 典型例题\n\n例 1：说明上述定义在具体对象中的含义。\n\n"
        "## 1.5 解答过程\n\n解：根据定义，先确定研究对象，再分析对应关系。\n\n"
        "## 1.6 注释\n\n注：公式和例题均服务于概念边界的说明。\n\n"
        "## 1.7 习题\n\n习题 1：说明本节定义中的对象、条件与结论。\n\n"
        "## 下一节\n\n继续讨论相关方法。"
    )


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
    assert plan.math_adapter_enabled is False
    assert plan.board_template is not None
    assert plan.board_template.template_id == "concept_explanation_v1"
    assert "1.2 正式定义" in plan.board_template.required_headings
    first_lesson_text = "\n".join(section.title for section in plan.current_lesson.sections)
    assert "等价无穷小" not in first_lesson_text
    assert "介值定理" not in first_lesson_text
    assert "最值定理" not in first_lesson_text
    assert validate_board_plan(plan).passed is True
    assert "formula_rules" in generate_board_ai_input(plan)["quality_contract"]


def test_beginner_combined_topic_splits_even_when_granularity_is_single() -> None:
    plan = build_board_teaching_plan(
        {
            "startingPoint": "刚开始预习，想先从入口建立直觉",
            "contentToLearn": "概念A与概念B",
            "granularity": "single_knowledge_point",
        }
    )

    assert plan.scope_kind == "lesson_series"
    assert plan.course_series_plan is not None
    assert "概念A的直观含义" in plan.current_lesson.title


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


def test_validate_generated_board_text_allows_latex_inside_math_delimiters() -> None:
    plan = build_board_teaching_plan({"domain": "公式", "contentToLearn": "分式与极限"})
    content = _textbook_content("分式与极限", "$$\\lim_{x\\to0}\\frac{\\sin x}{x}=1$$")

    result = validate_generated_board_text(content, plan)

    assert result.passed is True


def test_validate_generated_board_text_rejects_latex_command_outside_math_delimiters() -> None:
    plan = build_board_teaching_plan({"domain": "公式", "contentToLearn": "分式与极限"})
    content = _textbook_content("分式与极限", "错误写法：\\frac{1}{n}。")

    result = validate_generated_board_text(content, plan)

    assert result.passed is False
    assert any("LaTeX 命令" in issue.message or "frac" in issue.message for issue in result.issues)


def test_validate_generated_board_text_rejects_complex_inline_formula() -> None:
    plan = build_board_teaching_plan({"domain": "公式", "contentToLearn": "符号表达"})
    content = _textbook_content("符号表达", "复杂公式不应内联：$\\lim_{x\\to0}\\frac{\\sin x}{x}=1$。")

    result = validate_generated_board_text(content, plan)

    assert result.passed is False
    assert any("独立公式块" in issue.message for issue in result.issues)


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
    assert plan.board_template is not None
    assert plan.board_template.template_id == "field_map_v1"
    assert "基本框架" in plan.current_lesson.title
    for term in ["账户", "交易", "Gas", "EVM", "智能合约", "DApp"]:
        assert term in payload_text
    assert "一个完整流程" in payload_text
    assert payload["quality_contract"]["template_id"] == "field_map_v1"
    assert "board_template" in payload
    assert any("教材章节" in rule for rule in payload["quality_contract"]["title_rules"])
    assert any("例 1" in rule for rule in payload["quality_contract"]["example_rules"])
    assert any("diagram_prompt" in rule for rule in payload["quality_contract"]["visual_rules"])


def test_intro_topic_uses_textbook_concept_plan() -> None:
    plan = build_board_teaching_plan(
        {
            "startingPoint": "刚高考完，想预习，先建立直觉",
            "contentToLearn": "极限与连续的基本概念与直观理解（ε-δ语言基础）",
            "granularity": "single_knowledge_point",
        }
    )
    payload = generate_board_ai_input(plan)

    assert plan.board_mode == "concept_explanation"
    assert plan.current_lesson.title == "第 1 课：极限的直观含义"
    assert "协同流程" not in plan.current_lesson.title
    assert payload["quality_contract"]["writing_style"] == "textbook_lecture_notes"
    assert "概念引入" in payload["quality_contract"]["required_lesson_section_sequence"]


def test_validate_generated_board_text_rejects_oral_style() -> None:
    plan = build_board_teaching_plan({"domain": "概念", "contentToLearn": "概念A"})
    content = _textbook_content("概念A", "我们来看这个定义，你会发现它很容易理解。")

    result = validate_generated_board_text(content, plan)

    assert result.passed is False
    assert any(issue.dimension == "writingStyle" for issue in result.issues)


def test_validate_generated_board_text_rejects_numbered_oral_heading() -> None:
    plan = build_board_teaching_plan({"contentToLearn": "概念A"})
    content = _textbook_content("概念A").replace("## 1.1 概念引入", "## 1.1 核心直觉")

    result = validate_generated_board_text(content, plan)

    assert result.passed is False
    assert any(issue.dimension == "writingStyle" and "核心直觉" in issue.evidence for issue in result.issues)


def test_validate_generated_board_text_requires_numbered_textbook_section_headings() -> None:
    plan = build_board_teaching_plan({"contentToLearn": "概念A"})
    content = _textbook_content("概念A").replace("## 1.2 正式定义", "## 正式定义")

    result = validate_generated_board_text(content, plan)

    assert result.passed is False
    assert any("缺少 1.1/1.2" in issue.message for issue in result.issues)


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
            content_text=_textbook_content("公式质量", "$$\\lim_{x\\to 0}\\frac{\\sin x}{x}=1$$"),
            summary="已修复。",
            chatbot_message="已生成。",
            section_titles=[
                "1.1 概念引入",
                "1.2 正式定义",
                "1.3 性质或结论",
                "1.4 典型例题",
                "1.5 解答过程",
                "1.6 注释",
                "1.7 习题",
            ],
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


def test_generate_from_requirements_does_not_write_after_repeated_quality_failures(monkeypatch) -> None:
    lesson = create_empty_lesson("公式质量失败")
    requirements = lesson.learning_requirements
    assert requirements is not None
    requirements.theme = "公式"
    requirements.learning_goal = "理解公式表达"
    requirements.level = "有基础"
    requirements.board_scope = ["公式质量失败"]
    clarification = LearningClarificationStatus(progress=100, label="ready", reason="ready", ready_for_board=True)
    calls: list[dict[str, object]] = []

    def _bad_board_edit(**kwargs):
        calls.append(kwargs)
        return BoardDocumentEditResult(
            operation="replace_document",
            title="公式质量失败",
            content_text=(
                "# 公式质量失败\n\n"
                "## 本节目标\n\n看懂公式。\n\n"
                "## 核心直觉\n\n出现 displaystyle lim x→0。\n\n"
                "## 例子\n\nsin xsim x。\n\n"
                "## 课堂练习\n\n说出问题。\n\n"
                "## 本节小结\n\n仍未修复。\n\n"
                "## 下一步\n\n继续。"
            ),
            summary="坏公式。",
            chatbot_message="已生成。",
            section_titles=["本节目标", "核心直觉", "例子", "课堂练习", "本节小结", "下一步"],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _bad_board_edit)

    outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=clarification,
        resource_summary="",
    )

    assert outcome.changed is False
    assert outcome.quality_repair_attempts == 3
    assert outcome.quality_review_status == "repair_required"
    assert len(calls) == 3
    assert outcome.new_document.content_text == ""
