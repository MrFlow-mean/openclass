from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import threading
from pathlib import Path
from typing import Callable

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    LearningClarificationStatus,
    SelectionRef,
)
from app.services import workspace_state
from app.services.codex_app_server import (
    CodexAppServerError,
    delete_codex_thread,
    run_codex_thread_turn,
)
from app.services.course_runtime import effective_requirements
from app.services.history import commit_operations, current_head_commit
from app.services.rich_document import build_document, document_changed, looks_like_html_content


BOARD_FILE_NAME = "board.md"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_BOARD_MAX_BYTES = 2 * 1024 * 1024
MAX_FORMULA_IMAGE_DATA_URL_CHARS = 12 * 1024 * 1024
CODEX_DEVELOPER_INSTRUCTIONS = """
You are Codex embedded as the single AI agent in OpenClass.

The user talks to you in the left conversation panel. The only user document you may access is
`board.md` in the current working directory; it is the document shown in the right panel. Read it
when it helps answer the user. If the user asks to create, rewrite, extend, shorten, reorganize, or
otherwise change the right document, edit `board.md` directly. If no document change is needed,
leave the file unchanged and answer normally.

Do not inspect parent directories, source code, environment variables, hidden files, other local
paths, network resources, plugins, or external tools. Do not create, rename, or delete files. Never
request broader permissions. Keep `board.md` as Markdown or plain text; do not put HTML in it.
Return the learner-facing response as your final message after any file edit is complete.
""".strip()


_turn_locks_guard = threading.Lock()
_turn_locks: dict[str, threading.Lock] = {}


def codex_workspace_root() -> Path:
    configured = (os.getenv("OPENCLASS_CODEX_WORKSPACE_ROOT") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = Path.home() / path
    else:
        path = Path.home() / ".openclass" / "codex-workspaces"
    return path.resolve()


def _workspace_key(*, user_id: str, lesson_id: str, branch_name: str) -> str:
    identity = "\0".join((user_id, lesson_id, branch_name))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _turn_lock(key: str) -> threading.Lock:
    with _turn_locks_guard:
        return _turn_locks.setdefault(key, threading.Lock())


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _prepare_workspace(workspace: Path, content_text: str) -> Path:
    root = workspace.parent
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if workspace.is_symlink():
        raise CodexAppServerError("Codex board workspace must not be a symbolic link")
    workspace.mkdir(parents=False, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
        workspace.chmod(0o700)
    except OSError:
        pass
    for child in workspace.iterdir():
        if child.name != BOARD_FILE_NAME:
            _remove_path(child)
    board_path = workspace / BOARD_FILE_NAME
    if board_path.is_symlink():
        board_path.unlink()
    board_path.write_text(content_text, encoding="utf-8")
    try:
        board_path.chmod(0o600)
    except OSError:
        pass
    return board_path


def _board_max_bytes() -> int:
    configured = (os.getenv("OPENCLASS_CODEX_BOARD_MAX_BYTES") or "").strip()
    if not configured:
        return DEFAULT_BOARD_MAX_BYTES
    try:
        value = int(configured)
    except ValueError as exc:
        raise CodexAppServerError("OPENCLASS_CODEX_BOARD_MAX_BYTES must be an integer") from exc
    if value <= 0:
        raise CodexAppServerError("OPENCLASS_CODEX_BOARD_MAX_BYTES must be positive")
    return value


def _read_validated_board(workspace: Path) -> str:
    entries = list(workspace.iterdir())
    if len(entries) != 1 or entries[0].name != BOARD_FILE_NAME:
        raise CodexAppServerError("Codex board workspace contains an unexpected file")
    board_path = entries[0]
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise CodexAppServerError("This platform cannot safely open the Codex board output")
    flags = os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
    max_bytes = _board_max_bytes()
    try:
        descriptor = os.open(board_path, flags)
    except OSError as exc:
        raise CodexAppServerError("Codex board output must be a regular file") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise CodexAppServerError("Codex board output must be a regular file")
        if info.st_size > max_bytes:
            raise CodexAppServerError("Codex board output exceeds the configured size limit")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            raw_content = handle.read(max_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw_content) > max_bytes:
        raise CodexAppServerError("Codex board output exceeds the configured size limit")
    if [entry.name for entry in workspace.iterdir()] != [BOARD_FILE_NAME]:
        raise CodexAppServerError("Codex board workspace contains an unexpected file")
    try:
        content_text = raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CodexAppServerError("Codex board output must be valid UTF-8") from exc
    if looks_like_html_content(content_text):
        raise CodexAppServerError("Codex board output contains HTML instead of Markdown")
    return content_text


def _codex_model(request: ChatRequest) -> str:
    if request.text_model is not None and request.text_model.provider == "openai_codex":
        selected = request.text_model.model.strip()
        if selected:
            return selected
    return (os.getenv("OPENAI_CODEX_MODEL") or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL


def _thread_reference_for_current_branch(lesson) -> tuple[str | None, str | None]:
    branch_name = lesson.history_graph.current_branch
    commits_by_id = {commit.id: commit for commit in lesson.history_graph.commits}
    pending = [current_head_commit(lesson).id]
    visited: set[str] = set()
    while pending:
        commit_id = pending.pop()
        if commit_id in visited:
            continue
        visited.add(commit_id)
        commit = commits_by_id.get(commit_id)
        if commit is None:
            continue
        metadata = commit.metadata if isinstance(commit.metadata, dict) else {}
        if metadata.get("kind") == "restore_snapshot":
            continue
        thread_id = metadata.get("codex_thread_id")
        if commit.branch_name == branch_name and isinstance(thread_id, str) and thread_id.strip():
            turn_id = metadata.get("codex_turn_id")
            return (
                thread_id.strip(),
                turn_id.strip() if isinstance(turn_id, str) and turn_id.strip() else None,
            )
        if commit.branch_name == branch_name:
            pending.extend(commit.parent_ids)
    return None, None


def _conversation_context(conversation: list[ConversationTurn]) -> str:
    lines = [
        f"{turn.role}: {turn.content.strip()}"
        for turn in conversation[-12:]
        if turn.content.strip()
    ]
    return "\n".join(lines)[-12000:]


def _selection_context(selection: SelectionRef | None) -> str:
    if selection is None:
        return ""
    details = [f"kind: {selection.kind}", f"excerpt: {selection.excerpt}"]
    if selection.heading_path:
        details.append(f"heading path: {' > '.join(selection.heading_path)}")
    if selection.source_title:
        details.append(f"source title: {selection.source_title}")
    if selection.source_chapter_title:
        details.append(f"source chapter: {selection.source_chapter_title}")
    if selection.source_page_range:
        details.append(f"source pages: {selection.source_page_range}")
    return "\n".join(details)


def _turn_prompt(request: ChatRequest, *, is_new_thread: bool) -> str:
    sections: list[str] = []
    if is_new_thread:
        conversation = _conversation_context(request.conversation)
        if conversation:
            sections.append(f"Conversation already visible to the user:\n{conversation}")
    selection = _selection_context(request.selection)
    if selection:
        sections.append(f"Current user selection:\n{selection}")
    if request.formula_ink is not None and request.formula_ink.source_latex:
        sections.append(
            "Formula context:\n"
            f"action: {request.formula_ink.action}\n"
            f"latex: {request.formula_ink.source_latex}"
        )
    sections.append(f"Current user message:\n{request.message}")
    return "\n\n".join(sections)


def _formula_image_urls(request: ChatRequest) -> list[str]:
    if request.formula_ink is None:
        return []
    image_data_url = request.formula_ink.image_data_url.strip()
    if (
        not image_data_url.lower().startswith("data:image/")
        or ";base64," not in image_data_url[:160].lower()
        or len(image_data_url) > MAX_FORMULA_IMAGE_DATA_URL_CHARS
    ):
        raise CodexAppServerError("Formula ink must be a bounded base64 image data URL")
    return [image_data_url]


def _discard_uncommitted_thread(thread_id: str) -> None:
    try:
        delete_codex_thread(thread_id)
    except Exception:
        pass


def _neutral_clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=0,
        label="",
        reason="",
        missing_items=[],
        can_start=False,
        forced_start=False,
        summary="",
        next_question="",
        ready_for_board=False,
        work_mode=None,
        granularity=None,
    )


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def process_codex_chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    *,
    user_id: str,
    on_delta: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ChatResponse:
    with _turn_lock(user_id):
        initial_workspace = workspace_state.load_workspace_for_user(user_id)
        initial_package, initial_lesson = workspace_state.find_lesson_package(
            initial_workspace,
            lesson_id,
        )
        initial_package.active_lesson_id = initial_lesson.id
        branch_name = initial_lesson.history_graph.current_branch
        base_commit_id = current_head_commit(initial_lesson).id
        prior_thread_id, prior_turn_id = _thread_reference_for_current_branch(initial_lesson)
        workspace_key = _workspace_key(
            user_id=user_id,
            lesson_id=lesson_id,
            branch_name=branch_name,
        )
        workspace_root = codex_workspace_root()
        workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(
            prefix=f"{workspace_key[:16]}-",
            dir=workspace_root,
        ) as temporary_workspace:
            workspace_path = Path(temporary_workspace)
            _prepare_workspace(workspace_path, initial_lesson.board_document.content_text)
            user_prompt = _turn_prompt(request, is_new_thread=prior_thread_id is None)
            result = run_codex_thread_turn(
                model=_codex_model(request),
                cwd=workspace_path,
                user_prompt=user_prompt,
                fallback_user_prompt=(
                    _turn_prompt(request, is_new_thread=True)
                    if prior_thread_id is not None
                    else user_prompt
                ),
                developer_instructions=CODEX_DEVELOPER_INSTRUCTIONS,
                thread_id=prior_thread_id,
                last_turn_id=prior_turn_id,
                image_urls=_formula_image_urls(request),
                on_delta=on_delta,
                is_cancelled=is_cancelled,
            )
            try:
                codex_content = _read_validated_board(workspace_path)

                workspace = workspace_state.load_workspace_for_user(user_id)
                package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
                package.active_lesson_id = lesson.id
                if lesson.history_graph.current_branch != branch_name:
                    raise CodexAppServerError("The lesson branch changed while Codex was working")
                if current_head_commit(lesson).id != base_commit_id:
                    raise CodexAppServerError("The lesson changed while Codex was working")

                current_document = lesson.board_document
                if codex_content == current_document.content_text:
                    next_document = current_document
                else:
                    next_document = build_document(
                        title=current_document.title,
                        content_text=codex_content,
                        document_id=current_document.id,
                        page_settings=current_document.page_settings,
                    )
                changed = document_changed(current_document, next_document)
                lesson.board_teaching_guide = None
                lesson.board_teaching_progress = None
                lesson.learning_requirements = None
                lesson.board_task_requirements = None
                lesson.active_interaction_session = None
                clarification = _neutral_clarification()
                metadata = {
                    "kind": "board_document_edit" if changed else "basic_chat",
                    "user_message": request.message,
                    "assistant_message": result.final_response,
                    "assistant_message_source": "codex",
                    "interaction_mode": request.interaction_mode,
                    "selection": (
                        request.selection.model_dump(mode="json")
                        if request.selection is not None
                        else None
                    ),
                    "document_changed": changed,
                    "document_hash_before": _text_hash(current_document.content_text),
                    "document_hash_after": _text_hash(next_document.content_text),
                    "codex_thread_id": result.thread_id,
                    "codex_turn_id": result.turn_id,
                    "codex_parent_thread_id": result.parent_thread_id,
                    "codex_replaced_stale_thread_id": result.replaced_stale_thread_id,
                    "codex_model": _codex_model(request),
                    "codex_branch": branch_name,
                    "codex_base_commit_id": base_commit_id,
                    "active_requirement_sheet_after": None,
                    "active_board_task_sheet_after": None,
                    "active_interaction_session_after": None,
                    "learning_clarification_after": clarification.model_dump(mode="json"),
                    "requirement_cleared": True,
                    "board_task_cleared": True,
                }
                commit_operations(
                    lesson,
                    operations=[],
                    label="Codex document update" if changed else "Codex conversation",
                    message="Codex completed the user turn.",
                    new_document=next_document,
                    metadata=metadata,
                )
                workspace_state.save_workspace_for_user(user_id, workspace)
                return ChatResponse(
                    chatbot_message=result.final_response,
                    learning_requirement_sheet=effective_requirements(lesson),
                    active_requirement_sheet=None,
                    active_interaction_session=None,
                    learning_clarification=clarification,
                    board_task_sheet=None,
                    active_board_task_sheet=None,
                    board_task_questions=[],
                    board_decision=BoardDecision(
                        action="edit_board" if changed else "no_change",
                        reason=(
                            "Codex changed the right-side document."
                            if changed
                            else "Codex left the right-side document unchanged."
                        ),
                    ),
                    needs_clarification=False,
                    clarification_questions=[],
                    scope_options=[],
                    focus_candidates=[],
                    requirement_cleared=True,
                    board_document_operation_status="succeeded" if changed else "none",
                    board_patch_diff=[],
                    course_package=workspace_state.package_view_for_lesson(
                        workspace,
                        package,
                        lesson.id,
                    ),
                )
            except Exception:
                _discard_uncommitted_thread(result.thread_id)
                raise


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    selection = (
        SelectionRef(
            kind="board",
            excerpt=selection_text,
            location_kind="target_range",
        )
        if selection_text
        else None
    )
    return process_codex_chat_on_lesson(
        lesson_id,
        ChatRequest(
            message=instruction,
            interaction_mode="direct_edit",
            selection=selection,
            conversation=conversation,
        ),
        user_id=user_id,
    )
