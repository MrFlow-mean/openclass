from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from app.models import (
    BoardDecision,
    BoardBlock,
    BoardDocument,
    BranchRef,
    CommitRecord,
    LearningRequirementSheet,
    Lesson,
    LessonHistoryGraph,
    PatchOperation,
    ScopeAction,
    ScopeOption,
    TeachingGuide,
    new_id,
    now_iso,
)
from app.services.lesson_factory import slugify

logger = logging.getLogger(__name__)
load_dotenv()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


class TeacherMessageOutput(BaseModel):
    teacher_message: str


class PMAssessmentOutput(BaseModel):
    ready: bool
    reason: str
    clarification_questions: list[str] = Field(default_factory=list)
    learning_requirement_sheet: LearningRequirementSheet


class BoardPatchOutput(BaseModel):
    rationale: str
    commit_label: str
    operations: list[PatchOperation] = Field(default_factory=list)
    target_action: ScopeAction = "patch_current_lesson"
    suggested_title: str | None = None


class GeneratedLessonDocument(BaseModel):
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    blocks: list[BoardBlock]


class OpenAIConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    base_url: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL"))
    default_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.3"))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_GUIDE_MODEL"))
    teacher_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_TEACHER_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_LESSON_MODEL"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def model_for(self, role: str) -> str:
        value = getattr(self, f"{role}_model", None)
        return value or self.default_model


class OpenAICourseAI:
    def __init__(self) -> None:
        self.config = OpenAIConfig()
        self.client = (
            OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)
            if self.config.enabled
            else None
        )

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "models": {
                "pm": self.config.model_for("pm"),
                "board": self.config.model_for("board"),
                "guide": self.config.model_for("guide"),
                "teacher": self.config.model_for("teacher"),
                "lesson": self.config.model_for("lesson"),
            },
        }

    def _parse(self, role: str, system_prompt: str, user_prompt: str, schema: type[BaseModel]):
        if not self.client:
            return None

        try:
            response = self.client.responses.parse(
                model=self.config.model_for(role),
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text_format=schema,
            )
            return response.output_parsed
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            logger.warning("OpenAI %s call failed, falling back to heuristic flow: %s", role, exc)
            return None

    def generate_learning_requirements(
        self,
        *,
        lesson_title: str,
        lesson_summary: str,
        lesson_tags: list[str],
        block_titles: list[str],
        user_message: str,
        selection_excerpt: str | None,
    ) -> LearningRequirementSheet | None:
        return self._parse(
            "pm",
            system_prompt=(
                "You are PM AI for an AI blackboard teaching product. "
                "Return a LearningRequirementSheet in Chinese. "
                "Infer the user's learning goal, level, desired depth, output preference, "
                "scope boundary, and success criteria from the current lesson context and request. "
                "Keep it actionable for downstream board and teacher agents."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "lesson_summary": lesson_summary,
                    "lesson_tags": lesson_tags,
                    "visible_block_titles": block_titles,
                    "user_message": user_message,
                    "selection_excerpt": selection_excerpt,
                }
            ),
            schema=LearningRequirementSheet,
        )

    def assess_learning_requirements(
        self,
        *,
        lesson_title: str,
        lesson_summary: str,
        lesson_tags: list[str],
        block_titles: list[str],
        user_message: str,
        selection_excerpt: str | None,
        conversation: list[dict[str, Any]],
    ) -> PMAssessmentOutput | None:
        return self._parse(
            "pm",
            system_prompt=(
                "You are PM AI for an AI blackboard teaching product. "
                "Decide whether the learner's request is clear enough to proceed. "
                "If it is not clear enough, set ready=false, explain why, ask 1 to 3 concise clarification questions in Chinese, "
                "and still provide the best current LearningRequirementSheet. "
                "If it is clear enough, set ready=true and keep clarification_questions empty."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "lesson_summary": lesson_summary,
                    "lesson_tags": lesson_tags,
                    "visible_block_titles": block_titles,
                    "conversation": conversation,
                    "user_message": user_message,
                    "selection_excerpt": selection_excerpt,
                }
            ),
            schema=PMAssessmentOutput,
        )

    def generate_board_decision(
        self,
        *,
        lesson_title: str,
        request_message: str,
        selection: dict[str, Any] | None,
        scope_action: ScopeAction | None,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
        resource_matches: list[dict[str, Any]],
    ) -> BoardDecision | None:
        return self._parse(
            "board",
            system_prompt=(
                "You are Board Manager AI for an AI teaching workbench. "
                "Choose exactly one action in Chinese reasoning. "
                "Action meanings: clarify_request means PM must ask more before proceeding; "
                "no_change means keep the current board unchanged and only support Teacher AI; "
                "edit_board means modify part of the current board; "
                "append_section means add a new section inside the current lesson; "
                "create_new_lesson means create a brand-new lesson; "
                "await_scope_choice means the question exceeds the current lesson and the learner should choose a path first."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "user_message": request_message,
                    "selection": selection,
                    "scope_action": scope_action,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                    "resource_matches": resource_matches,
                }
            ),
            schema=BoardDecision,
        )

    def generate_patch_proposal(
        self,
        *,
        lesson_id: str,
        lesson_title: str,
        current_branch: str,
        request_message: str,
        selection: dict[str, Any] | None,
        scope_action: ScopeAction | None,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
        resource_matches: list[dict[str, Any]],
    ) -> BoardPatchOutput | None:
        return self._parse(
            "board",
            system_prompt=(
                "You are Board AI for an AI teaching workbench. "
                "Return only a structured patch plan, not a rewritten full document. "
                "Allowed operations are: insert_block, delete_block, update_block_content, "
                "replace_range_in_block, move_block, update_block_style, attach_asset. "
                "Prefer the smallest possible set of edits. "
                "Use exact existing block_id values for edits. "
                "For inserted blocks, assign a stable id starting with 'block_'. "
                "If the user asks for practice, examples, simplification, or a local expansion, "
                "keep target_action inside the current lesson."
            ),
            user_prompt=_json(
                {
                    "lesson_id": lesson_id,
                    "lesson_title": lesson_title,
                    "current_branch": current_branch,
                    "user_message": request_message,
                    "selection": selection,
                    "requested_scope_action": scope_action,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                    "resource_matches": resource_matches,
                }
            ),
            schema=BoardPatchOutput,
        )

    def generate_teaching_guide(
        self,
        *,
        lesson_id: str,
        lesson_title: str,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
    ) -> TeachingGuide | None:
        return self._parse(
            "guide",
            system_prompt=(
                "You are Board AI generating an internal teaching guide for Teacher AI. "
                "Return a TeachingGuide in Chinese. "
                "Each mapping must reference an exact block_id from the current board document. "
                "Explain which learning goal each block supports, how to teach it, where the重点 are, "
                "and what check questions to ask. This guide is internal and not shown to the user."
            ),
            user_prompt=_json(
                {
                    "lesson_id": lesson_id,
                    "lesson_title": lesson_title,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                }
            ),
            schema=TeachingGuide,
        )

    def generate_teacher_message(
        self,
        *,
        lesson_title: str,
        request_message: str,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
        guide: TeachingGuide,
        board_decision: BoardDecision,
        patch_proposal: BoardPatchOutput | None,
        scope_options: list[ScopeOption],
        resource_matches: list[dict[str, Any]],
        clarification_questions: list[str],
    ) -> str | None:
        result = self._parse(
            "teacher",
            system_prompt=(
                "You are Teacher AI speaking to the learner in Chinese. "
                "If board_decision.action is clarify_request, ask the clarification questions naturally. "
                "If board_decision.action is no_change, answer the learner's question directly using the current board. "
                "If a patch proposal exists, teach using the updated board structure and mention that a preview is ready. "
                "If board_decision.action is await_scope_choice, explain that the question exceeds the current lesson and ask the learner to choose between the options. "
                "If board_decision.action is create_new_lesson, introduce the new lesson and begin teaching from it. "
                "Do not mention the internal teaching guide. Be concise, supportive, and specific."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "user_message": request_message,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                    "teaching_guide": guide.model_dump(mode="json"),
                    "board_decision": board_decision.model_dump(mode="json"),
                    "patch_proposal": patch_proposal.model_dump(mode="json") if patch_proposal else None,
                    "scope_options": [option.model_dump(mode="json") for option in scope_options],
                    "resource_matches": resource_matches,
                    "clarification_questions": clarification_questions,
                }
            ),
            schema=TeacherMessageOutput,
        )
        return result.teacher_message if result else None

    def generate_lesson_document(self, *, topic: str) -> GeneratedLessonDocument | None:
        return self._parse(
            "lesson",
            system_prompt=(
                "You are Board AI creating a brand-new lesson document for an AI blackboard product. "
                "Return a concise, well-structured lesson in Chinese. "
                "Use 4 to 7 blocks. Pick from these block types: heading, paragraph, formula, table, image, note, exercise, dialogue. "
                "Only include formula/table/dialogue when truly relevant to the topic."
            ),
            user_prompt=_json({"topic": topic}),
            schema=GeneratedLessonDocument,
        )

    def build_lesson_from_generated(
        self,
        *,
        topic: str,
        generated: GeneratedLessonDocument,
        requirements: LearningRequirementSheet,
        guide: TeachingGuide,
    ) -> Lesson:
        lesson_id = guide.lesson_id
        document = BoardDocument(title=generated.title, blocks=generated.blocks)
        commit = CommitRecord(
            label="Initial board draft",
            message=f"Generated starter board for {topic} via OpenAI",
            branch_name="main",
            snapshot=document,
        )
        history = LessonHistoryGraph(
            branches={
                "main": BranchRef(
                    name="main", head_commit_id=commit.id, base_commit_id=commit.id
                )
            },
            commits=[commit],
            current_branch="main",
        )
        return Lesson(
            id=lesson_id,
            title=generated.title,
            slug=slugify(generated.title),
            summary=generated.summary,
            tags=generated.tags,
            board_document=document,
            learning_requirements=requirements,
            teaching_guide=guide,
            history_graph=history,
            created_at=now_iso(),
            updated_at=now_iso(),
        )


def build_generated_lesson(
    *,
    topic: str,
    generated: GeneratedLessonDocument,
    requirements: LearningRequirementSheet,
    guide_template: TeachingGuide,
) -> Lesson:
    lesson_id = new_id("lesson")
    guide = guide_template.model_copy(update={"lesson_id": lesson_id})
    document = BoardDocument(title=generated.title, blocks=generated.blocks)
    commit = CommitRecord(
        label="Initial board draft",
        message=f"Generated starter board for {topic} via OpenAI",
        branch_name="main",
        snapshot=document,
    )
    history = LessonHistoryGraph(
        branches={
            "main": BranchRef(
                name="main", head_commit_id=commit.id, base_commit_id=commit.id
            )
        },
        commits=[commit],
        current_branch="main",
    )
    return Lesson(
        id=lesson_id,
        title=generated.title,
        slug=slugify(generated.title),
        summary=generated.summary or requirements.learning_goal,
        tags=generated.tags or [topic],
        board_document=document,
        learning_requirements=requirements,
        teaching_guide=guide,
        history_graph=history,
        created_at=now_iso(),
        updated_at=now_iso(),
    )


openai_course_ai = OpenAICourseAI()
