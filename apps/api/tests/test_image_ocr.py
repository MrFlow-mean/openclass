import json
import subprocess

from reportlab.pdfgen import canvas

from app.services import image_ocr


def _write_blank_pdf(path, *, pages: int = 3) -> None:
    pdf = canvas.Canvas(str(path))
    for _ in range(pages):
        pdf.showPage()
    pdf.save()


def test_extract_pdf_page_texts_preserves_page_statuses(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "scan.pdf"
    _write_blank_pdf(pdf_path, pages=3)

    def fake_run(command, **kwargs):
        page_number = int(command[-3])
        if page_number == 2:
            raise subprocess.CalledProcessError(
                1,
                command,
                stderr="page render failed",
            )
        text = "OCR page one evidence." if page_number == 1 else ""
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"text": text, "lines": [text] if text else []}),
            stderr="",
        )

    monkeypatch.setattr(image_ocr.subprocess, "run", fake_run)

    pages = image_ocr.extract_pdf_page_texts(pdf_path, max_pages=3, page_timeout=5)

    assert [(page.page_number, page.status) for page in pages] == [
        (1, "text"),
        (2, "error"),
        (3, "empty"),
    ]
    assert pages[0].text == "OCR page one evidence."
    assert pages[1].error == "page render failed"
