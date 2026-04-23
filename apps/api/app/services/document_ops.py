from __future__ import annotations

from app.models import BoardDocument, DiffPreviewItem, PatchOperation


def apply_patch(
    document: BoardDocument, operations: list[PatchOperation]
) -> tuple[BoardDocument, list[DiffPreviewItem]]:
    # The application now persists full rich-document snapshots instead of block patches.
    # This compatibility shim keeps older callers import-safe while the UI and routes move
    # to document-level save/edit APIs.
    return document, []
