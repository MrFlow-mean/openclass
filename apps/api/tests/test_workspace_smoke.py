from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import EvidenceBundle, RetrievalEvidence, SourceIngestionRecord, UserView
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import workspace as workspace_router
from app.services import source_ingestion_service as source_ingestion_module
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import source_ingestion_service
from app.services.youtube_transcript_adapter import YouTubeTranscript


TEST_USER = UserView(
    id="user_smoke",
    email="smoke@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    upload_dir = tmp_path / "uploads"
    export_dir = tmp_path / "exports"

    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(workspace_state, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(documents_router, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(documents_router, "EXPORT_DIR", export_dir)
    workspace_state.ensure_data_dirs()

    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def _document_with_text(document: dict, text: str) -> dict:
    next_document = deepcopy(document)
    next_document["content_text"] = text
    next_document["content_html"] = f"<p>{text}</p>"
    next_document["content_json"] = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return next_document


def _docx_text_nodes(content: bytes) -> list[str]:
    with ZipFile(BytesIO(content)) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return [node.text or "" for node in root.findall(".//w:t", ns)]


def test_workspace_document_history_flow(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Smoke package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]

    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Smoke lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    package = generated.json()
    lesson = package["lessons"][0]

    first_document = _document_with_text(lesson["board_document"], "First smoke version")
    first_save = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": first_document,
            "label": "First smoke save",
            "message": "Saved first smoke version",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert first_save.status_code == 200
    first_commit_id = first_save.json()["lessons"][0]["history_graph"]["commits"][-1]["id"]

    second_document = _document_with_text(first_document, "Second smoke version")
    second_save = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": second_document,
            "label": "Second smoke save",
            "message": "Saved second smoke version",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert second_save.status_code == 200
    assert second_save.json()["lessons"][0]["board_document"]["content_text"] == "Second smoke version"

    search = api_client.get("/api/documents/search", params={"q": "Second smoke", "limit": 5})
    assert search.status_code == 200
    assert search.json()["results"]

    restored = api_client.post(
        f"/api/lessons/{lesson['id']}/restore",
        json={"commit_id": first_commit_id, "label": "Restore first smoke version"},
    )
    assert restored.status_code == 200
    assert restored.json()["lessons"][0]["board_document"]["content_text"] == "First smoke version"


def test_export_docx_rejects_empty_board_document(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Empty export package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]

    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Empty export lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]

    exported = api_client.get(f"/api/lessons/{lesson['id']}/document/export-docx")

    assert exported.status_code == 409
    assert "当前板书文档为空" in exported.text


def test_export_docx_uses_head_snapshot_when_current_document_is_empty(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Snapshot export package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]

    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Snapshot export lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]
    saved_document = _document_with_text(lesson["board_document"], "Head snapshot survives export")
    saved = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": saved_document,
            "label": "Save export source",
            "message": "Saved export source",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert saved.status_code == 200

    store = workspace_state.get_store()
    empty_doc = {"type": "doc", "content": [{"type": "paragraph"}]}
    with store._connect() as conn:
        with conn:
            conn.execute(
                """
                UPDATE lessons
                SET board_document_title = title,
                    board_content_json = ?,
                    board_content_html = '',
                    board_content_text = ''
                WHERE id = ?
                """,
                (json.dumps(empty_doc), lesson["id"]),
            )

    exported = api_client.get(f"/api/lessons/{lesson['id']}/document/export-docx")

    assert exported.status_code == 200
    assert exported.headers["cache-control"].startswith("no-store")
    assert "Head snapshot survives export" in "".join(_docx_text_nodes(exported.content))


def test_resource_upload_endpoint_is_not_exposed(api_client: TestClient) -> None:
    upload = api_client.post("/api/resources/upload")

    assert upload.status_code == 404


def test_lesson_resource_upload_endpoint_is_not_exposed(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Resource lesson", "start_blank": True},
    )
    assert created_workspace.status_code == 200
    lesson = created_workspace.json()["lessons"][0]

    upload = api_client.post(
        f"/api/lessons/{lesson['id']}/resources/upload",
        files={"file": ("resource.md", "# 第一章\n这是资料正文。".encode("utf-8"), "text/markdown")},
    )

    assert upload.status_code == 404


def test_native_url_source_import_and_evidence_confirm(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Source lesson", "target_package_id": package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]

    def _fake_snapshot(record: SourceIngestionRecord, source_uri: str) -> dict[str, str]:
        source_dir = workspace_state.UPLOAD_DIR / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = source_dir / f"{record.id}.html"
        snapshot_path.write_text("<h1>Native source</h1><p>Native indexed body.</p>", encoding="utf-8")
        return {"local_source_path": str(snapshot_path)}

    monkeypatch.setattr(source_ingestion_module, "_validate_public_url", lambda raw_uri: raw_uri)
    monkeypatch.setattr(source_ingestion_module, "fetch_url_source_snapshot", _fake_snapshot)

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        data={"source_uri": "https://example.com/source", "title": "示例网页"},
    )
    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["open_notebook_source_id"] == ""
    assert source["metadata"]["adapter"] == "openclass_native_url"
    assert source["structure_status"] == "ready"

    listed = api_client.get(f"/api/packages/{package_id}/sources")
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "示例网页"
    assert listed.json()[0]["structure_status"] == "ready"

    structure = api_client.get(f"/api/packages/{package_id}/sources/{source['id']}/structure")
    assert structure.status_code == 200
    assert structure.json()["structure"]["strategy"] == "markdown_heading"
    assert structure.json()["chapters"]
    assert structure.json()["chunks"]

    deleted = api_client.delete(f"/api/packages/{package_id}/sources/{source['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["id"] == source["id"]

    listed_after_delete = api_client.get(f"/api/packages/{package_id}/sources")
    assert listed_after_delete.status_code == 200
    assert listed_after_delete.json() == []

    bundle = source_evidence_store.save_bundle(
        EvidenceBundle(
            owner_user_id=TEST_USER.id,
            package_id=package_id,
            lesson_id=lesson["id"],
            purpose="board_generation",
            query="结合资料生成",
            evidence_items=[
                RetrievalEvidence(
                    source_ingestion_id=source["id"],
                    open_notebook_source_id="",
                    source_title="示例网页",
                    source_uri="https://example.com/source",
                    section_path=["第一节"],
                    page_range="p. 1",
                    chunk_ids=["chunk_api"],
                    excerpt="短摘录",
                    expanded_text="短摘录和上下文",
                    token_count=8,
                )
            ],
            context_text="资料上下文",
            token_count=8,
        )
    )

    confirmed = api_client.post(
        f"/api/lessons/{lesson['id']}/evidence/confirm",
        json={"bundle_id": bundle.id, "action": "confirm"},
    )
    assert confirmed.status_code == 200
    confirmed_payload = confirmed.json()
    assert confirmed_payload["evidence_bundle"]["status"] == "confirmed"
    assert confirmed_payload["evidence_bundle"]["confirmed_by_user"] is True
    assert confirmed_payload["active_requirement_sheet"] is None


def test_pending_lesson_evidence_can_be_recovered_after_reopening(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Pending evidence package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Pending evidence lesson", "target_package_id": package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    lesson_id = generated.json()["lessons"][0]["id"]
    requirement_run_id = "reqrun_pending_evidence"
    monkeypatch.setattr(
        workspace_state,
        "load_learning_requirement_history_state_for_user",
        lambda _user_id, _lesson_id: {"run_id": requirement_run_id},
    )
    bundle = source_evidence_store.save_bundle(
        EvidenceBundle(
            owner_user_id=TEST_USER.id,
            package_id=package_id,
            lesson_id=lesson_id,
            requirement_run_id=requirement_run_id,
            purpose="board_generation",
            query="需要确认的资料",
            evidence_items=[],
            context_text="资料上下文",
            token_count=0,
        )
    )

    pending = api_client.get(f"/api/lessons/{lesson_id}/evidence/pending")

    assert pending.status_code == 200
    assert pending.json()["id"] == bundle.id
    source_evidence_store.confirm_bundle(owner_user_id=TEST_USER.id, bundle_id=bundle.id)
    assert api_client.get(f"/api/lessons/{lesson_id}/evidence/pending").json() is None


def test_source_import_uses_native_local_index(
    api_client: TestClient,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Unavailable source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        files={"file": ("source.md", b"# title", "text/markdown")},
    )
    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["error"] == ""
    assert source["open_notebook_notebook_id"] == ""
    assert source["metadata"]["adapter"] == "openclass_native"
    assert source["metadata"]["content_hash"]
    assert "open_notebook_sync_status" not in source["metadata"]
    assert source["structure_status"] == "ready"

    listed = api_client.get(f"/api/packages/{package_id}/sources")
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "ready"
    assert listed.json()[0]["structure_has_verified_toc"] is True


def test_url_source_uses_native_local_snapshot(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "URL fallback package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]

    def _fake_snapshot(record: SourceIngestionRecord, source_uri: str) -> dict[str, str]:
        snapshot_path = tmp_path / f"{record.id}.txt"
        snapshot_path.write_text(
            "Local webpage concept.\nThis snapshot remains usable without Open Notebook.",
            encoding="utf-8",
        )
        return {"local_source_path": str(snapshot_path)}

    monkeypatch.setattr(source_ingestion_module, "_validate_public_url", lambda raw_uri: raw_uri)
    monkeypatch.setattr(source_ingestion_module, "fetch_url_source_snapshot", _fake_snapshot)

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        data={"source_uri": "https://example.com/article", "title": "示例网页"},
    )

    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["error"] == ""
    assert source["source_type"] == "web_url"
    assert source["metadata"]["adapter"] == "openclass_native_url"
    assert source["metadata"]["content_hash"]
    assert source["structure_status"] == "linear_only"


def test_youtube_url_source_uses_transcript_adapter(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "YouTube source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]

    class _FakeYouTubeAdapter:
        def extract(self, source_uri: str, *, title: str = "") -> YouTubeTranscript:
            return YouTubeTranscript(
                title=title or "Transcript source",
                video_id="video_123",
                language="en",
                text=(
                    "Title: Transcript source\n"
                    "Source: https://www.youtube.com/watch?v=video_123\n"
                    "Media type: YouTube video\n"
                    "Transcript:\n"
                    "[00:00] This transcript is indexed as local source text."
                ),
                metadata={
                    "adapter": "youtube_transcript",
                    "media_provider": "youtube",
                    "media_kind": "video",
                    "video_id": "video_123",
                    "transcript_language": "en",
                },
            )

    monkeypatch.setattr(source_ingestion_module, "_validate_public_url", lambda raw_uri: raw_uri)
    monkeypatch.setattr(source_ingestion_service, "youtube_adapter", _FakeYouTubeAdapter())

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        data={"source_uri": "https://www.youtube.com/watch?v=video_123", "title": "视频字幕"},
    )

    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["source_type"] == "video_url"
    assert source["mime_type"] == "text/plain"
    assert source["metadata"]["adapter"] == "youtube_transcript"
    assert source["metadata"]["video_id"] == "video_123"
    assert source["structure_status"] == "linear_only"

    structure = api_client.get(f"/api/packages/{package_id}/sources/{source['id']}/structure")
    assert structure.status_code == 200
    structure_payload = structure.json()
    assert structure_payload["structure"]["status"] == "linear_only"
    assert structure_payload["chunks"]
    assert "indexed as local source text" in structure_payload["chunks"][0]["text"]


def test_failed_legacy_source_can_be_retried_into_native_index(
    api_client: TestClient,
    tmp_path,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Recover source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]
    source_dir = workspace_state.UPLOAD_DIR / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    local_path = source_dir / "recover.md"
    local_path.write_text("# Recovered\n\nRecovered local file body.", encoding="utf-8")
    source_evidence_store.save_source(
        SourceIngestionRecord(
            owner_user_id=TEST_USER.id,
            package_id=package_id,
            title="recover.md",
            source_type="local_file",
            file_name="recover.md",
            mime_type="text/markdown",
            status="failed",
            error="Open Notebook 服务未启动或不可达：http://localhost:5055。",
            metadata={"local_source_path": str(local_path), "adapter": "open_notebook"},
        )
    )

    listed = api_client.get(f"/api/packages/{package_id}/sources")

    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "failed"

    retried = api_client.post(f"/api/packages/{package_id}/sources/{listed.json()[0]['id']}/retry")

    assert retried.status_code == 200
    recovered = retried.json()
    assert recovered["status"] == "ready"
    assert recovered["error"] == ""
    assert recovered["structure_status"] == "ready"
