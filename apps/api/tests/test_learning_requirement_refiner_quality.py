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


def test_practice_need_misrouted_as_domain_map_records_quality_issue_without_second_call(
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

    assert len(calls) == 1
    assert "quality_repair_context" not in calls[0]
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.work_mode == "knowledge_board"
    assert response.active_requirement_sheet.granularity == "broad_topic"
    assert response.learning_clarification.ready_for_board is False
    assert response.learning_clarification.missing_items == ["用户想学的内容需要收敛到具体知识点"]
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is False
    assert discovery["quality_repair_skipped"] is True
    assert any("练习型需求" in issue for issue in discovery["quality_issues"])


def test_practice_missing_level_uses_choice_cards_before_difficulty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_practice_level_cards_repair"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    calls: list[dict[str, object]] = []

    def _fake_refinement(**kwargs):
        calls.append(kwargs)
        if kwargs.get("quality_repair_context"):
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=(
                    "好的，练习代码能力最适合用阶梯任务来推进。"
                    "我先了解一下：你现在的代码练习水平更接近哪一种？\n\n"
                    "选一个最接近的就行，我会根据你的水平安排练习难度。\n\n"
                    "A. **纯入门**\n只了解大概概念，还没真正写过完整练习。\n\n"
                    "B. **语法入门**\n学过基础语法，但写完整小项目还不熟。\n\n"
                    "C. **写过基础项目**\n写过简单功能，但测试、拆分和边界处理不熟。\n\n"
                    "D. **能写标准组件**\n能完成常见组件，想提高结构和代码质量。\n\n"
                    "E. **想练复杂项目**\n想练综合交互、测试、性能或安全问题。\n\n"
                    "F. **不确定**\n你按从简单到进阶的路线帮我安排。"
                ),
                progress=45,
                summary="用户想练习代码能力，需要先确认当前水平。",
                work_mode="practice_artifact",
                granularity="practice_artifact",
                learning_goal="练习代码能力",
                guidance_strategy="choice_cards",
                entry_point_options=[
                    {
                        "label": "纯入门",
                        "why_it_matters": "确认是否需要从最小完整练习开始。",
                        "best_for": "只了解概念、没写过完整练习的人。",
                    },
                    {
                        "label": "语法入门",
                        "why_it_matters": "确认是否需要把语法转成完整产物。",
                        "best_for": "学过基础语法但不熟练的人。",
                    },
                    {
                        "label": "写过基础项目",
                        "why_it_matters": "确认是否可以进入更完整的任务。",
                        "best_for": "写过简单功能的人。",
                    },
                    {
                        "label": "能写标准组件",
                        "why_it_matters": "确认是否该提高结构和质量。",
                        "best_for": "能完成常见组件的人。",
                    },
                    {
                        "label": "想练复杂项目",
                        "why_it_matters": "确认是否进入综合任务。",
                        "best_for": "想练复杂交互或质量问题的人。",
                    },
                    {
                        "label": "不确定",
                        "why_it_matters": "降低用户判断成本。",
                        "best_for": "不知道自己水平的人。",
                    },
                ],
                missing_items=["当前水平", "面向场景"],
                next_question="你现在的代码练习水平更接近哪一种？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "好的，练习代码能力可以从简单项目开始，再逐步挑战更复杂的组件。"
                "我想先了解一下：你目前掌握到什么程度，比如有没有写过简单项目？"
            ),
            progress=35,
            summary="用户想练习代码能力。",
            work_mode="practice_artifact",
            granularity="practice_artifact",
            learning_goal="练习代码能力",
            guidance_strategy="starting_point",
            learning_map_summary="可以从简单项目逐步练到复杂组件。",
            entry_point_options=[
                {
                    "label": "简单项目",
                    "why_it_matters": "覆盖基础写法。",
                    "best_for": "基础较弱的人。",
                },
                {
                    "label": "复杂组件",
                    "why_it_matters": "提升结构能力。",
                    "best_for": "已有基础的人。",
                },
            ],
            recommended_entry_point="简单项目",
            reason_for_recommendation="当前水平未知，从基础开始更稳。",
            missing_items=["当前水平", "面向场景"],
            next_question="你目前掌握到什么程度，比如有没有写过简单项目？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想练习写几个小项目提高代码能力。"),
        user_id=user_id,
    )

    assert len(calls) == 1
    assert "quality_repair_context" not in calls[0]
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.work_mode == "practice_artifact"
    assert response.active_requirement_sheet.granularity == "practice_artifact"
    assert response.learning_clarification.ready_for_board is False
    assert "当前水平" in response.learning_clarification.missing_items
    assert "你目前掌握到什么程度" in response.chatbot_message
    assert "A. **纯入门**" not in response.chatbot_message
    assert "F. **不确定**" not in response.chatbot_message
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is False
    assert discovery["quality_repair_skipped"] is True
    assert discovery["guidance_strategy"] == "starting_point"
    assert len(discovery["entry_point_options"]) == 2
    assert any("练习型水平选择卡片" in issue for issue in discovery["quality_issues"])


def test_known_unknown_self_report_missing_background_records_quality_issue_without_second_call(
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

    assert len(calls) == 1
    assert "quality_repair_context" not in calls[0]
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.level != "已经学过前置内容"
    assert response.active_requirement_sheet.known_background != "已会：前置内容；未会：后续部分。"
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is False
    assert discovery["quality_repair_skipped"] is True
    assert discovery["guidance_strategy"] == "domain_map"
    assert any("已会/未会" in issue for issue in discovery["quality_issues"])


def test_stuck_point_misrouted_as_domain_map_records_quality_issue_without_second_call(
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

    assert len(calls) == 1
    assert "quality_repair_context" not in calls[0]
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.known_background != "卡点：看不懂并且做不出来。"
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is False
    assert discovery["quality_repair_skipped"] is True
    assert discovery["guidance_strategy"] == "domain_map"
    assert any("卡点" in issue for issue in discovery["quality_issues"])


def test_pure_novice_route_choice_records_quality_issue_without_second_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_pure_novice_route_choice_repair"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    calls: list[dict[str, object]] = []

    def _fake_refinement(**kwargs):
        calls.append(kwargs)
        if kwargs.get("quality_repair_context"):
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=(
                    "明白，你是纯新手入门。那第一课我直接定为**这个领域的基础概念与整体组成**。"
                    "原因是零基础先建立整体结构，比直接进入后续工具、规则或实操更稳。"
                    "学完这一课，你应该能说清这个领域的核心组成和基本流程。"
                ),
                progress=100,
                summary="用户零基础纯新手，适合先学领域基础概念与整体组成。",
                work_mode="knowledge_board",
                granularity="single_knowledge_point",
                learning_goal="这个领域的基础概念与整体组成",
                current_level="零基础纯新手",
                known_background="用户明确表示纯新手入门。",
                target_depth="入门了解 / 建立领域地图",
                success_criteria="理解领域组成，并确定后续学习入口",
                guidance_strategy="recommended_entry",
                learning_map_summary="纯新手先理解领域基础概念与整体组成。",
                entry_point_options=[
                    {
                        "label": "这个领域的基础概念与整体组成",
                        "why_it_matters": "帮助零基础建立整体结构感。",
                        "best_for": "完全不了解的人。",
                    }
                ],
                recommended_entry_point="这个领域的基础概念与整体组成",
                reason_for_recommendation="它最基础，适合新手作为第一课。",
                missing_items=[],
                next_question="",
                ready_for_board=True,
            )
        if kwargs["user_message"] == "我想学一个领域":
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=(
                    "这个领域可以先看一张地图："
                    "\n1. **整体组成**：先知道它由哪些部分构成。"
                    "\n2. **基础概念**：再挑一个最小入口。"
                    "\n3. **后续实践**：基础站稳后再进入真实任务。"
                    "\n\n我把**整体组成**作为暂定入口，因为它能先帮你建立方向感。"
                    "如果你已经有一点基础，我会继续往具体概念收；如果你完全没接触过，我会先从整体地图开始。"
                    "你之前接触过这个领域吗，还是更接近完全新手？"
                ),
                progress=45,
                summary="用户想学一个宽泛领域。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个宽泛领域",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看整体组成、基础概念和后续实践。",
                entry_point_options=[
                    {
                        "label": "整体组成",
                        "why_it_matters": "帮助建立方向感。",
                        "best_for": "不知道从哪开始的人。",
                    },
                    {
                        "label": "基础概念",
                        "why_it_matters": "帮助形成第一块理解。",
                        "best_for": "想开始的人。",
                    },
                ],
                recommended_entry_point="整体组成",
                reason_for_recommendation="它最适合先建立方向感。",
                next_question="你之前接触过这个领域吗，还是更接近完全新手？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "明白，你是入门新手。这里有三个入口："
                "\n1. **基础概念**：先了解基本对象。"
                "\n2. **进阶规则**：理解规则如何运转。"
                "\n3. **实践工具**：开始做一个小任务。"
                "\n\n这三个入口，哪个最吸引你？或者你愿意先从基础概念开始？"
            ),
            progress=70,
            summary="用户是入门新手，但入口尚未落定。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="一个领域入门",
            current_level="零基础纯新手",
            known_background="用户明确表示入门新手。",
            guidance_strategy="recommended_entry",
            learning_map_summary="入门新手可以先看基础概念、进阶规则和实践工具。",
            entry_point_options=[
                {
                    "label": "基础概念",
                    "why_it_matters": "帮助建立第一块理解。",
                    "best_for": "零基础纯新手。",
                },
                {
                    "label": "进阶规则",
                    "why_it_matters": "帮助理解后续运转方式。",
                    "best_for": "已有一点基础的人。",
                },
                {
                    "label": "实践工具",
                    "why_it_matters": "帮助进入实际任务。",
                    "best_for": "准备动手的人。",
                },
            ],
            recommended_entry_point="基础概念",
            reason_for_recommendation="它最适合入门新手。",
            missing_items=["用户想学的内容需要收敛到具体知识点"],
            next_question="这三个入口，哪个最吸引你？或者你愿意先从基础概念开始？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    first_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个领域"),
        user_id=user_id,
    )
    second_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我是入门新手"),
        user_id=user_id,
    )

    assert len(calls) == 2
    assert "quality_repair_context" not in calls[1]
    assert second_response.requirement_run_id == first_response.requirement_run_id
    assert second_response.active_requirement_sheet is not None
    assert second_response.active_requirement_sheet.learning_goal == "一个领域入门"
    assert second_response.active_requirement_sheet.granularity == "broad_topic"
    assert second_response.active_requirement_sheet.current_questions == [
        "这三个入口，哪个最吸引你？或者你愿意先从基础概念开始？"
    ]
    assert second_response.learning_clarification.ready_for_board is False
    assert second_response.learning_clarification.missing_items == ["用户想学的内容需要收敛到具体知识点"]
    assert second_response.requirement_phase == "collecting"
    assert "哪个最吸引你" in second_response.chatbot_message
    assert "你愿意先从" in second_response.chatbot_message
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is False
    assert discovery["quality_repair_skipped"] is True
    assert any("新手基础入口" in issue for issue in discovery["quality_issues"])
