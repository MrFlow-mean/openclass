from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.models import (
    BoardDecision,
    BoardDocument,
    BranchRef,
    CommitRecord,
    LearningRequirementSheet,
    Lesson,
    LessonHistoryGraph,
    ScopeAction,
    ScopeOption,
    TeachingGuide,
    new_id,
    now_iso,
)
from app.services.ai_logging import ai_usage_logger
from app.services.lesson_factory import slugify
from app.services.rich_document import build_document

logger = logging.getLogger(__name__)
load_dotenv()
DEFAULT_TEXT_MODEL = "gpt-5-mini"


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _redact_reference_payload(reference: dict[str, Any] | None) -> dict[str, Any] | None:
    if reference is None:
        return None
    redacted = dict(reference)
    chapter_text = str(redacted.pop("chapter_text", "") or "")
    if chapter_text:
        redacted["chapter_text_redacted"] = f"<omitted {len(chapter_text)} chars>"
    return redacted


class TeacherMessageOutput(BaseModel):
    teacher_message: str


class PMAssessmentOutput(BaseModel):
    ready: bool
    reason: str
    clarification_questions: list[str] = Field(default_factory=list)
    learning_requirement_sheet: LearningRequirementSheet


class DocumentEditOutput(BaseModel):
    rationale: str
    commit_label: str = "AI document edit"
    replacement_html: str
    replacement_text: str = ""
    replace_whole: bool = False
    target_action: ScopeAction = "patch_current_lesson"
    suggested_title: str | None = None


class GeneratedLessonDocument(BaseModel):
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    content_html: str
    content_text: str = ""
    content_json: dict[str, Any] = Field(default_factory=lambda: {"type": "doc", "content": [{"type": "paragraph"}]})


class OpenAIConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    base_url: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL"))
    default_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", DEFAULT_TEXT_MODEL))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_GUIDE_MODEL"))
    teacher_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_TEACHER_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_LESSON_MODEL"))
    fallback_model: str = Field(default_factory=lambda: os.getenv("OPENAI_FALLBACK_MODEL", DEFAULT_TEXT_MODEL))

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

    def _call_parse(self, *, model: str, system_prompt: str, user_prompt: str, schema: type[BaseModel]):
        assert self.client is not None
        return self.client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text_format=schema,
        )

    def _fallback_model_for(self, exc: Exception, attempted_model: str) -> str | None:
        fallback_model = self.config.fallback_model.strip()
        if not fallback_model or fallback_model == attempted_model:
            return None

        error_code = getattr(exc, "code", None)
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                error_code = error.get("code") or error_code

        message = str(exc).lower()
        if error_code == "model_not_found" or "model_not_found" in message or "does not exist" in message:
            return fallback_model
        return None

    def _parse(
        self,
        role: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        *,
        log_user_prompt: str | None = None,
    ):
        requested_model = self.config.model_for(role)
        call_details = {
            "role": role,
            "model": requested_model,
            "schema": schema.__name__,
            "system_prompt": system_prompt,
            "user_prompt": log_user_prompt or user_prompt,
        }
        if not self.client:
            ai_usage_logger.log_event(
                "openai_text_call_skipped",
                **call_details,
                reason="client_disabled",
            )
            return None

        try:
            response = self._call_parse(
                model=requested_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
            )
            ai_usage_logger.log_event(
                "openai_text_call",
                **call_details,
                response_id=getattr(response, "id", None),
                output_text=getattr(response, "output_text", None),
                usage=getattr(response, "usage", None),
                parsed_output=response.output_parsed,
            )
            return response.output_parsed
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            fallback_model = self._fallback_model_for(exc, requested_model)
            if fallback_model:
                ai_usage_logger.log_event(
                    "openai_text_call_retry",
                    **call_details,
                    retry_model=fallback_model,
                    error=str(exc),
                )
                try:
                    response = self._call_parse(
                        model=fallback_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        schema=schema,
                    )
                    ai_usage_logger.log_event(
                        "openai_text_call",
                        **{**call_details, "model": fallback_model},
                        fallback_from_model=requested_model,
                        response_id=getattr(response, "id", None),
                        output_text=getattr(response, "output_text", None),
                        usage=getattr(response, "usage", None),
                        parsed_output=response.output_parsed,
                    )
                    return response.output_parsed
                except Exception as retry_exc:  # pragma: no cover - network/runtime dependent
                    ai_usage_logger.log_event(
                        "openai_text_call_error",
                        **{**call_details, "model": fallback_model},
                        fallback_from_model=requested_model,
                        error=str(retry_exc),
                    )
                    logger.warning(
                        "OpenAI %s fallback model call failed after %s was unavailable: %s",
                        role,
                        requested_model,
                        retry_exc,
                    )
                    return None
            ai_usage_logger.log_event(
                "openai_text_call_error",
                **call_details,
                error=str(exc),
            )
            logger.warning("OpenAI %s call failed, falling back to heuristic flow: %s", role, exc)
            return None

    def generate_learning_requirements(
        self,
        *,
        lesson_title: str,
        lesson_summary: str,
        lesson_tags: list[str],
        document_outline: list[str] | None = None,
        block_titles: list[str] | None = None,
        user_message: str,
        selection_excerpt: str | None,
    ) -> LearningRequirementSheet | None:
        outline = document_outline or block_titles or []
        return self._parse(
            "pm",
            system_prompt=(
                "You are PM AI for an AI Word-like teaching document product. "
                "Return a LearningRequirementSheet in Chinese. Infer the learner's goal, level, desired depth, "
                "output preference, document scope, and success criteria from the current rich document and request. "
                "The board is now one continuous rich document, not separate blocks."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "lesson_summary": lesson_summary,
                    "lesson_tags": lesson_tags,
                    "document_outline": outline,
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
        document_outline: list[str] | None = None,
        block_titles: list[str] | None = None,
        user_message: str,
        selection_excerpt: str | None,
        conversation: list[dict[str, Any]],
    ) -> PMAssessmentOutput | None:
        outline = document_outline or block_titles or []
        return self._parse(
            "pm",
            system_prompt=(
                "You are PM AI for an AI teaching workbench. Decide whether the learner's request is clear enough. "
                "If not, set ready=false and ask 1 to 3 concise clarification questions in Chinese. "
                "If ready, set ready=true. Always provide the best current LearningRequirementSheet. "
                "The visible board is a single Word-like rich document."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "lesson_summary": lesson_summary,
                    "lesson_tags": lesson_tags,
                    "document_outline": outline,
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
        interaction_mode: str,
        scope_action: ScopeAction | None,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
        resource_matches: list[dict[str, Any]],
    ) -> BoardDecision | None:
        return self._parse(
            "board",
            system_prompt=(
                "You are Board Manager AI for a Word-like teaching document. Choose one action. "
                "clarify_request asks PM follow-up questions; no_change only answers; edit_board edits the current document; "
                "append_section appends a section; create_new_lesson creates a separate lesson; await_scope_choice asks the learner to choose. "
                "Because the board is now a full rich document, prefer edit_board for requests that ask to generate or rewrite teaching material."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "user_message": request_message,
                    "selection": selection,
                    "interaction_mode": interaction_mode,
                    "scope_action": scope_action,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                    "resource_matches": resource_matches,
                }
            ),
            schema=BoardDecision,
        )

    def generate_document_edit(
        self,
        *,
        lesson_id: str,
        lesson_title: str,
        current_branch: str,
        request_message: str,
        selection: dict[str, Any] | None,
        interaction_mode: str,
        scope_action: ScopeAction | None,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
        selected_reference: dict[str, Any] | None,
    ) -> DocumentEditOutput | None:
        prompt_payload = {
            "lesson_id": lesson_id,
            "lesson_title": lesson_title,
            "current_branch": current_branch,
            "user_message": request_message,
            "selection": selection,
            "interaction_mode": interaction_mode,
            "requested_scope_action": scope_action,
            "learning_requirement_sheet": requirements.model_dump(mode="json"),
            "board_document": document.model_dump(mode="json"),
            "selected_reference": selected_reference,
        }
        log_payload = dict(prompt_payload)
        log_payload["selected_reference"] = _redact_reference_payload(selected_reference)
        return self._parse(
            "board",
            system_prompt=(
                "You are Board AI editing a Word-like rich teaching document. "
                "Return replacement_html containing coherent long-form teaching prose. "
                "If a selection is provided and the user did not explicitly ask to rewrite the whole document, edit only that selection and never rewrite the full document. "
                "For enhancement requests such as 完善/补充/详细解析/全面/展开, keep the selected original wording visible and continue writing from it instead of deleting it. "
                "If the user asks to generate or rewrite the lesson, return a complete handout-style HTML document with headings, long dialogue/body content, "
                "explanations, examples, exercises, and answers. Do not split content into blocks or cards. "
                "If selected_reference.chapter_text is provided, treat it as the full relevant chapter content and ground the handout in that chapter. "
                "For French cafe dialogue lessons, the dialogue must be the main body and should be long enough for teaching."
            ),
            user_prompt=_json(prompt_payload),
            log_user_prompt=_json(log_payload),
            schema=DocumentEditOutput,
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
                "You are generating an internal teaching guide for a continuous Word-like board document. "
                "Return a TeachingGuide in Chinese. Mappings may use synthetic ids such as section_1. "
                "Explain which document sections support the goal, how to teach them, and what check questions to ask."
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
        document_updated: bool,
        scope_options: list[ScopeOption],
        resource_matches: list[dict[str, Any]],
        clarification_questions: list[str],
        reference_prompt: dict[str, Any] | None,
        selected_reference: dict[str, Any] | None,
    ) -> str | None:
        result = self._parse(
            "teacher",
            system_prompt=(
                "You are Teacher AI speaking to the learner in Chinese. "
                "If clarification is needed, ask the questions naturally. If the document was updated, mention that the right-side Word-like board has been updated. "
                "If no_change, answer directly using the current document. Do not mention internal schemas."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "user_message": request_message,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                    "teaching_guide": guide.model_dump(mode="json"),
                    "board_decision": board_decision.model_dump(mode="json"),
                    "document_updated": document_updated,
                    "scope_options": [option.model_dump(mode="json") for option in scope_options],
                    "resource_matches": resource_matches,
                    "clarification_questions": clarification_questions,
                    "reference_prompt": reference_prompt,
                    "selected_reference": selected_reference,
                }
            ),
            schema=TeacherMessageOutput,
        )
        return result.teacher_message if result else None

    def generate_lesson_document(
        self,
        *,
        topic: str,
        reference_context: dict[str, Any] | None = None,
    ) -> GeneratedLessonDocument | None:
        prompt_payload = {"topic": topic, "reference_context": reference_context}
        log_payload = {
            "topic": topic,
            "reference_context": _redact_reference_payload(reference_context),
        }
        return self._parse(
            "lesson",
            system_prompt=(
                "You are Board AI creating a brand-new Word-like rich teaching document. "
                "Return one complete handout-style document, not blocks. Use HTML with h1/h2/h3/p/ol/ul/table when helpful. "
                "The document should be long enough for a real lesson. For French cafe ordering lessons, include: title, scene, complete bilingual dialogue, "
                "grammar focus, sentence analysis, vocabulary expressions, exercises, and answers. "
                "The dialogue should be the main body, not a tiny sample. Avoid card-like fragmented notes. "
                "If reference_context.chapter_text is provided, treat it as the full relevant chapter content and ground the new lesson in that chapter."
            ),
            user_prompt=_json(prompt_payload),
            log_user_prompt=_json(log_payload),
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
        document = build_document(
            title=generated.title,
            content_html=generated.content_html,
            content_text=generated.content_text,
            content_json=generated.content_json,
        )
        commit = CommitRecord(
            label="Initial document draft",
            message=f"Generated starter rich document for {topic} via OpenAI",
            branch_name="main",
            snapshot=document,
        )
        history = LessonHistoryGraph(
            branches={
                "main": BranchRef(
                    name="main",
                    head_commit_id=commit.id,
                    base_commit_id=commit.id,
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
    document = build_document(
        title=generated.title,
        content_html=generated.content_html,
        content_text=generated.content_text,
        content_json=generated.content_json,
    )
    commit = CommitRecord(
        label="Initial document draft",
        message=f"Generated starter rich document for {topic} via OpenAI",
        branch_name="main",
        snapshot=document,
    )
    history = LessonHistoryGraph(
        branches={
            "main": BranchRef(
                name="main",
                head_commit_id=commit.id,
                base_commit_id=commit.id,
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
