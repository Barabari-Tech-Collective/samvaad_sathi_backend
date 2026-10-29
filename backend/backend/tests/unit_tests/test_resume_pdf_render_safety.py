import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.resume.safe_rendering import safe_resume_markup_data, deny_resume_resource


def test_all_nested_resume_text_is_escaped_without_mutating_original():
    text = "<img src='file:///private/test'> & नमस्ते"
    data = {"header": {"fullName": text}, "projects": [{"title": text, "bullets": [text]}]}
    clean = safe_resume_markup_data(data)
    assert "<img" not in str(clean)
    assert "&lt;img" in clean["projects"][0]["bullets"][0]
    assert "नमस्ते" in clean["header"]["fullName"]
    assert data["header"]["fullName"] == text


@pytest.mark.parametrize("link", ["javascript:alert(1)", "file:///private/test", "data:text/html,x", "https://user:password@example.test", "https://example.test/\npath", "https://[broken"])
def test_unsafe_hyperlinks_are_not_embedded(link):
    assert safe_resume_markup_data({"github_link": link})["github_link"] == ""


def test_safe_link_quotes_cannot_escape_attribute():
    assert safe_resume_markup_data({"liveUrl": "https://example.test/?q='x'&b=2"})["liveUrl"] == "https://example.test/?q=&#x27;x&#x27;&amp;b=2"


@pytest.mark.parametrize("uri", ["https://example.test/pixel", "file:///private/test", "data:image/png,test", "../test"])
def test_renderer_cannot_fetch_any_resource(uri):
    with pytest.raises(ValueError, match="not permitted"):
        deny_resume_resource(uri)


@pytest.mark.asyncio
async def test_download_route_uses_escaped_data_and_no_fetch_renderer(monkeypatch):
    from src.api.routes import resume_builder
    payload = '<img src="https://example.invalid/pixel"><script>x</script>'
    monkeypatch.setattr(resume_builder, "ResumeBuilderRepository", lambda session: SimpleNamespace(get_resume_by_id_and_user=AsyncMock(return_value=SimpleNamespace(resume_data={"header": {"fullName": payload}, "summary": payload, "projects": [{"title": payload, "github_link": "javascript:alert(1)", "bullets": [payload]}]}))))
    captured = {}

    def create(source, dest, link_callback):
        captured["html"] = source.read()
        captured["callback"] = link_callback
        dest.write(b"%PDF-test")
        return SimpleNamespace(err=0)

    monkeypatch.setitem(sys.modules, "xhtml2pdf", SimpleNamespace(pisa=SimpleNamespace(CreatePDF=create)))
    response = await resume_builder.download_resume_pdf(1, session=None, current_user=SimpleNamespace(id=1))
    assert response.media_type == "application/pdf"
    assert "<script>" not in captured["html"] and "<img" not in captured["html"]
    assert "&lt;img" in captured["html"]
    assert "javascript:" not in captured["html"]
    assert captured["callback"] is deny_resume_resource


@pytest.mark.asyncio
async def test_pdf_errors_do_not_return_internal_details(monkeypatch):
    from fastapi import HTTPException
    from src.api.routes import resume_builder
    monkeypatch.setattr(resume_builder, "ResumeBuilderRepository", lambda session: SimpleNamespace(get_resume_by_id_and_user=AsyncMock(return_value=SimpleNamespace(resume_data={"header": {"fullName": "Example"}}))))
    def fail(*args, **kwargs):
        raise RuntimeError("private renderer detail")
    monkeypatch.setitem(sys.modules, "xhtml2pdf", SimpleNamespace(pisa=SimpleNamespace(CreatePDF=fail)))
    with pytest.raises(HTTPException) as failure:
        await resume_builder.download_resume_pdf(1, session=None, current_user=SimpleNamespace(id=1))
    assert failure.value.status_code == 500
    assert "private" not in failure.value.detail


def test_real_pdf_renderer_keeps_literal_text_and_paginates():
    from xhtml2pdf import pisa
    from pypdf import PdfReader
    import io
    data = safe_resume_markup_data({"summary": "<script>literal</script> & résumé"})
    markup = "<html><body>" + "".join(
        f"<p>Paragraph {index}: {data['summary']}</p>" for index in range(150)
    ) + "<p>END OF RESUME</p></body></html>"
    output = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(markup), dest=output, link_callback=deny_resume_resource)
    assert not result.err
    document = PdfReader(output)
    text = "\n".join(page.extract_text() for page in document.pages)
    assert len(document.pages) > 1
    assert "END OF RESUME" in text
    assert "<script>literal</script>" in text
