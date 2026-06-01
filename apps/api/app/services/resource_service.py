from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from app.models import CoursePackage, ResourceLibraryItem


def remove_resource_from_package(package: CoursePackage, resource_id: str) -> ResourceLibraryItem:
    for index, resource in enumerate(package.resources):
        if resource.id == resource_id:
            return package.resources.pop(index)
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
