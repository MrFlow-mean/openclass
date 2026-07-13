from __future__ import annotations

from dataclasses import dataclass

from app.models import RetrievalEvidence, now_iso
from app.research_models import (
    ResearchArtifact,
    ResearchArtifactCreate,
    ResearchAskRequest,
    ResearchAskResponse,
    ResearchCapabilities,
    ResearchChatMessage,
    ResearchChatRequest,
    ResearchChatResponse,
    ResearchChatThread,
    ResearchChatThreadCreate,
    ResearchChatThreadUpdate,
    ResearchCitation,
    ResearchNote,
    ResearchNoteCreate,
    ResearchNoteUpdate,
    ResearchSearchRequest,
    ResearchSearchResponse,
    ResearchSearchResult,
    ResearchSpeaker,
    ResearchEpisodeProfile,
    ResearchEpisodeProfileCreate,
    ResearchEpisodeProfileUpdate,
    ResearchSpeakerProfile,
    ResearchSpeakerProfileCreate,
    ResearchSpeakerProfileUpdate,
    ResearchTransformation,
    ResearchTransformationCreate,
    ResearchTransformationRun,
    ResearchTransformationUpdate,
)
from app.services.research_ai import ResearchAIError, ResearchAIService, research_ai_service
from app.services.research_configuration_store import ResearchConfigurationStore, research_configuration_store
from app.services.research_store import ResearchStore, research_store
from app.services.source_evidence_store import SourceEvidenceStore, source_evidence_store
from app.services.source_structure_store import SourceStructureStore, source_structure_store


class ResearchWorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactGenerationResult:
    artifact: ResearchArtifact
    audio_path: str | None = None


@dataclass(frozen=True)
class ImportTransformationBatchResult:
    artifacts: list[ResearchArtifact]
    errors: list[str]


class ResearchWorkspaceService:
    def __init__(
        self,
        *,
        store: ResearchStore = research_store,
        source_store: SourceEvidenceStore = source_evidence_store,
        configuration_store: ResearchConfigurationStore = research_configuration_store,
        structure_store: SourceStructureStore = source_structure_store,
        ai: ResearchAIService = research_ai_service,
    ) -> None:
        self.store = store
        self.source_store = source_store
        self.configuration_store = configuration_store
        self.structure_store = structure_store
        self.ai = ai

    def create_note(self, *, owner_user_id: str, package_id: str, request: ResearchNoteCreate) -> ResearchNote:
        content = request.content.strip()
        if not content:
            raise ResearchWorkspaceError("笔记内容不能为空。")
        note = ResearchNote(
            owner_user_id=owner_user_id,
            package_id=package_id,
            title=request.title.strip() or _content_title(content),
            content=content,
            tags=_dedupe(request.tags),
            citations=request.citations,
            metadata=request.metadata,
        )
        return self.store.save_note(note)

    def update_note(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        note_id: str,
        request: ResearchNoteUpdate,
    ) -> ResearchNote:
        note = self.store.get_note(owner_user_id=owner_user_id, package_id=package_id, note_id=note_id)
        if note is None:
            raise ResearchWorkspaceError("笔记不存在。")
        updates = request.model_dump(exclude_unset=True)
        if "content" in updates:
            updates["content"] = str(updates["content"] or "").strip()
            if not updates["content"]:
                raise ResearchWorkspaceError("笔记内容不能为空。")
        if "title" in updates:
            updates["title"] = str(updates["title"] or "").strip() or _content_title(updates.get("content") or note.content)
        if "tags" in updates and updates["tags"] is not None:
            updates["tags"] = _dedupe(updates["tags"])
        return self.store.save_note(note.model_copy(update=updates))

    def search(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        request: ResearchSearchRequest,
    ) -> ResearchSearchResponse:
        query = request.query.strip()
        if not query:
            raise ResearchWorkspaceError("检索问题不能为空。")
        source_evidence = self._search_sources(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            mode=request.mode,
            limit=request.limit,
            token_budget=request.token_budget,
            source_ingestion_ids=request.source_ingestion_ids,
        )
        results = [
            ResearchSearchResult(kind="source", score=item.relevance_score, evidence=item)
            for item in source_evidence
        ]
        if request.include_notes:
            results.extend(
                ResearchSearchResult(kind="note", score=score, note=note)
                for score, note in self.store.search_notes(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    query=query,
                    limit=request.limit,
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return ResearchSearchResponse(query=query, mode=request.mode, results=results[: request.limit])

    def create_thread(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        request: ResearchChatThreadCreate,
    ) -> ResearchChatThread:
        return self.store.save_thread(
            ResearchChatThread(
                owner_user_id=owner_user_id,
                package_id=package_id,
                title=request.title.strip() or "新资料对话",
                context_mode=request.context_mode,
                source_ingestion_ids=_dedupe(request.source_ingestion_ids),
                note_ids=_dedupe(request.note_ids),
            )
        )

    def update_thread(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        thread_id: str,
        request: ResearchChatThreadUpdate,
    ) -> ResearchChatThread:
        thread = self._require_thread(owner_user_id=owner_user_id, package_id=package_id, thread_id=thread_id)
        updates = request.model_dump(exclude_unset=True)
        if "title" in updates:
            updates["title"] = str(updates["title"] or "").strip() or thread.title
        for key in ("source_ingestion_ids", "note_ids"):
            if key in updates and updates[key] is not None:
                updates[key] = _dedupe(updates[key])
        return self.store.save_thread(thread.model_copy(update=updates))

    def chat(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        thread_id: str,
        request: ResearchChatRequest,
    ) -> ResearchChatResponse:
        thread = self._require_thread(owner_user_id=owner_user_id, package_id=package_id, thread_id=thread_id)
        message = request.message.strip()
        if not message:
            raise ResearchWorkspaceError("消息不能为空。")
        context_mode = request.context_mode or thread.context_mode
        source_ids = _dedupe(request.source_ingestion_ids or thread.source_ingestion_ids)
        note_ids = _dedupe(request.note_ids or thread.note_ids)
        if request.context_mode is not None or request.source_ingestion_ids or request.note_ids:
            thread = self.store.save_thread(
                thread.model_copy(
                    update={
                        "context_mode": context_mode,
                        "source_ingestion_ids": source_ids,
                        "note_ids": note_ids,
                    }
                )
            )
        self.store.save_message(ResearchChatMessage(thread_id=thread.id, role="user", content=message))
        evidence = [] if context_mode in {"off", "notes"} else self._search_sources(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=message,
            mode="hybrid",
            limit=10 if context_mode == "full" else 6,
            token_budget=10000 if context_mode == "full" else 5000,
            source_ingestion_ids=source_ids,
        )
        notes = self._selected_notes(
            owner_user_id=owner_user_id,
            package_id=package_id,
            note_ids=note_ids,
            query=message,
            include=context_mode in {"full", "notes", "retrieval"},
        )
        history = self.store.list_messages(thread_id=thread.id)[-12:]
        try:
            answer = self.ai.generate_text(
                instruction=message,
                context=_research_context(evidence=evidence, notes=notes),
                conversation="\n".join(f"{item.role}: {item.content}" for item in history[:-1]),
                text_model=request.text_model,
            )
        except ResearchAIError as exc:
            raise ResearchWorkspaceError(str(exc)) from exc
        assistant = self.store.save_message(
            ResearchChatMessage(
                thread_id=thread.id,
                role="assistant",
                content=answer,
                citations=[_citation(item) for item in evidence],
            )
        )
        if thread.title == "新资料对话":
            thread = self.store.save_thread(thread.model_copy(update={"title": _content_title(message)}))
        else:
            thread = self.store.save_thread(thread)
        return ResearchChatResponse(thread=thread, message=assistant)

    def queue_artifact(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        request: ResearchArtifactCreate,
        provenance: dict[str, object] | None = None,
    ) -> ResearchArtifact:
        if request.kind == "podcast" and not [speaker for speaker in request.speakers if speaker.name.strip()]:
            raise ResearchWorkspaceError("生成播客前需要提供至少一个说话者。")
        source_ids = _dedupe(request.source_ingestion_ids)
        note_ids = _dedupe(request.note_ids)
        title = request.title.strip() or _artifact_title(request.kind)
        persisted_request = request.model_copy(
            update={
                "title": title,
                "source_ingestion_ids": source_ids,
                "note_ids": note_ids,
            }
        )
        return self.store.save_artifact(
            ResearchArtifact(
                owner_user_id=owner_user_id,
                package_id=package_id,
                kind=request.kind,
                status="queued",
                title=title,
                source_ingestion_ids=source_ids,
                note_ids=note_ids,
                metadata={
                    "instructions": request.instructions,
                    "language": request.language,
                    "tone": request.tone,
                    "length": request.length,
                    "segment_count": request.segment_count,
                    "speakers": [speaker.model_dump(mode="json") for speaker in request.speakers],
                    "artifact_request": persisted_request.model_dump(mode="json"),
                    "attempt_count": 0,
                    "queued_at": now_iso(),
                    **(provenance or {}),
                },
            )
        )

    def generate_artifact(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        request: ResearchArtifactCreate,
        provenance: dict[str, object] | None = None,
        include_unselected_notes: bool = True,
        artifact_id: str | None = None,
    ) -> ArtifactGenerationResult:
        if request.kind == "podcast" and not [speaker for speaker in request.speakers if speaker.name.strip()]:
            raise ResearchWorkspaceError("生成播客前需要提供至少一个说话者。")
        source_ids = _dedupe(request.source_ingestion_ids)
        note_ids = _dedupe(request.note_ids)
        title = request.title.strip() or _artifact_title(request.kind)
        if artifact_id:
            queued = self.store.get_artifact(
                owner_user_id=owner_user_id,
                package_id=package_id,
                artifact_id=artifact_id,
            )
            if queued is None:
                raise ResearchWorkspaceError("资料产物不存在。")
            if queued.status in {"ready", "failed"}:
                return ArtifactGenerationResult(artifact=queued)
            artifact = self.store.save_artifact(
                queued.model_copy(
                    update={
                        "status": "generating",
                        "error": "",
                        "metadata": {
                            **queued.metadata,
                            "attempt_count": int(queued.metadata.get("attempt_count") or 0) + 1,
                            "started_at": now_iso(),
                        },
                    }
                )
            )
        else:
            artifact = self.store.save_artifact(
                ResearchArtifact(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    kind=request.kind,
                    status="generating",
                    title=title,
                    source_ingestion_ids=source_ids,
                    note_ids=note_ids,
                    metadata={
                        "instructions": request.instructions,
                        "language": request.language,
                        "tone": request.tone,
                        "length": request.length,
                        "segment_count": request.segment_count,
                        "speakers": [speaker.model_dump(mode="json") for speaker in request.speakers],
                        **(provenance or {}),
                    },
                )
            )
        try:
            evidence = self._search_sources(
                owner_user_id=owner_user_id,
                package_id=package_id,
                query=" ".join(part for part in [title, request.instructions] if part).strip() or title,
                mode="hybrid",
                limit=16,
                token_budget=14000,
                source_ingestion_ids=source_ids,
            )
            notes = self._selected_notes(
                owner_user_id=owner_user_id,
                package_id=package_id,
                note_ids=note_ids,
                query=title,
                include=include_unselected_notes or bool(note_ids),
            )
            instruction = _artifact_instruction(request)
            content = self.ai.generate_text(
                instruction=instruction,
                context=_research_context(evidence=evidence, notes=notes),
                text_model=request.text_model,
            )
            transcript = content if request.kind == "podcast" else ""
            generated = artifact.model_copy(
                update={
                    "content": content,
                    "transcript": transcript,
                    "citations": [_citation(item) for item in evidence],
                    "error": "",
                }
            )
            artifact = self.store.save_artifact(generated)
            audio_path: str | None = None
            if request.kind == "podcast" and request.synthesize_audio:
                speakers = request.speakers[:4]
                audio_path = str(self.ai.synthesize_podcast(artifact_id=artifact.id, transcript=transcript, speakers=speakers))
            ready = artifact.model_copy(
                update={
                    "status": "ready",
                    "metadata": {**artifact.metadata, "completed_at": now_iso()},
                }
            )
            return ArtifactGenerationResult(
                artifact=self.store.save_artifact(ready, audio_path=audio_path),
                audio_path=audio_path,
            )
        except Exception as exc:
            failed = artifact.model_copy(
                update={
                    "status": "failed",
                    "error": str(exc),
                    "metadata": {**artifact.metadata, "failed_at": now_iso()},
                }
            )
            self.store.save_artifact(failed)
            raise ResearchWorkspaceError(str(exc)) from exc

    def process_queued_artifact(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        artifact_id: str,
    ) -> ArtifactGenerationResult:
        artifact = self.store.get_artifact(
            owner_user_id=owner_user_id,
            package_id=package_id,
            artifact_id=artifact_id,
        )
        if artifact is None:
            raise ResearchWorkspaceError("资料产物不存在。")
        raw_request = artifact.metadata.get("artifact_request")
        if not isinstance(raw_request, dict):
            message = "资料产物缺少可恢复的生成请求，无法继续执行。"
            self.store.save_artifact(
                artifact.model_copy(
                    update={
                        "status": "failed",
                        "error": message,
                        "metadata": {**artifact.metadata, "failed_at": now_iso()},
                    }
                )
            )
            raise ResearchWorkspaceError(message)
        try:
            request = ResearchArtifactCreate.model_validate(raw_request)
        except ValueError as exc:
            message = "资料产物的生成请求无效，无法继续执行。"
            self.store.save_artifact(
                artifact.model_copy(
                    update={
                        "status": "failed",
                        "error": message,
                        "metadata": {**artifact.metadata, "failed_at": now_iso()},
                    }
                )
            )
            raise ResearchWorkspaceError(message) from exc
        return self.generate_artifact(
            owner_user_id=owner_user_id,
            package_id=package_id,
            request=request,
            include_unselected_notes=True,
            artifact_id=artifact_id,
        )

    def retry_artifact(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        artifact_id: str,
    ) -> ResearchArtifact:
        artifact = self.store.get_artifact(
            owner_user_id=owner_user_id,
            package_id=package_id,
            artifact_id=artifact_id,
        )
        if artifact is None:
            raise ResearchWorkspaceError("资料产物不存在。")
        if artifact.status != "failed":
            raise ResearchWorkspaceError("只有失败的资料产物可以重试。")
        raw_request = artifact.metadata.get("artifact_request")
        if not isinstance(raw_request, dict):
            raise ResearchWorkspaceError("资料产物缺少可恢复的生成请求，无法重试。")
        metadata = {**artifact.metadata, "queued_at": now_iso()}
        metadata.pop("failed_at", None)
        metadata.pop("completed_at", None)
        return self.store.save_artifact(
            artifact.model_copy(update={"status": "queued", "error": "", "metadata": metadata})
        )

    def capabilities(self) -> ResearchCapabilities:
        return ResearchCapabilities(
            podcast_audio=self.ai.podcast_audio_available(),
            supported_source_types=[
                "pdf", "epub", "docx", "pptx", "xlsx", "csv", "json", "xml",
                "text", "markdown", "html", "image", "audio", "video",
                "web_url", "youtube", "pasted_text",
            ],
        )

    def create_transformation(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        request: ResearchTransformationCreate,
    ) -> ResearchTransformation:
        if not request.name.strip() or not request.instructions.strip():
            raise ResearchWorkspaceError("转换名称和指令不能为空。")
        return self.configuration_store.save_transformation(
            ResearchTransformation(
                owner_user_id=owner_user_id,
                package_id=package_id,
                name=request.name.strip(),
                instructions=request.instructions.strip(),
                output_kind=request.output_kind,
                run_on_import=request.run_on_import,
                metadata=request.metadata,
            )
        )

    def update_transformation(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        transformation_id: str,
        request: ResearchTransformationUpdate,
    ) -> ResearchTransformation:
        item = self.configuration_store.get_transformation(
            owner_user_id=owner_user_id,
            package_id=package_id,
            item_id=transformation_id,
        )
        if item is None:
            raise ResearchWorkspaceError("转换不存在。")
        updates = request.model_dump(exclude_unset=True)
        for key in ("name", "instructions"):
            if key in updates:
                updates[key] = str(updates[key] or "").strip()
                if not updates[key]:
                    raise ResearchWorkspaceError("转换名称和指令不能为空。")
        return self.configuration_store.save_transformation(item.model_copy(update=updates))

    def run_transformation(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        transformation_id: str,
        request: ResearchTransformationRun,
        trigger: str = "manual",
    ) -> ResearchArtifact:
        item = self.configuration_store.get_transformation(
            owner_user_id=owner_user_id,
            package_id=package_id,
            item_id=transformation_id,
        )
        if item is None:
            raise ResearchWorkspaceError("转换不存在。")
        provenance = {
            "transformation_id": item.id,
            "transformation_name": item.name,
            "trigger": trigger,
        }
        if item.output_kind == "podcast":
            message = "播客转换需要说话者与语音配置，不能作为无交互的资料导入转换运行。"
            self.store.save_artifact(
                ResearchArtifact(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    kind=item.output_kind,
                    status="failed",
                    title=request.title.strip() or item.name,
                    source_ingestion_ids=_dedupe(request.source_ingestion_ids),
                    note_ids=_dedupe(request.note_ids),
                    error=message,
                    metadata={"instructions": item.instructions, **provenance},
                )
            )
            raise ResearchWorkspaceError(message)
        return self.generate_artifact(
            owner_user_id=owner_user_id,
            package_id=package_id,
            request=ResearchArtifactCreate(
                kind=item.output_kind,
                title=request.title.strip() or item.name,
                instructions=item.instructions,
                source_ingestion_ids=request.source_ingestion_ids,
                note_ids=request.note_ids,
                text_model=request.text_model,
                synthesize_audio=False,
            ),
            provenance=provenance,
            include_unselected_notes=trigger != "source_import",
        ).artifact

    def queue_transformation_artifact(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        transformation_id: str,
        request: ResearchTransformationRun,
    ) -> ResearchArtifact:
        item = self.configuration_store.get_transformation(
            owner_user_id=owner_user_id,
            package_id=package_id,
            item_id=transformation_id,
        )
        if item is None:
            raise ResearchWorkspaceError("转换不存在。")
        if item.output_kind == "podcast":
            raise ResearchWorkspaceError("播客转换需要说话者与语音配置，请从播客生成入口创建。")
        return self.queue_artifact(
            owner_user_id=owner_user_id,
            package_id=package_id,
            request=ResearchArtifactCreate(
                kind=item.output_kind,
                title=request.title.strip() or item.name,
                instructions=item.instructions,
                source_ingestion_ids=request.source_ingestion_ids,
                note_ids=request.note_ids,
                text_model=request.text_model,
                synthesize_audio=False,
            ),
            provenance={
                "transformation_id": item.id,
                "transformation_name": item.name,
                "trigger": "manual",
            },
        )

    def run_import_transformations(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_ingestion_id: str,
    ) -> ImportTransformationBatchResult:
        artifacts: list[ResearchArtifact] = []
        errors: list[str] = []
        transformations = self.configuration_store.list_import_transformations(
            owner_user_id=owner_user_id,
            package_id=package_id,
        )
        for transformation in transformations:
            before_ids = {
                artifact.id
                for artifact in self.store.list_artifacts(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                )
            }
            try:
                artifact = self.run_transformation(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    transformation_id=transformation.id,
                    request=ResearchTransformationRun(
                        source_ingestion_ids=[source_ingestion_id],
                    ),
                    trigger="source_import",
                )
                artifacts.append(artifact)
            except ResearchWorkspaceError as exc:
                created = next(
                    (
                        artifact
                        for artifact in self.store.list_artifacts(
                            owner_user_id=owner_user_id,
                            package_id=package_id,
                        )
                        if artifact.id not in before_ids
                        and artifact.metadata.get("transformation_id") == transformation.id
                        and artifact.metadata.get("trigger") == "source_import"
                    ),
                    None,
                )
                if created is not None:
                    artifacts.append(created)
                errors.append(f"{transformation.name}: {exc}")
        return ImportTransformationBatchResult(artifacts=artifacts, errors=errors)

    def create_speaker_profile(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        request: ResearchSpeakerProfileCreate,
    ) -> ResearchSpeakerProfile:
        if not request.name.strip() or any(not speaker.name.strip() for speaker in request.speakers):
            raise ResearchWorkspaceError("说话者配置名称不能为空。")
        return self.configuration_store.save_speaker_profile(
            ResearchSpeakerProfile(
                owner_user_id=owner_user_id,
                package_id=package_id,
                name=request.name.strip(),
                speakers=request.speakers,
            )
        )

    def create_episode_profile(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        request: ResearchEpisodeProfileCreate,
    ) -> ResearchEpisodeProfile:
        if not request.name.strip():
            raise ResearchWorkspaceError("节目配置名称不能为空。")
        return self.configuration_store.save_episode_profile(
            ResearchEpisodeProfile(
                owner_user_id=owner_user_id,
                package_id=package_id,
                name=request.name.strip(),
                language=request.language.strip(),
                tone=request.tone.strip(),
                length=request.length,
                segment_count=request.segment_count,
                instructions=request.instructions.strip(),
            )
        )

    def update_speaker_profile(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        profile_id: str,
        request: ResearchSpeakerProfileUpdate,
    ) -> ResearchSpeakerProfile:
        item = self.configuration_store.get_speaker_profile(
            owner_user_id=owner_user_id, package_id=package_id, item_id=profile_id
        )
        if item is None:
            raise ResearchWorkspaceError("说话者配置不存在。")
        updates: dict[str, object] = {}
        if request.name is not None:
            updates["name"] = request.name.strip()
        if request.speakers is not None:
            updates["speakers"] = request.speakers
        if not (updates.get("name", item.name)) or any(
            not speaker.name.strip() for speaker in (updates.get("speakers") or item.speakers)
        ):
            raise ResearchWorkspaceError("说话者配置名称不能为空。")
        return self.configuration_store.save_speaker_profile(item.model_copy(update=updates))

    def update_episode_profile(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        profile_id: str,
        request: ResearchEpisodeProfileUpdate,
    ) -> ResearchEpisodeProfile:
        item = self.configuration_store.get_episode_profile(
            owner_user_id=owner_user_id, package_id=package_id, item_id=profile_id
        )
        if item is None:
            raise ResearchWorkspaceError("节目配置不存在。")
        updates = request.model_dump(exclude_unset=True)
        for key in ("name", "language", "tone", "instructions"):
            if key in updates:
                updates[key] = str(updates[key] or "").strip()
        if not updates.get("name", item.name):
            raise ResearchWorkspaceError("节目配置名称不能为空。")
        return self.configuration_store.save_episode_profile(item.model_copy(update=updates))

    def ask(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        request: ResearchAskRequest,
    ) -> ResearchAskResponse:
        question = request.question.strip()
        if not question:
            raise ResearchWorkspaceError("问题不能为空。")
        try:
            plan_text = self.ai.generate_text(
                instruction=(
                    f"为这个资料问题产生最多 {request.max_queries} 条互补的检索式。"
                    "每行只输出一条检索式，不要解释。\n问题：" + question
                ),
                context="",
                text_model=request.text_model,
            )
        except ResearchAIError as exc:
            raise ResearchWorkspaceError(str(exc)) from exc
        queries = _search_plan_queries(plan_text, max_queries=request.max_queries)
        evidence_by_chunk: dict[str, RetrievalEvidence] = {}
        for query in queries:
            for item in self._search_sources(
                owner_user_id=owner_user_id,
                package_id=package_id,
                query=query,
                mode="semantic",
                limit=6,
                token_budget=6000,
                source_ingestion_ids=request.source_ingestion_ids,
            ):
                key = item.chunk_ids[0] if item.chunk_ids else item.id
                previous = evidence_by_chunk.get(key)
                if previous is None or item.relevance_score > previous.relevance_score:
                    evidence_by_chunk[key] = item
        evidence = sorted(evidence_by_chunk.values(), key=lambda item: item.relevance_score, reverse=True)[:12]
        notes = self._selected_notes(
            owner_user_id=owner_user_id,
            package_id=package_id,
            note_ids=request.note_ids,
            query=question,
            include=request.include_notes,
        )
        try:
            answer = self.ai.generate_text(
                instruction=question,
                context=_research_context(evidence=evidence, notes=notes),
                text_model=request.text_model,
            )
        except ResearchAIError as exc:
            raise ResearchWorkspaceError(str(exc)) from exc
        return ResearchAskResponse(
            question=question,
            search_queries=queries,
            answer=answer,
            citations=[_citation(item) for item in evidence],
        )

    def _search_sources(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        query: str,
        mode: str,
        limit: int,
        token_budget: int,
        source_ingestion_ids: list[str],
    ) -> list[RetrievalEvidence]:
        ready_sources = self.source_store.ready_sources(owner_user_id=owner_user_id, package_id=package_id)
        if source_ingestion_ids:
            requested = set(source_ingestion_ids)
            ready_sources = [source for source in ready_sources if source.id in requested]
        if not ready_sources:
            return []
        hybrid = getattr(self.structure_store, "hybrid_evidence_search", None)
        if callable(hybrid):
            return hybrid(
                owner_user_id=owner_user_id,
                package_id=package_id,
                query=query,
                mode=mode,
                limit=limit,
                token_budget=token_budget,
                source_ingestion_ids=tuple(source_ingestion_ids),
            )
        return self.structure_store.chunk_evidence_search(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            search_mode=mode,
            limit=limit,
            token_budget=token_budget,
            source_ingestion_ids=tuple(source_ingestion_ids),
        )

    def _selected_notes(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        note_ids: list[str],
        query: str,
        include: bool,
    ) -> list[ResearchNote]:
        if not include:
            return []
        if note_ids:
            return [
                note
                for note_id in note_ids
                if (note := self.store.get_note(owner_user_id=owner_user_id, package_id=package_id, note_id=note_id))
            ]
        return [
            note for _score, note in self.store.search_notes(
                owner_user_id=owner_user_id,
                package_id=package_id,
                query=query,
                limit=4,
            )
        ]

    def _require_thread(self, *, owner_user_id: str, package_id: str, thread_id: str) -> ResearchChatThread:
        thread = self.store.get_thread(owner_user_id=owner_user_id, package_id=package_id, thread_id=thread_id)
        if thread is None:
            raise ResearchWorkspaceError("资料对话不存在。")
        return thread


def _research_context(*, evidence: list[RetrievalEvidence], notes: list[ResearchNote]) -> str:
    parts: list[str] = []
    for index, item in enumerate(evidence, start=1):
        location = " / ".join(part for part in [item.source_title, " > ".join(item.section_path), item.page_range] if part)
        parts.append(f"[资料 {index}] {location}\n{item.expanded_text or item.excerpt}")
    for index, note in enumerate(notes, start=1):
        parts.append(f"[笔记 {index}] {note.title}\n{note.content}")
    return "\n\n".join(parts)


def _citation(item: RetrievalEvidence) -> ResearchCitation:
    return ResearchCitation(
        source_ingestion_id=item.source_ingestion_id,
        source_title=item.source_title,
        source_uri=item.source_uri,
        chapter_id=item.chapter_id,
        section_path=item.section_path,
        page_range=item.page_range,
        chunk_ids=item.chunk_ids,
        excerpt=item.excerpt,
    )


def _artifact_instruction(request: ResearchArtifactCreate) -> str:
    shape = {
        "insight": "从所给资料中提炼一个有证据支撑的洞察文档",
        "summary": "总结所给资料，并清楚区分主要结论与支持证据",
        "study_guide": "把所给资料组织成便于理解与复习的学习指南",
        "faq": "把所给资料转换为覆盖关键问题的问答文档",
        "timeline": "按时间或过程顺序组织所给资料中的事件与变化",
        "custom": "按照用户指令转换所给资料",
        "podcast": "根据所给资料写出可直接配音的播客文字稿，每行使用“说话者名称: 台词”格式",
    }[request.kind]
    constraints = [
        shape,
        f"标题：{request.title}" if request.title.strip() else "",
        f"附加要求：{request.instructions}" if request.instructions.strip() else "",
        f"语言：{request.language}" if request.language.strip() else "",
        f"语气：{request.tone}" if request.tone.strip() else "",
        f"篇幅：{request.length}",
    ]
    if request.kind == "podcast":
        speakers = request.speakers[:4]
        if request.segment_count is not None:
            constraints.append(f"节目段落数：{request.segment_count}")
        constraints.append(
            "说话者：" + "；".join(
                "，".join(part for part in [speaker.name, speaker.role, speaker.instructions] if part)
                for speaker in speakers
            )
        )
    constraints.append("只能使用提供的资料与笔记作为事实来源；无法由证据支持的内容要明确说明，不得编造引用。")
    return "\n".join(part for part in constraints if part)


def _artifact_title(kind: str) -> str:
    return {
        "insight": "资料洞察",
        "summary": "资料摘要",
        "study_guide": "学习指南",
        "faq": "资料问答",
        "timeline": "资料时间线",
        "custom": "资料产物",
        "podcast": "资料播客",
    }.get(kind, "资料产物")


def _content_title(content: str, limit: int = 48) -> str:
    first_line = next((line.strip("# *\t") for line in content.splitlines() if line.strip()), "")
    return first_line[:limit] or "未命名"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _search_plan_queries(text: str, *, max_queries: int) -> list[str]:
    queries: list[str] = []
    for line in text.splitlines():
        query = line.strip().lstrip("-*•0123456789.、) ").strip()
        if query and query not in queries:
            queries.append(query)
        if len(queries) >= max_queries:
            break
    if not queries:
        raise ResearchWorkspaceError("模型没有生成有效的资料检索计划。")
    return queries


research_workspace_service = ResearchWorkspaceService()
