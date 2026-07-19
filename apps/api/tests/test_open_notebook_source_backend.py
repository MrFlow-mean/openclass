from pathlib import Path

from app.models import SourceIngestionRecord
from app.services.open_notebook_adapter import OpenNotebookSourceResult
from app.services.open_notebook_source_backend import OpenNotebookSourceBackend
from app.services.source_evidence_store import SourceEvidenceStore


class FakeOpenNotebookAdapter:
    api_url = "http://notebook.test"

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def create_notebook(self, **_kwargs) -> str:
        return "notebook:test"

    def upload_file_source(self, **_kwargs) -> OpenNotebookSourceResult:
        return OpenNotebookSourceResult(
            source_id="source:remote",
            command_id="command:remote",
            status="processing",
            raw={"accepted": True},
        )

    def get_command(self, _command_id: str) -> dict[str, object]:
        return {
            "status": "completed",
            "result": {"source_id": "source:remote"},
        }

    def search(self, **_kwargs) -> list[dict[str, object]]:
        return [{"source_id": "source:remote", "text": "matched body"}]

    def delete_source(self, source_id: str) -> None:
        self.deleted.append(source_id)


def test_open_notebook_backend_syncs_refreshes_and_searches(tmp_path: Path) -> None:
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    adapter = FakeOpenNotebookAdapter()
    source_path = tmp_path / "source.md"
    source_path.write_text("# Source\n\nBody", encoding="utf-8")
    record = SourceIngestionRecord(
        owner_user_id="user",
        package_id="package",
        title="Source",
        file_name="source.md",
        mime_type="text/markdown",
        status="queued",
        metadata={"package_title": "Course", "local_source_path": str(source_path)},
    )
    backend = OpenNotebookSourceBackend(
        adapter=adapter,
        store=store,
        local_path=lambda _record: source_path,
    )
    store.save_source(record)

    synced = backend.sync_file(record)
    assert synced.open_notebook_notebook_id == "notebook:test"
    assert synced.open_notebook_source_id == "source:remote"
    assert synced.metadata["open_notebook_sync_status"] == "parsing"
    persisted = store.get_source(
        owner_user_id="user",
        package_id="package",
        source_id=record.id,
    )
    assert persisted is not None
    assert persisted.open_notebook_notebook_id == "notebook:test"

    ready = backend.refresh(synced, mirror_status=True)
    assert ready.status == "ready"
    assert ready.metadata["open_notebook_sync_status"] == "ready"
    assert (
        backend.search(
            owner_user_id="user",
            package_id="package",
            query="body",
            records=[ready.model_copy(update={"status": "ready"})],
            limit=5,
            source_ids=[ready.id],
        )[0]["text"]
        == "matched body"
    )
