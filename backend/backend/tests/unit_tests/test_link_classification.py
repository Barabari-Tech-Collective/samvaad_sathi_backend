import pytest
from src.utilities.link_validator import SmartLinkValidator
from src.services.ai_resume.bulk.link_requirements import (
    check_link_requirements,
    _safe_link_records,
    _matches_requirement,
)
from src.services.ai_resume.scoring.link_scorer import LinkScorer


def test_leetcode_and_portfolio_classification():
    validator = SmartLinkValidator()

    # LeetCode and competitive programming
    lc = validator.classify_url("https://leetcode.com/u/karthikthakkalapelly")
    assert lc["platform"] == "leetcode"
    assert lc["category"] == "coding_profile"

    hr = validator.classify_url("https://hackerrank.com/profile/karthik")
    assert hr["platform"] == "hackerrank"
    assert hr["category"] == "coding_profile"

    # Portfolios
    lovable = validator.classify_url("https://karthikthakkalapelly.lovable.app")
    assert lovable["platform"] == "lovable"
    assert lovable["category"] == "portfolio"

    carrd = validator.classify_url("https://candidate.carrd.co")
    assert carrd["platform"] == "carrd"
    assert carrd["category"] == "portfolio"


def test_safe_link_records_formats_labels_properly():
    verified_links = {
        "links": {
            "https://leetcode.com/u/karthik": {
                "valid": True,
                "platform": "leetcode",
                "category": "coding_profile",
                "status_code": 200,
            },
            "https://karthikthakkalapelly.lovable.app": {
                "valid": True,
                "platform": "lovable",
                "category": "portfolio",
                "status_code": 200,
            },
        }
    }

    records = _safe_link_records(verified_links)
    records_by_url = {r["url"]: r for r in records}

    lc_record = records_by_url["https://leetcode.com/u/karthik"]
    assert lc_record["label"] == "LeetCode"
    assert lc_record["category"] == "coding_profile"
    assert lc_record["platform"] == "leetcode"

    port_record = records_by_url["https://karthikthakkalapelly.lovable.app"]
    assert port_record["label"] == "Lovable"
    assert port_record["category"] == "portfolio"
    assert port_record["platform"] == "lovable"


def test_matches_requirement_with_coding_profile_and_portfolio():
    lc_link = {
        "url": "https://leetcode.com/u/karthik",
        "platform": "leetcode",
        "category": "coding_profile",
    }
    lovable_link = {
        "url": "https://karthikthakkalapelly.lovable.app",
        "platform": "lovable",
        "category": "portfolio",
    }

    assert _matches_requirement(lc_link, "coding_profile") is True
    assert _matches_requirement(lc_link, "portfolio") is False
    assert _matches_requirement(lovable_link, "portfolio") is True
    assert _matches_requirement(lovable_link, "coding_profile") is False


def test_link_scorer_with_coding_profile_and_portfolio():
    scorer = LinkScorer()
    validator_output = {
        "links": {
            "https://leetcode.com/u/karthik": {
                "valid": True,
                "platform": "leetcode",
                "category": "coding_profile",
                "status_code": 200,
            },
            "https://karthikthakkalapelly.lovable.app": {
                "valid": True,
                "platform": "lovable",
                "category": "portfolio",
                "status_code": 200,
            },
            "https://linkedin.com/in/karthik": {
                "valid": True,
                "platform": "linkedin",
                "category": "social",
                "status_code": 200,
            },
        }
    }

    report = scorer.score_links(validator_output)
    breakdown = report["categoryBreakdown"]

    assert breakdown["portfolio"]["present"] is True
    assert breakdown["portfolio"]["working"] is True
    assert breakdown["coding_profile"]["present"] is True
    assert breakdown["coding_profile"]["working"] is True
    assert breakdown["linkedin"]["present"] is True
