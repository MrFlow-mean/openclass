from pathlib import Path

import app.services.rag_anything_adapter as adapter


def test_rag_anything_path_prefers_repo_local_copy(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "openclass"
    repo_root.mkdir()
    repo_local = repo_root / "RAG-Anything-main"
    repo_local.mkdir()
    sibling = tmp_path / "RAG-Anything-main"
    sibling.mkdir()
    monkeypatch.delenv("OPENCLASS_RAG_ANYTHING_PATH", raising=False)
    monkeypatch.setattr(adapter, "_repo_root", lambda: repo_root)

    assert adapter._rag_anything_path() == repo_local.resolve()


def test_rag_anything_path_resolves_relative_override_from_repo_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "openclass"
    repo_root.mkdir()
    monkeypatch.setenv("OPENCLASS_RAG_ANYTHING_PATH", "vendor/RAG-Anything-main")
    monkeypatch.setattr(adapter, "_repo_root", lambda: repo_root)

    assert adapter._rag_anything_path() == (repo_root / "vendor" / "RAG-Anything-main").resolve()
