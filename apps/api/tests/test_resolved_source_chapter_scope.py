from app.models import EvidenceBundle, RetrievalEvidence, SelectionRef
from app.services.resolved_source_chapter_scope import resolved_source_chapter_scope


def _selection() -> SelectionRef:
    return SelectionRef(
        kind="source",
        excerpt="资料中的旧章节标题",
        source_ingestion_id="source_1",
        source_chapter_id="sourcechapter_stale",
        source_chapter_title="旧章节标题",
    )


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        purpose="board_generation",
        evidence_items=[
            RetrievalEvidence(
                source_ingestion_id="source_1",
                source_title="资料",
                chapter_id="sourcechapter_current",
                section_path=["当前章节标题"],
                chunk_ids=["chunk_1"],
                excerpt="章节摘录",
                expanded_text="章节正文",
                token_count=10,
                metadata={
                    "scope_chapter_id": "sourcechapter_current",
                    "scope_chapter_title": "当前章节标题",
                },
            )
        ],
        context_text="章节正文",
        token_count=10,
    )


def test_resolved_source_chapter_scope_uses_canonical_rebound_chapter() -> None:
    scope = resolved_source_chapter_scope(
        source_reference=_selection(),
        discovery_status="matched",
        evidence_bundle=_bundle(),
        discovery_metadata={
            "resolution": {
                "status": "matched",
                "source_ingestion_id": "source_1",
                "requested_chapter_id": "sourcechapter_stale",
                "resolved_chapter_id": "sourcechapter_current",
                "chapter_title": "当前章节标题",
            }
        },
    )

    assert scope is not None
    assert scope.source_chapter_id == "sourcechapter_current"
    assert scope.chapter_title == "当前章节标题"


def test_resolved_source_chapter_scope_rejects_a_nonmatching_or_unresolved_reference() -> None:
    scope = resolved_source_chapter_scope(
        source_reference=_selection(),
        discovery_status="no_match",
        evidence_bundle=_bundle(),
        discovery_metadata={
            "resolution": {
                "status": "matched",
                "source_ingestion_id": "source_1",
                "chapter_id": "sourcechapter_current",
                "chapter_title": "当前章节标题",
            }
        },
    )

    assert scope is None


def test_resolved_source_chapter_scope_accepts_an_explicit_textual_chapter_locator() -> None:
    scope = resolved_source_chapter_scope(
        source_reference=None,
        discovery_status="matched",
        evidence_bundle=_bundle(),
        discovery_metadata={
            "resolution": {
                "status": "matched",
                "intent_signals": ["explicit_chapter_locator"],
                "source_ingestion_id": "source_1",
                "chapter_id": "sourcechapter_current",
                "chapter_title": "当前章节标题",
            }
        },
    )

    assert scope is not None
    assert scope.chapter_title == "当前章节标题"
