from __future__ import annotations

from copy import deepcopy

from app.models import BoardBlock, BoardDocument, DiffPreviewItem, PatchOperation


def _find_index(blocks: list[BoardBlock], block_id: str | None) -> int | None:
    if block_id is None:
        return None
    for index, block in enumerate(blocks):
        if block.id == block_id:
            return index
    return None


def apply_patch(
    document: BoardDocument, operations: list[PatchOperation]
) -> tuple[BoardDocument, list[DiffPreviewItem]]:
    blocks = [BoardBlock.model_validate(block.model_dump()) for block in document.blocks]
    diff: list[DiffPreviewItem] = []

    for operation in operations:
        if operation.op == "insert_block" and operation.block is not None:
            target_index = _find_index(blocks, operation.after_block_id)
            insert_at = len(blocks) if target_index is None else target_index + 1
            blocks.insert(insert_at, deepcopy(operation.block))
            diff.append(
                DiffPreviewItem(
                    op=operation.op,
                    block_id=operation.block.id,
                    before=None,
                    after=operation.block,
                    summary=f"Added {operation.block.type} block",
                )
            )
            continue

        index = _find_index(blocks, operation.block_id)
        if index is None:
            continue

        current = blocks[index]
        before = BoardBlock.model_validate(current.model_dump())

        if operation.op == "delete_block":
            removed = blocks.pop(index)
            diff.append(
                DiffPreviewItem(
                    op=operation.op,
                    block_id=removed.id,
                    before=removed,
                    after=None,
                    summary=f"Deleted block “{removed.title}”",
                )
            )
            continue

        if operation.op == "update_block_content":
            if operation.title is not None:
                current.title = operation.title
            if operation.content is not None:
                current.content = operation.content
            diff.append(
                DiffPreviewItem(
                    op=operation.op,
                    block_id=current.id,
                    before=before,
                    after=BoardBlock.model_validate(current.model_dump()),
                    summary=f"Updated content for “{current.title}”",
                )
            )
            continue

        if operation.op == "replace_range_in_block" and operation.search:
            current.content = current.content.replace(
                operation.search, operation.replacement or ""
            )
            diff.append(
                DiffPreviewItem(
                    op=operation.op,
                    block_id=current.id,
                    before=before,
                    after=BoardBlock.model_validate(current.model_dump()),
                    summary=f"Replaced text inside “{current.title}”",
                )
            )
            continue

        if operation.op == "update_block_style" and operation.style is not None:
            current.style = operation.style
            diff.append(
                DiffPreviewItem(
                    op=operation.op,
                    block_id=current.id,
                    before=before,
                    after=BoardBlock.model_validate(current.model_dump()),
                    summary=f"Restyled “{current.title}”",
                )
            )
            continue

        if operation.op == "attach_asset" and operation.asset_url:
            current.metadata["asset_url"] = operation.asset_url
            diff.append(
                DiffPreviewItem(
                    op=operation.op,
                    block_id=current.id,
                    before=before,
                    after=BoardBlock.model_validate(current.model_dump()),
                    summary=f"Attached asset to “{current.title}”",
                )
            )
            continue

        if operation.op == "move_block":
            block = blocks.pop(index)
            target_index = _find_index(blocks, operation.after_block_id)
            insert_at = len(blocks) if target_index is None else target_index + 1
            blocks.insert(insert_at, block)
            diff.append(
                DiffPreviewItem(
                    op=operation.op,
                    block_id=block.id,
                    before=before,
                    after=BoardBlock.model_validate(block.model_dump()),
                    summary=f"Moved “{block.title}”",
                )
            )

    return (
        BoardDocument(id=document.id, title=document.title, blocks=blocks),
        diff,
    )

