from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

import app.main as main_module
from app.models import ResourceAIQueryRequest, UserView
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import workspace as workspace_router
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.resource_ai import query_resource_ai
from app.services.web_resource_adapter import build_web_resource_item_from_html


TEST_USER = UserView(
    id="user_source_hub",
    email="source-hub@example.com",
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
    monkeypatch.setattr(workspace_router, "UPLOAD_DIR", upload_dir)
    monkeypatch.setenv("OPENCLASS_RESOURCE_PARSER", "native")
    workspace_state.ensure_data_dirs()

    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def _html() -> str:
    return """
    <html>
      <head><title>Readable source</title><script>ignored()</script></head>
      <body>
        <nav>Navigation text should not become evidence.</nav>
        <h1>Source Hub</h1>
        <p>The retrieval anchor appears in the first readable paragraph.</p>
        <h2>Evidence Units</h2>
        <p>Paragraph source units keep URL, heading path, and paragraph index.</p>
      </body>
    </html>
    """


def _create_blank_lesson(api_client: TestClient) -> dict:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Source package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Source lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    return generated.json()["lessons"][0]


def test_web_resource_adapter_builds_url_source_units() -> None:
    resource = build_web_resource_item_from_html("https://example.test/readable", _html())

    assert resource.source_type == "web_url"
    assert resource.source_uri == "https://example.test/readable"
    assert resource.ingestion_status == "ready"
    assert resource.ingestion_job is not None
    assert resource.ingestion_job.phase_history == ["queued", "fetching", "parsing", "indexing", "ready"]
    assert resource.name == "Readable source"
    assert resource.source_units[0].url == "https://example.test/readable"
    assert resource.source_units[0].heading_path == ["Source Hub"]
    assert resource.source_units[0].paragraph_index == 0
    assert "Navigation text" not in resource.text_content


def test_add_url_endpoint_adds_ready_web_resource(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lesson = _create_blank_lesson(api_client)

    monkeypatch.setattr(
        workspace_router,
        "build_web_resource_item",
        lambda url, title=None: build_web_resource_item_from_html(url, _html(), title=title),
    )

    added = api_client.post(
        f"/api/lessons/{lesson['id']}/resources/add-url",
        json={"url": "https://example.test/readable"},
    )

    assert added.status_code == 200
    resource = added.json()["resources"][0]
    assert resource["source_type"] == "web_url"
    assert resource["ingestion_status"] == "ready"
    assert resource["source_units"][0]["url"] == "https://example.test/readable"
    assert resource["source_units"][0]["heading_path"] == ["Source Hub"]


def test_add_url_failure_keeps_existing_resources_and_records_failed_state(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lesson = _create_blank_lesson(api_client)
    uploaded = api_client.post(
        f"/api/lessons/{lesson['id']}/resources/upload",
        files={"file": ("resource.md", "# Existing\nStable resource.".encode("utf-8"), "text/markdown")},
    )
    assert uploaded.status_code == 200

    def fail_fetch(url: str, title: str | None = None):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(workspace_router, "build_web_resource_item", fail_fetch)
    added = api_client.post(
        f"/api/lessons/{lesson['id']}/resources/add-url",
        json={"url": "https://example.test/missing"},
    )

    assert added.status_code == 200
    resources = added.json()["resources"]
    assert resources[0]["source_type"] == "local_file"
    assert resources[0]["ingestion_status"] == "ready"
    assert resources[1]["source_type"] == "web_url"
    assert resources[1]["ingestion_status"] == "failed"
    assert resources[1]["ingestion_error"] == "network unavailable"
    assert resources[1]["source_units"] == []


def test_sqlite_store_round_trips_source_hub_fields(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("Source storage")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    package.resources.append(build_web_resource_item_from_html("https://example.test/readable", _html()))

    store.save(workspace)
    reloaded = store.load()
    resource = reloaded.packages[0].resources[0]

    assert resource.source_type == "web_url"
    assert resource.source_uri == "https://example.test/readable"
    assert resource.ingestion_status == "ready"
    assert resource.ingestion_job is not None
    assert resource.source_units[0].url == "https://example.test/readable"
    assert resource.source_units[0].heading_path == ["Source Hub"]


def test_resource_ai_queries_web_source_units() -> None:
    resource = build_web_resource_item_from_html("https://example.test/readable", _html())

    response = query_resource_ai(
        [resource],
        ResourceAIQueryRequest(query="retrieval anchor", max_results=3),
    )

    assert response.index_status[0].source_type == "web_url"
    assert response.index_status[0].ingestion_status == "ready"
    assert response.evidence_units
    assert response.evidence_units[0].metadata["url"] == "https://example.test/readable"
    assert response.evidence_units[0].metadata["heading_path"] == ["Source Hub"]
    assert "网页：" in response.evidence_units[0].reason
    assert "段落：" in response.evidence_units[0].reason
