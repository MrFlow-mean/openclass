import pytest

from app.models import (
    BoardDocument,
    BoardFocusRef,
    BoardPatchRequest,
    BoardTaskRequirementSheet,
    LearningClarificationStatus,
    LearningRequirementSheet,
    PatchOperation,
)
from app.services.chart_generation import extract_chart_data_fragments
from app.services.course_runtime import (
    build_lesson_for_topic,
    effective_requirements,
    normalize_requirements,
    refresh_lesson_runtime,
)
from app.services.document_ops import apply_patch, document_hash, read_board_snapshot
from app.services.board_document_editor import edit_existing_document
from app.services.history import bind_commit_metadata, commit_operations, create_branch, restore_commit, switch_branch
from app.services.lesson_factory import create_empty_lesson, create_lesson
from app.services.openai_course_ai import GeneratedCatalogChapter, GeneratedResourceCatalog, OpenAICourseAI, openai_course_ai
from app.services.resource_library import _epub_section_body_score, build_resource_item, extract_reference_context
from app.services.resource_resolver import resolve_resource_reference
from app.services.board_segment_index import build_board_segment_index
from app.services.board_task_manager import normalize_board_task_sheet
from app.services.rich_document import (
    build_document,
    document_to_markdown,
    export_docx,
    import_docx,
    replace_selection_in_document,
    upgrade_markdown_like_document,
)
from app.services.segment_resolver import resolve_board_focus


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
    assert effective_requirements(lesson).board_scope == ["第一段", "第二段"]


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
    assert "具体想学什么" in normalized.learning_goal
    assert "应用场景" in normalized.success_criteria


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
    assert resolution.focus.excerpt == "4. 检查问题"


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


def test_board_task_normalization_downgrades_unverified_target_location() -> None:
    sheet = BoardTaskRequirementSheet(
        target_hint="Sophie 第二句",
        target_location=BoardFocusRef(excerpt="Sophie: Moi, je savais que je voudrais commander un café crème."),
        location_status="resolved",
        requested_action="explain",
        question_or_topic="这句话是什么意思",
        progress=100,
        missing_items=[],
    )

    normalized = normalize_board_task_sheet(sheet)

    assert normalized.target_location is not None
    assert normalized.target_location.excerpt.startswith("Sophie:")
    assert normalized.location_status == "missing"
    assert normalized.progress == 100
    assert normalized.missing_items == []


def test_board_task_normalization_keeps_ambiguous_write_location_incomplete() -> None:
    sheet = BoardTaskRequirementSheet(
        target_hint="",
        target_location=None,
        location_status="ambiguous",
        requested_action="write",
        question_or_topic="扩写某个已有内容主题的更多说明",
        progress=100,
        missing_items=[],
    )

    normalized = normalize_board_task_sheet(sheet)

    assert normalized.progress == 75
    assert "目标位置" in normalized.missing_items
    assert normalized.clarification_question


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


def test_build_resource_item_extracts_markdown_outline_and_reference_context(tmp_path) -> None:
    resource_path = tmp_path / "resource.md"
    resource_path.write_text(
        "# 第一章\n这是第一章正文。\n\n## 第一节\n这里有可引用的教学材料。",
        encoding="utf-8",
    )

    resource = build_resource_item(resource_path, "resource.md")
    context = extract_reference_context(resource, resource.outline[0].id, user_query="第一章")

    assert resource.outline
    assert context is not None
    assert context.chapter_title == "第一章"
    assert "第一章正文" in context.full_text


def test_resource_resolver_selects_relevant_uploaded_chapter(tmp_path) -> None:
    resource_path = tmp_path / "resource.md"
    resource_path.write_text(
        "# 第一章\n这是第一章正文，解释资料里的核心概念。\n\n## 第二节\n这里是其他材料。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "resource.md")

    resolution = resolve_resource_reference(
        resources=[resource],
        user_message="根据上传资料讲一下第一章",
        allow_direct_reference=True,
    )

    assert resolution.selected_reference is not None
    assert resolution.selected_reference.chapter_title == "第一章"
    assert "第一章正文" in resolution.selected_reference.full_text
    assert resolution.matches


def test_epub_section_scoring_penalizes_generic_structural_shells() -> None:
    shell_sections = [
        {
            "title": "结构页",
            "level": 1,
            "content": "【目标】\n【练习】\n【复盘】",
        }
    ]
    body_sections = [
        {
            "title": "正文页",
            "level": 1,
            "content": "这里先说明一个核心概念如何在真实任务中使用，并进一步解释它和后续练习之间的关系。然后给出一个可以复述的判断标准。",
        }
    ]

    assert _epub_section_body_score(shell_sections, 0)[0] < 0
    assert _epub_section_body_score(body_sections, 0)[0] > _epub_section_body_score(shell_sections, 0)[0]


def test_build_resource_item_uses_catalog_ai_when_material_has_no_outline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    def _fake_outline(self, *, resource_name: str, extracted_text: str, max_chapters: int = 8):
        return GeneratedResourceCatalog(
            chapters=[
                GeneratedCatalogChapter(
                    title="学习入口",
                    summary="从资料正文生成的目录入口。",
                    keywords=["入口"],
                    level=1,
                )
            ]
        )

    monkeypatch.setattr(OpenAICourseAI, "generate_resource_outline", _fake_outline)
    resource_path = tmp_path / "plain.txt"
    resource_path.write_text("这是一段没有标题的资料正文。" * 20, encoding="utf-8")

    resource = build_resource_item(resource_path, "plain.txt")

    assert resource.outline[0].title == "学习入口"
    assert resource.outline[0].scan_strategy == "fulltext_match"


def test_docx_import_export_roundtrip(tmp_path) -> None:
    document = build_document(title="Doc", content_html="<h1>标题</h1><p>正文</p>")
    export_path = tmp_path / "out.docx"

    export_docx(document, export_path)
    imported = import_docx(export_path, title="Imported")

    assert imported.title == "Imported"
    assert "标题" in imported.content_text
    assert "正文" in imported.content_text
