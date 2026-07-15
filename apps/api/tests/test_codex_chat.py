from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import ChatRequest, SelectionRef
from app.services import codex_app_server, codex_chat, workspace_state
from app.services.codex_app_server import (
    CODEX_PROCESS_FILE_SIZE_LIMIT_BYTES,
    CodexAppServerError,
    CodexTurnCancelledError,
    CodexTurnResult,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.history import commit_operations, create_branch, current_head_commit
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.rich_document import build_document


TEST_USER_ID = "user_codex_chat"


def _thread_result(thread_id: str, cwd: Path) -> dict:
    return {
        "thread": {"id": thread_id},
        "activePermissionProfile": {"id": "openclass_board"},
        "sandbox": {
            "type": "workspaceWrite",
            "writableRoots": [str((cwd / "board.md").resolve())],
            "networkAccess": False,
            "excludeTmpdirEnvVar": True,
            "excludeSlashTmp": True,
        },
    }


def _seed_workspace(store: SqliteCourseStore, *, content_text: str = "# Existing board"):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Codex document")
    document = build_document(
        title=lesson.board_document.title,
        content_text=content_text,
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    lesson.board_document = document
    lesson.history_graph.commits[0].snapshot = document
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def test_codex_turn_prompt_uses_mode_and_ignores_source_selection() -> None:
    prompt = codex_chat._turn_prompt(
        ChatRequest(
            message="Explain the current document.",
            interaction_mode="ask",
            selection=SelectionRef(
                kind="source",
                excerpt="uploaded source excerpt must not be sent",
                source_title="Uploaded source title",
                source_chapter_title="Uploaded chapter",
                source_page_range="10-12",
            ),
        ),
        is_new_thread=True,
        board_state="empty",
    )

    assert "Interaction mode: ask" in prompt
    assert "Board state (computed by OpenClass): EMPTY." in prompt
    assert "Current user message:\nExplain the current document." in prompt
    assert "uploaded source excerpt" not in prompt
    assert "Uploaded source title" not in prompt
    assert "Uploaded chapter" not in prompt
    assert "10-12" not in prompt


def test_codex_turn_prompt_keeps_current_board_selection_for_editing() -> None:
    prompt = codex_chat._turn_prompt(
        ChatRequest(
            message="Rewrite this paragraph.",
            interaction_mode="direct_edit",
            selection=SelectionRef(
                kind="board",
                excerpt="Current board paragraph",
                heading_path=["Section"],
            ),
        ),
        is_new_thread=False,
        board_state="non_empty",
    )

    assert "Interaction mode: direct_edit" in prompt
    assert "Board state (computed by OpenClass): NON_EMPTY." in prompt
    assert "kind: board" in prompt
    assert "excerpt: Current board paragraph" in prompt
    assert "heading path: Section" in prompt


def test_board_state_detector_treats_whitespace_as_empty() -> None:
    assert codex_chat._board_state("") == "empty"
    assert codex_chat._board_state(" \n\t") == "empty"
    assert codex_chat._board_state("# Lesson") == "non_empty"


def test_codex_chat_passes_detected_board_state_to_every_turn(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text=" \n")
    prompts: list[str] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        prompts.append(kwargs["user_prompt"])
        return CodexTurnResult(
            thread_id="thread_board_state",
            turn_id=f"turn_{len(prompts)}",
            final_response="Waiting for the board update.",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Create learning material."),
        user_id=TEST_USER_ID,
    )
    codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Continue."),
        user_id=TEST_USER_ID,
    )

    assert len(prompts) == 2
    assert all("Board state (computed by OpenClass): EMPTY." in prompt for prompt in prompts)
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).metadata["board_state_before"] == "empty"
    assert current_head_commit(saved_lesson).metadata["board_state_after"] == "empty"


def test_codex_instructions_require_board_first_teaching() -> None:
    instructions = codex_chat.CODEX_DEVELOPER_INSTRUCTIONS
    normalized_instructions = " ".join(instructions.split())

    assert "start of every turn, read the current `board.md`" in instructions
    assert "sole source of truth" in instructions
    assert "board-first" in instructions
    assert "Before giving any substantive teaching content" in instructions
    assert "This applies even when the interaction mode is `ask`" in normalized_instructions
    assert "Never put a standalone lesson" in instructions
    assert "Do not duplicate the board's substantive teaching content in chat" in normalized_instructions


@pytest.fixture
def codex_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setenv("OPENCLASS_CODEX_WORKSPACE_ROOT", str(tmp_path / "codex-workspaces"))
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda _thread_id, **_kwargs: None,
    )
    return store


def test_codex_chat_preserves_frontend_contract_and_persists_thread(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    calls: list[dict[str, object]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        calls.append(kwargs)
        return CodexTurnResult(
            thread_id="thread_codex_1",
            turn_id=f"turn_{len(calls)}",
            final_response="Codex reply",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(
        codex_chat,
        "build_model_catalog",
        lambda _user_id: SimpleNamespace(
            defaults={"text": SimpleNamespace(model="gpt-5.6-sol")}
        ),
    )

    first = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Explain this without editing it.",
            text_model={
                "provider": "openai_codex",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "service_tier": "priority",
            },
        ),
        user_id=TEST_USER_ID,
    )
    second = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Continue."),
        user_id=TEST_USER_ID,
    )

    assert first.chatbot_message == "Codex reply"
    assert first.board_decision.action == "no_change"
    assert first.board_document_operation_status == "none"
    assert first.requirement_cleared is True
    assert first.active_requirement_sheet is None
    assert first.active_board_task_sheet is None
    assert first.learning_clarification.can_start is False
    assert first.learning_clarification.ready_for_board is False
    assert first.course_package.lessons[0].learning_requirements is None
    assert second.chatbot_message == "Codex reply"
    assert calls[0]["thread_id"] is None
    assert calls[0]["model"] == "gpt-5.6-sol"
    assert calls[0]["reasoning_effort"] == "xhigh"
    assert calls[0]["service_tier"] == "priority"
    assert calls[0]["service_tier_is_set"] is True
    assert calls[1]["thread_id"] == "thread_codex_1"
    assert calls[1]["last_turn_id"] == "turn_1"
    assert calls[1]["model"] == "gpt-5.6-sol"
    assert calls[1]["service_tier_is_set"] is False

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Existing board"
    assert saved_lesson.learning_requirements is None
    assert saved_lesson.board_task_requirements is None
    assert saved_lesson.active_interaction_session is None
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["assistant_message_source"] == "codex"
    assert commit.metadata["codex_thread_id"] == "thread_codex_1"
    assert commit.metadata["document_changed"] is False
    assert commit.metadata["requirement_cleared"] is True
    configured_commit = next(
        item
        for item in saved_lesson.history_graph.commits
        if item.metadata.get("codex_turn_id") == "turn_1"
    )
    assert configured_commit.metadata["codex_model"] == "gpt-5.6-sol"
    assert configured_commit.metadata["codex_reasoning_effort"] == "xhigh"
    assert configured_commit.metadata["codex_service_tier"] == "priority"
    assert configured_commit.metadata["codex_service_tier_is_set"] is True
    assert commit.metadata["codex_service_tier_is_set"] is False


def test_codex_chat_writes_only_final_markdown_back_to_rich_document(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    original_document_id = lesson.board_document.id
    original_page_settings = lesson.board_document.page_settings

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Revised board\n\n- First\n- Second\n", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_codex_edit",
            turn_id="turn_edit",
            final_response="Updated the document.",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Rewrite the right document."),
        user_id=TEST_USER_ID,
    )

    document = response.course_package.lessons[0].board_document
    assert response.board_decision.action == "edit_board"
    assert response.board_document_operation_status == "succeeded"
    assert document.id == original_document_id
    assert document.page_settings == original_page_settings
    assert document.content_text == "# Revised board\n\n- First\n- Second"
    assert "<h1>Revised board</h1>" in document.content_html
    assert document.content_json["content"][0]["type"] == "heading"
    assert list(codex_chat.codex_workspace_root().iterdir()) == []

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["kind"] == "board_document_edit"
    assert commit.metadata["codex_turn_id"] == "turn_edit"
    assert commit.metadata["document_changed"] is True


def test_codex_chat_passes_formula_ink_as_image_input(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    calls: list[dict[str, object]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        calls.append(kwargs)
        return CodexTurnResult("thread_formula", "turn_formula", "formula reviewed")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Use this handwritten formula.",
            formula_ink={
                "image_data_url": "data:image/png;base64,YQ==",
                "source_latex": None,
                "action": "reference",
            },
        ),
        user_id=TEST_USER_ID,
    )

    assert calls[0]["image_urls"] == ["data:image/png;base64,YQ=="]


def test_codex_chat_rejects_unexpected_file_without_committing(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    before = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    before_head = current_head_commit(before).id
    before_commit_count = len(before.history_graph.commits)

    def fake_turn(**kwargs) -> CodexTurnResult:
        workspace = Path(kwargs["cwd"])
        (workspace / "unexpected.txt").write_text("not allowed", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_bad",
            turn_id="turn_bad",
            final_response="done",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="unexpected file"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Create another file."),
            user_id=TEST_USER_ID,
        )

    after = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(after).id == before_head
    assert len(after.history_graph.commits) == before_commit_count
    assert after.board_document.content_text == "# Existing board"


def test_codex_chat_rejects_html_without_committing(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    discarded_threads: list[str] = []
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda thread_id, **_kwargs: discarded_threads.append(thread_id),
    )
    before_head = current_head_commit(
        codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    ).id

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("<h1>HTML is not allowed</h1>", encoding="utf-8")
        return CodexTurnResult("thread_html", "turn_html", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="contains HTML"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Use HTML."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).id == before_head
    assert saved_lesson.board_document.content_text == "# Existing board"
    assert discarded_threads == ["thread_html"]


def test_codex_chat_rejects_symlink_board_without_reading_target(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    lesson = _seed_workspace(codex_store)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret outside content", encoding="utf-8")

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.unlink()
        board_path.symlink_to(outside)
        return CodexTurnResult("thread_symlink", "turn_symlink", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="regular file"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Read another file."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Existing board"


def test_codex_chat_detects_concurrent_document_change(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)

    def fake_turn(**kwargs) -> CodexTurnResult:
        workspace = codex_store.load_for_user(TEST_USER_ID)
        current_lesson = workspace.packages[0].lessons[0]
        changed_document = build_document(
            title=current_lesson.board_document.title,
            content_text="# Manual change",
            document_id=current_lesson.board_document.id,
            page_settings=current_lesson.board_document.page_settings,
        )
        commit_operations(
            current_lesson,
            [],
            label="Manual edit",
            message="Concurrent manual edit",
            new_document=changed_document,
            metadata={"kind": "manual_document_edit"},
        )
        codex_store.save_for_user(TEST_USER_ID, workspace)
        return CodexTurnResult("thread_conflict", "turn_conflict", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="changed while Codex was working"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Rewrite it."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Manual change"
    assert current_head_commit(saved_lesson).metadata["kind"] == "manual_document_edit"


def test_codex_chat_serializes_turns_and_reloads_latest_document(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    calls: list[tuple[str | None, str]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        calls.append((kwargs["thread_id"], board_path.read_text(encoding="utf-8")))
        if len(calls) == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
            board_path.write_text("# First update", encoding="utf-8")
            return CodexTurnResult("thread_shared", "turn_first", "first done")
        second_entered.set()
        board_path.write_text("# Second update", encoding="utf-8")
        return CodexTurnResult("thread_shared", "turn_second", "second done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    errors: list[BaseException] = []

    def run(message: str) -> None:
        try:
            codex_chat.process_codex_chat_on_lesson(
                lesson.id,
                ChatRequest(message=message),
                user_id=TEST_USER_ID,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=run, args=("first",))
    second = threading.Thread(target=run, args=("second",))
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert errors == []
    assert calls == [
        (None, "# Existing board"),
        ("thread_shared", "# First update"),
    ]
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Second update"


def test_codex_chat_atomic_save_rejects_last_moment_target_change(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    discarded_threads: list[str] = []
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda thread_id, **_kwargs: discarded_threads.append(thread_id),
    )

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Codex update", encoding="utf-8")
        return CodexTurnResult("thread_conflict", "turn_conflict", "done")

    original_atomic_save = workspace_state.save_lesson_for_user_if_head

    def conflicting_atomic_save(user_id, next_lesson, **kwargs) -> bool:
        concurrent_workspace = codex_store.load_for_user(TEST_USER_ID)
        _package, concurrent_lesson = workspace_state.find_lesson_package(
            concurrent_workspace,
            lesson.id,
        )
        concurrent_document = build_document(
            title=concurrent_lesson.board_document.title,
            content_text="# Concurrent user update",
            document_id=concurrent_lesson.board_document.id,
            page_settings=concurrent_lesson.board_document.page_settings,
        )
        commit_operations(
            concurrent_lesson,
            operations=[],
            label="Concurrent update",
            message="A different writer changed the lesson.",
            new_document=concurrent_document,
        )
        codex_store.save_for_user(TEST_USER_ID, concurrent_workspace)
        return original_atomic_save(user_id, next_lesson, **kwargs)

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(
        workspace_state,
        "save_lesson_for_user_if_head",
        conflicting_atomic_save,
    )

    with pytest.raises(CodexAppServerError, match="lesson changed"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Update the document."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Concurrent user update"
    assert discarded_threads == ["thread_conflict"]


def test_lesson_atomic_save_preserves_concurrent_other_lesson_change(
    codex_store: SqliteCourseStore,
) -> None:
    target = _seed_workspace(codex_store)
    setup_workspace = codex_store.load_for_user(TEST_USER_ID)
    other = create_empty_lesson("Other lesson")
    setup_workspace.packages[0].lessons.append(other)
    codex_store.save_for_user(TEST_USER_ID, setup_workspace)

    codex_workspace = codex_store.load_for_user(TEST_USER_ID)
    _package, codex_target = workspace_state.find_lesson_package(codex_workspace, target.id)
    base_commit_id = current_head_commit(codex_target).id
    target_document = build_document(
        title=codex_target.board_document.title,
        content_text="# Codex target update",
        document_id=codex_target.board_document.id,
        page_settings=codex_target.board_document.page_settings,
    )
    commit_operations(
        codex_target,
        operations=[],
        label="Codex update",
        message="Update only the target lesson.",
        new_document=target_document,
    )

    latest_workspace = codex_store.load_for_user(TEST_USER_ID)
    _package, latest_other = workspace_state.find_lesson_package(latest_workspace, other.id)
    other_document = build_document(
        title=latest_other.board_document.title,
        content_text="# Other concurrent update",
        document_id=latest_other.board_document.id,
        page_settings=latest_other.board_document.page_settings,
    )
    commit_operations(
        latest_other,
        operations=[],
        label="Other update",
        message="Update a different lesson.",
        new_document=other_document,
    )
    codex_store.save_for_user(TEST_USER_ID, latest_workspace)

    assert codex_store.save_lesson_for_user_if_head(
        TEST_USER_ID,
        codex_target,
        expected_branch_name=codex_target.history_graph.current_branch,
        expected_head_commit_id=base_commit_id,
    )
    saved = codex_store.load_for_user(TEST_USER_ID)
    _package, saved_target = workspace_state.find_lesson_package(saved, target.id)
    _package, saved_other = workspace_state.find_lesson_package(saved, other.id)
    assert saved_target.board_document.content_text == "# Codex target update"
    assert saved_other.board_document.content_text == "# Other concurrent update"


def test_codex_chat_rejects_oversized_existing_board_before_turn(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="123456789")
    monkeypatch.setenv("OPENCLASS_CODEX_BOARD_MAX_BYTES", "8")
    monkeypatch.setattr(
        codex_chat,
        "run_codex_thread_turn",
        lambda **_kwargs: pytest.fail("Codex must not start for an oversized board"),
    )

    with pytest.raises(CodexAppServerError, match="current board exceeds"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Continue."),
            user_id=TEST_USER_ID,
        )


def test_codex_chat_cancels_turn_when_board_exceeds_runtime_quota(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="# Board")
    monkeypatch.setenv("OPENCLASS_CODEX_BOARD_MAX_BYTES", "64")
    before_head = current_head_commit(
        codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    ).id

    def oversized_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_bytes(b"x" * 1024)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if kwargs["is_cancelled"]():
                raise CodexTurnCancelledError("cancelled by quota")
            time.sleep(0.01)
        pytest.fail("quota monitor did not cancel the Codex turn")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", oversized_turn)

    with pytest.raises(CodexAppServerError, match="configured size limit"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Make it very large."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).id == before_head
    assert saved_lesson.board_document.content_text == "# Board"


def test_board_quota_cannot_exceed_process_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OPENCLASS_CODEX_BOARD_MAX_BYTES",
        str(CODEX_PROCESS_FILE_SIZE_LIMIT_BYTES + 1),
    )

    with pytest.raises(CodexAppServerError, match="process hard limit"):
        codex_chat._board_max_bytes()


def test_codex_app_server_command_uses_exact_board_permission_profile() -> None:
    command = codex_app_server._codex_app_server_command("/usr/local/bin/codex")
    rendered = "\n".join(command)

    assert 'default_permissions="openclass_board"' in rendered
    assert '":workspace_roots"={"board.md"="write"}' in rendered
    assert "permissions.openclass_board.network.enabled=false" in rendered
    assert 'approval_policy="never"' in rendered
    assert 'web_search="disabled"' in rendered
    assert "mcp_servers={}" in rendered
    assert "apps={_default={enabled=false}}" in rendered
    assert "features.apps=false" in rendered
    assert "features.hooks=false" in rendered
    assert "features.plugins=false" in rendered
    assert "features.computer_use=false" in rendered
    assert "--strict-config" in command
    assert "danger-full-access" not in rendered


def test_codex_app_server_process_has_file_size_hard_limit() -> None:
    command = codex_app_server._codex_limited_process_command("/usr/local/bin/codex")

    assert command[:2] == ["/bin/sh", "-c"]
    assert "ulimit -f" in command[2]
    assert str(CODEX_PROCESS_FILE_SIZE_LIMIT_BYTES // 1024) in command
    assert command[5:8] == ["/usr/local/bin/codex", "app-server", "--strict-config"]
    assert command[-2:] == ["-c", "features.workspace_dependencies=false"]


def test_effective_codex_config_rejects_legacy_sandbox_override() -> None:
    disabled_features = {
        feature: False
        for feature in (
            "apps",
            "auth_elicitation",
            "browser_use",
            "browser_use_external",
            "browser_use_full_cdp_access",
            "code_mode_host",
            "computer_use",
            "goals",
            "hooks",
            "image_generation",
            "in_app_browser",
            "multi_agent",
            "plugin_sharing",
            "plugins",
            "remote_plugin",
            "skill_mcp_dependency_install",
            "tool_call_mcp_elicitation",
            "tool_suggest",
            "workspace_dependencies",
        )
    }
    valid = {
        "config": {
            "sandbox_mode": None,
            "default_permissions": "openclass_board",
            "approval_policy": "never",
            "web_search": "disabled",
            "permissions": {
                "openclass_board": {
                    "filesystem": {
                        "glob_scan_max_depth": None,
                        ":minimal": "read",
                        ":workspace_roots": {"board.md": "write"},
                    },
                    "network": {"enabled": False, "domains": None},
                }
            },
            "shell_environment_policy": {"inherit": "none"},
            "features": disabled_features,
            "mcp_servers": {},
            "apps": {"_default": {"enabled": False}},
            "hooks": None,
            "plugins": {},
        }
    }

    codex_app_server._validate_effective_permission_config(valid)
    invalid = {"config": {**valid["config"], "sandbox_mode": "workspace-write"}}
    with pytest.raises(CodexAppServerError, match="exact board.md-only profile"):
        codex_app_server._validate_effective_permission_config(invalid)

    external_tool = {
        "config": {
            **valid["config"],
            "features": {**disabled_features, "plugins": True},
        }
    }
    with pytest.raises(CodexAppServerError, match="exact board.md-only profile"):
        codex_app_server._validate_effective_permission_config(external_tool)


def test_new_and_reloaded_lessons_hide_legacy_ai_runtime(
    codex_store: SqliteCourseStore,
) -> None:
    lesson = create_empty_lesson("Codex-only lesson")
    initial_metadata = current_head_commit(lesson).metadata

    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None
    assert lesson.active_interaction_session is None
    assert lesson.board_teaching_guide is None
    assert lesson.board_teaching_progress is None
    assert initial_metadata["active_requirement_sheet_after"] is None
    assert initial_metadata["active_board_task_sheet_after"] is None
    assert initial_metadata["active_interaction_session_after"] is None

    lesson.learning_requirements = build_requirements(lesson.title)
    workspace = build_initial_workspace_state()
    workspace.packages[0].lessons.append(lesson)
    codex_store.save_for_user(TEST_USER_ID, workspace)

    reloaded = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert reloaded.learning_requirements is None
    assert reloaded.board_task_requirements is None
    assert reloaded.active_interaction_session is None
    assert reloaded.board_teaching_guide is None
    assert reloaded.board_teaching_progress is None


def test_branch_restore_does_not_revive_legacy_ai_runtime() -> None:
    lesson = create_empty_lesson("Legacy runtime")
    lesson.history_graph.commits[0].metadata["active_requirement_sheet_after"] = (
        build_requirements(lesson.title).model_dump(mode="json")
    )
    lesson.learning_requirements = build_requirements(lesson.title)

    create_branch(lesson, "codex-only", lesson.history_graph.commits[0].id)

    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None
    assert lesson.active_interaction_session is None
    assert lesson.board_teaching_guide is None
    assert lesson.board_teaching_progress is None


def test_thread_permission_response_rejects_broad_writable_root(tmp_path: Path) -> None:
    codex_app_server._validate_thread_permission_response(
        _thread_result("thread_safe", tmp_path),
        cwd=tmp_path,
    )
    unsafe = _thread_result("thread_unsafe", tmp_path)
    unsafe["sandbox"]["writableRoots"] = [str(tmp_path.resolve())]

    with pytest.raises(CodexAppServerError, match="exact board.md-only sandbox"):
        codex_app_server._validate_thread_permission_response(unsafe, cwd=tmp_path)


def test_structured_codex_adapter_uses_supported_read_only_sandbox() -> None:
    captured: dict[str, object] = {}

    class StopSession:
        deadline_monotonic = time.monotonic() + 5

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            captured.update(params)
            raise CodexAppServerError("stop after capturing thread params")

    with pytest.raises(CodexAppServerError, match="stop after capturing"):
        codex_app_server._run_structured_turn(
            session=StopSession(),  # type: ignore[arg-type]
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=ChatRequest,
        )

    assert captured["sandbox"] == "read-only"


def test_conversation_turn_collects_delta_and_final_message() -> None:
    class FakeSession:
        _next_id = 7

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.writes: list[dict] = []

        def _write(self, payload: dict) -> None:
            self.writes.append(payload)

        def _answer_server_request(self, message: dict) -> None:
            raise AssertionError(message)

    session = FakeSession()
    session._messages.put({"id": 7, "result": {"turn": {"id": "turn_7"}}})
    session._messages.put(
        {
            "method": "item/agentMessage/delta",
            "params": {"delta": "partial"},
        }
    )
    session._messages.put(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "text": "final response"}},
        }
    )
    session._messages.put(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn_7", "status": "completed"}},
        }
    )
    deltas: list[str] = []

    result = codex_app_server._run_conversation_turn(
        session=session,  # type: ignore[arg-type]
        thread_id="thread_7",
        model="gpt-5.5",
        cwd=Path("/tmp/board-only"),
        user_prompt="hello",
        image_urls=["data:image/png;base64,YQ=="],
        deadline_monotonic=time.monotonic() + 5,
        on_delta=deltas.append,
        is_cancelled=None,
        reasoning_effort="xhigh",
        service_tier="priority",
        service_tier_is_set=True,
    )

    assert result.thread_id == "thread_7"
    assert result.turn_id == "turn_7"
    assert result.final_response == "final response"
    assert deltas == ["partial"]
    params = session.writes[0]["params"]
    assert params["cwd"] == "/tmp/board-only"
    assert params["approvalPolicy"] == "never"
    assert params["effort"] == "xhigh"
    assert params["serviceTier"] == "priority"
    assert params["input"][1] == {
        "type": "image",
        "url": "data:image/png;base64,YQ==",
        "detail": "original",
    }
    assert "sandboxPolicy" not in params


def test_runtime_settings_distinguish_inherited_and_standard_speed() -> None:
    inherited = codex_app_server._runtime_setting_params(
        reasoning_effort=None,
        service_tier=None,
        service_tier_is_set=False,
        include_effort=True,
    )
    standard = codex_app_server._runtime_setting_params(
        reasoning_effort=None,
        service_tier=None,
        service_tier_is_set=True,
        include_effort=True,
    )

    assert inherited == {}
    assert standard == {"serviceTier": None}


def test_existing_codex_thread_is_forked_before_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict]] = []

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            self.requests.append((method, params))
            return _thread_result("thread_fork", tmp_path)

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    observed_prompts: list[str] = []
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_run_conversation_turn",
        lambda **kwargs: (
            observed_prompts.append(kwargs["user_prompt"])
            or CodexTurnResult("thread_fork", "turn_fork", "done")
        ),
    )

    result = codex_app_server.run_codex_thread_turn(
        user_id=TEST_USER_ID,
        model="gpt-5.5",
        cwd=tmp_path,
        user_prompt="normal prompt",
        fallback_user_prompt="recovery prompt",
        developer_instructions="board only",
        thread_id="thread_base",
        last_turn_id="turn_base",
        service_tier="priority",
        service_tier_is_set=True,
    )

    assert session.requests[0][0] == "thread/fork"
    assert session.requests[0][1]["threadId"] == "thread_base"
    assert session.requests[0][1]["lastTurnId"] == "turn_base"
    assert session.requests[0][1]["ephemeral"] is False
    assert session.requests[0][1]["serviceTier"] == "priority"
    assert observed_prompts == ["normal prompt"]
    assert result.thread_id == "thread_fork"
    assert result.parent_thread_id == "thread_base"
    assert result.replaced_stale_thread_id is None


def test_stale_codex_thread_starts_fresh_with_recovery_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict]] = []

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            self.requests.append((method, params))
            if method == "thread/fork":
                raise CodexAppServerError("thread not found")
            return _thread_result("thread_recovered", tmp_path)

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    observed_prompts: list[str] = []
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_run_conversation_turn",
        lambda **kwargs: (
            observed_prompts.append(kwargs["user_prompt"])
            or CodexTurnResult("thread_recovered", "turn_recovered", "done")
        ),
    )

    result = codex_app_server.run_codex_thread_turn(
        user_id=TEST_USER_ID,
        model="gpt-5.5",
        cwd=tmp_path,
        user_prompt="normal prompt",
        fallback_user_prompt="conversation recovery prompt",
        developer_instructions="board only",
        thread_id="thread_missing",
        service_tier=None,
        service_tier_is_set=True,
    )

    assert [method for method, _params in session.requests] == ["thread/fork", "thread/start"]
    assert all(params["serviceTier"] is None for _method, params in session.requests)
    assert observed_prompts == ["conversation recovery prompt"]
    assert result.thread_id == "thread_recovered"
    assert result.parent_thread_id == "thread_missing"
    assert result.replaced_stale_thread_id == "thread_missing"


def test_non_stale_fork_error_is_not_retried_as_a_new_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.methods: list[str] = []

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

        def request(self, method: str, _params: dict, **_kwargs) -> dict:
            self.methods.append(method)
            raise CodexAppServerError("authentication failed")

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )

    with pytest.raises(CodexAppServerError, match="authentication failed"):
        codex_app_server.run_codex_thread_turn(
            user_id=TEST_USER_ID,
            model="gpt-5.5",
            cwd=tmp_path,
            user_prompt="normal prompt",
            fallback_user_prompt="recovery prompt",
            developer_instructions="board only",
            thread_id="thread_base",
        )

    assert session.methods == ["thread/fork"]
