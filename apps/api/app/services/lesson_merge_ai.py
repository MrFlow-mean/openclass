from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import (
    AgentActivityEvent,
    LessonMergeConflictResolution,
    LessonMergeSession,
    new_id,
    now_iso,
)
from app.services.ai_execution_adapter import AIExecutionAdapter, build_ai_execution_adapter
from app.services.lesson_merge import LessonMergeError, update_merge_session


class AIConflictDecision(BaseModel):
    conflict_id: str
    resolution: Literal["target", "source", "both", "custom", "clear"]
    custom_value: Any = None
    explanation: str = Field(default="", max_length=500)


class AIConflictProposal(BaseModel):
    decisions: list[AIConflictDecision] = Field(default_factory=list)


AI_MERGE_SYSTEM_PROMPT = """
You resolve explicit conflicts in an OpenClass lesson merge draft. The deterministic merge has
already preserved every non-conflicting board block and runtime field. Decide only the supplied
conflicts; never rewrite the full board. Treat rich-text blocks, tables, lists, formulas, images,
and source-evidence blocks as atomic values. Preserve stable source identities and do not invent
facts, citations, runtime state, or chronology.

For every conflict_id return exactly one decision. Choose target, source, both, clear, or custom.
Use both only when the two values can safely coexist in target-then-source order. Use custom only
when a genuine synthesis is necessary, and return a value with the same data shape as the target
or source value. Explanations must be concise decision notes, not hidden reasoning or chain of
thought.
""".strip()


def propose_ai_merge(
    session: LessonMergeSession,
    *,
    expected_version: int,
    adapter: AIExecutionAdapter | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_activity: Callable[[AgentActivityEvent], None] | None = None,
) -> LessonMergeSession:
    if session.version != expected_version:
        raise LessonMergeError("合并草案已更新，请刷新后重试。")
    if session.status not in {"draft", "ready", "failed"}:
        raise LessonMergeError("当前合并会话不能生成 AI 建议。")
    if session.ai_model is None:
        raise LessonMergeError("当前合并会话没有选定文本模型。")
    unresolved = [conflict for conflict in session.conflicts if not conflict.resolved]
    if not unresolved:
        session.mode = "ai"
        return session
    if is_cancelled is not None and is_cancelled():
        raise LessonMergeError("AI 合并已取消。")

    turn_id = new_id("mergeturn")

    def emit(event: AgentActivityEvent) -> None:
        session.agent_activity.append(event)
        if on_activity is not None:
            on_activity(event)

    emit(
        AgentActivityEvent(
            turn_id=turn_id,
            stage="build_context",
            label="整理待解决的合并冲突",
            status="completed",
            role="lesson_merge",
            metadata={"conflict_count": len(unresolved)},
        )
    )
    selected_adapter = adapter or build_ai_execution_adapter(
        session.ai_model,
        owner_user_id=session.owner_user_id,
    )
    session.status = "ai_running"
    session.mode = "ai"
    try:
        result = selected_adapter.parse_structured(
            system_prompt=AI_MERGE_SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {
                    "merge": {
                        "base_commit_id": session.base_commit_id,
                        "target_branch": session.target_branch_name,
                        "target_head_commit_id": session.target_head_commit_id,
                        "source_branch": session.source_branch_name,
                        "source_head_commit_id": session.source_head_commit_id,
                    },
                    "conflicts": [
                        {
                            "conflict_id": conflict.id,
                            "kind": conflict.kind,
                            "path": conflict.path,
                            "title": conflict.title,
                            "base_value": conflict.base_value,
                            "target_value": conflict.target_value,
                            "source_value": conflict.source_value,
                        }
                        for conflict in unresolved
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            schema=AIConflictProposal,
            allow_live_web_search=False,
            on_activity=emit,
        )
        if is_cancelled is not None and is_cancelled():
            session.status = "draft"
            session.version += 1
            session.updated_at = now_iso()
            raise LessonMergeError("AI 合并已取消。")
        proposal = AIConflictProposal.model_validate(result.output_parsed)
        decision_by_id = {decision.conflict_id: decision for decision in proposal.decisions}
        expected_ids = {conflict.id for conflict in unresolved}
        if set(decision_by_id) != expected_ids:
            missing = sorted(expected_ids - set(decision_by_id))
            unknown = sorted(set(decision_by_id) - expected_ids)
            raise LessonMergeError(
                "AI 返回的冲突集与当前草案不一致。"
                f" missing={missing} unknown={unknown}"
            )
        resolutions = [
            LessonMergeConflictResolution(
                conflict_id=decision.conflict_id,
                resolution=decision.resolution,
                custom_value=decision.custom_value,
            )
            for decision in proposal.decisions
        ]
        session.status = "draft"
        update_merge_session(
            session,
            expected_version=expected_version,
            resolutions=resolutions,
        )
        session.audit["ai_proposal"] = {
            "provider": session.ai_model.provider,
            "model": session.ai_model.model,
            "reasoning_effort": session.ai_model.reasoning_effort,
            "service_tier": session.ai_model.service_tier,
            "input_target_head_commit_id": session.target_head_commit_id,
            "input_source_head_commit_id": session.source_head_commit_id,
            "decisions": [decision.model_dump(mode="json") for decision in proposal.decisions],
        }
        emit(
            AgentActivityEvent(
                turn_id=turn_id,
                stage="final",
                label="AI 合并建议已写入草案",
                status="completed",
                role="lesson_merge",
                metadata={"decision_count": len(proposal.decisions)},
            )
        )
        session.updated_at = now_iso()
        return session
    except Exception:
        if session.status == "ai_running":
            session.status = "failed"
            session.version += 1
            session.updated_at = now_iso()
        raise
