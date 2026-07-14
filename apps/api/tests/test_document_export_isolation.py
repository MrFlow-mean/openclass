from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.models import UserView
from app.routers import documents as documents_router
from app.services.rich_document import build_document


def _user(user_id: str) -> UserView:
    return UserView(
        id=user_id,
        email=f"{user_id}@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("route_name", "exporter_name", "suffix"),
    [
        ("export_document_docx", "export_docx", ".docx"),
        ("export_document_html", "export_html", ".html"),
    ],
)
def test_concurrent_same_slug_exports_are_isolated_by_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route_name: str,
    exporter_name: str,
    suffix: str,
) -> None:
    export_root = tmp_path / "exports"
    lesson_id = "shared-lesson-id"
    lessons = {
        "owner-a": SimpleNamespace(
            id=lesson_id,
            slug="shared-board",
            board_document=build_document(title="Owner A", content_text="private content A"),
        ),
        "owner-b": SimpleNamespace(
            id=lesson_id,
            slug="shared-board",
            board_document=build_document(title="Owner B", content_text="private content B"),
        ),
    }
    ready = Barrier(2)

    def fake_export(document, path: Path, *, asset_resolver=None) -> Path:
        del asset_resolver
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(document.content_text.encode("utf-8"))
        ready.wait(timeout=5)
        return path

    monkeypatch.setattr(documents_router, "EXPORT_DIR", export_root)
    monkeypatch.setattr(documents_router, "load_workspace_for_user", lambda user_id: user_id)
    monkeypatch.setattr(
        documents_router,
        "find_lesson_package",
        lambda owner_id, requested_lesson_id: (None, lessons[owner_id])
        if requested_lesson_id == lesson_id
        else (_ for _ in ()).throw(AssertionError("unexpected lesson id")),
    )
    monkeypatch.setattr(documents_router, exporter_name, fake_export)
    route = getattr(documents_router, route_name)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            owner_id: executor.submit(route, lesson_id, user=_user(owner_id))
            for owner_id in lessons
        }
        responses = {owner_id: future.result(timeout=10) for owner_id, future in futures.items()}

    paths = {owner_id: Path(response.path) for owner_id, response in responses.items()}
    assert paths["owner-a"] != paths["owner-b"]
    assert paths["owner-a"].read_text(encoding="utf-8") == "private content A"
    assert paths["owner-b"].read_text(encoding="utf-8") == "private content B"
    assert all(owner_id not in str(path) for owner_id, path in paths.items())
    assert all(
        f'filename="shared-board{suffix}"' in response.headers["content-disposition"]
        for response in responses.values()
    )

    for response in responses.values():
        assert response.background is not None
        asyncio.run(response.background())

    assert all(not path.parent.exists() for path in paths.values())
    assert export_root.exists()
    assert not list(export_root.iterdir())


@pytest.mark.parametrize(
    ("route_name", "exporter_name"),
    [
        ("export_document_docx", "export_docx"),
        ("export_document_html", "export_html"),
    ],
)
def test_failed_export_removes_request_directory_immediately(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route_name: str,
    exporter_name: str,
) -> None:
    export_root = tmp_path / "exports"
    lesson = SimpleNamespace(
        id="lesson-a",
        slug="shared-board",
        board_document=build_document(title="Board", content_text="private content"),
    )
    created_directories: list[Path] = []

    def failing_export(document, path: Path, *, asset_resolver=None) -> Path:
        del document, asset_resolver
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"partial")
        created_directories.append(path.parent)
        raise RuntimeError("export failed")

    monkeypatch.setattr(documents_router, "EXPORT_DIR", export_root)
    monkeypatch.setattr(documents_router, "load_workspace_for_user", lambda _user_id: object())
    monkeypatch.setattr(documents_router, "find_lesson_package", lambda _workspace, _lesson_id: (None, lesson))
    monkeypatch.setattr(documents_router, exporter_name, failing_export)

    with pytest.raises(RuntimeError, match="export failed"):
        getattr(documents_router, route_name)(lesson.id, user=_user("owner-a"))

    assert created_directories
    assert all(not directory.exists() for directory in created_directories)
    assert export_root.exists()
    assert not list(export_root.iterdir())
