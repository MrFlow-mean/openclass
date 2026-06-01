import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.constants import PageLabelStyle
from reportlab.pdfgen import canvas

from app.models import BoardDocument, InteractionSession, LearningRequirementSheet, LibraryChapter, PatchOperation, ResourceLibraryItem
from app.services.chart_generation import extract_chart_data_fragments
from app.services.course_runtime import (
    build_lesson_for_topic,
    effective_requirements,
    normalize_requirements,
    refresh_lesson_runtime,
)
from app.services.document_ops import apply_patch
from app.services.history import build_merge_preview, commit_operations, create_branch, merge_branch, restore_commit, switch_branch
from app.services.lesson_factory import create_empty_lesson, create_lesson
from app.services import resource_resolver as resource_resolver_module
from app.services.resource_library import _epub_section_body_score, build_resource_item, extract_reference_context
from app.services.resource_resolver import resolve_resource_reference
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


def _write_pdf_with_outline(path, *, outline_title: str, lines: list[str]) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.bookmarkPage("target")
    pdf.addOutlineEntry(outline_title, "target", level=0)
    y = 760
    for line in lines:
        pdf.drawString(72, y, line)
        y -= 18
    pdf.showPage()
    pdf.save()
    assert PdfReader(str(path)).outline


def _write_pdf_with_toc_and_body(
    path,
    *,
    toc_lines: list[str],
    body_pages: list[list[str]],
    preface_pages: int = 1,
    draw_page_numbers: bool = True,
    set_page_labels: bool = False,
) -> int:
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 760, "Cover")
    pdf.showPage()

    pdf.drawString(72, 760, "Contents")
    y = 730
    for line in toc_lines:
        pdf.drawString(72, y, line)
        y -= 18
    pdf.showPage()

    for index in range(preface_pages):
        pdf.drawString(72, 760, f"Preface {index + 1}")
        pdf.showPage()

    body_first_actual_page = 2 + preface_pages + 1
    for printed_page, lines in enumerate(body_pages, start=1):
        y = 760
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        if draw_page_numbers:
            pdf.drawCentredString(300, 36, str(printed_page))
        pdf.showPage()
    pdf.save()

    if set_page_labels:
        reader = PdfReader(str(path))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.set_page_label(
            body_first_actual_page - 1,
            len(reader.pages) - 1,
            style=PageLabelStyle.DECIMAL,
            start=1,
        )
        labeled_path = path.with_suffix(".labeled.pdf")
        with labeled_path.open("wb") as output:
            writer.write(output)
        labeled_path.replace(path)

    return body_first_actual_page


def _write_pdf_pages(path, pages: list[list[str]]) -> None:
    pdf = canvas.Canvas(str(path))
    for lines in pages:
        y = 760
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        pdf.showPage()
    pdf.save()


def test_build_lesson_for_topic_creates_blank_lesson_without_ai_runtime() -> None:
    lesson = build_lesson_for_topic("新的学习页")

    assert lesson.title == "新的学习页"
    assert lesson.board_document.content_text == ""
    assert lesson.summary
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


def test_apply_patch_is_a_document_snapshot_compatibility_shim() -> None:
    document = build_document(title="Test", content_text="first\nsecond")
    next_document, diff = apply_patch(
        document,
        [PatchOperation(op="update_block_content", content="ignored by rich document mode")],
    )

    assert next_document.content_text == document.content_text
    assert diff == []


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


def test_branch_switch_restores_runtime_state_from_commit_metadata() -> None:
    lesson = create_lesson("分支状态测试")
    first_requirements = LearningRequirementSheet.model_validate(lesson.learning_requirements.model_dump(mode="json"))
    first_requirements.learning_goal = "第一轮学习目标"
    lesson.learning_requirements = first_requirements
    commit_operations(
        lesson,
        [],
        "First chat",
        "First chat update",
        metadata={
            "kind": "chat_flow",
            "user_message": "第一轮问题",
            "active_requirement_sheet_after": first_requirements.model_dump(mode="json"),
            "active_interaction_session_after": None,
        },
    )
    first_chat_commit_id = lesson.history_graph.commits[-1].id

    second_requirements = LearningRequirementSheet.model_validate(first_requirements.model_dump(mode="json"))
    second_requirements.learning_goal = "第二轮学习目标"
    lesson.learning_requirements = second_requirements
    commit_operations(
        lesson,
        [],
        "Second chat",
        "Second chat update",
        metadata={
            "kind": "chat_flow",
            "user_message": "第二轮问题",
            "active_requirement_sheet_after": second_requirements.model_dump(mode="json"),
            "active_interaction_session_after": None,
        },
    )

    create_branch(lesson, "edited-from-first", first_chat_commit_id)
    assert lesson.learning_requirements is not None
    assert lesson.learning_requirements.learning_goal == "第一轮学习目标"

    switch_branch(lesson, "main")
    assert lesson.learning_requirements is not None
    assert lesson.learning_requirements.learning_goal == "第二轮学习目标"


def test_merge_branch_creates_two_parent_commit_and_merges_runtime_state() -> None:
    lesson = create_lesson("合并测试")
    base_commit_id = lesson.history_graph.commits[0].id
    target_requirements = LearningRequirementSheet.model_validate(lesson.learning_requirements.model_dump(mode="json"))
    target_requirements.learning_goal = "目标分支需求"
    target_document = build_document(title="合并测试", content_text="目标分支文档")
    lesson.learning_requirements = target_requirements
    commit_operations(
        lesson,
        [],
        "Target update",
        "Target branch update",
        new_document=target_document,
        metadata={
            "kind": "chat_flow",
            "user_message": "目标分支问题",
            "active_requirement_sheet_after": target_requirements.model_dump(mode="json"),
            "active_interaction_session_after": None,
        },
    )
    target_head_id = lesson.history_graph.branches["main"].head_commit_id

    create_branch(lesson, "source", base_commit_id)
    source_requirements = LearningRequirementSheet.model_validate(lesson.learning_requirements.model_dump(mode="json"))
    source_requirements.learning_goal = "来源分支需求"
    source_session = InteractionSession(interaction_goal="来源分支聊天目标", progress_note="来源分支进展")
    source_document = build_document(title="合并测试", content_text="来源分支文档")
    lesson.learning_requirements = source_requirements
    lesson.active_interaction_session = source_session
    commit_operations(
        lesson,
        [],
        "Source update",
        "Source branch update",
        new_document=source_document,
        metadata={
            "kind": "chat_flow",
            "user_message": "来源分支问题",
            "active_requirement_sheet_after": source_requirements.model_dump(mode="json"),
            "active_interaction_session_after": source_session.model_dump(mode="json"),
        },
    )
    source_head_id = lesson.history_graph.branches["source"].head_commit_id

    preview = build_merge_preview(lesson, "source", "main")
    assert preview.base_commit_id == base_commit_id
    assert preview.target_head_commit_id == target_head_id
    assert preview.source_head_commit_id == source_head_id
    assert preview.document.status == "conflict"
    assert preview.requirements.status == "conflict"
    assert preview.session.status == "source_only"

    merge_branch(
        lesson,
        source_branch="source",
        target_branch="main",
        expected_target_head_commit_id=target_head_id,
        expected_source_head_commit_id=source_head_id,
        document_choice="source",
        requirements_choice="source",
        session_choice="source",
    )

    merge_commit = lesson.history_graph.commits[-1]
    assert merge_commit.metadata["kind"] == "branch_merge"
    assert merge_commit.parent_ids == [target_head_id, source_head_id]
    assert lesson.history_graph.branches["main"].head_commit_id == merge_commit.id
    assert lesson.history_graph.branches["source"].head_commit_id == source_head_id
    assert lesson.board_document.content_text == "来源分支文档"
    assert lesson.learning_requirements is not None
    assert lesson.learning_requirements.learning_goal == "来源分支需求"
    assert lesson.active_interaction_session is not None
    assert lesson.active_interaction_session.interaction_goal == "来源分支聊天目标"

    merged_preview = build_merge_preview(lesson, "source", "main")
    assert merged_preview.already_merged is True
    assert merged_preview.can_merge is False


def test_merge_branch_rejects_stale_head_ids() -> None:
    lesson = create_lesson("过期合并测试")
    base_commit_id = lesson.history_graph.commits[0].id
    target_head_id = lesson.history_graph.branches["main"].head_commit_id
    create_branch(lesson, "source", base_commit_id)
    commit_operations(
        lesson,
        [],
        "Source update",
        "Source branch update",
        new_document=build_document(title="过期合并测试", content_text="来源变化"),
        metadata={"kind": "manual_document_save"},
    )
    source_head_id = lesson.history_graph.branches["source"].head_commit_id

    with pytest.raises(ValueError, match="Target branch changed"):
        merge_branch(
            lesson,
            source_branch="source",
            target_branch="main",
            expected_target_head_commit_id="stale",
            expected_source_head_commit_id=source_head_id,
            document_choice="target",
            requirements_choice="target",
            session_choice="target",
        )
    assert lesson.history_graph.branches["main"].head_commit_id == target_head_id


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


def test_pdf_outline_without_text_is_not_marked_as_extracted(tmp_path) -> None:
    resource_path = tmp_path / "outline-only.pdf"
    _write_pdf_with_outline(resource_path, outline_title="Chapter 15 Rule Structure", lines=[])

    resource = build_resource_item(resource_path, "outline-only.pdf")

    assert resource.outline
    assert resource.extracted_text_available is False
    assert resource.segments == []


def test_pdf_page_text_generates_resource_segments(tmp_path) -> None:
    resource_path = tmp_path / "chapter.pdf"
    _write_pdf_with_outline(
        resource_path,
        outline_title="Chapter 15 Rule Structure",
        lines=[
            "15.3 Pruning Optimization",
            "This section explains pruning evidence used for resource-backed generation.",
        ],
    )

    resource = build_resource_item(resource_path, "chapter.pdf")

    assert resource.extracted_text_available is True
    assert resource.segments
    assert any("Pruning Optimization" in segment.text for segment in resource.segments)
    assert all(segment.parser_name for segment in resource.segments)


def test_resource_page_navigator_extracts_numbered_pdf_subsection(tmp_path) -> None:
    resource_path = tmp_path / "numbered-section.pdf"
    _write_pdf_with_outline(
        resource_path,
        outline_title="Chapter 13 Semi Supervised Learning",
        lines=[
            "13.1 Unlabeled Samples",
            "Figure 13.2 is a figure label, not the target heading.",
            "Equation (13.2) is not a subsection heading.",
            "13.2 Generative Methods",
            "Target body evidence for the requested numbered section.",
            "More target evidence before the next sibling heading.",
            "13.3 Semi-supervised SVM",
            "This later section must not be included.",
        ],
    )
    resource = build_resource_item(resource_path, "numbered-section.pdf")

    resolution = resolve_resource_reference(
        resources=[resource],
        user_message="Please present section 13.2.",
        allow_direct_reference=True,
    )

    assert resolution.selected_reference is not None
    assert resolution.selected_reference.text_evidence_status == "page_navigator"
    assert resolution.matches[0].text_source == "page_navigator"
    assert "Target body evidence" in resolution.selected_reference.full_text
    assert "This later section must not be included" not in resolution.selected_reference.full_text


def test_pdf_toc_printed_page_anchor_maps_to_actual_body_page(tmp_path) -> None:
    resource_path = tmp_path / "toc-anchor.pdf"
    body_first_page = _write_pdf_with_toc_and_body(
        resource_path,
        toc_lines=["1.1 Target Section 3"],
        body_pages=[
            ["Body opening page."],
            ["Body bridge page."],
            ["1.1 Target Section", "Target body evidence from printed page three."],
        ],
    )

    resource = build_resource_item(resource_path, "toc-anchor.pdf")

    assert resource.outline
    assert resource.outline[0].page_start == body_first_page + 2
    assert "page_offset=3" in (resource.outline[0].locator_hint or "")
    assert resource.extracted_text_available is True
    assert any("Target body evidence" in segment.text for segment in resource.segments)


def test_pdf_toc_anchor_window_recovers_nearby_heading(tmp_path) -> None:
    resource_path = tmp_path / "toc-window.pdf"
    body_first_page = _write_pdf_with_toc_and_body(
        resource_path,
        toc_lines=["1.1 Windowed Section 1"],
        body_pages=[
            ["Opening context for this section."],
            ["More context before the heading."],
            ["1.1 Windowed Section", "Windowed target evidence near the printed page anchor."],
        ],
    )

    resource = build_resource_item(resource_path, "toc-window.pdf")

    assert resource.outline
    assert resource.outline[0].page_start == body_first_page
    assert resource.extracted_text_available is True
    assert any("Windowed target evidence" in segment.text for segment in resource.segments)


def test_pdf_toc_uses_explicit_page_labels_as_printed_page_anchor(tmp_path) -> None:
    resource_path = tmp_path / "toc-page-labels.pdf"
    body_first_page = _write_pdf_with_toc_and_body(
        resource_path,
        toc_lines=["1.1 Label Section 2"],
        body_pages=[
            ["Body page with no visible footer."],
            ["1.1 Label Section", "Label-based target evidence from printed page two."],
        ],
        draw_page_numbers=False,
        set_page_labels=True,
    )

    resource = build_resource_item(resource_path, "toc-page-labels.pdf")

    assert resource.outline
    assert resource.outline[0].page_start == body_first_page + 1
    assert "page_offset_support=page_labels" in (resource.outline[0].locator_hint or "")
    assert resource.extracted_text_available is True
    assert any("Label-based target evidence" in segment.text for segment in resource.segments)


def test_short_pdf_without_outline_or_toc_generates_page_range_segments(tmp_path) -> None:
    resource_path = tmp_path / "short-notes.pdf"
    _write_pdf_pages(
        resource_path,
        [
            ["Short note page one has opening evidence."],
            ["Short note page two has the target explanation."],
            ["Short note page three closes the resource."],
        ],
    )

    resource = build_resource_item(resource_path, "short-notes.pdf")

    assert resource.outline
    assert resource.outline[0].scan_strategy == "fulltext_match"
    assert "source=pdf_small_document" in (resource.outline[0].locator_hint or "")
    assert resource.extracted_text_available is True
    assert [segment.page_range for segment in resource.segments] == ["1", "2", "3"]
    assert any("target explanation" in segment.text for segment in resource.segments)


def test_resource_match_exposes_segment_page_range_and_text_source(tmp_path) -> None:
    resource_path = tmp_path / "paged-notes.pdf"
    _write_pdf_pages(
        resource_path,
        [
            ["Opening page."],
            ["Page two target evidence for resolver display."],
        ],
    )
    resource = build_resource_item(resource_path, "paged-notes.pdf")

    resolution = resolve_resource_reference(
        resources=[resource],
        user_message="target evidence resolver display",
        allow_direct_reference=True,
    )

    assert resolution.matches
    assert resolution.matches[0].page_range == "2"
    assert resolution.matches[0].text_source == "source_file"
    assert resolution.selected_reference is not None
    target_chunk = next(
        chunk
        for chunk in resolution.selected_reference.chunks
        if chunk.segment_id == resolution.selected_reference.segment_id
    )
    assert target_chunk.page_range == "2"
    assert target_chunk.text_source == "source_file"


def test_metadata_only_reference_keeps_no_text_evidence_status() -> None:
    resource = ResourceLibraryItem(
        name="outline-only.pdf",
        mime_type="application/pdf",
        resource_type="document",
        size_bytes=1,
        outline=[
            LibraryChapter(
                title="Only Outline",
                summary="Only structural metadata is available.",
                keywords=["target"],
                page_range="12",
                order_index=0,
            )
        ],
        extracted_text_available=False,
    )

    resolution = resolve_resource_reference(
        resources=[resource],
        user_message="target",
        reference_action="confirm",
        reference_resource_id=resource.id,
        reference_chapter_id=resource.outline[0].id,
    )

    assert resolution.selected_reference is not None
    assert resolution.selected_reference.text_evidence_available is False
    assert resolution.selected_reference.text_evidence_status == "metadata_only"
    assert resolution.selected_reference.chunks[0].page_range == "12"
    assert resolution.selected_reference.chunks[0].text_source == "metadata_only"


def test_short_pdf_without_outline_uses_later_text_even_when_first_pages_are_blank(tmp_path) -> None:
    resource_path = tmp_path / "late-text.pdf"
    _write_pdf_pages(
        resource_path,
        [
            [],
            [],
            ["Late page evidence appears after blank opening pages."],
        ],
    )

    resource = build_resource_item(resource_path, "late-text.pdf")

    assert resource.extracted_text_available is True
    assert len(resource.segments) == 1
    assert resource.segments[0].page_range == "3"
    assert "Late page evidence" in resource.segments[0].text


def test_long_pdf_without_outline_does_not_use_small_document_mode(tmp_path) -> None:
    resource_path = tmp_path / "long-notes.pdf"
    long_line = "Long resource body text without a table of contents marker " * 3
    _write_pdf_pages(
        resource_path,
        [
            [
                f"{long_line} page {page_number} line {line_number}"
                for line_number in range(25)
            ]
            for page_number in range(1, 32)
        ],
    )

    resource = build_resource_item(resource_path, "long-notes.pdf")

    assert resource.outline
    assert "source=pdf_small_document" not in (resource.outline[0].locator_hint or "")
    assert resource.extracted_text_available is True
    assert resource.segments
    assert all(segment.page_range is None for segment in resource.segments)


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


def test_resource_resolver_selects_relevant_uploaded_segment(tmp_path) -> None:
    resource_path = tmp_path / "resource.md"
    resource_path.write_text(
        "# 定积分\n这一节先说明面积问题。\n\n牛顿莱布尼茨公式连接原函数与定积分，是正文里的目标片段。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "resource.md")

    resolution = resolve_resource_reference(
        resources=[resource],
        user_message="我要学牛顿莱布尼茨公式",
        allow_direct_reference=True,
    )

    assert resolution.selected_reference is not None
    assert resolution.selected_reference.segment_id is not None
    assert resolution.selected_reference.chapter_title == "定积分"
    assert "牛顿莱布尼茨公式" in resolution.selected_reference.full_text
    assert resolution.matches[0].segment_id == resolution.selected_reference.segment_id
    assert "牛顿莱布尼茨公式" in resolution.matches[0].excerpt
    assert "正文片段" in {item.label for item in resolution.matches[0].evidence}
    assert resolution.matches[0].score_breakdown["rerank"] > 0


def test_resource_resolver_uses_embedding_similarity_without_word_overlap(monkeypatch, tmp_path) -> None:
    resource_path = tmp_path / "resource.md"
    resource_path.write_text(
        "# 运动规律\n沿闭合曲线反复移动时，方向会持续改变。\n\n# 能量转换\n热量和做功都可以改变系统状态。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "resource.md")
    updated_segments = []
    for segment in resource.segments:
        vector = [1.0, 0.0] if "闭合曲线" in segment.text else [0.0, 1.0]
        updated_segments.append(
            segment.model_copy(
                update={
                    "embedding": vector,
                    "embedding_provider": "openai",
                    "embedding_model": "test-embedding",
                }
            )
        )
    resource.segments = updated_segments
    monkeypatch.setattr(
        resource_resolver_module.resource_embedding_service,
        "embed_query",
        lambda query: [1.0, 0.0],
    )

    resolution = resolve_resource_reference(
        resources=[resource],
        user_message="讲讲绕圈时速度朝哪里",
        allow_direct_reference=True,
    )

    assert resolution.selected_reference is not None
    assert resolution.selected_reference.chapter_title == "运动规律"
    assert "闭合曲线" in resolution.selected_reference.full_text
    assert "语义向量" in resolution.matches[0].reason
    assert resolution.matches[0].score_breakdown["semantic"] == 1.0


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


def test_build_resource_item_uses_deterministic_entry_when_material_has_no_outline(tmp_path) -> None:
    resource_path = tmp_path / "plain.txt"
    resource_path.write_text("这是一段没有标题的资料正文。" * 20, encoding="utf-8")

    resource = build_resource_item(resource_path, "plain.txt")

    assert resource.outline[0].title == "plain"
    assert resource.outline[0].scan_strategy == "fulltext_match"
    assert resource.extracted_text_available is True
    assert resource.text_content


def test_docx_import_export_roundtrip(tmp_path) -> None:
    document = build_document(title="Doc", content_html="<h1>标题</h1><p>正文</p>")
    export_path = tmp_path / "out.docx"

    export_docx(document, export_path)
    imported = import_docx(export_path, title="Imported")

    assert imported.title == "Imported"
    assert "标题" in imported.content_text
    assert "正文" in imported.content_text
