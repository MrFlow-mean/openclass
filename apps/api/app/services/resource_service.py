from __future__ import annotations

import mimetypes
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.models import CoursePackage, ResourceActivityAction, ResourceActivityEvent, ResourceLibraryItem, now_iso


def build_queued_resource(destination: Path, original_name: str, content_type: str | None = None) -> ResourceLibraryItem:
    mime_type = content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    return ResourceLibraryItem(
        name=original_name,
        mime_type=mime_type,
        resource_type="image" if mime_type.startswith("image/") else "document",
        size_bytes=destination.stat().st_size,
        outline=[],
        concept_index={},
        extracted_text_available=False,
        text_content=None,
        source_path=str(destination),
        index_status="queued",
        index_message="等待后台解析资料",
        index_updated_at=now_iso(),
        page_count=0,
        indexed_block_count=0,
    )


def record_resource_activity(
    package: CoursePackage,
    resource: ResourceLibraryItem,
    action: ResourceActivityAction,
) -> ResourceActivityEvent:
    event = ResourceActivityEvent(
        action=action,
        resource_id=resource.id,
        resource_name=resource.name,
        mime_type=resource.mime_type,
        resource_type=resource.resource_type,
        size_bytes=resource.size_bytes,
        scope_lesson_id=resource.scope_lesson_id,
    )
    package.resource_events.append(event)
    return event


def remove_resource_from_package(package: CoursePackage, resource_id: str) -> ResourceLibraryItem:
    for index, resource in enumerate(package.resources):
        if resource.id == resource_id:
            removed = package.resources.pop(index)
            record_resource_activity(package, removed, "deleted")
            return removed
    raise HTTPException(status_code=404, detail=f"Unknown resource {resource_id}")


def delete_uploaded_resource_file(resource: ResourceLibraryItem, upload_dir: Path) -> bool:
    if not resource.source_path:
        return False

    source_path = Path(resource.source_path)
    try:
        resolved_source = source_path.resolve(strict=False)
        resolved_upload_dir = upload_dir.resolve(strict=False)
    except OSError:
        return False

    if not resolved_source.is_relative_to(resolved_upload_dir):
        return False

    try:
        source_path.unlink(missing_ok=True)
    except OSError:
        return False
    return True
