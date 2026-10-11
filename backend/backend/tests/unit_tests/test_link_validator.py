import pytest
from unittest.mock import AsyncMock, patch
from src.utilities.link_validator import SmartLinkValidator


def test_classify_url_custom_domain_defaults_to_other():
    validator = SmartLinkValidator()
    result = validator.classify_url("https://example-random-domain.com")
    assert result["platform"] == "custom_domain"
    assert result["category"] == "other"


def test_classify_url_custom_deployment():
    validator = SmartLinkValidator()
    result = validator.classify_url("https://my-cool-app.com")
    assert result["platform"] == "custom_deployment"
    assert result["category"] == "deployment"


@pytest.mark.asyncio
async def test_validate_all_links_plain_text_custom_domain_not_portfolio():
    """
    Plain-text URLs extracted from resume text do not have spatial metadata (meta = {}).
    Ensure unrecognised custom domains are NOT falsely classified as 'portfolio'.
    """
    validator = SmartLinkValidator()

    # Mock network active check to avoid real network calls
    with patch.object(validator, "check_link_active_async", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = (True, 200, "")

        links = [
            "https://arbitrary-company.com",
            "https://random-blog-or-site.org",
        ]
        results = await validator.validate_all_links_async(links)

        for url in links:
            record = results["links"][url]
            assert record["category"] != "portfolio", f"{url} should not be classified as portfolio"
            assert record["category"] == "other"


@pytest.mark.asyncio
async def test_validate_all_links_explicit_portfolio_signals():
    """
    Ensure portfolio classification triggers on explicit signals:
    - 'portfolio' in URL
    - 'portfolio', 'contact', 'header' in spatial metadata section or anchor
    """
    validator = SmartLinkValidator()

    with patch.object(validator, "check_link_active_async", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = (True, 200, "")

        extracted = [
            # 1. Bare string with 'portfolio' in URL
            "https://alex-portfolio.com",
            # 2. Embedded link with 'portfolio' in section
            {"url": "https://alex-custom.com", "section": "Portfolio", "anchorText": "My Work"},
            # 3. Embedded link with 'contact' in section
            {"url": "https://personal-site.com", "section": "Contact Information", "anchorText": "Website"},
            # 4. Embedded link with 'header' in section
            {"url": "https://homepage.com", "section": "Header", "anchorText": "Home"},
            # 5. Embedded link with 'portfolio' in anchor text
            {"url": "https://creative-showcase.io", "section": "", "anchorText": "View Portfolio"},
            # 6. Embedded link in project section -> should be deployment
            {"url": "https://live-app.com", "section": "Projects", "anchorText": "Live Demo"},
            # 7. AI website builder (Lovable) with Portfolio anchor
            {"url": "https://karthikthakkalapelly.lovable.app/", "section": "header", "anchorText": "Portfolio"},
        ]

        results = await validator.validate_all_links_async(extracted)

        assert results["links"]["https://alex-portfolio.com"]["category"] == "portfolio"
        assert results["links"]["https://alex-custom.com"]["category"] == "portfolio"
        assert results["links"]["https://personal-site.com"]["category"] == "portfolio"
        assert results["links"]["https://homepage.com"]["category"] == "portfolio"
        assert results["links"]["https://creative-showcase.io"]["category"] == "portfolio"
        assert results["links"]["https://live-app.com"]["category"] == "deployment"
        assert results["links"]["https://karthikthakkalapelly.lovable.app/"]["category"] == "portfolio"


@pytest.mark.asyncio
async def test_validate_all_links_nearby_text_portfolio_signal():
    """
    Ensure plain-text URL without metadata is classified as portfolio
    when 'portfolio' is right next to it in resume text.
    """
    validator = SmartLinkValidator()

    with patch.object(validator, "check_link_active_async", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = (True, 200, "")

        links = ["https://custom-creator-site.com"]
        resume_text = "Portfolio: https://custom-creator-site.com | Phone: 1234567890"

        results = await validator.validate_all_links_async(links, resume_text=resume_text)
        assert results["links"]["https://custom-creator-site.com"]["category"] == "portfolio"

