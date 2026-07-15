from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.models import (
    BoardBlock,
    BoardDocument,
    BoardPatchRequest,
    BoardPatchValidationResult,
    DiffPreviewItem,
    PatchOperation,
)
from app.services.rich_document import (
    _markdown_blocks,
    build_document,
    document_changed,
    looks_like_html_content,
    rebuild_document_from_content_json,
    would_flatten_rich_document,
)

_SUPPORTED_PATCH_OPS = {"insert_block", "update_block_content", "delete_block", "attach_asset"}
_BOARD_ASSET_URL_RE = re.compile(r"^/api/board-assets/(?P<asset_id>basset_[A-Za-z0-9_-]+)/content$")


@dataclass(frozen=True)
class BoardPatchApplyOutcome:
    new_document: BoardDocument
    diff_preview: list[DiffPreviewItem]
    validation: BoardPatchValidationResult
    operations: list[PatchOperation]


def document_hash(document: BoardDocument) -> str:
    payload = {
        "id": document.id,
        "title": document.title,
        "content_json": document.content_json,
        "content_html": document.content_html,
        "content_text": document.content_text,
        "page_settings": document.page_settings.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def text_hash(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def read_board_snapshot(document: BoardDocument, *, source_commit_id: str | None = None) -> dict[str, Any]:
    blocks = _snapshot_blocks(document)
    return {
        "document_id": document.id,
        "source_commit_id": source_commit_id,
        "source_document_hash": document_hash(document),
        "title": document.title,
        "blocks": [
            {
                "block_id": block["block_id"],
                "node_path": block["node_path"],
                "heading_path": block["heading_path"],
                "type": block["type"],
                "text": block["text"],
                "markdown": block["markdown"],
                "text_hash": block["text_hash"],
            }
            for block in blocks
        ],
    }


def apply_patch(
    document: BoardDocument,
    patch: BoardPatchRequest | list[PatchOperation],
    *,
    current_commit_id: str | None = None,
    allow_high_risk: bool = False,
) -> tuple[BoardDocument, list[DiffPreviewItem], BoardPatchValidationResult]:
    request = patch if isinstance(patch, BoardPatchRequest) else BoardPatchRequest(operations=patch)
    outcome = apply_board_patch(
        document,
        request,
        current_commit_id=current_commit_id,
        allow_high_risk=allow_high_risk,
    )
    return outcome.new_document, outcome.diff_preview, outcome.validation


def apply_board_patch(
    document: BoardDocument,
    patch: BoardPatchRequest,
    *,
    current_commit_id: str | None = None,
    allow_high_risk: bool = False,
) -> BoardPatchApplyOutcome:
    validation = _validate_patch_request(
        document,
        patch,
        current_commit_id=current_commit_id,
        allow_high_risk=allow_high_risk,
    )
    if validation.status == "failed":
        return BoardPatchApplyOutcome(
            new_document=document,
            diff_preview=[],
            validation=validation,
            operations=[],
        )

    if patch.operations and all(operation.op == "attach_asset" for operation in patch.operations):
        return _apply_attach_asset_operations(document, patch.operations, validation)

    blocks = _snapshot_blocks(document)
    markdown_blocks = [block["markdown"] for block in blocks]
    block_by_id = {block["block_id"]: index for index, block in enumerate(blocks)}
    diff_preview: list[DiffPreviewItem] = []
    applied_operations: list[PatchOperation] = []

    for operation in patch.operations:
        op_type = operation.op
        target_index = _operation_target_index(operation, blocks, block_by_id)
        if op_type == "insert_block":
            insert_at = len(markdown_blocks) if target_index is None else target_index + 1
            content = _operation_content(operation)
            markdown_blocks.insert(insert_at, content)
            after_block_id = operation.after_block_id or operation.block_id
            target_block = blocks[target_index] if target_index is not None and target_index < len(blocks) else None
            diff_preview.append(
                DiffPreviewItem(
                    op=op_type,
                    block_id=after_block_id,
                    node_path=target_block["node_path"] if target_block else [],
                    heading_path=target_block["heading_path"] if target_block else [],
                    after=_preview_block(after_block_id or "inserted", content, target_block),
                    after_text=content,
                    summary=operation.note or "Inserted board content after the target block.",
                )
            )
            applied_operations.append(operation)
            continue

        if target_index is None:
            validation.issues.append("Patch target block could not be resolved during application.")
            validation.status = "failed"
            return BoardPatchApplyOutcome(document, [], validation, [])

        target_block = blocks[target_index]
        before_text = markdown_blocks[target_index]
        if op_type == "update_block_content":
            content = _operation_content(operation)
            markdown_blocks[target_index] = content
            diff_preview.append(
                DiffPreviewItem(
                    op=op_type,
                    block_id=target_block["block_id"],
                    node_path=target_block["node_path"],
                    heading_path=target_block["heading_path"],
                    before=_preview_block(target_block["block_id"], before_text, target_block),
                    after=_preview_block(target_block["block_id"], content, target_block),
                    before_text=before_text,
                    after_text=content,
                    summary=operation.note or "Updated the target board block.",
                )
            )
            applied_operations.append(operation)
            continue

        if op_type == "delete_block":
            del markdown_blocks[target_index]
            diff_preview.append(
                DiffPreviewItem(
                    op=op_type,
                    block_id=target_block["block_id"],
                    node_path=target_block["node_path"],
                    heading_path=target_block["heading_path"],
                    before=_preview_block(target_block["block_id"], before_text, target_block),
                    before_text=before_text,
                    summary=operation.note or "Deleted the target board block.",
                )
            )
            applied_operations.append(operation)

    next_document = build_document(
        title=document.title,
        content_text="\n\n".join(block.strip() for block in markdown_blocks if block.strip()),
        document_id=document.id,
        page_settings=document.page_settings,
    )
    result_issues = verify_board_patch_result(document, next_document)
    if result_issues:
        validation.status = "failed"
        validation.issues.extend(result_issues)
        return BoardPatchApplyOutcome(document, [], validation, [])

    validation.applied_operations = len(applied_operations)
    return BoardPatchApplyOutcome(next_document, diff_preview, validation, applied_operations)


def verify_board_patch_result(current_document: BoardDocument, new_document: BoardDocument) -> list[str]:
    issues: list[str] = []
    if not document_changed(current_document, new_document):
        issues.append("Patch did not change the board document.")
    if would_flatten_rich_document(current_document=current_document, new_document=new_document):
        issues.append("Patch result would flatten existing heading, list, bold, or table structure.")
    if looks_like_html_content(new_document.content_text):
        issues.append("Patch result content_text contains HTML.")
    return issues


def _validate_patch_request(
    document: BoardDocument,
    patch: BoardPatchRequest,
    *,
    current_commit_id: str | None,
    allow_high_risk: bool,
) -> BoardPatchValidationResult:
    current_hash = document_hash(document)
    issues: list[str] = []
    if patch.source_commit_id and current_commit_id and patch.source_commit_id != current_commit_id:
        issues.append("Patch source commit is not the current board commit.")
    if patch.source_document_hash and patch.source_document_hash != current_hash:
        issues.append("Patch source document hash does not match the current board document.")
    if not patch.operations:
        issues.append("Patch request has no operations.")
    if any(operation.op == "attach_asset" for operation in patch.operations) and any(
        operation.op != "attach_asset" for operation in patch.operations
    ):
        issues.append("attach_asset operations cannot be mixed with text patch operations.")
    if patch.target_scope == "whole_document" and not allow_high_risk:
        issues.append("Whole-document board patches require an explicit high-risk confirmation.")
    if patch.risk_level == "high" and not allow_high_risk:
        issues.append("High-risk board patches require explicit confirmation.")

    blocks = _snapshot_blocks(document)
    block_by_id = {block["block_id"]: index for index, block in enumerate(blocks)}
    for operation in patch.operations:
        if operation.op not in _SUPPORTED_PATCH_OPS:
            issues.append(f"Unsupported board patch operation: {operation.op}.")
            continue
        content = _operation_content(operation)
        if operation.op in {"insert_block", "update_block_content"} and not content.strip():
            issues.append(f"{operation.op} requires non-empty content.")
        if operation.op == "attach_asset":
            asset_url = str(operation.asset_url or "").strip()
            if not asset_url:
                issues.append("attach_asset requires asset_url.")
            elif not _BOARD_ASSET_URL_RE.fullmatch(asset_url):
                issues.append("attach_asset only accepts a permanent board asset URL.")
        if content and looks_like_html_content(content):
            issues.append(f"{operation.op} content must be Markdown or plain text, not HTML.")
        if operation.op == "delete_block" and not allow_high_risk:
            issues.append("delete_block requires explicit high-risk confirmation.")

        target_index = _operation_target_index(operation, blocks, block_by_id)
        if operation.op != "insert_block" and target_index is None:
            issues.append(f"{operation.op} target block could not be resolved.")
            continue
        if operation.op == "insert_block":
            has_anchor = operation.after_block_id or operation.block_id or operation.node_path
            # An append patch has an unambiguous safe fallback: the document end.
            # Keep rejecting unresolved anchors for every other target scope, where
            # silently moving content would change the requested location.
            if has_anchor and target_index is None and patch.target_scope != "append":
                issues.append("insert_block anchor could not be resolved.")
            continue
        target = blocks[target_index]
        if operation.expected_text and not _expected_text_matches(
            operation.expected_text,
            [target["text"], target["markdown"]],
        ):
            issues.append("Patch expected_text does not match the target block.")
        if operation.expected_text_hash and operation.expected_text_hash != target["text_hash"]:
            issues.append("Patch expected_text_hash does not match the target block.")

    return BoardPatchValidationResult(
        status="failed" if issues else "pass",
        issues=issues,
        source_commit_id=patch.source_commit_id,
        source_document_hash=patch.source_document_hash,
        current_document_hash=current_hash,
    )


def _snapshot_blocks(document: BoardDocument) -> list[dict[str, Any]]:
    content_json = document.content_json if isinstance(document.content_json, dict) else {}
    nodes = content_json.get("content")
    if not isinstance(nodes, list) or not nodes:
        fallback = build_document(title=document.title, content_text=document.content_text, document_id=document.id)
        nodes = fallback.content_json.get("content", []) if isinstance(fallback.content_json, dict) else []

    blocks: list[dict[str, Any]] = []
    heading_stack: list[str] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "paragraph")
        markdown = "\n\n".join(_markdown_blocks([node])).strip()
        text = _node_text(node).strip() or markdown
        if node_type == "heading":
            level = _heading_level(node)
            title = text.strip()
            heading_stack = heading_stack[: max(level - 1, 0)]
            if title:
                heading_stack.append(title)
            heading_path = list(heading_stack)
        else:
            heading_path = list(heading_stack)
        node_path = [index]
        block_hash = text_hash(text or markdown)
        blocks.append(
            {
                "block_id": f"blk_{index}_{block_hash[:10]}",
                "node_path": node_path,
                "heading_path": heading_path,
                "type": _block_type(node_type),
                "text": text,
                "markdown": markdown or text,
                "text_hash": block_hash,
            }
        )
    return blocks


def _node_text(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("type") == "text":
            return str(value.get("text") or "")
        if value.get("type") in {"inlineMath", "blockMath"}:
            return str(value.get("attrs", {}).get("latex") or "")
        if value.get("type") == "resourceVisualBlock":
            return str(value.get("attrs", {}).get("caption") or "")
        children = value.get("content")
        if isinstance(children, list):
            return "".join(_node_text(child) for child in children)
    if isinstance(value, list):
        return "".join(_node_text(child) for child in value)
    return ""


def _heading_level(node: dict[str, Any]) -> int:
    try:
        return int(node.get("attrs", {}).get("level") or 1)
    except (TypeError, ValueError):
        return 1


def _block_type(node_type: str) -> str:
    if node_type == "heading":
        return "heading"
    if node_type == "table":
        return "table"
    if node_type in {"image", "resourceVisualBlock"}:
        return "image"
    if node_type == "blockMath":
        return "formula"
    return "paragraph"


def _operation_target_index(
    operation: PatchOperation,
    blocks: list[dict[str, Any]],
    block_by_id: dict[str, int],
) -> int | None:
    target_id = operation.block_id or operation.after_block_id
    if target_id and target_id in block_by_id:
        return block_by_id[target_id]
    if operation.node_path:
        index = operation.node_path[0]
        if 0 <= index < len(blocks):
            return index
    return None


def _operation_content(operation: PatchOperation) -> str:
    if operation.content is not None:
        return operation.content.strip()
    if operation.block is not None:
        parts = [operation.block.title.strip(), operation.block.content.strip()]
        return "\n\n".join(part for part in parts if part)
    return ""


def attach_asset_after_block(
    document: BoardDocument,
    *,
    after_block_id: str,
    node: dict[str, Any],
) -> BoardDocument:
    blocks = _snapshot_blocks(document)
    block_by_id = {block["block_id"]: index for index, block in enumerate(blocks)}
    target_index = block_by_id.get(after_block_id)
    if target_index is None:
        return document
    content_json = json.loads(json.dumps(document.content_json, ensure_ascii=False))
    nodes = content_json.get("content") if isinstance(content_json, dict) else None
    if not isinstance(nodes, list) or target_index >= len(nodes):
        return document
    nodes.insert(target_index + 1, node)
    return rebuild_document_from_content_json(document, content_json)


def _apply_attach_asset_operations(
    document: BoardDocument,
    operations: list[PatchOperation],
    validation: BoardPatchValidationResult,
) -> BoardPatchApplyOutcome:
    blocks = _snapshot_blocks(document)
    block_by_id = {block["block_id"]: index for index, block in enumerate(blocks)}
    content_json = json.loads(json.dumps(document.content_json, ensure_ascii=False))
    nodes = content_json.get("content") if isinstance(content_json, dict) else None
    if not isinstance(nodes, list):
        validation.status = "failed"
        validation.issues.append("Board document has no editable rich content.")
        return BoardPatchApplyOutcome(document, [], validation, [])

    insertions: dict[int, list[tuple[PatchOperation, dict[str, Any]]]] = {}
    for operation in operations:
        target_index = _operation_target_index(operation, blocks, block_by_id)
        if target_index is None:
            validation.status = "failed"
            validation.issues.append("attach_asset target block could not be resolved.")
            return BoardPatchApplyOutcome(document, [], validation, [])
        node = _attach_asset_node(operation)
        if node is None:
            validation.status = "failed"
            validation.issues.append("attach_asset payload is invalid.")
            return BoardPatchApplyOutcome(document, [], validation, [])
        insertions.setdefault(target_index, []).append((operation, node))

    next_nodes: list[dict[str, Any]] = []
    diff_preview: list[DiffPreviewItem] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        next_nodes.append(node)
        for operation, asset_node in insertions.get(index, []):
            next_nodes.append(asset_node)
            target = blocks[index]
            caption = _operation_content(operation) or operation.note or "Board asset"
            diff_preview.append(
                DiffPreviewItem(
                    op="attach_asset",
                    block_id=target["block_id"],
                    node_path=target["node_path"],
                    heading_path=target["heading_path"],
                    after=_preview_block(target["block_id"], caption, {**target, "type": "image"}),
                    after_text=caption,
                    summary=operation.note or "Attached a permanent board asset after the target block.",
                )
            )

    next_json = {**content_json, "content": next_nodes}
    next_document = rebuild_document_from_content_json(document, next_json)
    result_issues = verify_board_patch_result(document, next_document)
    if result_issues:
        validation.status = "failed"
        validation.issues.extend(result_issues)
        return BoardPatchApplyOutcome(document, [], validation, [])
    validation.applied_operations = len(operations)
    return BoardPatchApplyOutcome(next_document, diff_preview, validation, list(operations))


def _attach_asset_node(operation: PatchOperation) -> dict[str, Any] | None:
    asset_url = str(operation.asset_url or "").strip()
    caption = _operation_content(operation) or str(operation.title or operation.note or "Board asset").strip()
    asset_match = _BOARD_ASSET_URL_RE.fullmatch(asset_url)
    if asset_match:
        return {
            "type": "resourceVisualBlock",
            "attrs": {
                "marker": "",
                "visualId": "",
                "assetId": asset_match.group("asset_id"),
                "caption": caption,
                "source": "",
                "sourceLocator": "",
                "recreationKind": "original",
                "recreationStatus": "original_only",
                "recreationConfidence": "1.00",
                "recreationNote": "",
                "recreationHtml": "",
                # Keep the URL as an input compatibility format only. New rich
                # document nodes persist the permanent BoardAsset ID alone.
                "originalSrc": "",
                "originalAlt": caption,
                "originalInitiallyCollapsed": False,
            },
        }
    return None


def _preview_block(block_id: str, content: str, source: dict[str, Any] | None) -> BoardBlock:
    title = ""
    if source:
        title = " / ".join(source.get("heading_path") or [])
    return BoardBlock(
        id=block_id,
        type=(source or {}).get("type", "paragraph"),
        title=title,
        content=content,
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _expected_text_matches(expected: str, candidates: list[str]) -> bool:
    expected_text = _normalize_text(expected)
    if not expected_text:
        return True
    for candidate in candidates:
        candidate_text = _normalize_text(candidate)
        if expected_text == candidate_text or expected_text in candidate_text:
            return True
    return False
