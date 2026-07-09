from __future__ import annotations

from dataclasses import dataclass

from app.models import BoardTaskRequirementSheet, EvidenceBundle, EvidencePurpose
from app.services.resource_resolver import resource_resolver


@dataclass(frozen=True)
class EvidenceGateOutcome:
    evidence_bundle: EvidenceBundle | None = None
    chatbot_message: str = ""
    should_execute: bool = True


def evidence_reference_text(bundle: EvidenceBundle) -> str:
    references = []
    for index, item in enumerate(bundle.evidence_items, start=1):
        location = " / ".join(
            part
            for part in [
                item.source_title or "未命名资料",
                " > ".join(item.section_path) if item.section_path else "",
                item.page_range,
            ]
            if part
        )
        references.append(f"{index}. {location}：{item.excerpt[:120]}")
    return "\n".join(references)


def evidence_confirmation_message(base_message: str, bundle: EvidenceBundle, *, action_label: str) -> str:
    intro = base_message.strip()
    evidence_text = evidence_reference_text(bundle)
    prompt = (
        f"我已经在已处理资料中找到和本轮需求相关的内容。{action_label}前需要你确认是否使用这些资料：\n"
        f"{evidence_text}"
    )
    return "\n\n".join(part for part in [intro, prompt] if part)


def source_absent_message() -> str:
    return "我还没有在已处理资料中找到足够相关的内容。请先导入并等待资料解析完成，或换一个更明确的资料范围。"


def resolve_board_task_evidence_gate(
    *,
    owner_user_id: str,
    package_id: str,
    lesson_id: str,
    user_message: str,
    board_task: BoardTaskRequirementSheet,
    board_task_run_id: str | None,
    base_chatbot_message: str,
) -> EvidenceGateOutcome:
    source_grounded = resource_resolver.should_use_sources(
        " ".join([user_message, board_task.question_or_topic, board_task.target_hint])
    )
    purpose = _board_task_evidence_purpose(board_task)
    if board_task.requested_action in {"write", "edit"}:
        confirmed = resource_resolver.latest_confirmed_bundle(
            owner_user_id=owner_user_id,
            lesson_id=lesson_id,
            purpose="board_edit",
            board_task_run_id=board_task_run_id,
        )
        if confirmed is not None:
            return EvidenceGateOutcome(evidence_bundle=confirmed, chatbot_message=base_chatbot_message)
        if not source_grounded:
            return EvidenceGateOutcome(chatbot_message=base_chatbot_message)
        candidate = resource_resolver.resolve_for_board_task(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            user_message=user_message,
            board_task=board_task,
            board_task_run_id=board_task_run_id,
            purpose="board_edit",
        )
        if candidate is None:
            return EvidenceGateOutcome(chatbot_message=source_absent_message(), should_execute=False)
        return EvidenceGateOutcome(
            evidence_bundle=candidate,
            chatbot_message=evidence_confirmation_message(
                base_chatbot_message,
                candidate,
                action_label="写入或改写板书",
            ),
            should_execute=False,
        )
    if not source_grounded:
        return EvidenceGateOutcome(chatbot_message=base_chatbot_message)
    candidate = resource_resolver.resolve_for_board_task(
        owner_user_id=owner_user_id,
        package_id=package_id,
        lesson_id=lesson_id,
        user_message=user_message,
        board_task=board_task,
        board_task_run_id=board_task_run_id,
        purpose=purpose,
    )
    return EvidenceGateOutcome(evidence_bundle=candidate, chatbot_message=base_chatbot_message)


def _board_task_evidence_purpose(board_task: BoardTaskRequirementSheet) -> EvidencePurpose:
    if board_task.requested_action in {"write", "edit"}:
        return "board_edit"
    if board_task.requested_action == "explain":
        return "board_explain"
    return "board_chat"
