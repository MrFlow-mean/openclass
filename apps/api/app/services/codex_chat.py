from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel

from app.models import (
    AgentActivityEvent,
    BoardDecision,
    BoardDocument,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    LearningClarificationStatus,
    LearningRequirementSheet,
    RetrievalEvidence,
    SelectionRef,
    SourceVisualAsset,
    SourceVisualEvidence,
)
from app.services import workspace_state
from app.services.ai_execution_adapter import (
    BoardGenerationExecutionRequest,
    CodexAIExecutionAdapter,
)
from app.services.ai_model_catalog import build_model_catalog
from app.services.blank_board_intake import process_blank_board_turn
from app.services.board_visual_insertion import (
    BoardInsertionPlan,
    apply_board_insertion_plan,
    build_board_insertion_plan,
    derive_board_visual_placements,
)
from app.services.chat_attachments import prepare_chat_attachments, verify_chat_attachments
from app.services.codex_app_server import (
    CodexAppServerError,
    CodexTurnCancelledError,
    CodexTurnResult,
    delete_codex_thread,
    run_codex_thread_turn,
)
from app.services.history import commit_operations, current_head_commit
from app.services.follow_up_suggestions import generate_follow_up_suggestions
from app.services.lesson_factory import build_requirements
from app.services.rich_document import (
    build_document,
    document_changed,
    looks_like_html_content,
    rebuild_document_from_content_json,
    would_flatten_rich_document,
)
from app.services.source_grounded_board import (
    SOURCE_BOARD_TOKEN_BUDGET,
    SourceGroundedBoardError,
    resolve_source_grounded_board_plan,
)
from app.services.source_structure_store import source_structure_store
from app.services.source_visual_region_resolution import resolve_visual_clues_for_requirement


BOARD_FILE_NAME = "board.md"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_BOARD_MAX_BYTES = 2 * 1024 * 1024
MAX_FORMULA_IMAGE_DATA_URL_CHARS = 12 * 1024 * 1024
MAX_SOURCE_VISUAL_BYTES = 4 * 1024 * 1024
MAX_SOURCE_VISUALS_PER_BATCH = 8
_PRESERVED_VISUAL_MARKER_RE = re.compile(
    r"\[\[OPENCLASS_PRESERVED_VISUAL_[0-9a-f]{12}_\d{4}\]\]"
)
BoardState = Literal["empty", "non_empty"]
CODEX_DEVELOPER_INSTRUCTIONS = """
You are Codex embedded as the single AI agent in OpenClass.

The user talks to you in the left conversation panel. The only user document you may access is
`board.md` in the current working directory; it is the document shown in the right panel. At the
start of every turn, read the current `board.md`. Treat its current contents, rather than prior
thread memory, as the source of truth for the right document. When the current prompt contains a
`Verified source context`, that backend-verified context is an additional mandatory source of truth
for this turn.

Never ignore a `Verified source context`. Before responding or editing, inspect its confirmed
reference metadata and frozen evidence. Ground the requested work in that evidence instead of
continuing from board content or thread memory alone. If the user asks to continue or extend the
board from the reference, add material derived from the verified source range and do not silently
substitute a nearby topic. Keep source-derived claims within the supplied range. If a visual
manifest is present, handle every item exactly once. For a regular table or a single-direction
linear flow whose labels and relationships are fully readable, recreate it as editable Markdown
and then write its `recreation_marker` once on a standalone line. For a complex, branching,
networked, spatial, ambiguous, or partially unreadable visual, write its `marker` once on a
standalone line so the backend can insert the complete verified original. Never use both markers,
never crop a visual yourself, and never omit both.

For a non-empty board, keep every teaching action grounded in the current `board.md`. If the
learner asks for teaching material that is absent, add it to the board before discussing it. Never
put a standalone lesson, exercise, example set, or course text only in the left conversation
panel. After a board update, keep the learner-facing response brief and do not duplicate the
board's substantive content in chat.

For non-teaching conversation, answer normally and leave `board.md` unchanged. If an intended
board change lacks a safe target or enough information, ask one concise clarification and leave the
board unchanged.

Do not inspect parent directories, source code, environment variables, hidden files, other local
paths, network resources, plugins, or external tools. Do not create, rename, or delete files. Never
request broader permissions. Keep `board.md` as Markdown or plain text; do not put HTML in it.

Any standalone line matching `[[OPENCLASS_PRESERVED_VISUAL_...]]` is a backend-owned placeholder
for an existing board image. Preserve every such line exactly once and in its current relative
position. Never alter, duplicate, move, explain, wrap, or remove these placeholders.

Formatting contract for `board.md`: use fenced code blocks only for executable or source code. Never
put a formula, equation, key sentence, definition, explanation, or ordinary text inside a code fence.
Write display formulas as `$$` on their own lines with LaTeX inside; write inline formulas as `$...$`.
Use ordinary paragraphs, lists, headings, and `**bold**` for key statements. OpenClass renders those
formula delimiters as HTML math in the board, while Markdown remains the source of truth.
Return the learner-facing response as your final message after any file edit is complete.
""".strip()

BOARD_GENERATION_DEVELOPER_INSTRUCTIONS = """
You are Codex acting as the board-writing capability inside OpenClass. The only user document you
may access is `board.md` in the current working directory. It is empty at the start of this turn.
The user prompt contains a frozen, structured learning requirement and a teaching plan that were
persisted before this call. Generate a self-contained teaching board from only that payload and
write it to `board.md` as Markdown or plain text. Do not infer requirements from thread memory, do
not ask the learner questions, and do not put HTML in the file. Use fenced code blocks only for real
code. Write every display formula as `$$` on its own lines with LaTeX inside, and keep key sentences
as normal Markdown text or `**bold**`, never inside a code fence. Do not inspect any other path,
source code, environment variable, network resource, plugin, or external tool.

The frozen payload may include a `visual_manifest`. Every manifest item is verified evidence from
the learner-selected source scope. Preserve manifest order and handle every item exactly once.

For a manifest item without `recreation_marker`, write its `marker` exactly once as a standalone
ordinary paragraph immediately after the paragraph that introduces it. OpenClass will materialize
the backend-owned editable table or original asset.

For a manifest item with `recreation_marker`, inspect its corresponding image input when
`image_input_index` is present, otherwise use only its supplied extracted visual description. Choose
exactly one of these two paths:

1. Editable recreation: use this only when every essential label, value, and relationship is
readable and the visual is either a regular row/column or grid table, or one single-direction linear
flow with no branches, cross-links, nested topology, or spatial relationship that would be lost.
Recreate it as editable Markdown: a Markdown table for tabular data, or ordinary text/list content
with arrows for a linear flow. Do not use HTML, image syntax, Mermaid, ASCII box art, or a code
fence. Then write `recreation_marker` exactly once as a standalone paragraph immediately after the
recreated content.

2. Original asset: use this for complex diagrams, branching or networked flows, dense hardware or
system layouts, illustrations, ambiguous scans, unreadable labels, or any visual whose meaning
depends on two-dimensional placement. Write `marker` exactly once as a standalone ordinary
paragraph after the paragraph that introduces it. OpenClass will insert the verified crop.

Never write both choice markers, and never omit both. Never alter, invent, duplicate, wrap, or place
a marker inside a heading, list, table, code fence, formula, link, or image syntax. Do not write image
bytes, base64, HTML, file paths, or URLs. OpenClass validates the choice and placement after this
turn. Return only a brief completion acknowledgement after the file is written.
""".strip()

SOURCE_BATCH_SUMMARY_INSTRUCTIONS = """
Summarize one consecutive source batch for later board generation. Preserve definitions,
relationships, examples, qualifications, formulas, and section order. Use only the supplied text.
The summary must remain traceable to the supplied chunk IDs and must not add outside knowledge.
""".strip()

SOURCE_VISUAL_ANALYSIS_INSTRUCTIONS = """
You are analyzing a bounded batch of source visuals for later board generation. Do not edit
board.md. Describe every image in the supplied order, preserve labels, axes, table relationships,
and visible qualifications, and identify each description with the corresponding visual ID from
the prompt. Do not add facts that are not visible in the image or its supplied metadata.
""".strip()


class _SourceBatchSummary(BaseModel):
    summary: str


@dataclass(frozen=True)
class CodexBoardGenerationResult:
    """Codex turn plus the backend-owned visual insertion contract."""

    turn: CodexTurnResult
    insertion_plan: BoardInsertionPlan
    visual_assets: dict[str, tuple[SourceVisualAsset, bytes]]

    @property
    def thread_id(self) -> str:
        return self.turn.thread_id

    @property
    def turn_id(self) -> str | None:
        return self.turn.turn_id

    @property
    def final_response(self) -> str:
        return self.turn.final_response

    @property
    def activity(self) -> list[AgentActivityEvent]:
        return self.turn.activity


@dataclass(frozen=True)
class ExistingBoardSourceContext:
    """Backend-verified source material for one existing-board Codex turn."""

    prompt_context: str
    image_inputs: list[str]
    insertion_plan: BoardInsertionPlan
    visual_assets: dict[str, tuple[SourceVisualAsset, bytes]]
    requirement: LearningRequirementSheet


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
    encoded_content = content_text.encode("utf-8")
    if len(encoded_content) > _board_max_bytes():
        raise CodexAppServerError("The current board exceeds the configured size limit")
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
    board_path.write_bytes(encoded_content)
    try:
        board_path.chmod(0o600)
    except OSError:
        pass
    return board_path


def _document_for_codex(
    document: BoardDocument,
) -> tuple[str, dict[str, dict[str, Any]]]:
    content_json = copy.deepcopy(document.content_json)
    nodes = content_json.get("content") if isinstance(content_json, dict) else None
    if not isinstance(nodes, list):
        return document.content_text, {}
    preserved: dict[str, dict[str, Any]] = {}
    visual_index = 0

    def replace_visuals(items: list[Any]) -> list[Any]:
        nonlocal visual_index
        replaced: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                replaced.append(item)
                continue
            if item.get("type") == "resourceVisualBlock":
                digest = hashlib.sha256(
                    json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                        "utf-8"
                    )
                ).hexdigest()[:12]
                marker = f"[[OPENCLASS_PRESERVED_VISUAL_{digest}_{visual_index:04d}]]"
                visual_index += 1
                preserved[marker] = copy.deepcopy(item)
                replaced.append(
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": marker}],
                    }
                )
                continue
            content = item.get("content")
            if isinstance(content, list):
                item["content"] = replace_visuals(content)
            replaced.append(item)
        return replaced

    content_json["content"] = replace_visuals(nodes)
    # The rich editor may persist a plain content_text projection while
    # content_json still contains headings, lists, tables, and math nodes.  Codex
    # must always receive a Markdown serialization of the canonical rich tree;
    # otherwise a later write can flatten every heading into a paragraph/list.
    serialized = rebuild_document_from_content_json(document, content_json)
    return serialized.content_text, preserved


def _restore_preserved_visuals(
    document: BoardDocument,
    preserved: dict[str, dict[str, Any]],
) -> BoardDocument:
    if not preserved:
        return document
    content_json = copy.deepcopy(document.content_json)
    nodes = content_json.get("content") if isinstance(content_json, dict) else None
    if not isinstance(nodes, list):
        raise CodexAppServerError("Codex board output lost protected visual placeholders")
    counts = {marker: 0 for marker in preserved}

    def restore(items: list[Any]) -> list[Any]:
        restored: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                restored.append(item)
                continue
            text = _tiptap_plain_text(item).strip()
            if item.get("type") == "paragraph" and text in preserved:
                counts[text] += 1
                restored.append(copy.deepcopy(preserved[text]))
                continue
            content = item.get("content")
            if isinstance(content, list):
                item["content"] = restore(content)
            restored.append(item)
        return restored

    content_json["content"] = restore(nodes)
    tokens = _PRESERVED_VISUAL_MARKER_RE.findall(document.content_text)
    if set(tokens) != set(preserved) or any(count != 1 for count in counts.values()):
        raise CodexAppServerError("Codex board output altered protected visual placeholders")
    return rebuild_document_from_content_json(document, content_json)


def _tiptap_plain_text(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("type") == "text":
            return str(value.get("text") or "")
        content = value.get("content")
        if isinstance(content, list):
            return "".join(_tiptap_plain_text(child) for child in content)
    if isinstance(value, list):
        return "".join(_tiptap_plain_text(child) for child in value)
    return ""


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


def _watch_board_quota(
    board_path: Path,
    *,
    max_bytes: int,
    stop_event: threading.Event,
    quota_exceeded: threading.Event,
) -> None:
    while not stop_event.wait(0.02):
        try:
            info = board_path.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size <= max_bytes:
            continue
        quota_exceeded.set()
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(
                board_path,
                os.O_WRONLY | no_follow | getattr(os, "O_CLOEXEC", 0),
            )
            opened = os.fstat(descriptor)
            if stat.S_ISREG(opened.st_mode) and opened.st_size > max_bytes:
                os.ftruncate(descriptor, max_bytes)
        except OSError:
            pass
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return


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


def _codex_model(request: ChatRequest, *, user_id: str) -> str:
    if request.text_model is not None and request.text_model.provider == "openai_codex":
        selected = request.text_model.model.strip()
        if selected:
            return selected
    try:
        return build_model_catalog(user_id).defaults["text"].model
    except Exception:
        pass
    return (os.getenv("OPENAI_CODEX_MODEL") or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL


def _codex_reasoning_effort(request: ChatRequest) -> str | None:
    selection = request.text_model
    if selection is None or selection.provider != "openai_codex":
        return None
    normalized = str(selection.reasoning_effort or "").strip()
    return normalized or None


def _codex_service_tier(request: ChatRequest) -> tuple[str | None, bool]:
    selection = request.text_model
    if selection is None or selection.provider != "openai_codex":
        return None, False
    if "service_tier" not in selection.model_fields_set:
        return None, False
    normalized = str(selection.service_tier or "").strip()
    return normalized or None, True


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
    if selection is None or selection.kind == "source":
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


def _board_state(content_text: str) -> BoardState:
    return "empty" if not content_text.strip() else "non_empty"


def _board_state_context(board_state: BoardState) -> str:
    if board_state == "empty":
        return (
            "Board state (computed by OpenClass): EMPTY.\n"
            "The right-side board contains no learning content. For a teaching request, create "
            "the initial board before giving substantive teaching content."
        )
    return (
        "Board state (computed by OpenClass): NON_EMPTY.\n"
        "The right-side board already contains learning content. Read it before responding and "
        "keep teaching grounded in it."
    )


def _turn_prompt(
    request: ChatRequest,
    *,
    is_new_thread: bool,
    board_state: BoardState,
    verified_source_context: str = "",
) -> str:
    sections: list[str] = []
    sections.append(f"Interaction mode: {request.interaction_mode}")
    sections.append(_board_state_context(board_state))
    if is_new_thread:
        conversation = _conversation_context(request.conversation)
        if conversation:
            sections.append(f"Conversation already visible to the user:\n{conversation}")
    selection = _selection_context(request.selection)
    if selection:
        sections.append(f"Current user selection:\n{selection}")
    if verified_source_context:
        sections.append(f"Verified source context (mandatory for this turn):\n{verified_source_context}")
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


def _discard_uncommitted_thread(thread_id: str, *, user_id: str) -> None:
    try:
        delete_codex_thread(thread_id, user_id=user_id)
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


def _run_frozen_board_generation(
    *,
    user_id: str,
    model: str,
    requirement: LearningRequirementSheet,
    teaching_plan: str,
    image_inputs: list[str] | None = None,
    visual_manifest: list[dict[str, Any]] | None = None,
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None = None,
) -> tuple[CodexTurnResult, str]:
    workspace_root = codex_workspace_root()
    workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="blank-board-", dir=workspace_root) as temporary:
        workspace_path = Path(temporary)
        board_path = _prepare_workspace(workspace_path, "")
        quota_stop = threading.Event()
        quota_exceeded = threading.Event()
        quota_monitor = threading.Thread(
            target=_watch_board_quota,
            kwargs={
                "board_path": board_path,
                "max_bytes": _board_max_bytes(),
                "stop_event": quota_stop,
                "quota_exceeded": quota_exceeded,
            },
            daemon=True,
        )
        quota_monitor.start()

        def turn_is_cancelled() -> bool:
            return quota_exceeded.is_set() or bool(is_cancelled and is_cancelled())

        payload = {
            "learning_requirement": requirement.model_dump(mode="json"),
            "teaching_plan": teaching_plan,
            "visual_manifest": visual_manifest or [],
        }
        try:
            result = run_codex_thread_turn(
                user_id=user_id,
                model=model,
                cwd=workspace_path,
                user_prompt=(
                    "Frozen board-generation payload:\n"
                    + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                ),
                developer_instructions=BOARD_GENERATION_DEVELOPER_INSTRUCTIONS,
                thread_id=None,
                image_urls=image_inputs,
                on_delta=None,
                on_activity=on_activity,
                is_cancelled=turn_is_cancelled,
            )
        finally:
            quota_stop.set()
            quota_monitor.join(timeout=0.2)
        if quota_exceeded.is_set():
            _discard_uncommitted_thread(result.thread_id, user_id=user_id)
            raise CodexAppServerError("Codex board output exceeds the configured size limit")
        try:
            content = _read_validated_board(workspace_path)
            if not content.strip():
                raise CodexAppServerError(
                    "Board generation completed without writing board.md"
                )
        except Exception:
            _discard_uncommitted_thread(result.thread_id, user_id=user_id)
            raise
        return result, content


def _source_visual_image_urls(
    *,
    user_id: str,
    requirement: LearningRequirementSheet,
) -> list[str]:
    image_urls: list[str] = []
    for evidence in requirement.source_grounding.frozen_visual_evidence:
        if _is_structured_table_evidence(evidence):
            continue
        stored = _read_frozen_source_visual(user_id=user_id, evidence=evidence)
        image_url = _image_data_url(stored)
        if not image_url:
            raise CodexAppServerError(
                f"Frozen source visual {evidence.visual_id} cannot be safely loaded"
            )
        image_urls.append(image_url)
    return image_urls


def _read_frozen_source_visual(
    *,
    user_id: str,
    evidence: SourceVisualEvidence,
) -> tuple[SourceVisualAsset, bytes] | None:
    if not evidence.package_id or not evidence.source_ingestion_id or not evidence.visual_id:
        return None
    stored = source_structure_store.read_visual_bytes(
        owner_user_id=user_id,
        package_id=evidence.package_id,
        source_id=evidence.source_ingestion_id,
        visual_id=evidence.visual_id,
    )
    if stored is None:
        return None
    visual, content = stored
    if (
        evidence.anchor_status != "verified"
        or visual.anchor_status != "verified"
        or not evidence.content_hash
        or visual.content_hash != evidence.content_hash
        or not evidence.position_hash
        or visual.position_hash != evidence.position_hash
        or len(content) > MAX_SOURCE_VISUAL_BYTES
    ):
        return None
    return visual, content


def _image_data_url(stored: tuple[SourceVisualAsset, bytes] | None) -> str:
    if stored is None:
        return ""
    visual, content = stored
    mime_type = visual.mime_type.split(";", 1)[0].strip().lower()
    if mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return ""
    try:
        encoded = base64.b64encode(content).decode("ascii")
    except (TypeError, ValueError):
        return ""
    return f"data:{mime_type};base64,{encoded}"


def _is_structured_table_evidence(evidence: SourceVisualEvidence) -> bool:
    return evidence.kind == "table" and bool(evidence.table_data)


def _prepare_source_generation_inputs(
    *,
    adapter: CodexAIExecutionAdapter,
    requirement: LearningRequirementSheet,
    owner_user_id: str,
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None,
) -> tuple[LearningRequirementSheet, list[str]]:
    prepared = requirement.model_copy(deep=True)
    prepared = resolve_visual_clues_for_requirement(
        adapter=adapter,
        requirement=prepared,
        owner_user_id=owner_user_id,
        is_cancelled=is_cancelled,
        on_activity=on_activity,
    )
    grounding = prepared.source_grounding
    evidence = grounding.frozen_evidence
    if sum(item.token_count for item in evidence) > SOURCE_BOARD_TOKEN_BUDGET:
        chunk_ids = list(
            dict.fromkeys(
                chunk_id
                for reference in grounding.confirmed_references
                for chunk_id in reference.chunk_ids
            )
        )
        chunks = source_structure_store.source_chunks_by_ids(
            owner_user_id=owner_user_id,
            chunk_ids=chunk_ids,
        )
        if len({chunk.id for chunk in chunks}) != len(set(chunk_ids)):
            raise CodexAppServerError(
                "The frozen source range cannot be fully reconstructed from its chunk IDs"
            )
        prototype = evidence[0]
        summaries: list[RetrievalEvidence] = []
        for batch_index, batch in enumerate(_source_text_batches(chunks)):
            batch_chunk_ids = list(dict.fromkeys(item[0] for item in batch))
            response = adapter.parse_structured(
                system_prompt=SOURCE_BATCH_SUMMARY_INSTRUCTIONS,
                user_prompt=json.dumps(
                    {
                        "batch_index": batch_index,
                        "chunks": [
                            {"chunk_id": chunk_id, "text": text}
                            for chunk_id, text in batch
                        ],
                    },
                    ensure_ascii=False,
                ),
                schema=_SourceBatchSummary,
            )
            if on_activity is not None:
                for event in response.activity:
                    on_activity(event)
            summary = _SourceBatchSummary.model_validate(response.output_parsed).summary.strip()
            if not summary:
                raise CodexAppServerError("Source batch summarization returned empty content")
            summaries.append(
                prototype.model_copy(
                    update={
                        "id": f"{prototype.id}:summary:{batch_index}",
                        "chunk_ids": batch_chunk_ids,
                        "excerpt": summary[:360],
                        "expanded_text": summary,
                        "token_count": max(1, (len(summary) + 3) // 4),
                        "reason": "Provider-generated summary of one consecutive frozen source batch.",
                        "metadata": {
                            **prototype.metadata,
                            "retrieval_mode": "frozen_source_batch_summary",
                            "batch_index": batch_index,
                            "covered_chunk_ids": batch_chunk_ids,
                        },
                    }
                )
            )
        grounding.frozen_evidence = summaries

    visuals = grounding.frozen_visual_evidence
    raster_visuals = [item for item in visuals if not _is_structured_table_evidence(item)]
    if len(raster_visuals) <= MAX_SOURCE_VISUALS_PER_BATCH:
        return prepared, _source_visual_image_urls(
            user_id=owner_user_id,
            requirement=prepared,
        )
    analyzed_visuals: dict[str, SourceVisualEvidence] = {}
    for batch_start in range(0, len(raster_visuals), MAX_SOURCE_VISUALS_PER_BATCH):
        visual_batch = raster_visuals[batch_start : batch_start + MAX_SOURCE_VISUALS_PER_BATCH]
        image_inputs = [
            _image_data_url(
                _read_frozen_source_visual(user_id=owner_user_id, evidence=item)
            )
            for item in visual_batch
        ]
        if any(not image_input for image_input in image_inputs):
            raise CodexAppServerError("A frozen source visual cannot be safely loaded")
        analysis = adapter.analyze_image_batch(
            prompt=json.dumps(
                {
                    "visuals": [item.model_dump(mode="json") for item in visual_batch],
                },
                ensure_ascii=False,
            ),
            image_inputs=image_inputs,
            is_cancelled=is_cancelled,
            on_activity=on_activity,
        ).strip()
        if not analysis:
            raise CodexAppServerError("Source visual analysis returned empty content")
        visual_ids = [item.visual_id for item in visual_batch]
        analyzed_visuals.update(
            {
                item.visual_id: item.model_copy(
                update={
                    "extracted_text": "\n\n".join(
                        part for part in [item.extracted_text, analysis] if part
                    ),
                    "surrounding_text": "\n\n".join(
                        part
                        for part in [
                            item.surrounding_text,
                            f"Analyzed visual batch: {', '.join(visual_ids)}",
                        ]
                        if part
                    ),
                }
                )
                for item in visual_batch
            }
        )
    grounding.frozen_visual_evidence = [
        analyzed_visuals.get(item.visual_id, item) for item in visuals
    ]
    return prepared, []


def _prepare_existing_board_source_context(
    *,
    owner_user_id: str,
    lesson,
    selection: SelectionRef | None,
    model: str,
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None,
) -> ExistingBoardSourceContext | None:
    """Resolve, freeze, and serialize a structured source reference before Codex runs.

    Raw source chips are intentionally not copied into the prompt.  Only evidence
    resolved against the authenticated package and frozen by the backend reaches
    Codex, so a visible reference can neither be ignored nor spoof its contents.
    """

    plan = resolve_source_grounded_board_plan(
        owner_user_id=owner_user_id,
        lesson=lesson,
        selection=selection,
    )
    if plan is None:
        return None
    adapter = CodexAIExecutionAdapter(
        owner_user_id=owner_user_id,
        model=model,
        board_runner=_run_codex_board_generation,
        image_analysis_runner=_run_codex_visual_analysis,
    )
    prepared_requirement, image_inputs = _prepare_source_generation_inputs(
        adapter=adapter,
        requirement=plan.requirement,
        owner_user_id=owner_user_id,
        is_cancelled=is_cancelled,
        on_activity=on_activity,
    )
    grounding = prepared_requirement.source_grounding
    insertion_plan = build_board_insertion_plan(
        grounding.frozen_visual_evidence,
        source_titles={
            reference.source_ingestion_id: reference.source_title
            for reference in grounding.confirmed_references
        },
    )
    visual_manifest = _visual_manifest_payload(
        plan=insertion_plan,
        requirement=prepared_requirement,
        image_input_visual_ids=(
            [
                item.visual_id
                for item in grounding.frozen_visual_evidence
                if not _is_structured_table_evidence(item)
            ]
            if image_inputs
            else []
        ),
    )
    prompt_payload = {
        "contract": (
            "Use this backend-verified source range in the current response and any board edit. "
            "Do not replace it with board-only continuation or outside knowledge."
        ),
        "confirmed_references": [
            reference.model_dump(mode="json") for reference in grounding.confirmed_references
        ],
        "frozen_text_evidence": [
            {
                "evidence_id": evidence.id,
                "source_title": evidence.source_title,
                "section_path": evidence.section_path,
                "page_range": evidence.page_range,
                "chunk_ids": evidence.chunk_ids,
                "text": evidence.expanded_text,
                "retrieval_metadata": evidence.metadata,
            }
            for evidence in grounding.frozen_evidence
        ],
        "visual_manifest": visual_manifest,
        "visual_contract": (
            "For every visual_manifest item, follow the marker and recreation rules in the "
            "developer instructions. Never invent, crop, or partially reproduce a verified visual."
        ),
    }
    visual_assets: dict[str, tuple[SourceVisualAsset, bytes]] = {}
    evidence_by_id = {
        item.visual_id: item
        for item in grounding.frozen_visual_evidence
        if item.visual_id
    }
    for item in insertion_plan.items:
        evidence = evidence_by_id.get(item.visual_id)
        if evidence is None or _is_structured_table_evidence(evidence):
            continue
        stored = _read_frozen_source_visual(user_id=owner_user_id, evidence=evidence)
        if stored is not None:
            visual_assets[item.visual_id] = stored
    return ExistingBoardSourceContext(
        prompt_context=json.dumps(prompt_payload, ensure_ascii=False, separators=(",", ":")),
        image_inputs=image_inputs,
        insertion_plan=insertion_plan,
        visual_assets=visual_assets,
        requirement=prepared_requirement,
    )


def _source_text_batches(chunks) -> list[list[tuple[str, str]]]:
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    used_tokens = 0
    for chunk in chunks:
        max_chars = SOURCE_BOARD_TOKEN_BUDGET * 4
        text_parts = [
            chunk.text[index : index + max_chars]
            for index in range(0, len(chunk.text), max_chars)
        ] or [""]
        for text in text_parts:
            token_count = max(1, (len(text) + 3) // 4)
            if current and used_tokens + token_count > SOURCE_BOARD_TOKEN_BUDGET:
                batches.append(current)
                current = []
                used_tokens = 0
            current.append((chunk.id, text))
            used_tokens += token_count
    if current:
        batches.append(current)
    return batches


def _visual_manifest_payload(
    *,
    plan: BoardInsertionPlan,
    requirement: LearningRequirementSheet,
    image_input_visual_ids: list[str] | None = None,
) -> list[dict[str, object]]:
    visuals_by_id = {
        item.visual_id: item
        for item in requirement.source_grounding.frozen_visual_evidence
        if item.visual_id
    }
    source_titles = {
        reference.source_ingestion_id: reference.source_title
        for reference in requirement.source_grounding.confirmed_references
    }
    image_input_indexes = {
        visual_id: index
        for index, visual_id in enumerate(image_input_visual_ids or [])
    }
    manifest: list[dict[str, object]] = []
    for item in plan.items:
        evidence = visuals_by_id.get(item.visual_id)
        manifest.append(
            {
                "visual_id": item.visual_id,
                "marker": item.marker,
                "original_marker": item.marker,
                "recreation_marker": item.recreation_marker,
                "allowed_handling": (
                    ["editable_recreation", "original_asset"]
                    if item.recreation_marker
                    else ["backend_materialization"]
                ),
                "image_input_index": image_input_indexes.get(item.visual_id),
                "order_index": item.order_index,
                "kind": item.kind,
                "caption": item.caption,
                "source_title": source_titles.get(item.source_ingestion_id, ""),
                "source_locator": item.source_locator,
                "page_start": getattr(evidence, "page_start", None),
                "page_end": getattr(evidence, "page_end", None),
                "slide_no": getattr(evidence, "slide_no", None),
                "sheet_name": getattr(evidence, "sheet_name", ""),
                "before_chunk_id": getattr(evidence, "before_chunk_id", None),
                "after_chunk_id": getattr(evidence, "after_chunk_id", None),
                "extracted_text": getattr(evidence, "extracted_text", "")[:1200],
                "surrounding_text": getattr(evidence, "surrounding_text", "")[:1200],
            }
        )
    return manifest


def _generate_blank_board(
    user_id: str,
    model: str,
    requirement: LearningRequirementSheet,
    teaching_plan: str,
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None = None,
) -> tuple[CodexBoardGenerationResult, str]:
    adapter = CodexAIExecutionAdapter(
        owner_user_id=user_id,
        model=model,
        board_runner=_run_codex_board_generation,
        image_analysis_runner=_run_codex_visual_analysis,
    )
    prepared_requirement, image_inputs = _prepare_source_generation_inputs(
        adapter=adapter,
        requirement=requirement,
        owner_user_id=user_id,
        is_cancelled=is_cancelled,
        on_activity=on_activity,
    )
    insertion_plan = build_board_insertion_plan(
        prepared_requirement.source_grounding.frozen_visual_evidence,
        source_titles={
            reference.source_ingestion_id: reference.source_title
            for reference in prepared_requirement.source_grounding.confirmed_references
        },
    )
    visual_manifest = _visual_manifest_payload(
        plan=insertion_plan,
        requirement=prepared_requirement,
        image_input_visual_ids=(
            [
                item.visual_id
                for item in prepared_requirement.source_grounding.frozen_visual_evidence
                if not _is_structured_table_evidence(item)
            ]
            if image_inputs
            else []
        ),
    )
    turn, content = adapter.generate_board(
        BoardGenerationExecutionRequest(
            requirement=prepared_requirement,
            teaching_plan=teaching_plan,
            image_inputs=image_inputs,
            visual_manifest=visual_manifest,
        ),
        is_cancelled=is_cancelled,
        on_activity=on_activity,
    )
    evidence_by_id = {
        item.visual_id: item
        for item in prepared_requirement.source_grounding.frozen_visual_evidence
        if item.visual_id
    }
    visual_assets: dict[str, tuple[SourceVisualAsset, bytes]] = {}
    for item in insertion_plan.items:
        evidence = evidence_by_id.get(item.visual_id)
        if evidence is None or _is_structured_table_evidence(evidence):
            continue
        stored = _read_frozen_source_visual(user_id=user_id, evidence=evidence)
        if stored is not None:
            visual_assets[item.visual_id] = stored
    return (
        CodexBoardGenerationResult(
            turn=turn,
            insertion_plan=insertion_plan,
            visual_assets=visual_assets,
        ),
        content,
    )


def _run_codex_board_generation(
    user_id: str,
    model: str,
    requirement: LearningRequirementSheet,
    teaching_plan: str,
    image_inputs: list[str],
    visual_manifest: list[dict[str, Any]],
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None,
) -> tuple[CodexTurnResult, str]:
    return _run_frozen_board_generation(
        user_id=user_id,
        model=model,
        requirement=requirement,
        teaching_plan=teaching_plan,
        image_inputs=image_inputs,
        visual_manifest=visual_manifest,
        is_cancelled=is_cancelled,
        on_activity=on_activity,
    )


def _run_codex_visual_analysis(
    user_id: str,
    model: str,
    prompt: str,
    image_inputs: list[str],
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None,
) -> str:
    workspace_root = codex_workspace_root()
    workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="source-visuals-", dir=workspace_root) as temporary:
        workspace_path = Path(temporary)
        board_path = _prepare_workspace(workspace_path, "")
        result = run_codex_thread_turn(
            user_id=user_id,
            model=model,
            cwd=workspace_path,
            user_prompt=prompt,
            developer_instructions=SOURCE_VISUAL_ANALYSIS_INSTRUCTIONS,
            thread_id=None,
            image_urls=image_inputs,
            on_delta=None,
            on_activity=on_activity,
            is_cancelled=is_cancelled,
        )
        try:
            if board_path.read_text(encoding="utf-8"):
                raise CodexAppServerError("Source visual analysis attempted to edit board.md")
            return result.final_response
        finally:
            _discard_uncommitted_thread(result.thread_id, user_id=user_id)


def process_codex_chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    *,
    user_id: str,
    on_delta: Callable[[str], None] | None = None,
    on_requirement_update: Callable[[dict[str, object]], None] | None = None,
    on_agent_activity: Callable[[AgentActivityEvent], None] | None = None,
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
        board_state_before = _board_state(initial_lesson.board_document.content_text)
        codex_model = _codex_model(request, user_id=user_id)
        verified_attachments = verify_chat_attachments(
            owner_user_id=user_id,
            package_id=initial_package.id,
            attachments=request.attachments,
        )
        prepared_attachments = prepare_chat_attachments(attachments=verified_attachments)
        if board_state_before == "empty":
            return process_blank_board_turn(
                lesson=initial_lesson,
                request=request,
                user_id=user_id,
                model=codex_model,
                conversation_text="\n\n".join(
                    item
                    for item in (
                        _conversation_context(request.conversation),
                        prepared_attachments.prompt_context,
                    )
                    if item
                ),
                on_delta=on_delta,
                on_requirement_update=on_requirement_update,
                on_agent_activity=on_agent_activity,
                is_cancelled=is_cancelled,
                generate_board=_generate_blank_board,
                discard_generated_thread=lambda thread_id: _discard_uncommitted_thread(
                    thread_id,
                    user_id=user_id,
                ),
            )

        if request.teaching_action is not None:
            from app.services.auto_board_teaching import continue_board_teaching

            teaching_result = continue_board_teaching(
                owner_user_id=user_id,
                lesson_id=lesson_id,
                model=codex_model,
                restart=request.teaching_action == "restart",
            )
            for event in teaching_result.activity:
                if on_agent_activity is not None:
                    on_agent_activity(event)
            if teaching_result.chatbot_message and on_delta is not None:
                on_delta(teaching_result.chatbot_message)
            workspace = workspace_state.load_workspace_for_user(user_id)
            package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
            return ChatResponse(
                chatbot_message=teaching_result.chatbot_message,
                follow_up_suggestions=teaching_result.follow_up_suggestions,
                agent_activity=teaching_result.activity,
                learning_requirement_sheet=build_requirements(lesson.title),
                active_requirement_sheet=None,
                learning_clarification=_neutral_clarification(),
                board_task_sheet=teaching_result.board_task,
                active_board_task_sheet=None,
                board_task_run_id=teaching_result.board_task_run_id,
                board_task_version_id=teaching_result.board_task_version_id,
                board_task_phase=(
                    "consumed" if teaching_result.status == "succeeded" else "not_executed"
                ),
                board_task_questions=[],
                board_decision=BoardDecision(
                    action="no_change",
                    reason="The Board AI authorized a bounded section explanation.",
                ),
                requirement_cleared=True,
                board_document_operation_status="none",
                teaching_progress=teaching_result.progress,
                auto_teaching_operation_status=teaching_result.status,
                auto_teaching_operation_failure_reason=teaching_result.failure_reason,
                course_package=workspace_state.package_view_for_lesson(
                    workspace,
                    package,
                    lesson.id,
                ),
            )

        source_context = None
        if request.selection is not None and request.selection.kind == "source":
            try:
                source_context = _prepare_existing_board_source_context(
                    owner_user_id=user_id,
                    lesson=initial_lesson,
                    selection=request.selection,
                    model=codex_model,
                    is_cancelled=is_cancelled,
                    on_activity=on_agent_activity,
                )
            except SourceGroundedBoardError as exc:
                # Never fall through to a board-only Codex turn when the visible
                # source chip cannot be verified.  Running without the reference
                # would falsely report success while ignoring the learner's scope.
                raise CodexAppServerError(str(exc)) from exc

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
            codex_board_text, preserved_visuals = _document_for_codex(
                initial_lesson.board_document
            )
            board_path = _prepare_workspace(workspace_path, codex_board_text)
            verified_context = "\n\n".join(
                item
                for item in (
                    source_context.prompt_context if source_context is not None else "",
                    prepared_attachments.prompt_context,
                )
                if item
            )
            user_prompt = _turn_prompt(
                request,
                is_new_thread=prior_thread_id is None,
                board_state=board_state_before,
                verified_source_context=verified_context,
            )
            codex_reasoning_effort = _codex_reasoning_effort(request)
            codex_service_tier, codex_service_tier_is_set = _codex_service_tier(
                request
            )
            quota_stop = threading.Event()
            quota_exceeded = threading.Event()
            quota_monitor = threading.Thread(
                target=_watch_board_quota,
                kwargs={
                    "board_path": board_path,
                    "max_bytes": _board_max_bytes(),
                    "stop_event": quota_stop,
                    "quota_exceeded": quota_exceeded,
                },
                daemon=True,
            )
            quota_monitor.start()

            def turn_is_cancelled() -> bool:
                return quota_exceeded.is_set() or bool(is_cancelled and is_cancelled())

            result = None
            try:
                result = run_codex_thread_turn(
                    user_id=user_id,
                    model=codex_model,
                    cwd=workspace_path,
                    user_prompt=user_prompt,
                    fallback_user_prompt=(
                        _turn_prompt(
                            request,
                            is_new_thread=True,
                            board_state=board_state_before,
                            verified_source_context=verified_context,
                        )
                        if prior_thread_id is not None
                        else user_prompt
                    ),
                    developer_instructions=CODEX_DEVELOPER_INSTRUCTIONS,
                    thread_id=prior_thread_id,
                    last_turn_id=prior_turn_id,
                    image_urls=(
                        (source_context.image_inputs if source_context is not None else [])
                        + prepared_attachments.image_inputs
                        + _formula_image_urls(request)
                    ),
                    on_delta=on_delta,
                    on_activity=on_agent_activity,
                    is_cancelled=turn_is_cancelled,
                    reasoning_effort=codex_reasoning_effort,
                    service_tier=codex_service_tier,
                    service_tier_is_set=codex_service_tier_is_set,
                )
            except CodexTurnCancelledError as exc:
                if quota_exceeded.is_set():
                    raise CodexAppServerError(
                        "Codex board output exceeds the configured size limit"
                    ) from exc
                raise
            finally:
                quota_stop.set()
                quota_monitor.join(timeout=0.2)
            if quota_exceeded.is_set():
                if result is not None:
                    _discard_uncommitted_thread(result.thread_id, user_id=user_id)
                raise CodexAppServerError(
                    "Codex board output exceeds the configured size limit"
                )
            assert result is not None
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
                if codex_content == codex_board_text:
                    next_document = current_document
                else:
                    rebuilt_document = build_document(
                        title=current_document.title,
                        content_text=codex_content,
                        document_id=current_document.id,
                        page_settings=current_document.page_settings,
                    )
                    next_document = _restore_preserved_visuals(
                        rebuilt_document,
                        preserved_visuals,
                    )
                    if source_context is not None and source_context.insertion_plan.items:
                        placements = derive_board_visual_placements(
                            next_document,
                            plan=source_context.insertion_plan,
                        )

                        def resolve_visual_bytes(visual_id: str):
                            return source_context.visual_assets.get(visual_id)

                        visual_result = apply_board_insertion_plan(
                            next_document,
                            plan=source_context.insertion_plan,
                            placements=placements,
                            owner_user_id=user_id,
                            lesson_id=lesson.id,
                            visual_bytes_resolver=resolve_visual_bytes,
                            preserved_document=current_document,
                        )
                        next_document = visual_result.document
                    if would_flatten_rich_document(
                        current_document=current_document,
                        new_document=next_document,
                        operation="replace_document",
                    ):
                        raise CodexAppServerError(
                            "Codex board output would flatten the existing document structure"
                        )
                changed = document_changed(current_document, next_document)
                follow_up_suggestions = generate_follow_up_suggestions(
                    adapter=CodexAIExecutionAdapter(
                        owner_user_id=user_id,
                        model=codex_model,
                    ),
                    user_message=request.message,
                    assistant_message=result.final_response,
                    board_state=board_state_before,
                    workflow_state="board_changed" if changed else "conversation",
                )
                lesson.board_teaching_guide = None
                lesson.board_teaching_progress = None
                lesson.learning_requirements = None
                lesson.board_task_requirements = None
                clarification = _neutral_clarification()
                metadata = {
                    "kind": "board_document_edit" if changed else "basic_chat",
                    "user_message": request.message,
                    "assistant_message": result.final_response,
                    "assistant_message_source": "codex",
                    "follow_up_suggestions": follow_up_suggestions,
                    "interaction_mode": request.interaction_mode,
                    "selection": (
                        request.selection.model_dump(mode="json")
                        if request.selection is not None
                        else None
                    ),
                    "chat_attachments": prepared_attachments.metadata,
                    "verified_source_reference_used": source_context is not None,
                    "verified_source_bundle_ids": (
                        [
                            reference.evidence_bundle_id
                            for reference in source_context.requirement.source_grounding.confirmed_references
                        ]
                        if source_context is not None
                        else []
                    ),
                    "verified_source_chapter_ids": (
                        [
                            reference.source_chapter_id
                            for reference in source_context.requirement.source_grounding.confirmed_references
                            if reference.source_chapter_id
                        ]
                        if source_context is not None
                        else []
                    ),
                    "verified_source_evidence_ids": (
                        [
                            evidence.id
                            for evidence in source_context.requirement.source_grounding.frozen_evidence
                        ]
                        if source_context is not None
                        else []
                    ),
                    "document_changed": changed,
                    "board_state_before": board_state_before,
                    "board_state_after": _board_state(next_document.content_text),
                    "document_hash_before": _text_hash(current_document.content_text),
                    "document_hash_after": _text_hash(next_document.content_text),
                    "codex_thread_id": result.thread_id,
                    "codex_turn_id": result.turn_id,
                    "codex_parent_thread_id": result.parent_thread_id,
                    "codex_replaced_stale_thread_id": result.replaced_stale_thread_id,
                    "codex_model": codex_model,
                    "codex_reasoning_effort": codex_reasoning_effort,
                    "codex_service_tier": codex_service_tier,
                    "codex_service_tier_is_set": codex_service_tier_is_set,
                    "codex_branch": branch_name,
                    "codex_base_commit_id": base_commit_id,
                    "agent_activity": [
                        event.model_dump(mode="json") for event in result.activity
                    ],
                    "active_requirement_sheet_after": None,
                    "active_board_task_sheet_after": None,
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
                saved = workspace_state.save_lesson_for_user_if_head(
                    user_id,
                    lesson,
                    expected_branch_name=branch_name,
                    expected_head_commit_id=base_commit_id,
                )
                if not saved:
                    raise CodexAppServerError("The lesson changed while Codex was working")
                workspace = workspace_state.load_workspace_for_user(user_id)
                package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
                return ChatResponse(
                    chatbot_message=result.final_response,
                    follow_up_suggestions=follow_up_suggestions,
                    agent_activity=result.activity,
                    learning_requirement_sheet=build_requirements(lesson.title),
                    active_requirement_sheet=None,
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
                    requirement_cleared=True,
                    board_document_operation_status="succeeded" if changed else "none",
                    course_package=workspace_state.package_view_for_lesson(
                        workspace,
                        package,
                        lesson.id,
                    ),
                )
            except Exception:
                _discard_uncommitted_thread(result.thread_id, user_id=user_id)
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
