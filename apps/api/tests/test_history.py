import pytest
from docx import Document as DocxDocument
from docx.enum.section import WD_ORIENT
from pypdf import PdfWriter

from app.models import ChatRequest, ConversationTurn, PatchOperation, SelectionRef
from app.services.ai_workflow import classify_scope, course_workflow, match_resources
from app.services.course_runtime import effective_requirements
from app.services.course_store import build_initial_course_package
from app.services.document_ops import apply_patch
from app.services.history import create_branch, restore_commit
from app.services.lesson_factory import create_empty_lesson, create_lesson
from app.services.openai_course_ai import DocumentEditOutput, openai_course_ai
from app.services.resource_library import _keywords_from_text, build_resource_item, extract_reference_context
from app.services.rich_document import build_document, export_docx, import_docx, replace_selection_in_document


@pytest.fixture(autouse=True)
def disable_openai_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "client", None)


def test_apply_patch_is_a_document_snapshot_compatibility_shim() -> None:
    document = build_document(title="Test", content_text="first\nsecond")
    next_document, diff = apply_patch(
        document,
        [PatchOperation(op="update_block_content", content="ignored by rich document mode")],
    )

    assert next_document.content_text == document.content_text
    assert diff == []


def test_branch_and_restore_keep_history() -> None:
    lesson = create_lesson("勾股定理")
    first_commit_id = lesson.history_graph.commits[0].id

    create_branch(lesson, "alt-proof", first_commit_id)
    assert lesson.history_graph.current_branch == "alt-proof"
    assert lesson.history_graph.branches["alt-proof"].base_commit_id == first_commit_id

    restore_commit(lesson, first_commit_id, "Restore origin")
    assert lesson.history_graph.branches["alt-proof"].head_commit_id == lesson.history_graph.commits[-1].id


def test_scope_escalation_detects_out_of_domain_question() -> None:
    lesson = create_lesson("微积分入门")
    assert classify_scope("什么是袋代数中的环和层？什么是光滑？", lesson) == "scope_escalation"
    assert classify_scope("为这些内容出几道习题", lesson) == "in_scope"


def test_create_empty_lesson_starts_with_blank_rich_document() -> None:
    lesson = create_empty_lesson("抽象代数导论")

    assert lesson.board_document.title == "抽象代数导论"
    assert lesson.board_document.content_text == ""
    assert lesson.history_graph.commits[0].snapshot.content_text == ""


def test_workflow_asks_for_clarification_when_request_is_too_vague() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="这里没懂"),
        }
    )

    assert result["needs_clarification"] is True
    assert result["board_decision"].action == "clarify_request"
    assert result.get("document_updated") is False
    assert result["clarification_questions"]


def test_workflow_scores_subject_only_learning_goal_as_35_percent() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我想学法语"),
        }
    )

    assert result["learning_clarification"].progress == 35
    assert result["needs_clarification"] is True


def test_workflow_marks_detailed_learning_goal_as_fully_clarified() -> None:
    package = build_initial_course_package()
    lesson = create_empty_lesson("板书测试")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message=(
                    "我是一名法语的学习者，我的法语水平是B2，词汇是3500左右。"
                    "我要去法国旅游了，你能不能给我生成一篇在法国咖啡厅点餐的一篇情景对话课文，"
                    "要用上关于过去将来时的语法"
                ),
            ),
        }
    )

    assert result["learning_clarification"].progress == 100
    assert result["needs_clarification"] is False
    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True


def test_workflow_can_start_when_user_forces_teaching_before_goal_is_clear() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我想学法语，直接开始教学"),
        }
    )

    assert result["learning_clarification"].progress < 60
    assert result["learning_clarification"].forced_start is True
    assert result["needs_clarification"] is False


def test_workflow_extracts_level_and_goal_from_user_message() -> None:
    package = build_initial_course_package()
    lesson = create_empty_lesson("法语口语")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="教我法语 B2 我想去法国旅游"),
        }
    )

    assert result["learning_requirement_sheet"].level == "B2"
    assert "法国旅游" in result["learning_requirement_sheet"].success_criteria
    assert result["needs_clarification"] is False


def test_workflow_generates_board_for_blank_lesson_when_user_requests_direct_open_lecture(tmp_path) -> None:
    package = build_initial_course_package()
    lesson = create_empty_lesson("cs测试2")
    package.lessons.append(lesson)
    resource_path = tmp_path / "csapp-mini.md"
    resource_path.write_text(
        "\n".join(
            [
                "# Chapter 1",
                "## Section 1.1",
                "Intro 1.1",
                "## Section 1.2",
                "Intro 1.2",
                "# Chapter 2",
                "## Section 2.1",
                "Intro 2.1",
                "## Section 2.2",
                "Intro 2.2",
                "# Chapter 3",
                "## Section 3.1",
                "Intro 3.1",
                "## Section 3.2",
                "Intro 3.2",
                "# Chapter 4",
                "## Section 4.1",
                "Intro 4.1",
                "## Section 4.2",
                "Intro 4.2",
                "# Chapter 5",
                "## Expressing Program Performance",
                "This section explains how to express program performance clearly.",
                "## Program Example",
                "This section walks through a concrete program example.",
            ]
        ),
        encoding="utf-8",
    )
    package.resources.append(build_resource_item(resource_path, "CSAPP mini.md"))

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="为我讲解教材中的第5章第2节的内容，直接开讲"),
        }
    )

    assert result["needs_clarification"] is False
    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert result["selected_reference"] is not None
    assert result["selected_reference"].chapter_title == "Program Example"
    assert result["board_teaching_guide"] is not None
    assert result["teacher_document"].content_text.strip()
    assert "什么水平" not in result["teacher_message"]


def test_workflow_can_answer_without_changing_the_board() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="请解释一下勾股定理的核心公式"),
        }
    )

    assert result["needs_clarification"] is False
    assert result["board_decision"].action == "no_change"
    assert result["document_updated"] is False
    assert result["board_teaching_guide"] is not None
    assert "勾股定理" in result["teacher_message"] or "直角三角形" in result["teacher_message"]


def test_workflow_direct_start_after_brief_clarification_generates_board_for_blank_lesson() -> None:
    package = build_initial_course_package()
    lesson = create_empty_lesson("法考 测试")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="直接开讲",
                conversation=[
                    ConversationTurn(role="user", content="为我讲中华人民共和国民法典是什么"),
                    ConversationTurn(role="assistant", content="你现在大概什么水平，准备用在哪种场景里？"),
                ],
            ),
        }
    )

    assert result["needs_clarification"] is False
    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert result["teacher_document"].content_text.strip()


def test_workflow_formats_teacher_message_into_readable_paragraphs(monkeypatch: pytest.MonkeyPatch) -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]

    monkeypatch.setattr(
        openai_course_ai,
        "generate_teacher_message",
        lambda **kwargs: (
            "勾股定理先抓一件事，它说的不是死记公式，而是直角三角形三条边之间的稳定关系。"
            "为什么重要，因为你后面算距离、判定图形、做几何证明都会反复用到它。"
            "你可以把它理解成一把固定尺子，只要直角确定了，两条边一变，第三条边就被锁定了。"
            "最后你可以自己检查一下，3、4、5 为什么会刚好满足这个关系。"
        ),
    )

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="请解释一下勾股定理的核心公式"),
        }
    )

    assert "\n\n" in result["teacher_message"]
    assert "最后你可以自己检查一下" in result["teacher_message"]


def test_workflow_direct_edit_rewrites_only_selected_excerpt() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]
    excerpt = "在直角三角形中，两条直角边的平方和，等于斜边的平方。"

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="把这一句讲得更易懂",
                interaction_mode="direct_edit",
                selection=SelectionRef(
                    kind="board",
                    lesson_id=lesson.id,
                    excerpt=excerpt,
                ),
            ),
        }
    )

    assert result["needs_clarification"] is False
    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert excerpt not in result["teacher_document"].content_text
    assert "换一种更好懂的说法" in result["teacher_document"].content_text


def test_workflow_direct_edit_enhancement_preserves_original_excerpt() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]
    lesson.board_document = build_document(
        title=lesson.title,
        content_text=(
            "题干：已知函数 f(x) 在区间上单调递增，求参数 a 的取值范围。\n"
            "解题方法：先求导，再根据导数符号分类讨论。\n"
            "课后提醒：注意端点条件。"
        ),
        document_id=lesson.board_document.id,
    )
    excerpt = "题干：已知函数 f(x) 在区间上单调递增，求参数 a 的取值范围。\n解题方法：先求导，再根据导数符号分类讨论。"

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="帮我把题干和解题方法写得更加完善全面",
                interaction_mode="direct_edit",
                selection=SelectionRef(
                    kind="board",
                    lesson_id=lesson.id,
                    excerpt=excerpt,
                ),
            ),
        }
    )

    assert result["needs_clarification"] is False
    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert "题干：已知函数 f(x) 在区间上单调递增，求参数 a 的取值范围。" in result["teacher_document"].content_text
    assert "解题方法：先求导，再根据导数符号分类讨论。" in result["teacher_document"].content_text
    assert "补充解析" in result["teacher_document"].content_text
    assert "课后提醒：注意端点条件。" in result["teacher_document"].content_text


def test_replace_selection_in_document_replaces_exact_block_without_nested_paragraphs() -> None:
    document = build_document(
        title="测试",
        content_html="<h1>测试</h1><p>原始题干</p><p>后续内容</p>",
    )

    next_document = replace_selection_in_document(
        document,
        selection_text="原始题干",
        replacement_text="原始题干\n\n补充说明：先圈出已知条件，再写解题步骤。",
    )

    assert "<p><p>" not in next_document.content_html
    assert "原始题干" in next_document.content_text
    assert "补充说明" in next_document.content_text
    assert "后续内容" in next_document.content_text


def test_workflow_generates_initial_dialogue_document_for_blank_lesson() -> None:
    package = build_initial_course_package()
    lesson = create_empty_lesson("板书测试")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message=(
                    "嗨，我是一名法语学习者，我的法语水平是B2，词汇量3500左右。"
                    "我要去法国旅游，你能不能给我生成一篇法国咖啡厅点餐的一篇情景对话课文，"
                    "要用上关于过去将来的语法"
                ),
            ),
        }
    )

    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert "咖啡厅" in result["teacher_document"].title
    assert "完整双语对话" in result["teacher_document"].content_text
    assert "Je pensais que je prendrais" in result["teacher_document"].content_text


def test_workflow_uses_fast_path_for_clear_generation_request(monkeypatch: pytest.MonkeyPatch) -> None:
    package = build_initial_course_package()
    lesson = create_empty_lesson("板书测试")
    package.lessons.append(lesson)

    monkeypatch.setattr(
        openai_course_ai,
        "assess_learning_requirements",
        lambda **kwargs: pytest.fail("clear generation request should skip PM AI assessment"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_decision",
        lambda **kwargs: pytest.fail("clear generation request should skip board manager AI decision"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_teacher_message",
        lambda **kwargs: pytest.fail("document generation with talk track should skip extra teacher AI call"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_document_edit",
        lambda **kwargs: DocumentEditOutput(
            rationale="fast path",
            replacement_html="<h1>虚拟内存</h1><p>先理解地址空间，再理解页表和缺页异常。</p>",
            replacement_text="虚拟内存\n先理解地址空间，再理解页表和缺页异常。",
            teacher_talk_track="这节不用背定义，我们先抓住虚拟内存是在帮程序和物理内存之间做一层更灵活的映射。",
            replace_whole=True,
        ),
    )

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message=(
                    "我是一名计算机专业学生，请给我生成一版虚拟内存的讲义，"
                    "重点讲地址空间、页表和缺页异常，并带一个入门例子"
                ),
            ),
        }
    )

    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert "虚拟内存" in result["teacher_document"].content_text
    assert "不用背定义" in result["teacher_message"]


def test_workflow_auto_selects_reference_when_one_candidate_is_clearly_best(tmp_path) -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]
    resource_path = tmp_path / "memory-notes.md"
    resource_path.write_text(
        "# 虚拟内存\n虚拟内存这一章主要解释地址空间、页表和缺页异常。\n\n## 地址转换\n地址转换依赖页表。",
        encoding="utf-8",
    )
    package.resources.append(build_resource_item(resource_path, "计算机系统导论笔记.md"))

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="请把虚拟内存这一节整理成更易懂的板书"),
        }
    )

    assert result["board_decision"].action == "edit_board"
    assert result["reference_prompt"] is None
    assert result["selected_reference"] is not None
    assert result["selected_reference"].chapter_title == "虚拟内存"


def test_workflow_prompts_for_reference_when_top_candidates_are_close(tmp_path) -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]
    first_path = tmp_path / "ring1.md"
    first_path.write_text(
        "# 环论\n## 环\n环这一节先解释加法群与乘法封闭。",
        encoding="utf-8",
    )
    second_path = tmp_path / "ring2.md"
    second_path.write_text(
        "# 抽象代数\n## 环\n环这一节重点讲单位元、零因子与理想。",
        encoding="utf-8",
    )
    package.resources.append(build_resource_item(first_path, "环论讲义.md"))
    package.resources.append(build_resource_item(second_path, "抽象代数讲义.md"))

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="请把环这一节整理成更易懂的板书"),
        }
    )

    assert result["board_decision"].action == "await_reference_choice"
    assert result["reference_prompt"] is not None
    assert "要参考它来生成吗" in result["reference_prompt"].question


def test_workflow_uses_selected_reference_after_user_confirms(tmp_path) -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]
    resource_path = tmp_path / "memory-notes.md"
    resource_path.write_text(
        "# 虚拟内存\n虚拟内存用于把程序看到的地址空间和物理内存解耦。\n\n## 页表\n页表记录虚拟页到物理页的映射。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "计算机系统导论笔记.md")
    package.resources.append(resource)
    chapter = resource.outline[0]

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="请把虚拟内存这一节整理成更易懂的板书",
                resource_reference_action="confirm",
                resource_reference_resource_id=resource.id,
                resource_reference_chapter_id=chapter.id,
            ),
        }
    )

    assert result["board_decision"].action == "edit_board"
    assert result["selected_reference"] is not None
    assert result["selected_reference"].chunks
    assert "虚拟内存用于把程序看到的地址空间和物理内存解耦" in result["selected_reference"].full_text
    assert result["document_updated"] is True
    assert result["board_teaching_guide"] is not None


def test_build_resource_item_extracts_image_ocr_text(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    image_path = tmp_path / "math-problem.png"
    image_path.write_bytes(b"fake-image")
    extracted = "设正整数 n >= 2\n求证该数列满足条件"
    monkeypatch.setattr("app.services.resource_library.extract_image_text", lambda path: extracted)

    resource = build_resource_item(image_path, "数学题.png")

    assert resource.extracted_text_available is True
    assert resource.text_content == extracted
    assert resource.outline
    reference = extract_reference_context(resource, resource.outline[0].id, user_query="正整数数列")
    assert reference is not None
    assert "正整数" in reference.full_text


def test_build_resource_item_extracts_docx_outline_and_reference_context(tmp_path) -> None:
    resource_path = tmp_path / "math_solutions.docx"
    document = DocxDocument()
    document.add_heading("函数压轴题答案", level=0)
    document.add_heading("题目分析", level=1)
    document.add_paragraph("先判断单调性，再看零点分布。")
    document.add_heading("参考答案", level=1)
    document.add_paragraph("设 f(x)=x^2-2x+1，然后分类讨论。")
    document.save(resource_path)

    resource = build_resource_item(resource_path, "math_solutions.docx")

    titles = [chapter.title for chapter in resource.outline]
    assert "题目分析" in titles
    chapter = next(chapter for chapter in resource.outline if chapter.title == "题目分析")
    reference = extract_reference_context(resource, chapter.id, user_query="单调性")
    assert reference is not None
    assert "先判断单调性" in reference.full_text


def test_build_resource_item_uses_pdf_outline_ranges_by_hierarchy(tmp_path) -> None:
    resource_path = tmp_path / "csapp.pdf"
    writer = PdfWriter()
    for _ in range(10):
        writer.add_blank_page(width=200, height=200)
    chapter_one = writer.add_outline_item("Chapter 1", 0)
    writer.add_outline_item("Section 1.1", 1, parent=chapter_one)
    writer.add_outline_item("Section 1.2", 4, parent=chapter_one)
    chapter_two = writer.add_outline_item("Chapter 2", 6)
    writer.add_outline_item("Section 2.1", 7, parent=chapter_two)
    with resource_path.open("wb") as target:
        writer.write(target)

    resource = build_resource_item(resource_path, "csapp.pdf")

    chapter_one_outline = next(chapter for chapter in resource.outline if chapter.title == "Chapter 1")
    section_one_one = next(chapter for chapter in resource.outline if chapter.title == "Section 1.1")
    section_one_two = next(chapter for chapter in resource.outline if chapter.title == "Section 1.2")
    chapter_two_outline = next(chapter for chapter in resource.outline if chapter.title == "Chapter 2")

    assert chapter_one_outline.page_start == 1
    assert chapter_one_outline.page_end == 6
    assert section_one_one.page_start == 2
    assert section_one_one.page_end == 4
    assert section_one_two.page_start == 5
    assert section_one_two.page_end == 6
    assert chapter_two_outline.page_start == 7
    assert chapter_two_outline.page_end == 10


def test_build_resource_item_handles_same_page_outline_entries(tmp_path) -> None:
    resource_path = tmp_path / "same-page-outline.pdf"
    writer = PdfWriter()
    for _ in range(10):
        writer.add_blank_page(width=200, height=200)
    chapter_one = writer.add_outline_item("Chapter 1", 0)
    writer.add_outline_item("Section 1 overview", 1, parent=chapter_one)
    writer.add_outline_item("Section 1.1", 1, parent=chapter_one)
    writer.add_outline_item("Section 1.2", 4, parent=chapter_one)
    writer.add_outline_item("Chapter 2", 6)
    with resource_path.open("wb") as target:
        writer.write(target)

    resource = build_resource_item(resource_path, "same-page-outline.pdf")

    section_overview = next(chapter for chapter in resource.outline if chapter.title == "Section 1 overview")
    section_one_one = next(chapter for chapter in resource.outline if chapter.title == "Section 1.1")

    assert section_overview.page_start == 2
    assert section_overview.page_end == 4
    assert section_one_one.page_start == 2
    assert section_one_one.page_end == 4


def test_keywords_from_text_filters_common_english_function_words() -> None:
    keywords = _keywords_from_text("Virtual memory is the way a system maps virtual addresses to physical memory.")

    assert "virtual" in keywords
    assert "memory" in keywords
    assert "to" not in keywords
    assert "is" not in keywords


def test_match_resources_uses_current_board_scope_for_directory_matching(tmp_path) -> None:
    lesson = create_lesson("虚拟内存")
    package = build_initial_course_package()
    package.lessons.append(lesson)
    resource_path = tmp_path / "memory-notes.md"
    resource_path.write_text(
        "# 虚拟内存\n虚拟内存这一章主要解释地址空间、页表和缺页异常。\n\n## 地址转换\n地址转换依赖页表。",
        encoding="utf-8",
    )
    package.resources.append(build_resource_item(resource_path, "计算机系统导论笔记.md"))

    matches = match_resources(
        package,
        lesson,
        ChatRequest(message="把这一节整理得更易懂一点"),
        effective_requirements(lesson),
    )

    assert matches
    assert matches[0].chapter_title == "虚拟内存"


def test_match_resources_uses_requirement_theme_for_cross_language_directory_matching(tmp_path) -> None:
    lesson = create_lesson("虚拟内存")
    package = build_initial_course_package()
    package.lessons.append(lesson)
    resource_path = tmp_path / "csapp.md"
    resource_path.write_text(
        "# Virtual Memory\nVirtual memory explains address translation, page tables, TLBs, and page faults.",
        encoding="utf-8",
    )
    package.resources.append(build_resource_item(resource_path, "CSAPP notes.md"))

    requirements = effective_requirements(lesson)
    requirements.theme = "虚拟内存（板书版，适合 15–25 分钟讲解）"

    matches = match_resources(
        package,
        lesson,
        ChatRequest(message="请把这一节整理成更易懂的板书"),
        requirements,
    )

    assert matches
    assert matches[0].chapter_title == "Virtual Memory"
    assert matches[0].is_high_overlap is True


def test_match_resources_prioritizes_current_request_over_context_noise(tmp_path) -> None:
    lesson = create_lesson("进程与系统")
    lesson.board_document = build_document(
        title="进程与系统",
        content_html="<h1>进程与系统</h1><p>进程、进程、进程、并发、进程调度。</p>",
    )
    package = build_initial_course_package()
    package.lessons.append(lesson)

    vm_path = tmp_path / "vm.md"
    vm_path.write_text("# Virtual Memory\nAddress translation and page tables.", encoding="utf-8")
    proc_path = tmp_path / "proc.md"
    proc_path.write_text("# Concurrent Programming with Processes\nProcesses and concurrency.", encoding="utf-8")
    package.resources.append(build_resource_item(vm_path, "CSAPP VM.md"))
    package.resources.append(build_resource_item(proc_path, "CSAPP PROC.md"))

    requirements = effective_requirements(lesson)
    requirements.theme = "进程与系统"
    requirements.learning_goal = "理解进程调度，但当前这次请求是把虚拟内存这一节整理得更易懂。"

    matches = match_resources(
        package,
        lesson,
        ChatRequest(message="请把虚拟内存这一节整理成更易懂的板书"),
        requirements,
    )

    assert matches
    assert matches[0].chapter_title == "Virtual Memory"


def test_match_resources_understands_numeric_chapter_and_section_reference(tmp_path) -> None:
    lesson = create_empty_lesson("教材测试")
    package = build_initial_course_package()
    package.lessons.append(lesson)
    resource_path = tmp_path / "structured-notes.md"
    resource_path.write_text(
        "\n".join(
            [
                "# 第一章",
                "## 第一节",
                "内容 1.1",
                "## 第二节",
                "内容 1.2",
                "# 第二章",
                "## 第一节",
                "内容 2.1",
                "## 第二节",
                "内容 2.2",
                "# 第三章",
                "## 第一节",
                "内容 3.1",
                "## 第二节",
                "内容 3.2",
                "# 第四章",
                "## 第一节",
                "内容 4.1",
                "## 第二节",
                "内容 4.2",
                "# 第五章",
                "## 第一节",
                "内容 5.1",
                "## 第二节",
                "内容 5.2",
            ]
        ),
        encoding="utf-8",
    )
    package.resources.append(build_resource_item(resource_path, "structured-notes.md"))

    matches = match_resources(
        package,
        lesson,
        ChatRequest(message="请直接讲教材里的第5章第2节"),
        effective_requirements(lesson),
    )

    assert matches
    assert matches[0].chapter_title == "第二节"


def test_docx_import_export_roundtrip(tmp_path) -> None:
    document = build_document(
        title="法国咖啡厅点餐情景对话（含过去将来时）",
        content_html="""
<h1>法国咖啡厅点餐情景对话（含过去将来时）</h1>
<p>完整双语对话。</p>
<p>Je pensais que je prendrais seulement un café.</p>
        """.strip(),
    )
    target = tmp_path / "lesson.docx"

    export_docx(document, target)
    imported = import_docx(target)

    assert target.exists()
    assert "完整双语对话" in imported.content_text
    assert "Je pensais que je prendrais" in imported.content_text


def test_replace_selection_preserves_page_settings() -> None:
    document = build_document(
        title="页面设置保留测试",
        content_text="第一段\n第二段",
        page_settings={
            "orientation": "landscape",
            "margin_preset": "narrow",
            "show_page_number": True,
            "header_text": "课堂讲义",
            "footer_text": "黑板 AI",
        },
    )

    updated = replace_selection_in_document(
        document,
        selection_text="第二段",
        replacement_text="更新后的第二段",
    )

    assert updated.content_text.endswith("更新后的第二段")
    assert updated.page_settings.orientation == "landscape"
    assert updated.page_settings.margin_preset == "narrow"
    assert updated.page_settings.show_page_number is True
    assert updated.page_settings.header_text == "课堂讲义"
    assert updated.page_settings.footer_text == "黑板 AI"


def test_docx_export_applies_basic_page_settings(tmp_path) -> None:
    document = build_document(
        title="导出版式测试",
        content_text="讲义正文",
        page_settings={
            "orientation": "landscape",
            "margin_preset": "wide",
            "header_text": "页眉示例",
            "footer_text": "页脚示例",
        },
    )
    target = tmp_path / "page-settings.docx"

    export_docx(document, target)
    exported = DocxDocument(target)
    section = exported.sections[0]

    assert section.orientation == WD_ORIENT.LANDSCAPE
    assert section.page_width > section.page_height
    assert exported.sections[0].header.paragraphs[0].text == "页眉示例"
    assert exported.sections[0].footer.paragraphs[0].text == "页脚示例"
