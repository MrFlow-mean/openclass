from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel

from app.models import AgentActivityEvent, LearningRequirementSheet
from app.services.codex_app_server import CodexAppServerTextClient


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


@dataclass(frozen=True)
class StructuredExecutionResult:
    output_parsed: Any
    activity: list[AgentActivityEvent] = field(default_factory=list)


@dataclass(frozen=True)
class BoardGenerationExecutionRequest:
    requirement: LearningRequirementSheet
    teaching_plan: str
    image_inputs: list[str] = field(default_factory=list)
    visual_manifest: list[dict[str, Any]] = field(default_factory=list)


class BoardGenerationExecutionResult(Protocol):
    thread_id: str
    turn_id: str | None
    final_response: str
    activity: list[AgentActivityEvent]


class AIExecutionAdapter(Protocol):
    """Provider-neutral execution boundary for OpenClass AI roles."""

    def parse_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
    ) -> StructuredExecutionResult: ...

    def generate_board(
        self,
        request: BoardGenerationExecutionRequest,
        *,
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> tuple[BoardGenerationExecutionResult, str]: ...

    def explain_from_directive(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
    ) -> StructuredExecutionResult: ...

    def analyze_image_batch(
        self,
        *,
        prompt: str,
        image_inputs: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> str: ...


BoardRunner = Callable[
    [
        str,
        str,
        LearningRequirementSheet,
        str,
        list[str],
        list[dict[str, Any]],
        Callable[[], bool] | None,
        Callable[[AgentActivityEvent], None] | None,
    ],
    tuple[BoardGenerationExecutionResult, str],
]
ImageAnalysisRunner = Callable[
    [
        str,
        str,
        str,
        list[str],
        Callable[[], bool] | None,
        Callable[[AgentActivityEvent], None] | None,
    ],
    str,
]


class CodexAIExecutionAdapter:
    """Codex app-server implementation of the provider-neutral execution contract."""

    def __init__(
        self,
        *,
        owner_user_id: str,
        model: str,
        board_runner: BoardRunner | None = None,
        image_analysis_runner: ImageAnalysisRunner | None = None,
    ) -> None:
        self.owner_user_id = owner_user_id
        self.model = model
        self._board_runner = board_runner
        self._image_analysis_runner = image_analysis_runner

    def parse_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
    ) -> StructuredExecutionResult:
        response = CodexAppServerTextClient(self.owner_user_id).parse(
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            image_inputs=image_inputs,
        )
        return StructuredExecutionResult(
            output_parsed=response.output_parsed,
            activity=response.activity,
        )

    def generate_board(
        self,
        request: BoardGenerationExecutionRequest,
        *,
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> tuple[BoardGenerationExecutionResult, str]:
        if self._board_runner is None:
            raise RuntimeError("This AI adapter has no board-generation runner")
        return self._board_runner(
            self.owner_user_id,
            self.model,
            request.requirement,
            request.teaching_plan,
            request.image_inputs,
            request.visual_manifest,
            is_cancelled,
            on_activity,
        )

    def explain_from_directive(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
    ) -> StructuredExecutionResult:
        return self.parse_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
        )

    def analyze_image_batch(
        self,
        *,
        prompt: str,
        image_inputs: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> str:
        if self._image_analysis_runner is None:
            raise RuntimeError("This AI adapter has no image-analysis runner")
        return self._image_analysis_runner(
            self.owner_user_id,
            self.model,
            prompt,
            image_inputs,
            is_cancelled,
            on_activity,
        )
