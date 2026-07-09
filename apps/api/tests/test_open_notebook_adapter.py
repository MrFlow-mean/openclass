import httpx

from app.services.open_notebook_adapter import OpenNotebookAdapter


def _response(method: str, url: str, payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


def test_open_notebook_adapter_wraps_source_command_and_search(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_request(method: str, url: str, **kwargs):
        calls.append((method, url))
        if url.endswith("/api/notebooks"):
            return _response(method, url, {"id": "nb_1"})
        if url.endswith("/api/sources") and kwargs.get("files"):
            return _response(method, url, {"source_id": "src_file", "command_id": "cmd_file", "status": "processing"})
        if url.endswith("/api/sources"):
            assert kwargs["data"]["type"] == "link"
            return _response(method, url, {"source_id": "src_url", "command_id": "cmd_url", "status": "queued"})
        if url.endswith("/api/commands/jobs/cmd_url"):
            return _response(method, url, {"status": "completed", "result": {"source_id": "src_url"}})
        if url.endswith("/api/commands/cmd_url"):
            return _response(method, url, {"status": "completed", "source_id": "src_url"})
        if url.endswith("/api/search"):
            assert kwargs["json"]["type"] == "text"
            assert kwargs["json"]["search_notes"] is False
            return _response(method, url, {"results": [{"source_id": "src_url", "chunk_id": "chunk_1", "text": "命中内容"}]})
        raise AssertionError(f"unexpected Open Notebook call {method} {url}")

    monkeypatch.setattr(httpx, "request", _fake_request)
    adapter = OpenNotebookAdapter(api_url="http://notebook.local", password="secret", timeout_seconds=1)

    notebook_id = adapter.create_notebook(title="资料容器")
    url_result = adapter.add_url_source(notebook_id=notebook_id, source_uri="https://example.com/a", title="网页")
    file_result = adapter.upload_file_source(
        notebook_id=notebook_id,
        file_name="source.md",
        content=b"# title",
        mime_type="text/markdown",
        title="文件",
    )
    command = adapter.get_command(url_result.command_id)
    results = adapter.search(notebook_id=notebook_id, query="学习目标", limit=4, source_ids=["src_url"])

    assert notebook_id == "nb_1"
    assert url_result.source_id == "src_url"
    assert file_result.source_id == "src_file"
    assert command["status"] == "completed"
    assert results[0]["chunk_id"] == "chunk_1"
    assert {url for _method, url in calls} >= {
        "http://notebook.local/api/notebooks",
        "http://notebook.local/api/sources",
        "http://notebook.local/api/commands/jobs/cmd_url",
        "http://notebook.local/api/search",
    }
