import io
import zipfile

import pytest

from src.services.ai_resume.parser_service import inspect_resume_upload


def _docx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
    return output.getvalue()


def test_accepts_valid_pdf_even_when_the_browser_mime_type_is_generic():
    assert inspect_resume_upload(b"%PDF-1.7\nresume", "candidate.pdf") == (
        ".pdf",
        "application/pdf",
    )


def test_accepts_a_valid_docx_container_and_returns_canonical_mime_type():
    assert inspect_resume_upload(_docx_bytes(), "candidate.docx") == (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        (b"not a document", "candidate.pdf", "contents do not match a PDF"),
        (b"not a document", "candidate.docx", "contents do not match a DOCX"),
        (b"%PDF-1.7", "candidate.exe", "Only PDF and DOCX"),
    ],
)
def test_rejects_filename_or_content_spoofing(content, filename, message):
    with pytest.raises(ValueError, match=message):
        inspect_resume_upload(content, filename)
