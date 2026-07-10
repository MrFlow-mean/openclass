from __future__ import annotations

from app.models import ChatRequest, SelectionRef


def source_reference_selection(request: ChatRequest) -> SelectionRef | None:
    selection = request.selection
    if selection is None or selection.kind != "source" or not selection.source_chapter_id:
        return None
    return selection


def board_target_selection(selection: SelectionRef | None) -> SelectionRef | None:
    if selection is None or selection.kind == "source":
        return None
    return selection


def source_aware_user_message(request: ChatRequest, *, include_locator: bool = False) -> str:
    selection = source_reference_selection(request)
    if selection is None:
        return request.message

    chapter_label = _chapter_label(selection)
    source_label = selection.source_title.strip() or "已上传资料"
    path = " > ".join(part.strip() for part in selection.heading_path if part.strip())
    location = " / ".join(part for part in [chapter_label, path, selection.source_page_range.strip()] if part)
    context = f"本轮引用的资料章节：《{source_label}》{f' / {location}' if location else ''}。"
    parts = [request.message.strip(), context]
    if include_locator:
        parts.append(f"source_chapter_id={selection.source_chapter_id}")
    return "\n".join(part for part in parts if part)


def _chapter_label(selection: SelectionRef) -> str:
    title = selection.source_chapter_title.strip()
    number = selection.source_chapter_number.strip()
    if not number or title.startswith(number):
        return title
    return f"{number} {title}".strip()
