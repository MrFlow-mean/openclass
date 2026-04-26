from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from app.models import CoursePackage, ResourceLibraryItem
from app.services.resource_library import build_resource_item


def add_uploaded_resource(
    package: CoursePackage,
    file: UploadFile,
    upload_dir: Path,
    *,
    scope_lesson_id: str | None = None,
) -> ResourceLibraryItem:
    original_name = Path(file.filename or "resource").name
    destination = upload_dir / f"{uuid4().hex[:8]}_{original_name}"
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    resource = build_resource_item(destination, original_name)
    resource.scope_lesson_id = scope_lesson_id
    package.resources.append(resource)
    return resource


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
