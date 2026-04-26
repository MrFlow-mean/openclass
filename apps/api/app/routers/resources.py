from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, UploadFile

from app.models import CoursePackageView
from app.services.resource_library import build_resource_item
from app.services.workspace_state import UPLOAD_DIR, load_workspace_package, package_view, save_workspace

router = APIRouter()


@router.post("/api/resources/upload", response_model=CoursePackageView)
def upload_resource(file: UploadFile = File(...)) -> CoursePackageView:
    workspace, package = load_workspace_package()
    original_name = Path(file.filename or "resource").name
    destination = UPLOAD_DIR / f"{uuid4().hex[:8]}_{original_name}"
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    resource = build_resource_item(destination, original_name)
    package.resources.append(resource)
    save_workspace(workspace)
    return package_view(package)
