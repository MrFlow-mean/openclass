from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import EvidenceBundle, RetrievalEvidence, UserView
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import workspace as workspace_router
from app.services import source_ingestion_service as source_ingestion_module
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore
from app.services.open_notebook_adapter import OpenNotebookAdapterError, OpenNotebookSourceResult
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import source_ingestion_service


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


def test_open_notebook_source_import_and_evidence_confirm(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
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

    class _FakeAdapter:
        def create_notebook(self, *, title: str, description: str = "") -> str:
            return "nb_api"

        def add_url_source(self, *, notebook_id: str, source_uri: str, title: str = "") -> OpenNotebookSourceResult:
            assert notebook_id == "nb_api"
            return OpenNotebookSourceResult(
                source_id="src_api",
                command_id="cmd_api",
                status="completed",
                raw={"source_id": "src_api"},
            )

        def delete_source(self, source_id: str) -> None:
            assert source_id == "src_api"

    monkeypatch.setattr(source_ingestion_module, "_validate_public_url", lambda raw_uri: raw_uri)
    monkeypatch.setattr(source_ingestion_service, "adapter", _FakeAdapter())

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        data={"source_uri": "https://example.com/source", "title": "示例网页"},
    )
    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["open_notebook_source_id"] == "src_api"
    assert source["structure_status"] == "linear_only"

    listed = api_client.get(f"/api/packages/{package_id}/sources")
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "示例网页"
    assert listed.json()[0]["structure_status"] == "linear_only"

    structure = api_client.get(f"/api/packages/{package_id}/sources/{source['id']}/structure")
    assert structure.status_code == 200
    assert structure.json()["structure"]["strategy"] == "open_notebook_search_only"
    assert structure.json()["chapters"] == []

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
                    open_notebook_source_id="src_api",
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
    assert confirmed_payload["status"] == "confirmed"
    assert confirmed_payload["confirmed_by_user"] is True


def test_source_import_records_failed_open_notebook_connection(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Unavailable source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    package_id = created_workspace.json()["active_package_id"]

    class _UnavailableAdapter:
        api_url = "http://localhost:5055"

        def create_notebook(self, *, title: str, description: str = "") -> str:
            raise OpenNotebookAdapterError("[Errno 61] Connection refused")

    monkeypatch.setattr(source_ingestion_service, "adapter", _UnavailableAdapter())

    imported = api_client.post(
        f"/api/packages/{package_id}/sources",
        files={"file": ("source.md", b"# title", "text/markdown")},
    )
    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "failed"
    assert source["open_notebook_notebook_id"] == ""
    assert "Open Notebook 服务未启动或不可达" in source["error"]

    listed = api_client.get(f"/api/packages/{package_id}/sources")
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "failed"
