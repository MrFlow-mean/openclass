import pytest

from app.models import ChatRequest, PatchOperation
from app.services.ai_workflow import course_workflow
from app.services.chart_generation import extract_chart_data_fragments
from app.services.course_runtime import build_lesson_for_topic, effective_requirements, refresh_lesson_runtime
from app.services.document_ops import apply_patch
from app.services.history import create_branch, restore_commit
from app.services.lesson_factory import create_empty_lesson, create_lesson
from app.services.openai_course_ai import GeneratedCatalogChapter, GeneratedResourceCatalog, OpenAICourseAI
from app.services.resource_library import build_resource_item, extract_reference_context
from app.services.rich_document import build_document, export_docx, import_docx, replace_selection_in_document


def test_workflow_runtime_creates_generic_board_entry() -> None:
    lesson = create_empty_lesson("测试主题")

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "request": ChatRequest(message="我想先建立一个学习入口"),
            "resources": [],
        }
    )

    assert result.board_decision.action == "edit_board"
    assert result.learning_requirement_sheet.learning_need_checklist
    assert "学习入口" in lesson.board_document.content_text


def test_workflow_teaches_from_relevant_board_without_editing() -> None:
    lesson = create_empty_lesson("工作流")
    lesson.board_document = build_document(title="工作流", content_text="入口条件\n处理步骤\n输出结果")

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "request": ChatRequest(message="处理步骤怎么理解"),
            "resources": [],
        }
    )

    assert result.board_decision.action == "no_change"
    assert result.document_changed is False
    assert "处理步骤" in result.teacher_message


def test_workflow_asks_before_using_matched_resource(tmp_path) -> None:
    resource_path = tmp_path / "resource.md"
    resource_path.write_text(
        "# 入口条件\n这里说明如何识别进入学习流程前需要确认的信息。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "resource.md")
    lesson = create_empty_lesson("工作流")

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "request": ChatRequest(message="入口条件怎么整理"),
            "resources": [resource],
        }
    )

    assert result.board_decision.action == "await_reference_choice"
    assert result.reference_prompt is not None
    assert result.reference_prompt.chapter_id == resource.outline[0].id
    assert result.document_changed is False


def test_workflow_writes_board_after_confirmed_resource(tmp_path) -> None:
    resource_path = tmp_path / "resource.md"
    resource_path.write_text(
        "# 输出结果\n这里说明学习流程最后应该留下可复习的记录。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "resource.md")
    lesson = create_empty_lesson("工作流")

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "request": ChatRequest(
                message="输出结果怎么整理",
                resource_reference_action="confirm",
                resource_reference_resource_id=resource.id,
                resource_reference_chapter_id=resource.outline[0].id,
            ),
            "resources": [resource],
        }
    )

    assert result.board_decision.action == "edit_board"
    assert result.selected_reference is not None
    assert result.document_changed is True
    assert "可复习的记录" in lesson.board_document.content_text


def test_build_lesson_for_topic_creates_blank_lesson_without_ai_runtime() -> None:
    lesson = build_lesson_for_topic("新的学习页")

    assert lesson.title == "新的学习页"
    assert lesson.board_document.content_text == ""
    assert lesson.summary


def test_refresh_lesson_runtime_uses_local_lesson_factory_only() -> None:
    lesson = create_empty_lesson("本地文档")
    document = build_document(title="本地文档", content_text="第一段\n第二段")

    refresh_lesson_runtime(lesson, document=document)

    assert lesson.board_document.content_text == "第一段\n第二段"
    assert lesson.learning_requirements.theme == "本地文档"
    assert lesson.teaching_guide.lesson_id == lesson.id
    assert effective_requirements(lesson).board_scope == ["第一段", "第二段"]


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
