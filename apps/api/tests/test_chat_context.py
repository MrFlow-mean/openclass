from app.models import ConversationTurn, ResourceContextChunk, ResourceReferenceContext
from app.services.chat.context import (
    compact_text,
    conversation_summary,
    merge_selection_and_reference,
    resource_context_excerpt,
)


def test_compact_text_collapses_whitespace_and_truncates() -> None:
    assert compact_text("  a\n\n b\tc  ", limit=20) == "a b c"
    assert compact_text("abcdef", limit=4) == "abc..."


def test_conversation_summary_uses_recent_turns() -> None:
    turns = [ConversationTurn(role="user", content=f"问题{i}") for i in range(10)]

    summary = conversation_summary(turns)

    assert "问题0" not in summary
    assert "问题2" in summary
    assert "问题9" in summary


def test_resource_context_excerpt_and_selection_merge() -> None:
    reference = ResourceReferenceContext(
        resource_id="res_1",
        chapter_id="chap_1",
        resource_name="资料A",
        chapter_title="第一章",
        summary="章节摘要",
        teaching_points=["要点1", "要点2"],
        chunks=[ResourceContextChunk(title="片段", excerpt="  片段正文\n很多空格  ", teaching_hint="")],
    )

    excerpt = resource_context_excerpt(reference)
    merged = merge_selection_and_reference("选区内容", reference)

    assert excerpt is not None
    assert "资料A / 第一章" in excerpt
    assert "讲解要点：要点1；要点2" in excerpt
    assert "片段：片段正文 很多空格" in excerpt
    assert merged is not None
    assert merged.startswith("选区内容\n\n参考资料：")
