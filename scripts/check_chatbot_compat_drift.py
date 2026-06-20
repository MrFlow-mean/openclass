#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _parse(root: Path, relative_path: str) -> ast.Module:
    path = root / relative_path
    return ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)


def _name_path(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_path(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _literal_values(node: ast.AST) -> set[object]:
    if isinstance(node, ast.Constant):
        return {node.value}
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        values: set[object] = set()
        for item in node.elts:
            values.update(_literal_values(item))
        return values
    return set()


def _literal_alias_values(tree: ast.Module, name: str) -> set[object]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if not isinstance(value, ast.Subscript):
            return set()
        if _name_path(value.value) != "Literal":
            return set()
        return _literal_values(value.slice)
    return set()


def _class_fields(tree: ast.Module, class_name: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            fields: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.add(item.target.id)
            return fields
    return set()


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        name = _name_path(item.func)
        if not name:
            continue
        names.add(name)
        names.add(name.rsplit(".", 1)[-1])
    return names


def _contains_is_not_none(node: ast.AST, left_path: str) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Compare) or len(item.ops) != 1 or len(item.comparators) != 1:
            continue
        if _name_path(item.left) != left_path:
            continue
        if isinstance(item.ops[0], ast.IsNot) and isinstance(item.comparators[0], ast.Constant):
            if item.comparators[0].value is None:
                return True
    return False


def _contains_eq_const(node: ast.AST, left_path: str, value: object) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Compare) or len(item.ops) != 1 or len(item.comparators) != 1:
            continue
        if _name_path(item.left) != left_path:
            continue
        if isinstance(item.ops[0], ast.Eq) and isinstance(item.comparators[0], ast.Constant):
            if item.comparators[0].value == value:
                return True
    return False


def _contains_not_eq_const(node: ast.AST, left_path: str, value: object) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Compare) or len(item.ops) != 1 or len(item.comparators) != 1:
            continue
        if _name_path(item.left) != left_path:
            continue
        if isinstance(item.ops[0], ast.NotEq) and isinstance(item.comparators[0], ast.Constant):
            if item.comparators[0].value == value:
                return True
    return False


def _contains_in_constants(node: ast.AST, left_path: str, values: set[object]) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Compare) or len(item.ops) != 1 or len(item.comparators) != 1:
            continue
        if _name_path(item.left) != left_path or not isinstance(item.ops[0], ast.In):
            continue
        if values <= _literal_values(item.comparators[0]):
            return True
    return False


def _contains_in_set_with_star(
    node: ast.AST,
    *,
    left_path: str,
    starred_name: str,
    constant_value: object,
) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Compare) or len(item.ops) != 1 or len(item.comparators) != 1:
            continue
        if _name_path(item.left) != left_path or not isinstance(item.ops[0], ast.In):
            continue
        comparator = item.comparators[0]
        if not isinstance(comparator, ast.Set):
            continue
        has_star = any(
            isinstance(elt, ast.Starred) and isinstance(elt.value, ast.Name) and elt.value.id == starred_name
            for elt in comparator.elts
        )
        has_constant = any(isinstance(elt, ast.Constant) and elt.value == constant_value for elt in comparator.elts)
        if has_star and has_constant:
            return True
    return False


def _import_aliases(tree: ast.Module) -> set[tuple[str, str, str | None]]:
    aliases: set[tuple[str, str, str | None]] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        for alias in node.names:
            aliases.add((node.module, alias.name, alias.asname))
    return aliases


def _class_constant_values(tree: ast.Module, class_name: str) -> set[object]:
    class_node = _classes(tree).get(class_name)
    if class_node is None:
        return set()
    values: set[object] = set()
    for item in class_node.body:
        if isinstance(item, ast.Assign):
            values.update(_literal_values(item.value))
    return values


def _require(condition: bool, errors: list[str], message: str) -> None:
    if not condition:
        errors.append(message)


def _check_models(root: Path, errors: list[str]) -> None:
    tree = _parse(root, "apps/api/app/models.py")
    _require(
        _literal_alias_values(tree, "ChatInteractionMode") == {"ask", "direct_edit"},
        errors,
        "models.py ChatInteractionMode must remain Literal['ask', 'direct_edit'] until direct_edit is migrated.",
    )
    _require(
        _literal_alias_values(tree, "TeachingAction") == {"continue", "restart"},
        errors,
        "models.py TeachingAction must remain Literal['continue', 'restart'] until teaching_action is migrated.",
    )
    chat_request_fields = _class_fields(tree, "ChatRequest")
    for field in {"interaction_mode", "teaching_action", "board_generation_action"}:
        _require(field in chat_request_fields, errors, f"ChatRequest is missing compatibility field {field!r}.")


def _check_gate(root: Path, errors: list[str]) -> None:
    tree = _parse(root, "apps/api/app/services/chat_turn_gate.py")
    functions = _functions(tree)
    decide = functions.get("decide_chat_turn")
    signal = functions.get("_has_existing_board_task_signal")
    _require(decide is not None, errors, "chat_turn_gate.py must define decide_chat_turn.")
    _require(signal is not None, errors, "chat_turn_gate.py must define _has_existing_board_task_signal.")
    if decide is not None:
        _require(
            _contains_is_not_none(decide, "teaching_action"),
            errors,
            "decide_chat_turn must keep the teaching_action compatibility dispatch.",
        )
        rendered = ast.unparse(decide)
        _require(
            "existing_board_task" in rendered and "teaching_action:" in rendered,
            errors,
            "decide_chat_turn teaching_action branch must still route to existing_board_task with traceable rule text.",
        )
    if signal is not None:
        _require(
            _contains_eq_const(signal, "interaction_mode", "direct_edit"),
            errors,
            "_has_existing_board_task_signal must keep direct_edit as an existing-board task signal.",
        )


def _check_decider(root: Path, errors: list[str]) -> None:
    tree = _parse(root, "apps/api/app/services/board_task_decider.py")
    decide = _functions(tree).get("decide_board_task_action")
    _require(decide is not None, errors, "board_task_decider.py must define decide_board_task_action.")
    if decide is None:
        return
    rendered = ast.unparse(decide)
    _require(
        _contains_eq_const(decide, "interaction_mode", "direct_edit"),
        errors,
        "decide_board_task_action must keep its direct_edit compatibility branch.",
    )
    for action in {"append_section", "simplify_target", "expand_target", "rewrite_target"}:
        _require(action in rendered, errors, f"direct_edit compatibility branch no longer exposes {action!r}.")


def _check_workflow_trace(root: Path, errors: list[str]) -> None:
    tree = _parse(root, "apps/api/app/services/workflow_trace.py")
    values = _class_constant_values(tree, "NodeId")
    for node_id in {
        "LEGACY_COMPATIBILITY_DISPATCH",
        "LEGACY_TEACHING_ACTION",
        "LEGACY_DIRECT_EDIT_ACTION",
        "LEGACY_DOCUMENT_ACTION",
        "LEGACY_FALLBACK_EXPLAIN",
    }:
        _require(node_id in values, errors, f"workflow_trace.NodeId is missing {node_id}.")


def _check_chatbot(root: Path, errors: list[str]) -> None:
    tree = _parse(root, "apps/api/app/services/chatbot.py")
    aliases = _import_aliases(tree)
    _require(
        (
            "app.services.board_explanation_gate",
            "generate_board_directed_explanation_message",
            "_gate_board_directed_explanation_message",
        )
        in aliases,
        errors,
        "chatbot.py must keep the board explanation gate private alias.",
    )
    _require(
        (
            "app.services.chat.paths.board_task_write",
            "handle_board_task_write_terminal",
            "_execute_board_task_write",
        )
        in aliases,
        errors,
        "chatbot.py must keep the board-task write terminal private alias until callers migrate.",
    )

    functions = _functions(tree)
    required_functions = {
        "_chat_response",
        "_handle_existing_board_task_flow",
        "_maybe_start_interaction_session",
        "_generate_board_directed_explanation_message",
        "_board_task_write_deps",
        "_fallback_board_task_decision",
        "_looks_like_recent_edit_followup",
        "_looks_like_recent_write_followup",
        "_latest_successful_board_edit_focus",
        "_maybe_inherit_recent_board_edit_focus",
        "_recent_board_edit_focus_for_commit",
        "_focus_from_section_title",
    }
    for name in sorted(required_functions):
        _require(name in functions, errors, f"chatbot.py is missing pending migration function {name}.")

    board_task_flow = functions.get("_handle_existing_board_task_flow")
    if board_task_flow is not None:
        _require(
            _contains_eq_const(board_task_flow, "request.board_generation_action", "start"),
            errors,
            "_handle_existing_board_task_flow must keep the board_generation_action=start bypass.",
        )
        _require(
            _contains_is_not_none(board_task_flow, "request.teaching_action"),
            errors,
            "_handle_existing_board_task_flow must keep the teaching_action bypass so legacy teaching can run.",
        )
        for call in {"update_board_task_from_chat", "_maybe_inherit_recent_board_edit_focus"}:
            _require(call in _call_names(board_task_flow), errors, f"_handle_existing_board_task_flow no longer calls {call}.")

    chat_response = functions.get("_chat_response")
    if chat_response is not None:
        for call in {
            "decide_chat_turn",
            "_handle_existing_interaction_session",
            "_handle_existing_board_task_flow",
            "_handle_initial_learning_work_mode",
            "teach_first_section",
            "teach_next_section",
            "edit_existing_document",
            "_generate_board_directed_explanation_message",
            "handle_ordinary_chat",
        }:
            _require(call in _call_names(chat_response), errors, f"_chat_response no longer calls {call}.")
        _require(
            _contains_in_constants(chat_response, "request.teaching_action", {"continue", "restart"}),
            errors,
            "_chat_response must keep the teaching_action continue/restart compatibility branch.",
        )
        _require(
            _contains_eq_const(chat_response, "request.interaction_mode", "direct_edit"),
            errors,
            "_chat_response must keep the direct_edit compatibility branch.",
        )
        _require(
            _contains_in_set_with_star(
                chat_response,
                left_path="action_type",
                starred_name="DOCUMENT_WRITE_ACTIONS",
                constant_value="explain_target",
            ),
            errors,
            "_chat_response must keep the legacy DOCUMENT_WRITE_ACTIONS/explain_target branch.",
        )
        rendered = ast.unparse(chat_response)
        _require(
            "_requests_explanation(request.message) and (not is_document_empty(lesson.board_document))" in rendered,
            errors,
            "_chat_response must keep the fallback board-directed explanation branch.",
        )

    start_interaction = functions.get("_maybe_start_interaction_session")
    if start_interaction is not None:
        _require(
            _contains_eq_const(start_interaction, "request.interaction_mode", "direct_edit")
            and _contains_not_eq_const(start_interaction, "action_type", "append_section"),
            errors,
            "_maybe_start_interaction_session must keep the direct_edit/append_section compatibility guard.",
        )

    explain_gate = functions.get("_generate_board_directed_explanation_message")
    if explain_gate is not None:
        _require(
            "_gate_board_directed_explanation_message" in _call_names(explain_gate),
            errors,
            "_generate_board_directed_explanation_message must keep delegating through board_explanation_gate.",
        )

    write_deps = functions.get("_board_task_write_deps")
    if write_deps is not None:
        rendered = ast.unparse(write_deps)
        for symbol in {"_recent_board_edit_focus_for_commit", "_generate_board_directed_explanation_message"}:
            _require(symbol in rendered, errors, f"_board_task_write_deps no longer wires {symbol}.")

    inherit_focus = functions.get("_maybe_inherit_recent_board_edit_focus")
    if inherit_focus is not None:
        calls = _call_names(inherit_focus)
        for call in {
            "_looks_like_recent_edit_followup",
            "_looks_like_recent_write_followup",
            "_latest_successful_board_edit_focus",
            "_recent_focus_matches_board_task",
            "normalize_board_task_sheet",
        }:
            _require(call in calls, errors, f"_maybe_inherit_recent_board_edit_focus no longer calls {call}.")

    commit_focus = functions.get("_recent_board_edit_focus_for_commit")
    if commit_focus is not None:
        _require(
            "_focus_from_section_title" in _call_names(commit_focus),
            errors,
            "_recent_board_edit_focus_for_commit must keep the section-title fallback locator.",
        )


def _check_frontend_callers(root: Path, errors: list[str]) -> None:
    files_and_needles = {
        "apps/web/src/hooks/course-studio/use-lesson-chat-agent.ts": [
            'payload.teaching_action === "continue"',
            'payload.teaching_action === "restart"',
            'teaching_action: "continue"',
            'payloadForTurn.interaction_mode === "direct_edit"',
        ],
        "apps/web/src/components/course-studio/chat-sidebar.tsx": [
            'composerMode === "direct_edit"',
            'composerMode: "direct_edit"',
        ],
        "apps/web/src/components/course-studio/selection-popover.tsx": [
            'onFocusComposerWithSelection("direct_edit")',
        ],
        "apps/web/src/components/course-studio/history-utils.ts": [
            'metadataText(commit, "interaction_mode") === "direct_edit"',
        ],
    }
    for relative_path, needles in files_and_needles.items():
        text = (root / relative_path).read_text(encoding="utf-8")
        for needle in needles:
            _require(needle in text, errors, f"{relative_path} no longer contains caller symbol {needle!r}.")


def _check_handoff_doc(root: Path, errors: list[str]) -> None:
    text = (root / "docs/maintenance/compatibility-drift-guard-wave8.md").read_text(encoding="utf-8")
    for marker in {
        "base_sha: c413a192e7805df95b14b86809afe661d5721dd1",
        "## Compatibility Decisions",
        "## Guard Behavior",
        "## Recommended Integration Order",
    }:
        _require(marker in text, errors, f"compatibility-drift-guard-wave8.md is missing marker {marker!r}.")


def check_repo(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    _check_models(root, errors)
    _check_gate(root, errors)
    _check_decider(root, errors)
    _check_workflow_trace(root, errors)
    _check_chatbot(root, errors)
    _check_frontend_callers(root, errors)
    _check_handoff_doc(root, errors)
    return errors


def main() -> int:
    errors = check_repo(ROOT)
    if errors:
        print("Chatbot compatibility drift guard failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Chatbot compatibility drift guard passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
