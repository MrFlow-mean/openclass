import pytest

from app.models import ChatRequest
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BlankBoardRequirementRefinement, openai_course_ai


def _seed_empty_workspace(store: SqliteCourseStore, user_id: str, title: str = "空白学习页"):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson(title)
    lesson.learning_requirements = None
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(user_id, workspace)
    return lesson


def test_practice_need_misrouted_as_domain_map_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_practice_strategy_repair"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    calls: list[dict[str, object]] = []

    def _fake_refinement(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=(
                    "这个方向可以先看一张学习地图："
                    "\n1. **基础概念**：理解基本对象。"
                    "\n2. **规则方法**：掌握通用方法。"
                    "\n\n我推荐先从**基础概念**开始，因为它最基础。"
                    "你之前接触过这个方向吗？"
                ),
                progress=45,
                summary="用户想提高一项技能。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一项技能",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看基础概念和规则方法。",
                entry_point_options=[
                    {
                        "label": "基础概念",
                        "why_it_matters": "帮助建立理解。",
                        "best_for": "新手。",
                    },
                    {
                        "label": "规则方法",
                        "why_it_matters": "帮助掌握方法。",
                        "best_for": "想系统学习的人。",
                    },
                ],
                recommended_entry_point="基础概念",
                reason_for_recommendation="它最基础。",
                next_question="你之前接触过这个方向吗？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "明白，你这次不是单纯想听概念，而是想把一项技能练起来。"
                "我先把练习目标定为这项技能，下一步需要判断练习难度："
                "你现在大概能独立做到什么程度？"
            ),
            progress=65,
            summary="用户想练习并提高一项技能。",
            work_mode="practice_artifact",
            granularity="practice_artifact",
            learning_goal="一项技能",
            guidance_strategy="starting_point",
            current_level="",
            missing_items=["当前水平", "面向场景"],
            next_question="你现在大概能独立做到什么程度？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想练习一项技能，提高实际能力。"),
        user_id=user_id,
    )

    assert len(calls) == 2
    repair_context = calls[1]["quality_repair_context"]
    assert repair_context is not None
    assert "练习型需求" in repair_context["repair_reason"]
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.work_mode == "practice_artifact"
    assert response.active_requirement_sheet.granularity == "practice_artifact"
    assert response.learning_clarification.ready_for_board is False
    assert "当前水平" in response.learning_clarification.missing_items
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is True
    assert any("练习型需求" in issue for issue in discovery["quality_issues"])


def test_known_unknown_self_report_missing_background_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_known_unknown_repair"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    calls: list[dict[str, object]] = []

    def _fake_refinement(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=(
                    "这个方向可以先分成整体组成和基础入口。"
                    "我建议先从**基础入口**开始。你想先学哪部分？"
                ),
                progress=45,
                summary="用户想继续学习一个方向。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个方向",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看整体组成和基础入口。",
                entry_point_options=[
                    {
                        "label": "整体组成",
                        "why_it_matters": "帮助建立方向。",
                        "best_for": "不知道从哪开始的人。",
                    },
                    {
                        "label": "基础入口",
                        "why_it_matters": "帮助开始第一步。",
                        "best_for": "想继续学习的人。",
                    },
                ],
                recommended_entry_point="基础入口",
                reason_for_recommendation="它适合作为下一步。",
                next_question="你想先学哪部分？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "你已经把起点说清楚了：前置内容学过，后续部分还没学。"
                "那我会避开重复铺垫，先从**后续概念的直观含义**切入。"
                "你最近看到它时，是概念本身不理解，还是能懂例子但不会自己用？"
            ),
            progress=70,
            summary="用户说明了已会和未会内容。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="后续概念的直观含义",
            current_level="已经学过前置内容",
            known_background="已会：前置内容；未会：后续部分。",
            guidance_strategy="known_unknown",
            learner_profile_inference="用户已学过前置内容，还没学后续部分，适合从后续概念的直观含义开始。",
            recommended_entry_point="后续概念的直观含义",
            reason_for_recommendation="它承接用户已会内容，同时避开重复讲解。",
            key_facts=[
                {
                    "label": "当前水平",
                    "value": "已经学过前置内容",
                    "evidence": "用户说前面的学过。",
                    "category": "level",
                }
            ],
            missing_items=["用户想学的内容需要收敛到具体知识点"],
            next_question="你最近看到它时，是概念本身不理解，还是能懂例子但不会自己用？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="前面的内容我学过，后面的还没学。"),
        user_id=user_id,
    )

    assert len(calls) == 2
    repair_context = calls[1]["quality_repair_context"]
    assert repair_context is not None
    assert "已会/未会" in repair_context["repair_reason"]
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.level == "已经学过前置内容"
    assert response.active_requirement_sheet.known_background == "已会：前置内容；未会：后续部分。"
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is True
    assert discovery["guidance_strategy"] == "known_unknown"


def test_stuck_point_misrouted_as_domain_map_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_stuck_strategy_repair"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    calls: list[dict[str, object]] = []

    def _fake_refinement(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=(
                    "这个方向可以先看整体组成和基础入口。"
                    "我建议先从**基础入口**开始。你想学哪部分？"
                ),
                progress=45,
                summary="用户想学一个方向。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个方向",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看整体组成和基础入口。",
                entry_point_options=[
                    {
                        "label": "整体组成",
                        "why_it_matters": "帮助建立方向。",
                        "best_for": "不知道从哪开始的人。",
                    },
                    {
                        "label": "基础入口",
                        "why_it_matters": "帮助进入第一步。",
                        "best_for": "想开始的人。",
                    },
                ],
                recommended_entry_point="基础入口",
                reason_for_recommendation="它最基础。",
                next_question="你想学哪部分？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "你现在的关键信号不是“领域太宽”，而是已经卡住了。"
                "我先把入口收窄到**卡点复盘**：看它是概念没懂、步骤断了，还是会看例子但不会迁移。"
                "你最卡的那一步，更像概念听不懂，还是自己做不出来？"
            ),
            progress=65,
            summary="用户遇到明确卡点。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="卡住内容的复盘入口",
            current_level="已经接触过相关内容，但遇到卡点",
            known_background="卡点：看不懂并且做不出来。",
            guidance_strategy="stuck_point",
            learner_profile_inference="用户已经接触过相关内容，当前主要卡在理解或独立操作。",
            recommended_entry_point="卡点复盘",
            reason_for_recommendation="它直接对应用户当前困难。",
            missing_items=["用户想学的内容需要收敛到具体知识点"],
            next_question="你最卡的那一步，更像概念听不懂，还是自己做不出来？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我最近看到这部分就卡住了，看不懂也做不出来。"),
        user_id=user_id,
    )

    assert len(calls) == 2
    repair_context = calls[1]["quality_repair_context"]
    assert repair_context is not None
    assert "卡点" in repair_context["repair_reason"]
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.known_background == "卡点：看不懂并且做不出来。"
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is True
    assert discovery["guidance_strategy"] == "stuck_point"
