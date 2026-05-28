from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models import BoardDocument, LearningRequirementSheet, Lesson, ResourceLibraryItem, ResourceReferenceContext
from app.services.board_teaching import build_board_teaching_guide
from app.services.course_runtime import refresh_lesson_runtime
from app.services.rich_document import build_document, document_to_markdown, import_docx, is_document_empty
from app.services.resource_library import extract_reference_context
from app.services.resource_resolver import ResourceResolution


RESOURCE_DOCUMENT_IMPORT_ACTION_PATTERN = re.compile(
    r"(导入|放入|放进|放到|放在|显示|展示|写入|插入|搬到|贴到|复制到|转到|同步到)"
)
RESOURCE_DOCUMENT_IMPORT_TARGET_PATTERN = re.compile(r"(文档框|文档区|黑板|板书|版书|讲义区|编辑器|右侧)")
RESOURCE_DOCUMENT_IMPORT_PRONOUN_PATTERN = re.compile(r"(其|它|这份|这个|该(?:文件|资料|文档|材料)|当前(?:文件|资料|文档|材料))")
RESOURCE_DOCUMENT_FULL_IMPORT_PATTERN = re.compile(r"(全部|整份|全文|完整|全篇|所有内容|整个(?:文件|资料|文档|材料)|原文)")
RESOURCE_DOCUMENT_REPLACE_PATTERN = re.compile(r"(替换|覆盖|清空后|重新放|换成|替掉)")
RESOURCE_REFERENCE_PATTERN = re.compile(r"(资料|材料|上传|教材|课本|原文|参考|根据|来自|文件|PDF|Word)", re.IGNORECASE)
APPEND_REQUEST_PATTERN = re.compile(
    r"(续写|继续写|接着写|往后写|后续|新增|追加|新加|新章节|新小节|下一节|下一章|下一部分|末尾)"
)


@dataclass(frozen=True)
class ResourceDocumentImportPayload:
    resource: ResourceLibraryItem
    title: str
    content_text: str
    import_scope: str
    operation: str
    content_html: str | None = None
    content_json: dict[str, Any] | None = None
    selected_reference: ResourceReferenceContext | None = None


def requests_resource_document_import(text: str, *, resources: list[ResourceLibraryItem]) -> bool:
    compact = _compact_text(text, limit=280)
    if not compact or not resources:
        return False
    has_import_action = bool(RESOURCE_DOCUMENT_IMPORT_ACTION_PATTERN.search(compact))
    has_document_target = bool(RESOURCE_DOCUMENT_IMPORT_TARGET_PATTERN.search(compact))
    if not has_import_action or not has_document_target:
        return False
    has_resource_reference = bool(RESOURCE_REFERENCE_PATTERN.search(compact))
    has_contextual_reference = bool(RESOURCE_DOCUMENT_IMPORT_PRONOUN_PATTERN.search(compact))
    return has_resource_reference or has_contextual_reference


def resource_import_operation(*, lesson: Lesson, user_message: str) -> str | None:
    if is_document_empty(lesson.board_document):
        return "replace_document"
    if _requests_append_section(user_message):
        return "append_section"
    if _requests_replace_import(user_message):
        return "replace_document"
    return None


def select_resource_import_payload(
    *,
    resources: list[ResourceLibraryItem],
    user_message: str,
    resource_resolution: ResourceResolution,
    operation: str,
) -> ResourceDocumentImportPayload | None:
    if _requests_full_import(user_message) and len(resources) == 1:
        return _full_resource_import_payload(
            resource=resources[0],
            user_message=user_message,
            operation=operation,
        )

    if resource_resolution.selected_reference is not None:
        resource = _resource_by_id(resources, resource_resolution.selected_reference.resource_id)
        if resource is None:
            return None
        return _chapter_resource_import_payload(
            resource=resource,
            reference=resource_resolution.selected_reference,
            operation=operation,
        )

    if len(resources) == 1:
        return _full_resource_import_payload(
            resource=resources[0],
            user_message=user_message,
            operation=operation,
        )

    return None


def apply_resource_document_import(
    *,
    lesson: Lesson,
    payload: ResourceDocumentImportPayload,
    requirements: LearningRequirementSheet | None,
) -> None:
    if payload.operation == "append_section":
        existing_text = _document_text(lesson).strip()
        next_text = "\n\n".join(part for part in [existing_text, payload.content_text.strip()] if part)
        new_document = build_document(
            title=lesson.board_document.title or lesson.title,
            content_text=next_text,
            content_html="\n".join(
                part
                for part in [
                    lesson.board_document.content_html.strip(),
                    (payload.content_html or "").strip(),
                ]
                if part
            ),
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
    else:
        new_document = build_document(
            title=payload.title or lesson.board_document.title or lesson.title,
            content_text=payload.content_text,
            content_html=payload.content_html,
            content_json=payload.content_json,
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
    refresh_lesson_runtime(lesson, document=new_document, requirements=requirements)
    lesson.board_teaching_guide = build_board_teaching_guide(lesson)
    lesson.board_teaching_progress = None


def _compact_text(value: str | None, *, limit: int) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _requests_append_section(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and APPEND_REQUEST_PATTERN.search(compact))


def _requests_full_import(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and RESOURCE_DOCUMENT_FULL_IMPORT_PATTERN.search(compact))


def _requests_replace_import(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and RESOURCE_DOCUMENT_REPLACE_PATTERN.search(compact))


def _document_text(lesson: Lesson) -> str:
    document = lesson.board_document
    return document_to_markdown(document) or document.content_text


def _full_resource_import_text(resource: ResourceLibraryItem, *, user_message: str) -> str:
    if resource.text_content and resource.text_content.strip():
        return resource.text_content.strip()

    parts: list[str] = []
    for chapter in resource.outline[:12]:
        context = extract_reference_context(resource, chapter.id, user_query=user_message)
        if context and context.full_text.strip():
            section_text = context.full_text.strip()
        else:
            section_text = chapter.summary.strip()
        if not section_text:
            continue
        title = chapter.title.strip()
        parts.append("\n".join(part for part in [f"## {title}" if title else "", section_text] if part))
        if sum(len(part) for part in parts) >= 60000:
            break
    return "\n\n".join(parts).strip()


def _chapter_resource_import_payload(
    *,
    resource: ResourceLibraryItem,
    reference: ResourceReferenceContext,
    operation: str,
) -> ResourceDocumentImportPayload | None:
    content_text = reference.full_text.strip()
    if not content_text:
        chunks = [chunk.excerpt.strip() for chunk in reference.chunks if chunk.excerpt.strip()]
        content_text = "\n\n".join(chunks).strip()
    if not content_text:
        return None
    title = " / ".join(part for part in [reference.resource_name, reference.chapter_title] if part.strip())
    return ResourceDocumentImportPayload(
        resource=resource,
        title=title or resource.name,
        content_text=content_text,
        import_scope="chapter",
        operation=operation,
        selected_reference=reference,
    )


def _full_resource_import_payload(
    *,
    resource: ResourceLibraryItem,
    user_message: str,
    operation: str,
) -> ResourceDocumentImportPayload | None:
    rich_document = _full_resource_import_document(resource)
    if rich_document is not None:
        return ResourceDocumentImportPayload(
            resource=resource,
            title=resource.name,
            content_text=rich_document.content_text,
            content_html=rich_document.content_html,
            content_json=rich_document.content_json,
            import_scope="full_resource",
            operation=operation,
        )

    content_text = _full_resource_import_text(resource, user_message=user_message)
    if not content_text:
        return None
    return ResourceDocumentImportPayload(
        resource=resource,
        title=resource.name,
        content_text=content_text,
        import_scope="full_resource" if resource.text_content else "available_resource_fragments",
        operation=operation,
    )


def _resource_by_id(resources: list[ResourceLibraryItem], resource_id: str) -> ResourceLibraryItem | None:
    return next((resource for resource in resources if resource.id == resource_id), None)


def _full_resource_import_document(resource: ResourceLibraryItem) -> BoardDocument | None:
    if not resource.source_path:
        return None
    file_path = Path(resource.source_path)
    if not file_path.exists():
        return None
    if file_path.suffix.lower() == ".docx":
        return import_docx(file_path, title=resource.name)
    return None
