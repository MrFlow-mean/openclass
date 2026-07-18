from types import SimpleNamespace

from app.models import Lesson, SelectionRef, SourceIngestionRecord
from app.services import source_grounded_board
from app.services import source_ingestion_service as source_ingestion_module


def test_whole_open_notebook_source_search_becomes_frozen_board_evidence(monkeypatch) -> None:
    lesson = Lesson.model_construct(id="lesson_open_notebook")
    source = SourceIngestionRecord(
        id="source_local",
        owner_user_id="user",
        package_id="package",
        title="Reference Book",
        status="ready",
        open_notebook_notebook_id="notebook_remote",
        open_notebook_source_id="source_remote",
        metadata={"source_processing_owner": "open_notebook"},
    )
    saved_bundles = []

    monkeypatch.setattr(source_grounded_board.workspace_state, "load_workspace_for_user", lambda _user_id: object())
    monkeypatch.setattr(
        source_grounded_board.workspace_state,
        "find_lesson_package",
        lambda _workspace, _lesson_id: (SimpleNamespace(id="package"), lesson),
    )
    monkeypatch.setattr(
        source_grounded_board.source_evidence_store,
        "get_source",
        lambda **_kwargs: source,
    )
    monkeypatch.setattr(
        source_grounded_board.source_evidence_store,
        "save_bundle",
        lambda bundle: saved_bundles.append(bundle) or bundle,
    )
    monkeypatch.setattr(
        source_ingestion_module.source_ingestion_service,
        "search_open_notebook",
        lambda **_kwargs: [
            {
                "chunk_id": "remote_chunk_1",
                "text": "The matched source passage.",
                "section_path": ["Part One", "Topic"],
                "page": 12,
                "score": 0.91,
            }
        ],
    )

    plan = source_grounded_board.resolve_source_grounded_board_plan(
        owner_user_id="user",
        lesson=lesson,
        selection=SelectionRef(
            kind="source",
            excerpt="Reference Book",
            source_ingestion_id=source.id,
            source_scope_kind="source",
        ),
        query="Explain the selected topic",
    )

    assert plan is not None
    assert plan.requirement.source_grounding.confirmation_status == "confirmed"
    assert plan.requirement.source_grounding.frozen_evidence[0].expanded_text == "The matched source passage."
    assert plan.requirement.source_grounding.confirmed_references[0].scope_kind == "source"
    assert saved_bundles[0].metadata["origin"] == "open_notebook_source_search"
