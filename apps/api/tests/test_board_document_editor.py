import base64
import hashlib

from app.models import BoardFocusRef, LearningClarificationStatus, RetrievalVisualEvidence
from app.services import board_visual_insertion
from app.services.board_asset_store import BoardAssetStore
from app.services.board_document_editor import edit_existing_document, generate_from_requirements
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardDocumentEditResult, openai_course_ai
from app.services.rich_document import build_document, rebuild_document_from_content_json


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_generate_from_requirements_saves_first_model_output_without_quality_retry(monkeypatch) -> None:
    lesson = create_empty_lesson("一次生成")
    requirements = lesson.learning_requirements
    assert requirements is not None
    requirements.learning_goal = "生成一份板书"
    requirements.level = "入门"
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="ready",
        ready_for_board=True,
    )
    calls: list[dict[str, object]] = []

    def _fake_board_edit(**kwargs):
        calls.append(kwargs)
        return BoardDocumentEditResult(
            operation="replace_document",
            title="一次生成",
            content_text="# 一次生成\n\n## 1.1 概念引入\n\n保留第一稿 displaystyle。",
            summary="已生成。",
            chatbot_message="已生成。",
            section_titles=["1.1 概念引入"],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)

    outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=clarification,
    )

    assert outcome.changed is True
    assert len(calls) == 1
    assert "board_generation_quality_pipeline" not in calls[0]["learning_requirement_context"]
    assert "document_quality_repair" not in calls[0]["learning_requirement_context"]
    assert "displaystyle" in outcome.new_document.content_text


def test_generate_from_requirements_places_two_confirmed_visuals_at_exact_markers(
    monkeypatch,
    tmp_path,
) -> None:
    lesson = create_empty_lesson("视觉证据")
    requirements = lesson.learning_requirements
    assert requirements is not None
    requirements.learning_goal = "根据确认资料生成板书"
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="ready",
        ready_for_board=True,
    )
    content_hash = hashlib.sha256(_PNG).hexdigest()
    visuals = [
        RetrievalVisualEvidence(
            visual_id=f"visual_{index}",
            source_ingestion_id="source_1",
            source_title="确认资料",
            chapter_id="chapter_1",
            kind="chart",
            order_index=index,
            page_no=index,
            page_range=f"p. {index}",
            before_chunk_id=f"chunk_{index}_before",
            after_chunk_id=f"chunk_{index}_after",
            caption=f"图表 {index}",
            anchor_status="verified",
            asset_hash=content_hash,
            anchor_hash=hashlib.sha256(f"position-{index}".encode()).hexdigest(),
            mime_type="image/png",
        )
        for index in (1, 2)
    ]

    def _fake_board_edit(**kwargs):
        manifest = kwargs["visual_manifest"]
        first, second = manifest
        return BoardDocumentEditResult(
            operation="replace_document",
            title="视觉证据",
            content_text=(
                "# 视觉证据\n\n"
                f"第一个唯一段落。\n\n{first['marker']}\n\n"
                f"第二个唯一段落。\n\n{second['marker']}\n\n结尾。"
            ),
            summary="已生成。",
            chatbot_message="已生成。",
            section_titles=["视觉证据"],
            visual_placements=[
                {
                    "visual_id": item["visual_id"],
                    "marker": item["marker"],
                    "target_text_anchor": f"第{label}个唯一段落。",
                    "source_before_chunk_id": item["source_before_chunk_id"],
                    "source_after_chunk_id": item["source_after_chunk_id"],
                    "reason": "图表对应相邻正文。",
                }
                for item, label in zip(manifest, ("一", "二"), strict=True)
            ],
        )

    asset_store = BoardAssetStore(tmp_path / "openclass.sqlite3", tmp_path / "board-assets")
    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)
    monkeypatch.setattr(board_visual_insertion, "get_board_asset_store", lambda: asset_store)

    outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=clarification,
        owner_user_id="owner_1",
        confirmed_visuals=visuals,
        visual_bytes_resolver=lambda visual_id: (next(item for item in visuals if item.visual_id == visual_id), _PNG),
    )

    assert outcome.changed is True
    assert outcome.applied_visual_ids == ("visual_1", "visual_2")
    nodes = outcome.new_document.content_json["content"]
    assert [node["type"] for node in nodes] == [
        "heading",
        "paragraph",
        "resourceVisualBlock",
        "paragraph",
        "resourceVisualBlock",
        "paragraph",
    ]
    assert [node["attrs"]["visualId"] for node in nodes if node["type"] == "resourceVisualBlock"] == [
        "visual_1",
        "visual_2",
    ]
    assert "OPENCLASS_VISUAL" not in outcome.new_document.content_text


def test_local_visual_edit_rejects_model_append_outside_target(monkeypatch) -> None:
    lesson = create_empty_lesson("Existing board")
    requirements = lesson.learning_requirements
    assert requirements is not None
    requirements.action_type = "rewrite_target"
    requirements.action_instruction = "Revise only the selected paragraph."
    lesson.board_document = build_document(
        title="Existing board",
        content_text="# Board\n\nTarget paragraph.\n\nOutside paragraph.",
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    visual = RetrievalVisualEvidence(
        visual_id="visual_1",
        source_ingestion_id="source_1",
        source_title="Reference",
        chapter_id="chapter_1",
        kind="chart",
        order_index=1,
        before_chunk_id="chunk_before",
        after_chunk_id="chunk_after",
        caption="Chart",
        anchor_status="verified",
        asset_hash=hashlib.sha256(_PNG).hexdigest(),
        anchor_hash=hashlib.sha256(b"position").hexdigest(),
        mime_type="image/png",
    )

    def _fake_board_edit(**kwargs):
        marker = kwargs["visual_manifest"][0]["marker"]
        return BoardDocumentEditResult(
            operation="append_section",
            content_text=f"Appended text.\n\n{marker}",
            summary="Appended outside the target.",
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)

    outcome = edit_existing_document(
        lesson=lesson,
        requirements=requirements,
        clarification=LearningClarificationStatus(
            progress=100,
            label="ready",
            reason="ready",
        ),
        resource_summary="Reference context",
        conversation_summary="",
        user_instruction="Revise only the selected paragraph.",
        selection_excerpt="Target paragraph.",
        focus=BoardFocusRef(excerpt="Target paragraph."),
        target_scope="focus",
        owner_user_id="owner_1",
        confirmed_visuals=[visual],
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
    )

    assert outcome.changed is False
    assert outcome.new_document == lesson.board_document
    assert "Outside paragraph." in outcome.new_document.content_text
    assert "Appended text." not in outcome.new_document.content_text


def test_local_visual_edit_inserts_in_target_and_preserves_existing_outside_asset(
    monkeypatch,
    tmp_path,
) -> None:
    lesson = create_empty_lesson("Existing board")
    requirements = lesson.learning_requirements
    assert requirements is not None
    requirements.action_type = "rewrite_target"
    requirements.action_instruction = "Revise only the selected paragraph."
    lesson.board_document = rebuild_document_from_content_json(
        build_document(
            title="Existing board",
            content_text="placeholder",
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        ),
        {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "Board"}],
                },
                {"type": "paragraph", "content": [{"type": "text", "text": "Before target."}]},
                {
                    "type": "resourceVisualBlock",
                    "attrs": {
                        "visualId": "visual_existing",
                        "assetId": "basset_existing_asset",
                        "caption": "Existing chart",
                        "originalSrc": "/api/board-assets/basset_existing_asset/content",
                        "sourceIngestionId": "source_existing",
                    },
                },
                {"type": "paragraph", "content": [{"type": "text", "text": "Target paragraph."}]},
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Outside literal [[OPENCLASS_VISUAL_legacy_0000]] must stay.",
                        }
                    ],
                },
            ],
        },
    )
    visual = RetrievalVisualEvidence(
        visual_id="visual_new",
        source_ingestion_id="source_1",
        source_title="Reference",
        chapter_id="chapter_1",
        kind="chart",
        order_index=1,
        before_chunk_id="chunk_before",
        after_chunk_id="chunk_after",
        caption="New chart",
        anchor_status="verified",
        asset_hash=hashlib.sha256(_PNG).hexdigest(),
        anchor_hash=hashlib.sha256(b"new-position").hexdigest(),
        mime_type="image/png",
    )

    def _fake_board_edit(**kwargs):
        manifest_item = kwargs["visual_manifest"][0]
        return BoardDocumentEditResult(
            operation="replace_selection",
            content_text=f"Revised target paragraph.\n\n{manifest_item['marker']}",
            summary="Revised the selected paragraph.",
            visual_placements=[
                {
                    "visual_id": visual.visual_id,
                    "marker": manifest_item["marker"],
                    "target_text_anchor": "Revised target paragraph.",
                    "source_before_chunk_id": manifest_item["source_before_chunk_id"],
                    "source_after_chunk_id": manifest_item["source_after_chunk_id"],
                    "reason": "The source chart belongs to the revised paragraph.",
                }
            ],
        )

    asset_store = BoardAssetStore(tmp_path / "openclass.sqlite3", tmp_path / "board-assets")
    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)
    monkeypatch.setattr(board_visual_insertion, "get_board_asset_store", lambda: asset_store)

    outcome = edit_existing_document(
        lesson=lesson,
        requirements=requirements,
        clarification=LearningClarificationStatus(progress=100, label="ready", reason="ready"),
        resource_summary="Reference context",
        conversation_summary="",
        user_instruction="Revise only the selected paragraph.",
        selection_excerpt="Target paragraph.",
        focus=BoardFocusRef(excerpt="Target paragraph."),
        target_scope="focus",
        owner_user_id="owner_1",
        confirmed_visuals=[visual],
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
    )

    assert outcome.changed is True
    assert outcome.applied_visual_ids == ("visual_new",)
    nodes = outcome.new_document.content_json["content"]
    assert [node["type"] for node in nodes] == [
        "heading",
        "paragraph",
        "resourceVisualBlock",
        "paragraph",
        "resourceVisualBlock",
        "paragraph",
    ]
    assert nodes[2]["attrs"]["visualId"] == "visual_existing"
    assert nodes[2]["attrs"]["assetId"] == "basset_existing_asset"
    assert nodes[4]["attrs"]["visualId"] == "visual_new"
    assert "Before target." in outcome.new_document.content_text
    assert "Outside literal [[OPENCLASS_VISUAL_legacy_0000]] must stay." in outcome.new_document.content_text


def test_append_section_visual_preserves_existing_asset_and_appends_new_visual(
    monkeypatch,
    tmp_path,
) -> None:
    lesson = create_empty_lesson("Existing board")
    requirements = lesson.learning_requirements
    assert requirements is not None
    requirements.action_type = "append_section"
    requirements.action_instruction = "Append a new source-grounded section."
    lesson.board_document = rebuild_document_from_content_json(
        build_document(
            title="Existing board",
            content_text="placeholder",
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        ),
        {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "Existing section"}],
                },
                {"type": "paragraph", "content": [{"type": "text", "text": "Existing paragraph."}]},
                {
                    "type": "resourceVisualBlock",
                    "attrs": {
                        "visualId": "visual_existing",
                        "assetId": "basset_existing_asset",
                        "caption": "Existing chart",
                        "originalSrc": "/api/board-assets/basset_existing_asset/content",
                        "sourceIngestionId": "source_existing",
                    },
                },
            ],
        },
    )
    visual = RetrievalVisualEvidence(
        visual_id="visual_appended",
        source_ingestion_id="source_append",
        source_title="Append reference",
        chapter_id="chapter_append",
        kind="chart",
        order_index=2,
        before_chunk_id="chunk_append_before",
        after_chunk_id="chunk_append_after",
        caption="Appended chart",
        anchor_status="verified",
        asset_hash=hashlib.sha256(_PNG).hexdigest(),
        anchor_hash=hashlib.sha256(b"append-position").hexdigest(),
        mime_type="image/png",
    )

    def _fake_board_edit(**kwargs):
        manifest_item = kwargs["visual_manifest"][0]
        return BoardDocumentEditResult(
            operation="replace_document",
            content_text=(
                "## Appended section\n\n"
                "Appended paragraph.\n\n"
                f"{manifest_item['marker']}"
            ),
            summary="Appended a section.",
            visual_placements=[
                {
                    "visual_id": visual.visual_id,
                    "marker": manifest_item["marker"],
                    "target_text_anchor": "Appended paragraph.",
                    "source_before_chunk_id": manifest_item["source_before_chunk_id"],
                    "source_after_chunk_id": manifest_item["source_after_chunk_id"],
                    "reason": "The chart follows its source-grounded paragraph.",
                }
            ],
        )

    asset_store = BoardAssetStore(tmp_path / "openclass.sqlite3", tmp_path / "board-assets")
    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)
    monkeypatch.setattr(board_visual_insertion, "get_board_asset_store", lambda: asset_store)

    outcome = edit_existing_document(
        lesson=lesson,
        requirements=requirements,
        clarification=LearningClarificationStatus(progress=100, label="ready", reason="ready"),
        resource_summary="Append reference context",
        conversation_summary="",
        user_instruction="Append a new source-grounded section.",
        selection_excerpt=None,
        target_scope=None,
        owner_user_id="owner_1",
        confirmed_visuals=[visual],
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
    )

    assert outcome.changed is True
    assert outcome.operation == "append_section"
    assert outcome.applied_visual_ids == ("visual_appended",)
    nodes = outcome.new_document.content_json["content"]
    visual_nodes = [node for node in nodes if node["type"] == "resourceVisualBlock"]
    assert [node["attrs"]["visualId"] for node in visual_nodes] == [
        "visual_existing",
        "visual_appended",
    ]
    assert visual_nodes[0]["attrs"]["assetId"] == "basset_existing_asset"
    assert nodes.index(visual_nodes[0]) < nodes.index(visual_nodes[1])
    assert "Existing paragraph." in outcome.new_document.content_text
    assert "Appended paragraph." in outcome.new_document.content_text


def test_skipped_visual_placement_suppresses_model_success_message(monkeypatch) -> None:
    lesson = create_empty_lesson("Visual placement")
    requirements = lesson.learning_requirements
    assert requirements is not None
    visual = RetrievalVisualEvidence(
        visual_id="visual_1",
        source_ingestion_id="source_1",
        source_title="Reference",
        chapter_id="chapter_1",
        kind="chart",
        order_index=1,
        before_chunk_id="chunk_before",
        after_chunk_id="chunk_after",
        caption="Chart",
        anchor_status="verified",
        asset_hash=hashlib.sha256(_PNG).hexdigest(),
        anchor_hash=hashlib.sha256(b"position").hexdigest(),
        mime_type="image/png",
    )

    def _fake_board_edit(**_kwargs):
        return BoardDocumentEditResult(
            operation="replace_document",
            title="Visual placement",
            content_text="# Visual placement\n\nText was generated without the required marker.",
            summary="Generated text.",
            chatbot_message="Two charts were inserted successfully.",
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)

    outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=LearningClarificationStatus(
            progress=100,
            label="ready",
            reason="ready",
            ready_for_board=True,
        ),
        owner_user_id="owner_1",
        confirmed_visuals=[visual],
        visual_bytes_resolver=lambda _visual_id: (visual, _PNG),
    )

    assert outcome.changed is True
    assert outcome.chatbot_message == ""
    assert outcome.applied_visual_ids == ()
    assert outcome.skipped_visual_placements[0]["reason"] == "placement_missing"
