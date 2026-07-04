from __future__ import annotations

from app.models import ChatRequest, FormulaInkPayload, SelectionRef
from app.services import formula_ink_resolver
from app.services.formula_ink_resolver import FormulaInkRecognition, resolve_formula_ink_request


def test_formula_ink_reference_adds_recognized_latex(monkeypatch) -> None:
    monkeypatch.setattr(
        formula_ink_resolver,
        "recognize_formula_ink",
        lambda _payload: FormulaInkRecognition(latex=r"\frac{x^2 - 1}{x - 1}", confidence=0.91),
    )
    request = ChatRequest(
        message="请识别我手写的公式，并结合当前选中的公式回答。",
        selection=SelectionRef(kind="board", location_kind="target_range", excerpt="f(x)"),
        formula_ink=FormulaInkPayload(
            action="reference",
            source_latex="f(x)",
            image_data_url="data:image/png;base64,abc",
        ),
    )

    resolved = resolve_formula_ink_request(request)

    assert resolved.formula_ink is None
    assert resolved.interaction_mode == "ask"
    assert resolved.selection == request.selection
    assert r"\frac{x^2 - 1}{x - 1}" in resolved.message
    assert "不要修改右侧板书" in resolved.message


def test_formula_ink_replace_adds_direct_edit_instruction(monkeypatch) -> None:
    monkeypatch.setattr(
        formula_ink_resolver,
        "recognize_formula_ink",
        lambda _payload: FormulaInkRecognition(latex=r"x_n=\frac{n}{n+1}", confidence=0.96),
    )
    request = ChatRequest(
        message="请识别我手写的公式，并把当前选中的公式更改为识别结果。",
        interaction_mode="direct_edit",
        selection=SelectionRef(kind="board", location_kind="target_range", excerpt="x_n"),
        formula_ink=FormulaInkPayload(
            action="replace",
            source_latex="x_n",
            image_data_url="data:image/png;base64,abc",
        ),
    )

    resolved = resolve_formula_ink_request(request)

    assert resolved.formula_ink is None
    assert resolved.interaction_mode == "direct_edit"
    assert resolved.selection == request.selection
    assert r"x_n=\frac{n}{n+1}" in resolved.message
    assert "只处理这个公式目标" in resolved.message


def test_formula_ink_replace_failure_does_not_keep_edit_intent(monkeypatch) -> None:
    monkeypatch.setattr(
        formula_ink_resolver,
        "recognize_formula_ink",
        lambda _payload: FormulaInkRecognition(latex="", needs_confirmation=True),
    )
    request = ChatRequest(
        message="请识别我手写的公式，并把当前选中的公式更改为识别结果。",
        interaction_mode="direct_edit",
        selection=SelectionRef(kind="board", location_kind="target_range", excerpt="A_0"),
        formula_ink=FormulaInkPayload(
            action="replace",
            source_latex="A_0",
            image_data_url="data:image/png;base64,abc",
        ),
    )

    resolved = resolve_formula_ink_request(request)

    assert resolved.formula_ink is None
    assert resolved.interaction_mode == "ask"
    assert resolved.selection is None
    assert "不要修改右侧板书" in resolved.message
    assert "重画" in resolved.message


def test_formula_ink_replace_uncertain_recognition_requires_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        formula_ink_resolver,
        "recognize_formula_ink",
        lambda _payload: FormulaInkRecognition(latex=r"A_1", confidence=0.42, needs_confirmation=True),
    )
    request = ChatRequest(
        message="请识别我手写的公式，并把当前选中的公式更改为识别结果。",
        interaction_mode="direct_edit",
        selection=SelectionRef(kind="board", location_kind="target_range", excerpt="A_0"),
        formula_ink=FormulaInkPayload(
            action="replace",
            source_latex="A_0",
            image_data_url="data:image/png;base64,abc",
        ),
    )

    resolved = resolve_formula_ink_request(request)

    assert resolved.formula_ink is None
    assert resolved.interaction_mode == "ask"
    assert resolved.selection is None
    assert "A_1" in resolved.message
    assert "确认" in resolved.message
    assert "不要修改右侧板书" in resolved.message
