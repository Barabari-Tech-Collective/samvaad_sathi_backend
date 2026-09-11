import pytest

from src.main import initialize_backend_application


def test_resume_extraction_response_includes_details_schema():
    app = initialize_backend_application()
    schema = app.openapi()

    components = schema.get("components", {})
    schemas = components.get("schemas", {})

    rx_schema = schemas.get("ResumeExtractionResponse")
    assert rx_schema is not None
    props = rx_schema.get("properties", {})
    assert "details" in props

    # ResumeDetails should also be present with some expected nested structures
    details_schema = schemas.get("ResumeDetails")
    assert details_schema is not None
    d_props = details_schema.get("properties", {})
    # Spot-check a few fields
    assert "fullName" in d_props
    assert "education" in d_props
    assert "experience" in d_props


