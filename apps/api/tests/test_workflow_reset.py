import pytest

from app.models import (
    AIModelSelection,
    BoardDocument,
    BoardTeachingProgress,
    ChatRequest,
    GoogleRealtimeSessionRequest,
    LibraryChapter,
    OCRChunkLocator,
    RealtimeConnectRequest,
    RealtimeTranscriptLogRequest,
    ResourceLibraryItem,
    ResourceOCRChunk,
    SelectionRef,
    UserView,
)
from app.routers import realtime as realtime_router
from app.services.ai_workflow import course_workflow
from app.services.course_runtime import build_lesson_for_topic
from app.services.course_store import build_initial_course_package
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import (
    BoardIntentOutput,
    ResourceRelevanceMatchOutput,
    ResourceRelevanceOutput,
    openai_course_ai,
)
from app.services.openai_realtime import openai_realtime_teacher


def _user() -> UserView:
    return UserView(
        id="user_test",
        email="test@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _disable_course_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "assess_learning_requirements", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "decide_board_intent", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "compare_requirements_to_resource_catalog", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_document_edit", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_board_teaching_guide", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_realtime_lecture_guide", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_reading_companion_guide", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_teaching_guide", lambda **kwargs: None)


def test_course_workflow_collects_requirement_sheet_before_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我想学数学"),
        }
    )

    requirements = result["learning_requirement_sheet"]
    assert result["board_decision"].action == "clarify_request"
    assert result["document_updated"] is False
    assert requirements.theme == "数学"
    assert "学习主题：数学" in requirements.learning_need_checklist
    assert result["learning_clarification"].can_start is False
    assert "学习者当前水平/已学背景" in result["learning_clarification"].missing_items


def test_course_workflow_generates_board_when_requirement_is_sufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="我是小学生，我们老师刚刚给我们讲了平方和开方，你能为我讲解一下相关的知识吗？"
            ),
        }
    )

    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert result["learning_clarification"].can_start is True
    assert "平方和开方" in result["teacher_document"].content_text
    assert result["board_teaching_guide"] is not None


def test_course_workflow_replaces_topic_when_user_corrects_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    package = build_initial_course_package()
    lesson = create_empty_lesson("数学")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="其实我不想学数学，我想学化学"),
        }
    )

    requirements = result["learning_requirement_sheet"]
    assert requirements.theme == "化学"
    assert "学习主题：化学" in requirements.learning_need_checklist
    assert all("学习主题：数学" not in item for item in requirements.learning_need_checklist)


def test_course_workflow_routes_to_teach_realtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(intent="teach_realtime", confidence=0.93, reason="用户要口头实时讲解"),
    )
    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我是小学生，我们老师刚刚给我们讲了平方和开方，你像老师一样语音讲一下"),
        }
    )

    assert result["learning_clarification"].can_start is True
    assert result["board_decision"].action == "teach_realtime"
    assert result["document_updated"] is False
    assert result["needs_clarification"] is False
    assert "讲解模式" in result["teacher_message"]
    assert result["teaching_location"].needs_clarification is True


def test_teaching_locator_prefers_selection_for_realtime_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(intent="teach_realtime", confidence=0.93, reason="用户要口头实时讲解"),
    )
    package = build_initial_course_package()
    lesson = create_empty_lesson("英语文章")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="我想学英语单词，我是初中生，为了考试想要讲解型板书，这个单词是什么意思？",
                selection=SelectionRef(kind="board", excerpt="resilient"),
            ),
        }
    )

    location = result["teaching_location"]
    assert result["board_decision"].action == "teach_realtime"
    assert location.source == "selection"
    assert location.target_text == "resilient"
    guide = result["board_teaching_guide"]
    assert guide is not None
    assert guide.realtime_lecture is True
    assert len(guide.section_plans) >= 2
    assert result["board_teaching_progress"].current_section_index == 0
    assert "口播正文" in result["teacher_message"]


def test_teaching_locator_finds_target_in_board_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(intent="teach_realtime", confidence=0.93, reason="用户要口头实时讲解"),
    )
    package = build_initial_course_package()
    lesson = create_empty_lesson("英语文章")
    lesson.board_document = BoardDocument(
        title="英语文章",
        content_text="The resilient student kept practicing after every mistake.",
        content_html="<p>The resilient student kept practicing after every mistake.</p>",
    )
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="给我讲解一下 resilient 这个单词"),
        }
    )

    location = result["teaching_location"]
    assert location.source == "board"
    assert location.target_text == "resilient"
    assert "student" in location.surrounding_text
    assert result["board_teaching_guide"].realtime_lecture is True
    assert result["board_teaching_progress"] is not None


def test_teaching_locator_finds_target_in_image_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(intent="teach_realtime", confidence=0.93, reason="用户要口头实时讲解"),
    )
    package = build_initial_course_package()
    lesson = create_empty_lesson("英语文章")
    package.lessons.append(lesson)
    chapter = LibraryChapter(title="截图文字", summary="英语文章截图", keywords=["resilient"])
    package.resources.append(
        ResourceLibraryItem(
            name="english.png",
            mime_type="image/png",
            resource_type="image",
            size_bytes=10,
            outline=[chapter],
            extracted_text_available=True,
            text_content="The resilient student kept practicing.",
            ocr_chunks=[
                ResourceOCRChunk(
                    text="The resilient student kept practicing.",
                    terms=["resilient", "student"],
                    locator=OCRChunkLocator(x=0.1, y=0.5, width=0.5, height=0.1),
                )
            ],
        )
    )

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我想学英语单词，我是初中生，为了考试想要讲解型板书，请语音讲解 resilient"),
        }
    )

    location = result["teaching_location"]
    assert result["board_decision"].action == "teach_realtime"
    assert location.source == "ocr"
    assert location.resource_id is not None
    assert location.chunk_id is not None
    assert location.locator is not None
    assert result["board_teaching_guide"].realtime_lecture is True


def test_realtime_lecture_continue_advances_section(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(intent="teach_realtime", confidence=0.93, reason="用户要口头实时讲解"),
    )
    package = build_initial_course_package()
    lesson = create_empty_lesson("英语文章")
    lesson.board_document = BoardDocument(
        title="英语文章",
        content_text="The resilient student kept practicing.",
        content_html="<p>The resilient student kept practicing.</p>",
    )
    package.lessons.append(lesson)

    first = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="我想学英语单词，我是初中生，为了考试想要讲解型板书，给我口头讲解 resilient",
                selection=SelectionRef(kind="board", excerpt="resilient"),
            ),
        }
    )
    assert first["board_decision"].action == "teach_realtime"
    guide = first["board_teaching_guide"]
    progress = first["board_teaching_progress"]
    assert guide is not None and progress is not None
    assert len(guide.section_plans) >= 2
    lesson.board_teaching_guide = guide
    lesson.board_teaching_progress = progress

    second = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="继续下一节", teaching_action="continue"),
        }
    )
    assert second["board_decision"].action == "teach_realtime"
    assert second["board_teaching_progress"].current_section_index == 1
    assert "（第 2/" in second["teacher_message"]

    lesson.board_teaching_progress = second["board_teaching_progress"]
    third = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="继续", teaching_action="continue"),
        }
    )
    assert third["board_decision"].action == "no_change"

    lesson.board_teaching_progress = BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash=guide.board_snapshot_hash,
        current_section_index=1,
        completed_section_indexes=[0],
        waiting_for_continue=False,
    )
    restart = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="重来", teaching_action="restart"),
        }
    )
    assert restart["board_teaching_progress"].current_section_index == 0
    assert restart["board_decision"].action == "teach_realtime"


def test_reading_companion_starts_from_current_dialogue_and_takes_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    package = build_initial_course_package()
    lesson = create_empty_lesson("酒店英语对话")
    lesson.board_document = BoardDocument(
        title="酒店英语对话",
        content_text=(
            "客人：你好，我想订一间房。\n"
            "服务员：您好，请问您想订哪一天的房间？\n"
            "客人：明天晚上。\n"
            "服务员：好的，请问住几晚？"
        ),
        content_html=(
            "<p>客人：你好，我想订一间房。</p>"
            "<p>服务员：您好，请问您想订哪一天的房间？</p>"
            "<p>客人：明天晚上。</p>"
            "<p>服务员：好的，请问住几晚？</p>"
        ),
    )
    package.lessons.append(lesson)

    first = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我们来扮演这个情景对话的两个角色，我是客人你是服务员，我们轮流读课文读对话台词。"),
        }
    )

    assert first["board_decision"].action == "reading_companion"
    assert first["document_updated"] is False
    guide = first["board_teaching_guide"]
    assert guide.reading_companion is True
    assert guide.realtime_lecture is False
    assert guide.reading_rule.user_role == "客人"
    assert guide.reading_rule.assistant_role == "服务员"
    assert guide.reading_rule.valid_user_inputs == ["你好，我想订一间房。", "明天晚上。"]
    assert first["board_teaching_progress"].current_section_index == 0
    assert "你先读" in first["teacher_message"]

    lesson.board_teaching_guide = guide
    lesson.board_teaching_progress = first["board_teaching_progress"]
    second = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="你好，我想订一间房"),
        }
    )

    assert second["board_decision"].action == "reading_companion"
    assert "服务员：您好，请问您想订哪一天的房间？" in second["teacher_message"]
    assert second["board_teaching_progress"].current_section_index == 2
    assert second["teaching_progress"].waiting_for_continue is False


def test_reading_companion_exits_to_normal_workflow_when_input_is_out_of_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_course_ai(monkeypatch)
    package = build_initial_course_package()
    lesson = create_empty_lesson("酒店英语对话")
    lesson.board_document = BoardDocument(
        title="酒店英语对话",
        content_text="客人：你好，我想订一间房。\n服务员：您好，请问您想订哪一天的房间？",
        content_html="<p>客人：你好，我想订一间房。</p><p>服务员：您好，请问您想订哪一天的房间？</p>",
    )
    package.lessons.append(lesson)

    first = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我是客人你是服务员，我们轮流读对话。"),
        }
    )
    lesson.board_teaching_guide = first["board_teaching_guide"]
    lesson.board_teaching_progress = first["board_teaching_progress"]

    exited = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="这句话是什么意思？"),
        }
    )

    assert exited["board_decision"].action != "reading_companion"
    assert exited["board_teaching_guide"] is None
    assert lesson.board_teaching_guide is None


def test_course_workflow_routes_to_edit_board_text_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(intent="edit_board_text", confidence=0.88, reason="用户要先生成文字板书"),
    )
    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我是小学生，我们老师刚刚给我们讲了平方和开方，你能为我讲解一下相关的知识吗？"),
        }
    )

    assert result["learning_clarification"].can_start is True
    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True


def test_course_workflow_routes_to_clarify_when_intent_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(
            intent="clarify",
            confidence=0.61,
            reason="用户没有明确说明要口头讲解还是改文档",
            clarification_question="你希望我先进入讲解模式，还是先改板书文字？",
        ),
    )
    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="我是小学生，我们老师刚刚给我们讲了平方和开方，为了考试我想要讲解型板书，先继续吧"
            ),
        }
    )

    assert result["learning_clarification"].can_start is True
    assert result["board_decision"].action == "clarify_request"
    assert result["document_updated"] is False
    assert result["needs_clarification"] is True
    assert result["clarification_questions"] == ["你希望我先进入讲解模式，还是先改板书文字？"]


def test_course_workflow_auto_uses_high_overlap_image_ocr_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(intent="edit_board_text", confidence=0.95, reason="进入文本编辑流程"),
    )

    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)
    chapter = LibraryChapter(
        title="截图文字",
        summary="图片 OCR 提取片段",
        keywords=["平方和开方", "定义"],
    )
    package.resources.append(
        ResourceLibraryItem(
            name="math-note.png",
            mime_type="image/png",
            resource_type="image",
            size_bytes=1024,
            outline=[chapter],
            extracted_text_available=True,
            text_content="平方和开方的定义是把平方运算与开方运算联系起来理解。",
            ocr_chunks=[
                ResourceOCRChunk(
                    text="平方和开方的定义是把平方运算与开方运算联系起来理解。",
                    terms=["平方和开方", "定义", "开方运算"],
                    locator=OCRChunkLocator(x=0.11, y=0.52, width=0.63, height=0.12, page=1),
                    order_index=0,
                )
            ],
        )
    )

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="我想学平方和开方，我是小学生，为了考试我想要讲解型板书，平方和开方的定义是把平方运算与开方运算联系起来理解吗？"
            ),
        }
    )
    assert result["reference_prompt"] is None
    assert result["resource_matches"]
    assert result["resource_matches"][0].resource_name == "math-note.png"
    assert result["resource_matches"][0].is_high_overlap is True
    assert result["resource_matches"][0].matched_chunk_id is not None
    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert result["selected_reference"] is not None
    assert result["selected_reference"].resource_name == "math-note.png"
    assert result["selected_reference"].chunks
    assert result["selected_reference"].chunks[0].locator is not None


def test_catalog_ai_compares_requirement_sheet_with_resource_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "decide_board_intent",
        lambda **kwargs: BoardIntentOutput(intent="edit_board_text", confidence=0.95, reason="进入文本编辑流程"),
    )

    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)
    chapter = LibraryChapter(
        title="函数极限目录",
        summary="包含函数极限、连续性和导数的学习资料目录。",
        keywords=["极限", "连续", "导数"],
    )
    package.resources.append(
        ResourceLibraryItem(
            name="calculus-outline.pdf",
            mime_type="application/pdf",
            resource_type="document",
            size_bytes=2048,
            outline=[chapter],
            extracted_text_available=True,
            text_content="第一章 函数极限。第二章 连续性。第三章 导数。",
        )
    )

    def fake_catalog_ai(**kwargs):
        candidate = kwargs["resource_candidates"][0]
        return ResourceRelevanceOutput(
            matches=[
                ResourceRelevanceMatchOutput(
                    resource_id=candidate["resource_id"],
                    chapter_id=candidate["chapter_id"],
                    score=0.91,
                    reason="该资料目录覆盖学习清单中的函数极限与连续性需求。",
                )
            ]
        )

    monkeypatch.setattr(openai_course_ai, "compare_requirements_to_resource_catalog", fake_catalog_ai)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我想学函数极限，我是大学生，为了考试我想要讲解型板书"),
        }
    )

    assert result["resource_matches"]
    assert result["resource_matches"][0].resource_name == "calculus-outline.pdf"
    assert result["resource_matches"][0].is_high_overlap is True
    assert "学习清单" in result["resource_matches"][0].reason
    assert result["reference_prompt"] is None
    assert result["selected_reference"] is not None
    assert result["selected_reference"].resource_name == "calculus-outline.pdf"
    assert result["document_updated"] is True


def test_lesson_generation_still_creates_blank_lesson_without_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_ai_generation_is_called(*args, **kwargs):  # noqa: ANN002, ANN003
        pytest.fail("blank lesson creation should not call AI document generation")

    monkeypatch.setattr(openai_course_ai, "generate_lesson_document", fail_if_ai_generation_is_called)

    lesson = build_lesson_for_topic("新课堂")

    assert lesson.title == "新课堂"
    assert lesson.board_document.content_text == ""
    assert lesson.teaching_guide.mappings == []


def test_realtime_routes_create_sessions_and_log_events(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user()
    package = build_initial_course_package()
    lesson = package.lessons[0]
    monkeypatch.setattr(realtime_router, "_lesson_for_user", lambda lesson_id, user_id: (package, lesson))
    monkeypatch.setattr(openai_realtime_teacher, "create_call", lambda **kwargs: "answer-sdp")

    openai_response = realtime_router.connect_realtime_session(
        lesson.id,
        RealtimeConnectRequest(
            offer_sdp="v=0",
            realtime_model=AIModelSelection(provider="google", model="gemini-live"),
        ),
        user=user,
    )
    with pytest.raises(Exception) as google_error:
        realtime_router.create_google_realtime_session(
            lesson.id,
            GoogleRealtimeSessionRequest(
                realtime_model=AIModelSelection(
                    provider="google",
                    model="gemini-2.5-flash-native-audio-preview-12-2025",
                )
            ),
            user=user,
        )
    log_response = realtime_router.log_realtime_event(
        lesson.id,
        RealtimeTranscriptLogRequest(
            role="user",
            transport_event_type="transcript",
            transcript="开始讲",
        ),
        user=user,
    )

    assert openai_response.answer_sdp == "answer-sdp"
    assert openai_response.model == "gpt-realtime-mini"
    assert "当前仅支持 OpenAI 实时语音模型" in str(google_error.value.detail)
    assert log_response == {"status": "ok"}
