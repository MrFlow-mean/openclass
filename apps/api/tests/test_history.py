import base64
import sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest

from app.models import (
    BoardDocument,
    BoardFocusRef,
    BoardPatchRequest,
    BoardTaskRequirementSheet,
    LibraryChapter,
    LearningClarificationStatus,
    LearningRequirementSheet,
    PatchOperation,
)
from app.services.chart_generation import extract_chart_data_fragments
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.course_runtime import (
    build_lesson_for_topic,
    effective_requirements,
    normalize_requirements,
    refresh_lesson_runtime,
)
from app.services.document_ops import apply_patch, document_hash, read_board_snapshot
from app.services.board_document_editor import edit_existing_document, generate_from_requirements
from app.services.history import bind_commit_metadata, commit_operations, create_branch, restore_commit, switch_branch
from app.services.html_document_export import export_html
from app.services.lesson_factory import create_empty_lesson, create_lesson
from app.services.openai_course_ai import (
    BoardDocumentEditResult,
    openai_course_ai,
)
from app.services.board_segment_index import build_board_segment_index
from app.services.rich_document import (
    build_document,
    document_to_markdown,
    export_docx,
    import_docx,
    replace_selection_in_document,
    upgrade_markdown_like_document,
)
from app.services.segment_resolver import resolve_board_focus


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_build_lesson_for_topic_creates_blank_lesson_without_ai_runtime() -> None:
    lesson = build_lesson_for_topic("新的学习页")

    assert lesson.title == "新的学习页"
    assert lesson.board_document.content_text == ""
    assert lesson.summary == ""
    assert "具体想学什么" in lesson.learning_requirements.learning_goal
    assert lesson.learning_requirements.current_questions == [
        "你具体想学什么内容，或想解决哪个问题？",
        "你在这个领域目前是什么水平，已经掌握了哪些基础？",
        "你为什么学，之后要面对什么任务、场景或输出要求？",
    ]


def test_refresh_lesson_runtime_uses_local_lesson_factory_only() -> None:
    lesson = create_empty_lesson("本地文档")
    document = build_document(title="本地文档", content_text="第一段\n第二段")

    refresh_lesson_runtime(lesson, document=document)

    assert lesson.board_document.content_text == "第一段\n第二段"
    assert lesson.learning_requirements.theme == "本地文档"
    assert lesson.teaching_guide.lesson_id == lesson.id
    assert effective_requirements(lesson).board_scope == []


def test_normalize_requirements_migrates_legacy_default_clarification() -> None:
    requirements = LearningRequirementSheet(
        theme="旧页面",
        learning_goal="围绕“旧页面”建立可讲授、可复习、可练习的结构化讲义",
        level="根据用户背景和资料难度动态调整",
        known_background="用户背景尚未完全明确，先采用循序渐进的讲解方式",
        current_questions=[
            "“旧页面”的核心问题是什么",
            "它包含哪些关键概念、步骤或例子",
            "学习后如何检查是否真正理解",
        ],
        learning_need_checklist=[],
        target_depth="能复述核心内容，并能用例子解释或完成基础练习",
        output_preference="根据用户目标、资料结构和交互意图动态决定输出形态",
        boundary="优先围绕当前主题展开，不自动跳到无关领域",
        board_scope=[],
        success_criteria="用户能说清主线、解释关键概念，并完成至少一个检查问题",
    )
    normalized = normalize_requirements(
        requirements,
        lesson_title="旧页面",
        document=build_document(title="旧页面"),
    )

    assert normalized.current_questions == [
        "你具体想学什么内容，或想解决哪个问题？",
        "你在这个领域目前是什么水平，已经掌握了哪些基础？",
        "你为什么学，之后要面对什么任务、场景或输出要求？",
    ]
    assert normalized.board_workflow == "generate_from_scratch"
    assert "具体想学什么" in normalized.learning_goal
    assert normalized.success_criteria == ""


def test_apply_patch_updates_target_block_with_snapshot_anchors() -> None:
    document = build_document(title="Test", content_text="# Section\n\nfirst\n\nsecond")
    snapshot = read_board_snapshot(document)
    target = next(block for block in snapshot["blocks"] if block["text"] == "first")

    next_document, diff, validation = apply_patch(
        document,
        BoardPatchRequest(
            source_document_hash=document_hash(document),
            target_scope="focus",
            operations=[
                PatchOperation(
                    op="update_block_content",
                    block_id=target["block_id"],
                    expected_text="first",
                    expected_text_hash=target["text_hash"],
                    content="first, revised",
                )
            ],
            summary="Revise one block.",
            risk_level="medium",
        ),
    )

    assert validation.status == "pass"
    assert next_document.content_text == "# Section\n\nfirst, revised\n\nsecond"
    assert diff[0].op == "update_block_content"
    assert diff[0].before_text == "first"
    assert diff[0].after_text == "first, revised"


def test_apply_patch_inserts_after_target_block() -> None:
    document = build_document(title="Test", content_text="# Section\n\nfirst")
    snapshot = read_board_snapshot(document)
    target = snapshot["blocks"][-1]

    next_document, diff, validation = apply_patch(
        document,
        BoardPatchRequest(
            source_document_hash=document_hash(document),
            target_scope="append",
            operations=[
                PatchOperation(
                    op="insert_block",
                    after_block_id=target["block_id"],
                    content="added block",
                )
            ],
            summary="Add one block.",
            risk_level="low",
        ),
    )

    assert validation.status == "pass"
    assert next_document.content_text.endswith("first\n\nadded block")
    assert diff[0].op == "insert_block"


def test_apply_append_patch_falls_back_to_document_end_for_unresolved_anchor() -> None:
    document = build_document(title="Test", content_text="# Section\n\nfirst")

    next_document, diff, validation = apply_patch(
        document,
        BoardPatchRequest(
            source_document_hash=document_hash(document),
            target_scope="append",
            operations=[
                PatchOperation(
                    op="insert_block",
                    after_block_id="missing-anchor",
                    content="appended despite stale anchor",
                )
            ],
            summary="Append one block.",
            risk_level="low",
        ),
    )

    assert validation.status == "pass"
    assert next_document.content_text.endswith("first\n\nappended despite stale anchor")
    assert diff[0].op == "insert_block"


def test_apply_patch_accepts_nullable_node_path_when_anchor_is_present() -> None:
    document = build_document(title="Test", content_text="# Section\n\nfirst")
    snapshot = read_board_snapshot(document)
    target = snapshot["blocks"][-1]

    next_document, diff, validation = apply_patch(
        document,
        BoardPatchRequest(
            source_document_hash=document_hash(document),
            target_scope="focus",
            operations=[
                PatchOperation.model_validate(
                    {
                        "op": "insert_block",
                        "after_block_id": target["block_id"],
                        "node_path": None,
                        "content": "added near target",
                    }
                )
            ],
            summary="Add one block near the target.",
            risk_level="low",
        ),
    )

    assert validation.status == "pass"
    assert next_document.content_text.endswith("first\n\nadded near target")
    assert diff[0].op == "insert_block"


def test_apply_patch_rejects_stale_hash_and_html_content() -> None:
    document = build_document(title="Test", content_text="first")
    snapshot = read_board_snapshot(document)
    target = snapshot["blocks"][0]

    next_document, diff, validation = apply_patch(
        document,
        BoardPatchRequest(
            source_document_hash="stale",
            operations=[
                PatchOperation(
                    op="update_block_content",
                    block_id=target["block_id"],
                    expected_text="first",
                    content="<p>bad</p>",
                )
            ],
            summary="Invalid patch.",
        ),
    )

    assert validation.status == "failed"
    assert next_document.content_text == document.content_text
    assert diff == []
    assert any("hash" in issue for issue in validation.issues)
    assert any("HTML" in issue for issue in validation.issues)


def test_apply_patch_rejects_delete_without_high_risk_confirmation() -> None:
    document = build_document(title="Test", content_text="first")
    target = read_board_snapshot(document)["blocks"][0]

    next_document, diff, validation = apply_patch(
        document,
        BoardPatchRequest(
            source_document_hash=document_hash(document),
            operations=[
                PatchOperation(
                    op="delete_block",
                    block_id=target["block_id"],
                    expected_text_hash=target["text_hash"],
                )
            ],
            summary="Delete target.",
            risk_level="high",
        ),
    )

    assert validation.status == "failed"
    assert next_document.content_text == document.content_text
    assert diff == []


def test_board_document_editor_prefers_structured_patch_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    lesson = create_empty_lesson("板书编辑")
    lesson.board_document = build_document(title="板书编辑", content_text="# 目标\n\n原始段落")
    requirements = LearningRequirementSheet(
        theme="板书编辑",
        learning_goal="更新当前板书内容",
        level="",
        known_background="",
        current_questions=[],
        target_depth="",
        output_preference="",
        boundary="",
        board_scope=[],
        success_criteria="",
        action_type="rewrite_target",
        action_instruction="把目标段落改得更清楚",
    )

    def _fake_patch_plan(**kwargs):
        target = next(block for block in kwargs["board_snapshot"]["blocks"] if block["text"] == "原始段落")
        return BoardPatchRequest(
            source_commit_id=kwargs["board_snapshot"]["source_commit_id"],
            source_document_hash=kwargs["board_snapshot"]["source_document_hash"],
            target_scope="focus",
            operations=[
                PatchOperation(
                    op="update_block_content",
                    block_id=target["block_id"],
                    expected_text_hash=target["text_hash"],
                    content="更新后的段落",
                )
            ],
            summary="更新目标段落。",
            risk_level="medium",
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_patch_plan", _fake_patch_plan)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_document_edit",
        lambda **kwargs: pytest.fail("Existing-board edit should use the patch planner first."),
    )

    outcome = edit_existing_document(
        lesson=lesson,
        requirements=requirements,
        clarification=LearningClarificationStatus(progress=100, label="ready", reason=""),
        resource_summary="",
        conversation_summary="",
        user_instruction="",
        selection_excerpt="原始段落",
        target_scope="focus",
    )

    assert outcome.changed is True
    assert outcome.operation == "board_patch"
    assert outcome.operations and outcome.operations[0].op == "update_block_content"
    assert outcome.patch_validation is not None
    assert outcome.patch_validation.status == "pass"
    assert "更新后的段落" in outcome.new_document.content_text


def test_branch_and_restore_keep_history() -> None:
    lesson = create_lesson("历史测试")
    first_commit_id = lesson.history_graph.commits[0].id

    create_branch(lesson, "alt-proof", first_commit_id)
    assert lesson.history_graph.current_branch == "alt-proof"
    assert lesson.history_graph.branches["alt-proof"].base_commit_id == first_commit_id

    restore_commit(lesson, first_commit_id, "Restore origin")
    assert lesson.history_graph.branches["alt-proof"].head_commit_id == lesson.history_graph.commits[-1].id
    restore_metadata = lesson.history_graph.commits[-1].metadata
    assert restore_metadata["kind"] == "restore_snapshot"
    assert restore_metadata["restored_commit_id"] == first_commit_id


def test_initial_history_records_requirement_runtime_for_branching() -> None:
    lesson = create_empty_lesson("空白页")

    initial_metadata = lesson.history_graph.commits[0].metadata

    assert initial_metadata["kind"] == "initial_document"
    assert initial_metadata["active_requirement_sheet_after"]["theme"] == "空白页"
    assert initial_metadata["active_interaction_session_after"] is None
    assert initial_metadata["active_board_task_sheet_after"] is None


def test_branch_and_switch_restore_runtime_state_from_commit_metadata() -> None:
    lesson = create_empty_lesson("运行态恢复")
    initial_commit_id = lesson.history_graph.commits[0].id
    board_task = BoardTaskRequirementSheet(
        target_hint="当前板书",
        requested_action="explain",
        question_or_topic="讲解目标",
        progress=80,
    )
    assert board_task.board_workflow == "act_on_existing_board"
    lesson.learning_requirements = None
    lesson.board_task_requirements = board_task
    commit_operations(
        lesson,
        [],
        label="Board task collecting",
        message="Stored an active board task",
        metadata={
            "kind": "chat_flow",
            "board_task_sheet": board_task.model_dump(mode="json"),
            "board_task_cleared": False,
            "active_requirement_sheet_after": None,
            "active_board_task_sheet_after": board_task.model_dump(mode="json"),
        },
    )
    board_task_commit_id = lesson.history_graph.commits[-1].id
    lesson.board_task_requirements = None
    lesson.learning_requirements = None

    create_branch(lesson, "edited-path", initial_commit_id)
    assert lesson.learning_requirements is not None
    assert lesson.learning_requirements.theme == "运行态恢复"
    assert lesson.board_task_requirements is None

    switch_branch(lesson, "main")
    create_branch(lesson, "task-path", board_task_commit_id)
    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is not None
    assert lesson.board_task_requirements.question_or_topic == "讲解目标"


def test_commit_metadata_context_marks_edited_chat_branch() -> None:
    lesson = create_empty_lesson("编辑来源")

    with bind_commit_metadata(
        {
            "chat_edit_source_commit_id": "commit_old",
            "chat_edit_base_commit_id": lesson.history_graph.commits[0].id,
            "chat_edit_original_message": "原问题",
        }
    ):
        commit_operations(
            lesson,
            [],
            label="Chat turn",
            message="Recorded edited chat turn",
            metadata={"kind": "chat_flow", "user_message": "新问题"},
        )

    metadata = lesson.history_graph.commits[-1].metadata
    assert metadata["kind"] == "chat_flow"
    assert metadata["chat_edit_source_commit_id"] == "commit_old"
    assert metadata["chat_edit_base_commit_id"] == lesson.history_graph.commits[0].id
    assert metadata["chat_edit_original_message"] == "原问题"


def test_commit_operations_classifies_chat_history_node() -> None:
    lesson = create_empty_lesson("聊天节点")

    commit_operations(
        lesson,
        [],
        label="Basic chat",
        message="Recorded a basic chatbot conversation turn",
        metadata={
            "kind": "basic_chat",
            "user_message": "讲讲这一段",
            "assistant_message": "这一段可以先看主线。",
            "document_changed": False,
        },
    )

    metadata = lesson.history_graph.commits[-1].metadata
    assert metadata["history_node_kind"] == "chat"
    assert metadata["history_node_title"] == "讲讲这一段"
    assert metadata["history_node_summary"] == "这一段可以先看主线。"


def test_commit_operations_classifies_document_history_node() -> None:
    lesson = create_empty_lesson("文档节点")
    next_document = build_document(title="文档节点", content_text="新的板书内容")

    commit_operations(
        lesson,
        [PatchOperation(op="insert_block", content="新的板书内容")],
        label="Board task write",
        message="Executed an existing-board write task",
        new_document=next_document,
        metadata={
            "kind": "board_document_edit",
            "board_document_editor_summary": "补写目标位置的内容",
            "document_changed": True,
        },
    )

    metadata = lesson.history_graph.commits[-1].metadata
    assert metadata["history_node_kind"] == "document"
    assert metadata["history_node_title"] == "补写目标位置的内容"
    assert metadata["history_node_summary"] == "补写目标位置的内容"


def test_create_empty_lesson_starts_with_blank_rich_document() -> None:
    lesson = create_empty_lesson("空白页")

    assert lesson.board_document.title == "空白页"
    assert lesson.board_document.content_text == ""
    assert lesson.history_graph.commits[0].snapshot.content_text == ""


def test_chart_fragment_rules_choose_pie_for_share_data() -> None:
    document = build_document(title="占比", content_text="渠道A 40%，渠道B 35%，渠道C 25%。")

    fragments = extract_chart_data_fragments(document, request_message="生成占比图")

    assert fragments
    assert fragments[0].chart_type == "饼图 / 环形图"


def test_replace_selection_in_document_replaces_exact_block_without_nested_paragraphs() -> None:
    document = build_document(title="Doc", content_html="<p>第一段内容</p><p>第二段内容</p>")

    replaced = replace_selection_in_document(
        document,
        selection_text="第二段内容",
        replacement_text="新的第二段",
        replacement_html="<p>新的第二段</p>",
    )

    assert "新的第二段" in replaced.content_text
    assert "<p><p>" not in replaced.content_html


def test_replace_selection_in_document_does_not_append_when_selection_is_missing() -> None:
    document = build_document(title="Doc", content_text="# Section\n\n第一段内容")

    replaced = replace_selection_in_document(
        document,
        selection_text="不存在的选区",
        replacement_text="不应该追加的内容",
        replacement_html="<p>不应该追加的内容</p>",
    )

    assert replaced.content_text == document.content_text
    assert replaced.content_html == document.content_html


def test_board_segment_index_builds_machine_directory_from_rich_document() -> None:
    document = build_document(
        title="定位测试",
        content_text="# 主线\n## 形成机制\n这里说明影响因素和形成过程。\n## 示例\n这里给出一个例子。",
    )

    index = build_board_segment_index(document)

    assert index.document_id == document.id
    assert any(segment.kind == "heading" and segment.text == "形成机制" for segment in index.segments)
    paragraph = next(segment for segment in index.segments if "影响因素" in segment.text)
    assert paragraph.heading_path == ["主线", "形成机制"]
    assert paragraph.before_segment_id
    assert paragraph.after_segment_id
    assert index.chunks
    assert any(paragraph.segment_id in chunk.source_segment_ids for chunk in index.chunks)


def test_segment_resolver_uses_generic_semantic_aliases_without_selection() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text="# 主线\n## 形成机制\n这里说明影响因素和形成过程。\n## 示例\n这里给出一个例子。",
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="帮我讲一下为什么会这样",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "影响因素" in resolution.focus.excerpt or "形成机制" in resolution.focus.excerpt
    assert resolution.evidence is not None
    assert resolution.evidence.status == "found"


def test_segment_resolver_uses_generic_semantic_aliases_without_rerank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_search_rerank", lambda **kwargs: None)
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text="# 主线\n## 形成机制\n这里说明影响因素和形成过程。\n## 示例\n这里给出一个例子。",
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="帮我讲一下为什么会这样",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "影响因素" in resolution.focus.excerpt or "形成机制" in resolution.focus.excerpt
    assert resolution.evidence is not None
    assert resolution.evidence.status == "found"
    assert resolution.evidence.candidates[0].score_breakdown["semantic_alias_bonus"] > 0


def test_segment_resolver_keeps_equivalent_semantic_alias_candidates_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_search_rerank", lambda **kwargs: None)
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text="# 主线\n## 形成机制\n这里说明形成机制。\n## 原因来源\n这里说明原因来源。",
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="帮我讲一下为什么会这样",
        action_type="explain_target",
    )

    assert not resolution.resolved
    assert resolution.status == "ambiguous"
    assert resolution.evidence is not None
    assert resolution.evidence.status == "ambiguous"


def test_segment_resolver_uses_board_chunks_for_cross_segment_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_search_rerank", lambda **kwargs: None)
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "## 商业模式\n"
            "商业化路径先看用户转化。\n"
            "订阅制收入来源用于支撑长期运营。"
        ),
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="商业化收入来源",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "收入来源" in resolution.focus.excerpt
    assert resolution.evidence is not None
    assert resolution.evidence.candidates[0].source == "chunk_lexical"
    assert len(resolution.evidence.candidates[0].source_segment_ids) >= 2


def test_segment_resolver_marks_absent_explain_topic_as_content_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_search_rerank", lambda **kwargs: None)
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(title="定位测试", content_text="# 主线\n## 已有内容\n这里只讲已有内容。")
    board_task = BoardTaskRequirementSheet(
        target_hint="全新缺失主题",
        requested_action="explain",
        question_or_topic="全新缺失主题",
        progress=100,
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="全新缺失主题",
        action_type="explain_target",
        board_task=board_task,
    )

    assert not resolution.resolved
    assert resolution.status == "content_absent"
    assert resolution.evidence is not None
    assert resolution.evidence.status == "content_absent"


def test_segment_resolver_uses_numbered_heading_location_without_selection() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "## 1. 起点\n第一节正文。\n"
            "## 2. 推进\n第二节正文。\n"
            "## 3. 例子\n第三节正文。\n"
            "## 4. 检查问题\n第四节正文。"
        ),
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="为我讲解第4节",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "4. 检查问题" in resolution.focus.excerpt
    assert "第四节正文" in resolution.focus.excerpt
    assert resolution.evidence is not None
    assert resolution.evidence.read_context is not None
    assert "第四节正文" in resolution.evidence.read_context.target_excerpt


def test_segment_resolver_uses_exact_multi_level_heading_before_ordinal_fallback() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "## 1.4 核心概念\n父级正文。\n"
            "### 1.4.1 概念引入\n第一段。\n"
            "### 1.4.2 形式构成\n第二段。\n"
            "### 1.4.3 应用场景\n第三段。\n"
            "### 1.4.4 对比示例\n已有例子。\n"
            "## 1.5 练习\n练习正文。"
        ),
    )
    board_task = BoardTaskRequirementSheet(
        location_kind="target_range",
        target_hint="1.4.4 对比示例",
        requested_action="edit",
        question_or_topic="在1.4.4小节多加几个例子",
        progress=100,
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="1.4.4多加几个例子",
        action_type="rewrite_target",
        board_task=board_task,
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "1.4.4 对比示例" in resolution.focus.excerpt
    assert "已有例子" in resolution.focus.excerpt
    assert "1.5 练习" not in resolution.focus.excerpt
    assert resolution.focus.order_end is not None
    assert resolution.focus.order_start is not None
    assert resolution.focus.order_end > resolution.focus.order_start
    assert resolution.focus.segment_id is not None
    assert resolution.evidence is not None
    assert resolution.evidence.read_context is not None
    assert "已有例子" in resolution.evidence.read_context.target_excerpt
    assert resolution.evidence.query_plan.structured_target == "1.4.4"
    assert resolution.evidence.candidates[0].source == "heading_lookup"
    assert resolution.evidence.candidates[0].score_breakdown["heading_ref_exact"] == 0.98


def test_segment_resolver_keeps_parent_heading_ref_separate_from_child_heading() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "## 1.4 核心概念\n父级正文。\n"
            "### 1.4.1 概念引入\n第一段。\n"
            "### 1.4.4 对比示例\n子级正文。"
        ),
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="讲一下1.4小节",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert resolution.focus.heading_path[-1] == "1.4 核心概念"
    assert "父级正文" in resolution.focus.excerpt
    assert "1.4.1 概念引入" in resolution.focus.excerpt
    assert resolution.evidence is not None
    assert resolution.evidence.read_context is not None
    assert resolution.evidence.read_context.target_focus.heading_path[-1] == "1.4 核心概念"
    assert resolution.evidence.candidates[0].source == "heading_lookup"


def test_segment_resolver_uses_numbered_list_item_location_without_selection() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text="# 清单\n1. 确认目标\n2. 拆分任务\n3. 回顾结果",
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="修改第2项",
        action_type="rewrite_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert resolution.focus.kind == "list"
    assert resolution.focus.excerpt == "拆分任务"


def test_segment_resolver_prefers_speaker_turn_over_global_sentence_number() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "## 引言\n第一句背景。第二句不是目标。\n"
            "## 情景对话\n"
            "Sophie: Bonjour, je regardais la carte.\n"
            "Marc: Je pensais prendre un thé.\n"
            "Sophie: Moi, je savais que je voudrais commander un café crème.\n"
            "## 注释\n第一句注释。第二句也不是目标。"
        ),
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="Sophie 第二句说的是什么意思？",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "Sophie: Moi, je savais" in resolution.focus.excerpt
    assert "Sophie 第2次发言" in resolution.focus.display_label
    assert resolution.evidence is not None
    assert resolution.evidence.candidates[0].source == "speaker_turn"


def test_segment_resolver_can_target_sentence_inside_speaker_turn() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "## 情景对话\n"
            "Sophie: Bonjour. Je savais que je voudrais commander un café crème.\n"
            "Marc: Tu hésitais encore."
        ),
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="Sophie 第一次发言的第二句是什么意思？",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert resolution.focus.excerpt == "Sophie: Je savais que je voudrais commander un café crème."
    assert "Sophie 第1次发言第2句" in resolution.focus.display_label
    assert resolution.evidence is not None
    assert resolution.evidence.candidates[0].source == "speaker_sentence"


def test_segment_resolver_can_target_reverse_speaker_turn() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "## 情景对话\n"
            "Marc: Bonjour, je prendrai un café crème.\n"
            "Sophie: Oui, un thé vert, merci.\n"
            "Marc: Bien sûr ! Je pensais que nous irions à la librairie.\n"
            "Sophie: Tu savais que Paul voudrait un roman policier ?\n"
            "Marc: Oui, il me l’avait dit. Il espérait que je lui offrirais le dernier prix Goncourt.\n"
            "Marc: C’était sérieux. Je t’avais promis que ce serait mon invitation."
        ),
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="Marc 说的倒数第二句话是什么意思？",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "Il espérait que je lui offrirais" in resolution.focus.excerpt
    assert "Marc 倒数第2次发言" in resolution.focus.display_label
    assert resolution.evidence is not None
    assert resolution.evidence.query_plan.structured_target == "倒数2句"
    assert resolution.evidence.candidates[0].source == "speaker_turn"


def test_segment_resolver_can_target_last_speaker_turn() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "Sophie: Bonjour.\n"
            "Marc: Salut.\n"
            "Sophie: Je commanderais un café crème."
        ),
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="Sophie 最后一句是什么意思？",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "Je commanderais un café crème" in resolution.focus.excerpt
    assert "Sophie 倒数第1次发言" in resolution.focus.display_label


def test_segment_resolver_can_target_reverse_sentence_without_speaker() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text="# 主线\n第一句说明。第二句是目标。第三句收尾。",
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="倒数第二句是什么意思？",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert resolution.focus.excerpt == "第二句是目标。"
    assert resolution.evidence is not None
    assert resolution.evidence.query_plan.structured_target == "倒数2句"


def test_segment_resolver_can_target_last_paragraph_read_context() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text="# 主线\n\n第一段说明。\n\n第二段目标。",
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="最后一段是什么意思？",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert resolution.focus.excerpt == "第二段目标。"
    assert resolution.evidence is not None
    assert resolution.evidence.read_context is not None
    assert resolution.evidence.read_context.target_excerpt == "第二段目标。"


def test_segment_resolver_can_target_numbered_example_unit() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text="# 主线\n\n例子一：先处理基本情况。\n\n示例二：这里是目标内容。\n\n案例三：用于对照。",
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="讲一下第二个例子",
        action_type="explain_target",
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert "示例二" in resolution.focus.excerpt
    assert "目标内容" in resolution.focus.excerpt
    assert resolution.evidence is not None
    assert resolution.evidence.read_context is not None
    assert resolution.evidence.failure_reason_code == ""


def test_commit_snapshot_is_isolated_from_runtime_document_mutation() -> None:
    lesson = create_empty_lesson("历史快照")
    document = build_document(
        title="历史快照",
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "结构标题"}],
                }
            ],
        },
        content_text="结构标题",
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )

    commit_operations(lesson, [], label="Rich snapshot", message="Save rich document", new_document=document)
    commit = lesson.history_graph.commits[-1]
    lesson.board_document.content_json["content"][0]["type"] = "paragraph"

    assert commit.snapshot is not lesson.board_document
    assert commit.snapshot.content_json["content"][0]["type"] == "heading"


def test_segment_resolver_maps_unverified_task_excerpt_to_real_segment() -> None:
    lesson = create_empty_lesson("定位测试")
    lesson.board_document = build_document(
        title="定位测试",
        content_text=(
            "# 主线\n"
            "## 情景对话\n"
            "Sophie: Bonjour, je regardais la carte.\n"
            "Sophie: Moi, je savais que je voudrais commander un café crème."
        ),
    )
    board_task = BoardTaskRequirementSheet(
        target_hint="Sophie 第二句",
        target_location=BoardFocusRef(excerpt="Sophie: Moi, je savais que je voudrais commander un café crème."),
        requested_action="explain",
        question_or_topic="这句话是什么意思",
        progress=100,
    )

    resolution = resolve_board_focus(
        lesson=lesson,
        user_message="第二句是什么意思？",
        action_type="explain_target",
        board_task=board_task,
    )

    assert resolution.resolved
    assert resolution.focus is not None
    assert resolution.focus.segment_id
    assert "Sophie: Moi, je savais" in resolution.focus.excerpt
    assert resolution.evidence is not None
    assert resolution.evidence.candidates[0].source == "task_location_exact"


def _collect_node_types(node: dict) -> list[str]:
    node_type = node.get("type")
    result = [node_type] if isinstance(node_type, str) else []
    for child in node.get("content", []):
        if isinstance(child, dict):
            result.extend(_collect_node_types(child))
    return result


def _collect_mark_types(node: dict) -> list[str]:
    result = [
        mark.get("type", "")
        for mark in node.get("marks", [])
        if isinstance(mark, dict)
    ]
    for child in node.get("content", []):
        if isinstance(child, dict):
            result.extend(_collect_mark_types(child))
    return result


def test_build_document_converts_markdown_to_word_like_rich_nodes() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "## Dialogue\n"
            "**Speaker A:** Hello there.\n"
            "**Speaker B:** Nice to meet you.\n"
            "- **Goal:** Practice a short exchange\n"
            "\n"
            "| Term | Meaning |\n"
            "| --- | --- |\n"
            "| hello | greeting |"
        ),
    )

    node_types = _collect_node_types(document.content_json)

    assert "<strong>Speaker A:</strong> Hello there." in document.content_html
    assert "<ul><li><strong>Goal:</strong> Practice a short exchange</li></ul>" in document.content_html
    assert "<table>" in document.content_html
    assert "heading" in node_types
    assert "bulletList" in node_types
    assert "table" in node_types
    assert any(
        mark.get("type") == "bold"
        for node in document.content_json["content"]
        for child in node.get("content", [])
        if isinstance(child, dict)
        for mark in child.get("marks", [])
        if isinstance(mark, dict)
    )


def test_build_document_converts_display_math_delimiters_to_block_math() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "## Formula\n"
            "\\[\n"
            "\\lim_{x \\to a} \\frac{f(x)}{g(x)} = \\lim_{x \\to a} \\frac{f'(x)}{g'(x)}\n"
            "\\]\n"
            "After formula."
        ),
    )

    node_types = _collect_node_types(document.content_json)

    assert "blockMath" in node_types
    assert "\\[" not in document.content_html
    assert 'data-type="block-math"' in document.content_html
    assert "\\lim_{x \\to a}" in document.content_html


def test_build_document_keeps_factorial_equation_together() -> None:
    document = build_document(
        title="Doc",
        content_text="$$\nn! = n \\cdot (n-1)! \\quad (n>0)\n$$",
    )

    assert document.content_json["content"] == [
        {
            "type": "blockMath",
            "attrs": {"latex": "n! = n \\cdot (n-1)! \\quad (n>0)"},
        }
    ]
    assert 'data-type="block-math"' in document.content_html


def test_upgrade_markdown_like_document_repairs_split_factorial_equation() -> None:
    legacy = BoardDocument(
        title="Doc",
        content_text="$$\nn! = n \\cdot (n-1)! \\quad (n>0)\n$$",
        content_html=(
            '<p>n! <span data-type="inline-math" data-latex="= n \\cdot (n-1)"></span>'
            r'! \quad (n&gt;0)</p>'
        ),
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "n! "},
                        {"type": "inlineMath", "attrs": {"latex": "= n \\cdot (n-1)"}},
                        {"type": "text", "text": r"! \quad (n>0)"},
                    ],
                },
                {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "A"}]},
                {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "B"}]},
                {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "C"}]},
            ],
        },
    )

    upgraded = upgrade_markdown_like_document(legacy)

    assert upgraded.content_json["content"][0] == {
        "type": "blockMath",
        "attrs": {"latex": "n! = n \\cdot (n-1)! \\quad (n>0)"},
    }
    assert [node["type"] for node in upgraded.content_json["content"][1:]] == ["heading", "heading", "heading"]


def test_build_document_converts_inline_display_delimiters_to_inline_math() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "条件：即 \\[\\lim_{x \\to a} \\frac{f(x)}{g(x)}\\] 必须存在。\n"
            "1. \\[\\lim_{x \\to 0} \\frac{\\tan x}{x}\\]"
        ),
    )

    node_types = _collect_node_types(document.content_json)

    assert "inlineMath" in node_types
    assert "\\[" not in document.content_html
    assert 'data-type="inline-math"' in document.content_html
    assert "\\lim_{x \\to a}" in document.content_html


def test_build_document_keeps_dollar_delimited_prose_as_text() -> None:
    sentence = "$Je me disais que tu allais peut-être oublier notre rendez-vous.$"
    document = build_document(title="Doc", content_text=f"Paul : {sentence}")

    assert "inlineMath" not in _collect_node_types(document.content_json)
    assert 'data-type="inline-math"' not in document.content_html
    assert "Je me disais que tu allais peut-être oublier notre rendez-vous." in document.content_html


def test_build_document_keeps_grammar_arrow_feedback_as_text() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "Corrigé :\n"
            "boirais → hypothèse (si + imparfait)\n"
            "pleuvrait → futur dans le passé (la météo annonçait...)"
        ),
    )

    node_types = _collect_node_types(document.content_json)

    assert "inlineMath" not in node_types
    assert "blockMath" not in node_types
    assert 'data-type="inline-math"' not in document.content_html
    assert "boirais → hypothèse" in document.content_html
    assert "pleuvrait → futur dans le passé" in document.content_html


def test_build_document_keeps_delimited_grammar_feedback_as_text() -> None:
    document = build_document(title="Doc", content_text="$boirais → hypothèse$")

    assert "inlineMath" not in _collect_node_types(document.content_json)
    assert 'data-type="inline-math"' not in document.content_html
    assert "$boirais → hypothèse$" in document.content_html


def test_build_document_still_converts_real_inline_math() -> None:
    document = build_document(title="Doc", content_text="Formule : \\(x^2+y^2=1\\) et $\\frac{1}{2}$.")

    assert "inlineMath" in _collect_node_types(document.content_json)
    assert 'data-type="inline-math"' in document.content_html
    assert "x^2+y^2=1" in document.content_html
    assert "\\frac{1}{2}" in document.content_html


def test_build_document_converts_explicit_numeric_inline_math() -> None:
    document = build_document(
        title="Doc",
        content_text="最小值为 $0$，阈值为 $1.5$，最大值为 $\\log_2 |\\mathcal{Y}|$。",
    )

    assert document.content_html.count('data-type="inline-math"') == 3
    assert 'data-latex="0"' in document.content_html
    assert 'data-latex="1.5"' in document.content_html
    assert "$0$" not in document.content_html


def test_build_document_preserves_display_equation_tags_for_katex_layout() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "$$\n"
            "\\operatorname{Ent}(D) = -\\sum_{k=1}^{|\\mathcal{Y}|} p_k \\log_2 p_k. \\tag{4.1}\n"
            "$$"
        ),
    )

    assert 'data-type="block-math"' in document.content_html
    assert "\\tag{4.1}" in document.content_html


def test_build_document_converts_epsilon_latex_commands_to_inline_math() -> None:
    document = build_document(title="Doc", content_text="任意 $\\varepsilon$ 与 $\\epsilon$ 都应显示为公式。")

    assert document.content_html.count('data-type="inline-math"') == 2
    assert 'data-latex="\\varepsilon"' in document.content_html
    assert 'data-latex="\\epsilon"' in document.content_html


def test_upgrade_markdown_like_document_keeps_epsilon_math_nodes() -> None:
    legacy = BoardDocument(
        title="Doc",
        content_text="任意 $\\varepsilon$ 都应显示为公式。",
        content_html='<p>任意 <span data-type="inline-math" data-latex="\\varepsilon"></span> 都应显示为公式。</p>',
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "任意 "},
                        {"type": "inlineMath", "attrs": {"latex": "\\varepsilon"}},
                        {"type": "text", "text": " 都应显示为公式。"},
                    ],
                }
            ],
        },
    )

    upgraded = upgrade_markdown_like_document(legacy)

    assert "inlineMath" in _collect_node_types(upgraded.content_json)
    assert 'data-latex="\\varepsilon"' in upgraded.content_html


def test_build_document_repairs_raw_epsilon_and_malformed_mixed_math_html() -> None:
    document = build_document(
        title="Doc",
        content_html=(
            "<p>对于任意给定的正数 \\varepsilon，总存在正数 "
            '<span data-type="inline-math" data-latex="\\delta"></span>'
            "，使得当 $0&lt;|x-x_0|&lt;\\delta时，有|f(x)-L|&lt;\\varepsilon$。</p>"
        ),
    )

    assert document.content_html.count('data-type="inline-math"') == 4
    assert 'data-latex="\\varepsilon"' in document.content_html
    assert 'data-latex="0&lt;|x-x_0|&lt;\\delta"' in document.content_html
    assert 'data-latex="|f(x)-L|&lt;\\varepsilon"' in document.content_html
    assert "$0&lt;" not in document.content_html
    assert "时，有" in document.content_html


def test_build_document_rebuilds_json_when_repairing_math_html() -> None:
    stale_json = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "使得当 $0<|x-x_0|<\\delta"},
                    {"type": "text", "text": "时，有"},
                    {"type": "text", "text": "|f(x)-L|<\\varepsilon$。"},
                ],
            }
        ],
    }
    document = build_document(
        title="Doc",
        content_json=stale_json,
        content_html="<p>使得当 $0&lt;|x-x_0|&lt;\\delta时，有|f(x)-L|&lt;\\varepsilon$。</p>",
    )

    json_text = str(document.content_json)
    assert document.content_html.count('data-type="inline-math"') == 2
    assert _collect_node_types(document.content_json).count("inlineMath") == 2
    assert "$0<|x-x_0|" not in json_text
    assert "时，有" in json_text


def test_upgrade_markdown_like_document_rebuilds_stale_json_from_repaired_math_html() -> None:
    legacy = BoardDocument(
        title="Doc",
        content_text="旧正文",
        content_html=(
            '<p>使得当 <span data-type="inline-math" data-latex="0&lt;|x-x_0|&lt;\\delta"></span>'
            '时，有<span data-type="inline-math" data-latex="|f(x)-L|&lt;\\varepsilon"></span>。</p>'
        ),
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "使得当 $0<|x-x_0|<\\delta"},
                        {"type": "text", "text": "时，有"},
                        {"type": "text", "text": "|f(x)-L|<\\varepsilon$。"},
                    ],
                }
            ],
        },
    )

    upgraded = upgrade_markdown_like_document(legacy)

    assert upgraded.content_html.count('data-type="inline-math"') == 2
    assert _collect_node_types(upgraded.content_json).count("inlineMath") == 2
    assert "$0<|x-x_0|" not in str(upgraded.content_json)


def test_build_document_converts_latex_environment_inline_math() -> None:
    document = build_document(
        title="Doc",
        content_text="- 方程组 $$\\begin{cases} x^2 + y^2 + z^2 = 1 \\\\ x + y + z = 0 \\end{cases}$$ 的解集。",
    )

    assert "inlineMath" in _collect_node_types(document.content_json)
    assert 'data-type="inline-math"' in document.content_html
    assert "\\begin{cases}" in document.content_html
    assert "$$\\begin{cases}" not in document.content_html


def test_build_document_converts_polynomial_ring_and_function_notation() -> None:
    document = build_document(
        title="Doc",
        content_text="- 多项式环 $k[x_1, \\dots, x_n]$\n- 交集 $V(xy)$",
    )

    assert "inlineMath" in _collect_node_types(document.content_json)
    assert document.content_html.count('data-type="inline-math"') == 2
    assert "k[x_1, \\dots, x_n]" in document.content_html
    assert "V(xy)" in document.content_html
    assert "$V(xy)$" not in document.content_html


def test_build_document_converts_escaped_set_notation() -> None:
    document = build_document(title="Doc", content_text="解集是 $\\{2, -2\\}$。")

    assert "inlineMath" in _collect_node_types(document.content_json)
    assert 'data-type="inline-math"' in document.content_html
    assert "\\{2, -2\\}" in document.content_html
    assert "$\\{2, -2\\}$" not in document.content_html


def test_build_document_converts_raw_escaped_set_notation_in_prose() -> None:
    document = build_document(
        title="Doc",
        content_text="定义 1（数列极限）设 \\{a_n\\} 是数列，则称数列\\{a_n\\}以A为极限。",
    )

    assert document.content_html.count('data-type="inline-math"') == 2
    assert document.content_html.count('data-latex="\\{a_n\\}"') == 2
    assert "数列\\{a_n\\}" not in document.content_html
    assert _collect_node_types(document.content_json).count("inlineMath") == 2


def test_build_document_converts_coordinate_arrow_and_text_annotation_math() -> None:
    document = build_document(
        title="Doc",
        content_text="点 $(0,0)$，方向 $\\rightarrow$，映射 $V: \\text{理想} \\to \\text{代数簇}$。",
    )

    assert "inlineMath" in _collect_node_types(document.content_json)
    assert document.content_html.count('data-type="inline-math"') == 3
    assert "(0,0)" in document.content_html
    assert "\\rightarrow" in document.content_html
    assert "\\text{理想}" in document.content_html
    assert "$(0,0)$" not in document.content_html


def test_build_document_converts_raw_inline_latex_fragments_in_prose() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "当 x\\neq1 时，函数取值可化简；因此 x\\to1 时可以讨论趋势。\n"
            "> 使得当 0<|x-x_0|<\\delta 时，有 |f(x)-A|<\\varepsilon，"
            "则称当 x\\to x_0 时极限为 A，记作 \\lim_{x\\to x_0} f(x)=A "
            "或 f(x) \\to A (x \\to x_0). $"
        ),
    )

    assert document.content_html.count('data-type="inline-math"') >= 6
    assert 'data-latex="x\\neq1"' in document.content_html
    assert 'data-latex="x\\to1"' in document.content_html
    assert 'data-latex="0&lt;|x-x_0|&lt;\\delta"' in document.content_html
    assert 'data-latex="|f(x)-A|&lt;\\varepsilon"' in document.content_html
    assert 'data-latex="x\\to x_0"' in document.content_html
    assert 'data-latex="\\lim_{x\\to x_0} f(x)=A"' in document.content_html
    assert "$" not in document.content_html


def test_build_document_rebuilds_stale_json_with_raw_inline_latex_fragments() -> None:
    stale_json = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "当 x\\neq1 时，记作 \\lim_{x\\to x_0} f(x)=A. $"}],
            }
        ],
    }

    document = build_document(
        title="Doc",
        content_json=stale_json,
        content_html="<p>当 x\\neq1 时，记作 \\lim_{x\\to x_0} f(x)=A. $</p>",
    )

    assert document.content_html.count('data-type="inline-math"') == 2
    assert _collect_node_types(document.content_json).count("inlineMath") == 2
    assert "$" not in document.content_html
    assert "$" not in str(document.content_json)


def test_build_document_converts_mhchem_latex_commands_to_math_nodes() -> None:
    document = build_document(
        title="Doc",
        content_text="物质式 $\\ce{H2O}$，反应 \\ce{CO2 + C -> 2CO}，单位 \\pu{mol L-1}。",
    )

    assert document.content_html.count('data-type="inline-math"') == 3
    assert 'data-latex="\\ce{H2O}"' in document.content_html
    assert 'data-latex="\\ce{CO2 + C -&gt; 2CO}"' in document.content_html
    assert 'data-latex="\\pu{mol L-1}"' in document.content_html


def test_build_document_removes_orphan_dollars_around_repaired_math_spans() -> None:
    document = build_document(
        title="Doc",
        content_html=(
            '<blockquote><p>使得当 $<span data-type="inline-math" '
            'data-latex="0&lt;|x-x_0|&lt;\\delta"></span>时，恒有'
            '<span data-type="inline-math" data-latex="|f(x)-A|&lt;\\varepsilon"></span>$</p></blockquote>'
        ),
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "blockquote",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "使得当 $"},
                                {"type": "inlineMath", "attrs": {"latex": "0<|x-x_0|<\\delta"}},
                                {"type": "text", "text": "时，恒有"},
                                {"type": "inlineMath", "attrs": {"latex": "|f(x)-A|<\\varepsilon"}},
                                {"type": "text", "text": "$"},
                            ],
                        }
                    ],
                }
            ],
        },
    )

    assert "$" not in document.content_html
    assert "$" not in str(document.content_json)
    assert document.content_html.count('data-type="inline-math"') == 2
    assert _collect_node_types(document.content_json).count("inlineMath") == 2


def test_upgrade_markdown_like_document_repairs_suspicious_math_nodes() -> None:
    sentence = "Je me disais que tu allais peut-être oublier notre rendez-vous."
    legacy = BoardDocument(
        title="Doc",
        content_text=f"Paul : {sentence}",
        content_html=(
            '<p>Paul : <span data-latex="'
            f"{sentence}"
            '" data-type="inline-math"></span></p>'
        ),
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Paul : "},
                        {"type": "inlineMath", "attrs": {"latex": sentence}},
                    ],
                }
            ],
        },
    )

    upgraded = upgrade_markdown_like_document(legacy)

    assert "inlineMath" not in _collect_node_types(upgraded.content_json)
    assert upgraded.content_json["content"][0]["content"][1]["type"] == "text"
    assert upgraded.content_json["content"][0]["content"][1]["text"] == sentence
    assert 'data-type="inline-math"' not in upgraded.content_html
    assert sentence in upgraded.content_html


def test_upgrade_markdown_like_document_repairs_grammar_feedback_math_nodes() -> None:
    feedback = "boirais → hypothèse (si + imparfait)"
    legacy = BoardDocument(
        title="Doc",
        content_text=feedback,
        content_html=f'<p><span data-type="inline-math" data-latex="{feedback}"></span></p>',
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "inlineMath", "attrs": {"latex": feedback}}],
                }
            ],
        },
    )

    upgraded = upgrade_markdown_like_document(legacy)

    assert "inlineMath" not in _collect_node_types(upgraded.content_json)
    assert upgraded.content_json["content"][0]["content"][0]["type"] == "text"
    assert upgraded.content_json["content"][0]["content"][0]["text"] == feedback
    assert 'data-type="inline-math"' not in upgraded.content_html
    assert feedback in upgraded.content_html


def test_document_to_markdown_preserves_rich_structure_for_ai_edit_context() -> None:
    document = build_document(
        title="Doc",
        content_html=(
            "<h2>Dialogue</h2>"
            "<p><strong>Speaker A:</strong> Hello there.</p>"
            "<ul><li><strong>Goal:</strong> Keep structure</li></ul>"
            "<table><tbody><tr><th>Term</th><th>Meaning</th></tr>"
            "<tr><td>hello</td><td>greeting</td></tr></tbody></table>"
        ),
    )

    markdown = document_to_markdown(document)

    assert "## Dialogue" in markdown
    assert "**Speaker A:** Hello there." in markdown
    assert "- **Goal:** Keep structure" in markdown
    assert "| Term | Meaning |" in markdown


def test_replace_selection_preserves_existing_rich_document_structure() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "## Dialogue\n"
            "**Speaker A:** Original target.\n"
            "- **Goal:** Keep structure\n"
            "\n"
            "| Term | Meaning |\n"
            "| --- | --- |\n"
            "| target | selected line |"
        ),
    )

    updated = replace_selection_in_document(
        document,
        selection_text="Speaker A: Original target.",
        replacement_text="Speaker A: Simpler target.",
    )
    node_types = _collect_node_types(updated.content_json)

    assert "Speaker A: Simpler target." in updated.content_text
    assert "Original target" not in updated.content_text
    assert "<h2>" in updated.content_html
    assert "<strong>Goal:</strong> Keep structure" in updated.content_html
    assert "heading" in node_types
    assert "bulletList" in node_types
    assert "table" in node_types
    assert "bold" in _collect_mark_types(updated.content_json)


def test_upgrade_markdown_like_document_repairs_legacy_plain_paragraphs() -> None:
    legacy = BoardDocument(
        title="Doc",
        content_text="## Section\n**Speaker:** Editable line",
        content_html="<h2>Section</h2><p>**Speaker:** Editable line</p>",
        content_json={
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Section"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "**Speaker:** Editable line"}]},
            ],
        },
    )

    upgraded = upgrade_markdown_like_document(legacy)

    assert "<strong>Speaker:</strong> Editable line" in upgraded.content_html
    paragraph = upgraded.content_json["content"][1]
    assert paragraph["content"][0]["text"] == "Speaker:"
    assert paragraph["content"][0]["marks"][0]["type"] == "bold"


def test_upgrade_markdown_like_document_repairs_legacy_display_math() -> None:
    legacy = BoardDocument(
        title="Doc",
        content_text="\\[\n\\lim_{x \\to 0} \\frac{\\sin x}{x}\n\\]",
        content_html="<p>\\[</p><p>\\lim_{x \\to 0} \\frac{\\sin x}{x}</p><p>\\]</p>",
        content_json={
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "\\["}]},
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "\\lim_{x \\to 0} \\frac{\\sin x}{x}"}],
                },
                {"type": "paragraph", "content": [{"type": "text", "text": "\\]"}]},
            ],
        },
    )

    upgraded = upgrade_markdown_like_document(legacy)

    assert "blockMath" in _collect_node_types(upgraded.content_json)
    assert "\\[" not in upgraded.content_html
    assert 'data-type="block-math"' in upgraded.content_html


def test_docx_import_export_roundtrip(tmp_path) -> None:
    document = build_document(title="Doc", content_html="<h1>标题</h1><p>正文</p>")
    export_path = tmp_path / "out.docx"

    export_docx(document, export_path)
    imported = import_docx(export_path, title="Imported")

    assert imported.title == "Imported"
    assert "标题" in imported.content_text
    assert "正文" in imported.content_text


def test_html_export_renders_math_nodes_with_katex_loader(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_html=(
            '<h1>公式讲义</h1>'
            '<p>通项 <span data-type="inline-math" data-latex="a_n=1+\\frac{1}{n}"></span></p>'
            '<div data-type="block-math" data-latex="\\lim_{n\\to\\infty}a_n=1"></div>'
            '<script>alert("x")</script>'
        ),
    )
    export_path = tmp_path / "math.html"

    export_html(document, export_path)

    html_text = export_path.read_text(encoding="utf-8")
    assert "katex.min.css" in html_text
    assert "mhchem.min.js" in html_text
    assert "katex.render" in html_text
    assert 'data-type="inline-math"' in html_text
    assert 'data-type="block-math"' in html_text
    assert "a_n=1+\\frac{1}{n}" in html_text
    assert "\\lim_{n\\to\\infty}a_n=1" in html_text
    assert "<script>alert" not in html_text


def test_docx_export_preserves_math_as_word_omml(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "公式：$a_n=\\frac{1}{n}$，极限 $\\displaystyle\\lim_{x\\to0}\\frac{\\sin x}{x}=1$。\n\n"
            "$$f(x)=\\begin{cases} x^2, & x>0 \\\\ 0, & x=0 \\end{cases}$$"
        ),
    )
    export_path = tmp_path / "math.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    xml_text = document_xml.decode("utf-8")
    math_text = "".join(node.text or "" for node in root.findall(".//m:t", ns))

    assert root.findall(".//m:oMath", ns)
    assert "lim" in math_text
    assert "sin" in math_text
    assert "begin" not in math_text
    assert "displaystyle" not in math_text
    assert "begincases" not in xml_text


def test_docx_export_renders_display_math_as_word_omml(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "$$a_n=1+\\frac{1}{n}$$\n\n"
            "$$f(x)=\\frac{x^2-1}{x-1}$$\n\n"
            "$$\\lim_{n\\to\\infty} a_n=A,\\quad \\lim_{n\\to\\infty} a_n=B \\Longrightarrow A=B$$\n\n"
            "$$\\lim_{x\\to a^-} f(x)=L,\\quad \\lim_{x\\to a^+} f(x)=L "
            "\\Longrightarrow \\lim_{x\\to a} f(x)=L$$"
        ),
    )
    export_path = tmp_path / "structured-math.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    xml_text = document_xml.decode("utf-8")
    visible_text = "".join(node.text or "" for node in root.findall(".//w:t", ns))

    math_text = "".join(node.text or "" for node in root.findall(".//m:t", ns))

    assert root.findall(".//m:oMathPara", ns)
    assert root.findall(".//m:oMath", ns)
    assert root.findall(".//m:f", ns)
    assert root.findall(".//m:sSub", ns)
    assert root.findall(".//m:sSup", ns)
    assert root.findall(".//m:limLow", ns)
    assert "lim" in math_text
    assert "∞" in math_text
    assert "⟹" in math_text
    assert "ₙ" not in visible_text
    assert "Longrightarrow" not in visible_text
    assert "\\Longrightarrow" not in xml_text
    assert "\\frac" not in xml_text


def test_docx_export_renders_calculus_formulas_as_word_omml(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "设 \\(f\\) 在 \\([a,b]\\) 上可积，定义函数\n\n"
            "$$\\Phi(x)=\\int_{a}^{x} f(t)\\,dt,\\quad x\\in[a,b]$$\n\n"
            "$$\\Phi'(x)=f(x),\\quad \\forall x\\in[a,b]$$\n\n"
            "$$\\int_{a}^{b} f(x)\\,dx=F(b)-F(a)$$"
        ),
    )
    export_path = tmp_path / "calculus-math.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    xml_text = document_xml.decode("utf-8")
    math_text = "".join(node.text or "" for node in root.findall(".//m:t", ns))
    visible_text = "".join(node.text or "" for node in root.findall(".//w:t", ns))
    exported_text = math_text + visible_text

    assert root.findall(".//m:oMathPara", ns)
    assert root.findall(".//m:sSubSup", ns)
    assert "Φ(x)" in exported_text
    assert "∫" in exported_text
    assert "a" in math_text
    assert "b" in math_text
    assert "x" in math_text
    assert "∀" in exported_text
    assert "[a,b]" in exported_text
    assert "Phi(x)" not in exported_text
    assert "a ^ x" not in exported_text
    assert "\\(" not in xml_text
    assert "\\[" not in xml_text
    assert "\\Phi" not in xml_text
    assert "\\int" not in xml_text


def test_docx_export_rebuilds_stale_math_html_from_content_text(tmp_path) -> None:
    document = BoardDocument(
        title="Doc",
        content_text=(
            "介点组 \\(\\xi\\) 的黎曼和。\n\n"
            "\\[\n"
            "S_n = \\sum_{i=1}^n f\\!\\left(\\frac{i}{n}\\right)\\frac{1}{n}\n"
            "= \\frac{1}{n} \\sum_{i=1}^n \\frac{i}{n}\n"
            "\\]\n\n"
            "故 \\(C=-F(a)\\)，并讨论 \\(f(x)=|x|\\)。"
        ),
        content_html=(
            "<p>介点组 \\(\\xi\\) 的黎曼和。</p>"
            "<p>\\[</p>"
            "<p>S_n = \\sum_{i=1}^n f\\!\\left(\\frac{i}{n}\\right)\\frac{1}{n}</p>"
            "<p>= \\frac{1}{n} \\sum_{i=1}^n \\frac{i}{n}</p>"
            "<p>\\]</p>"
            "<p>故 \\(C=-F(a)\\)，并讨论 \\(f(x)=|x|\\)。</p>"
        ),
    )
    export_path = tmp_path / "stale-math-html.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    xml_text = document_xml.decode("utf-8")
    math_text = "".join(node.text or "" for node in root.findall(".//m:t", ns))
    visible_text = "".join(node.text or "" for node in root.findall(".//w:t", ns))
    exported_text = math_text + visible_text

    assert root.findall(".//m:oMathPara", ns)
    assert root.findall(".//m:f", ns)
    assert root.findall(".//m:sSubSup", ns)
    assert "ξ" in exported_text
    assert "S" in math_text
    assert "n" in math_text
    assert "C=-F(a)" in exported_text
    assert "f(x)=|x|" in exported_text
    assert "\\(" not in xml_text
    assert "\\[" not in xml_text
    assert "\\xi" not in xml_text
    assert "\\sum" not in xml_text
    assert "\\frac" not in xml_text


def test_docx_export_converts_commands_inside_function_parentheses(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "$$\\|P\\| = \\max_{1\\le i\\le n} \\Delta x_i$$\n\n"
            "$$S(P, \\xi) = \\sum_{i=1}^{n} f(\\xi_i)\\,\\Delta x_i$$\n\n"
            "$$S_n = \\sum_{i=1}^n f\\!\\left(\\frac{i}{n}\\right)\\frac{1}{n}$$"
        ),
    )
    export_path = tmp_path / "parenthesized-commands.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    }
    xml_text = document_xml.decode("utf-8")
    math_text = "".join(node.text or "" for node in root.findall(".//m:t", ns))

    assert root.findall(".//m:oMathPara", ns)
    assert root.findall(".//m:f", ns)
    assert root.findall(".//m:sSubSup", ns)
    assert "‖P‖" in math_text
    assert "ξ" in math_text
    assert "\\xi" not in xml_text
    assert "\\|" not in xml_text
    assert "\\!" not in xml_text
    assert "\\left" not in xml_text
    assert "\\right" not in xml_text


def test_docx_export_preserves_markdown_tables_as_word_tables(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "| x | f(x) | 观察 |\n"
            "| --- | --- | --- |\n"
            "| 1.9 | 3.61 | 接近 4 |\n"
            "| 1.99 | 3.9601 | 更接近 4 |"
        ),
    )
    export_path = tmp_path / "table.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    text = "".join(node.text or "" for node in root.findall(".//w:t", ns))

    assert len(root.findall(".//w:tbl", ns)) == 1
    assert len(root.findall(".//w:tblHeader", ns)) == 1
    assert len(root.findall(".//w:cantSplit", ns)) == 3
    assert "1.99" in text
    assert "|" not in text


def test_docx_export_moves_late_table_to_next_page(tmp_path) -> None:
    leading_paragraphs = "\n\n".join(
        f"铺垫段落 {index}：这是一段用于占据页面空间的普通板书说明，帮助测试表格不要卡在页面底部。"
        for index in range(12)
    )
    document = build_document(
        title="Doc",
        content_text=(
            f"{leading_paragraphs}\n\n"
            "| 步骤 | 说明 |\n"
            "| --- | --- |\n"
            "| 1 | 先观察输入 |\n"
            "| 2 | 再观察输出 |"
        ),
    )
    export_path = tmp_path / "late-table.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }

    assert root.findall(".//w:tbl", ns)
    assert root.findall(".//w:br[@w:type='page']", ns)


def test_build_document_renders_fenced_code_blocks() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "代码示例：\n\n"
            "```rust\n"
            "use std::io;\n\n"
            "fn main() {\n"
            '    println!("请输入您的猜测：");\n'
            "}\n"
            "```"
        ),
    )

    assert "```" not in document.content_html
    assert "<pre><code" in document.content_html
    assert "use std::io;" in document.content_html
    assert any(node.get("type") == "codeBlock" for node in document.content_json.get("content", []))


def test_build_document_indents_flat_fenced_code_blocks() -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "```rust\n"
            "fn main() {\n"
            'println!("hello");\n'
            "loop {\n"
            'println!("guess");\n'
            "}\n"
            "}\n"
            "```"
        ),
    )
    code_nodes = [
        node
        for node in document.content_json.get("content", [])
        if node.get("type") == "codeBlock"
    ]
    assert len(code_nodes) == 1
    code = "".join(
        str(child.get("text") or "")
        for child in code_nodes[0].get("content", [])
        if child.get("type") == "text"
    )
    assert '    println!("hello");' in code
    assert '        println!("guess");' in code


def test_upgrade_markdown_like_document_repairs_flat_code_block_indentation() -> None:
    legacy = BoardDocument(
        id="doc_flat_code",
        title="Doc",
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "codeBlock",
                    "attrs": {"language": "rust"},
                    "content": [
                        {
                            "type": "text",
                            "text": 'fn main() {\nprintln!("hello");\n}',
                        }
                    ],
                }
            ],
        },
        content_html='<pre><code class="language-rust">fn main() {\nprintln!("hello");\n}</code></pre>',
        content_text='```rust\nfn main() {\nprintln!("hello");\n}\n```',
    )

    upgraded = upgrade_markdown_like_document(legacy)
    code = "".join(
        str(child.get("text") or "")
        for child in upgraded.content_json["content"][0]["content"]
        if child.get("type") == "text"
    )
    assert '    println!("hello");' in code


def test_docx_export_strips_text_fence_markers(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "图像化地图：\n\n"
            "```text\n"
            "函数 f(x)\n"
            "↓\n"
            "观察 f(x) 是否靠近 L\n"
            "```"
        ),
    )
    export_path = tmp_path / "code-fence.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    text = "".join(node.text or "" for node in root.findall(".//w:t", ns))

    assert "函数 f(x)" in text
    assert "```" not in text
    assert "text函数" not in text


def test_docx_export_normalizes_complex_board_formulas(tmp_path) -> None:
    document = build_document(
        title="Doc",
        content_text=(
            "ε-δ 定义：\n\n"
            "$$\\forall \\varepsilon > 0, \\ \\exists \\delta > 0, "
            "\\text{使得当 } 0 < |x-a| < \\delta \\text{ 时，有 } |f(x)-L| < \\varepsilon$$\n\n"
            "分段函数：\n\n"
            "$$f(x)=\\begin{cases} x, & x<1 \\\\[4pt] x+1, & x\\ge1 \\end{cases}$$\n\n"
            "练习公式：$$f(x)=\\dfrac{x^2-4}{x-2}$$\n\n"
            "后续公式：$$\\lim_{x\\to \\infty}\\left(1 + \\frac{1}{x}\\right)^x$$"
        ),
    )
    export_path = tmp_path / "complex-math.docx"

    export_docx(document, export_path)

    with ZipFile(export_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    xml_text = document_xml.decode("utf-8")
    math_text = "".join(node.text or "" for node in root.findall(".//m:t", ns))
    visible_text = "".join(node.text or "" for node in root.findall(".//w:t", ns))
    exported_text = math_text + visible_text

    assert "∀" in exported_text
    assert "∃" in exported_text
    assert "使得当" in exported_text
    assert "x≥1" in exported_text
    assert "dfrac" not in exported_text
    assert "\\frac" not in exported_text
    assert "\\quad" not in exported_text
    assert "[4pt]" not in exported_text
    assert "text" not in exported_text
    assert "left" not in exported_text
    assert "right" not in exported_text
    assert "begin" not in exported_text
    assert "dfrac" not in xml_text
