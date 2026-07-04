from __future__ import annotations

import logging
import os
import re
import time

from pydantic import BaseModel, Field

from app.models import ChatRequest, FormulaInkPayload
from app.services.ai_logging import ai_usage_logger
from app.services.openai_course_ai import openai_course_ai

logger = logging.getLogger(__name__)

FORMULA_INK_MAX_DATA_URL_CHARS = int(os.getenv("FORMULA_INK_MAX_DATA_URL_CHARS", "1600000"))


class FormulaInkRecognition(BaseModel):
    latex: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_confirmation: bool = False
    note: str = ""


def resolve_formula_ink_request(request: ChatRequest) -> ChatRequest:
    payload = request.formula_ink
    if payload is None:
        return request

    source_latex = (payload.source_latex or request.selection.excerpt if request.selection else payload.source_latex or "").strip()
    recognition = recognize_formula_ink(payload)
    recognized_latex = _normalize_latex(recognition.latex)
    if not recognized_latex:
        return request.model_copy(
            update={
                "message": _unrecognized_message(payload, source_latex),
                "interaction_mode": "ask",
                "selection": None,
                "formula_ink": None,
            }
        )
    if payload.action == "replace" and recognition.needs_confirmation:
        return request.model_copy(
            update={
                "message": _confirmation_message(source_latex=source_latex, recognized_latex=recognized_latex),
                "interaction_mode": "ask",
                "selection": None,
                "formula_ink": None,
            }
        )

    return request.model_copy(
        update={
            "message": _recognized_message(
                base_message=request.message,
                action=payload.action,
                source_latex=source_latex,
                recognized_latex=recognized_latex,
                needs_confirmation=recognition.needs_confirmation,
            ),
            "formula_ink": None,
        }
    )


def recognize_formula_ink(payload: FormulaInkPayload) -> FormulaInkRecognition:
    if not _supported_image_data_url(payload.image_data_url):
        return FormulaInkRecognition(needs_confirmation=True, note="unsupported_or_too_large_image")
    if openai_course_ai.client is None:
        return FormulaInkRecognition(needs_confirmation=True, note="openai_not_configured")

    model = os.getenv("OPENAI_FORMULA_INK_MODEL") or openai_course_ai.config.default_model
    started_at = time.perf_counter()
    try:
        response = openai_course_ai.client.responses.parse(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You recognize handwritten mathematical formulas for a general document editor. "
                        "Return only structured data. Convert the handwriting to concise LaTeX. "
                        "Do not infer course-specific context or add explanations."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Recognize the handwritten formula image. "
                                f"The currently selected formula is: {payload.source_latex or 'unknown'}"
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": payload.image_data_url,
                            "detail": "high",
                        },
                    ],
                },
            ],
            text_format=FormulaInkRecognition,
        )
        parsed = response.output_parsed
        ai_usage_logger.log_event(
            "formula_ink_recognition",
            provider="openai",
            model=model,
            action=payload.action,
            source_latex=payload.source_latex or "",
            image_data_url_chars=len(payload.image_data_url),
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            parsed_output=parsed,
        )
        return parsed if isinstance(parsed, FormulaInkRecognition) else FormulaInkRecognition()
    except Exception as exc:  # pragma: no cover - provider/runtime dependent
        ai_usage_logger.log_event(
            "formula_ink_recognition_error",
            provider="openai",
            model=model,
            action=payload.action,
            source_latex=payload.source_latex or "",
            image_data_url_chars=len(payload.image_data_url),
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            error=str(exc),
        )
        logger.warning("Formula ink recognition failed: %s", exc)
        return FormulaInkRecognition(needs_confirmation=True, note="recognition_error")


def _supported_image_data_url(value: str) -> bool:
    return value.startswith("data:image/") and 0 < len(value) <= FORMULA_INK_MAX_DATA_URL_CHARS


def _normalize_latex(value: str) -> str:
    latex = re.sub(r"\s+", " ", value or "").strip()
    if latex.startswith("$$") and latex.endswith("$$"):
        latex = latex[2:-2].strip()
    if latex.startswith(r"\[") and latex.endswith(r"\]"):
        latex = latex[2:-2].strip()
    if latex.startswith("$") and latex.endswith("$"):
        latex = latex[1:-1].strip()
    return latex


def _recognized_message(
    *,
    base_message: str,
    action: str,
    source_latex: str,
    recognized_latex: str,
    needs_confirmation: bool,
) -> str:
    confidence_note = "\n如果识别结果看起来不可靠，先让用户确认，不要贸然写入。" if needs_confirmation else ""
    if action == "replace":
        return (
            f"{base_message.strip()}\n\n"
            f"手写公式识别结果（LaTeX）：{recognized_latex}\n"
            f"当前选中的原公式（LaTeX）：{source_latex or '未提供'}\n"
            "请把当前选中的公式更改为手写公式识别结果。只处理这个公式目标，不要改写无关内容。"
            f"{confidence_note}"
        ).strip()
    return (
        f"{base_message.strip()}\n\n"
        f"手写公式识别结果（LaTeX）：{recognized_latex}\n"
        f"当前选中的原公式（LaTeX）：{source_latex or '未提供'}\n"
        "请围绕这两个公式回答，不要修改右侧板书。"
        f"{confidence_note}"
    ).strip()


def _unrecognized_message(payload: FormulaInkPayload, source_latex: str) -> str:
    action_label = "更改" if payload.action == "replace" else "引用"
    return (
        f"用户尝试通过手写公式画板{action_label}当前公式，但系统没有可靠识别出手写公式。\n"
        f"当前选中的原公式（LaTeX）：{source_latex or '未提供'}\n"
        "不要修改右侧板书；请让用户重画、写得更清楚，或直接输入 LaTeX。"
    )


def _confirmation_message(*, source_latex: str, recognized_latex: str) -> str:
    return (
        "用户尝试通过手写公式画板更改当前公式，但识别结果需要确认。\n"
        f"当前选中的原公式（LaTeX）：{source_latex or '未提供'}\n"
        f"手写公式识别结果（LaTeX）：{recognized_latex}\n"
        "不要修改右侧板书；请先让用户确认是否使用这个识别结果。"
    )
