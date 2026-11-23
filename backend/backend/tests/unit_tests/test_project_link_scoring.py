import pytest
from src.services.ai_resume.scoring.project_scorer import ProjectScorer


@pytest.fixture
def scorer():
    return ProjectScorer()


def test_no_link_provided_capped_for_tech_role(scorer):
    """
    Shifa Dewani case:
    Candidate has detailed bullet points and metrics, but 0 links.
    In a data/tech domain, the score must be capped at 60% (21.0/35.0 max)
    instead of inflating to 74.5%.
    """
    projects = [
        {
            "title": "SQLite Sales Dashboard",
            "description": "Built a SQLite-backed sales dashboard using SQL (SELECT, GROUP BY, SUM/COUNT/AVG) to calculate 4 core metrics.",
            "highlights": [
                "Investigated funnel drop-offs and channel-wise conversion rates",
                "Automated weekly reporting pipelines",
                "Reduced reporting turnaround time by 30%",
                "Delivered actionable insights to business stakeholders",
            ],
            # No links
        }
    ]
    domain_profile = {"id": "data", "label": "Data & Analytics"}
    role_keywords = ["data analyst", "sql", "python", "power bi"]

    result = scorer._score_domain_aware(
        projects,
        role_keywords=role_keywords,
        domain_profile=domain_profile,
    )

    eval_item = result["projectEvaluation"][0]
    verif = eval_item["linkVerification"]

    assert verif["status"] == "NO_LINK_PROVIDED"
    assert verif["badgeType"] == "neutral"
    assert verif["isWorking"] is False
    assert verif["evidenceScore"] == 0.5
    assert "No Public Code or Demo Provided" in verif["title"]
    assert "Add a public GitHub" in verif["actionableFix"]

    # Score MUST be capped at 21.0 (60% max), cannot be 74.5!
    assert eval_item["score"] <= 21.0
    # Equivalent to <= 60% of 35
    assert (eval_item["score"] / 35.0) * 100 <= 60.0


def test_verified_live_repository_scores_full_points(scorer):
    projects = [
        {
            "title": "SQLite Sales Dashboard",
            "description": "Built a SQLite-backed sales dashboard using SQL (SELECT, GROUP BY, SUM/COUNT/AVG) to calculate 4 core metrics.",
            "highlights": [
                "Investigated funnel drop-offs and channel-wise conversion rates",
                "Automated weekly reporting pipelines",
                "Reduced reporting turnaround time by 30%",
            ],
            "repository": {
                "present": True,
                "working": True,
                "url": "https://github.com/shifa/sales-dashboard",
                "status_code": 200,
            },
        }
    ]
    domain_profile = {"id": "data", "label": "Data & Analytics"}
    role_keywords = ["data analyst", "sql"]

    result = scorer._score_domain_aware(
        projects,
        role_keywords=role_keywords,
        domain_profile=domain_profile,
    )

    eval_item = result["projectEvaluation"][0]
    verif = eval_item["linkVerification"]

    assert verif["status"] == "VERIFIED_LIVE"
    assert verif["badgeType"] == "success"
    assert verif["isWorking"] is True
    assert verif["evidenceScore"] == 7.0
    # Not capped at 21.0
    assert eval_item["score"] > 21.0


def test_private_repository_edge_case(scorer):
    projects = [
        {
            "title": "Machine Learning Model",
            "description": "Trained XGBoost model with Python",
            "highlights": ["Trained model", "95% accuracy"],
            "repository": {
                "present": True,
                "working": False,
                "url": "https://github.com/shifa/private-ml-project",
                "status_code": 404,
                "is_private_or_missing_repo": True,
                "user_exists": True,
            },
        }
    ]
    domain_profile = {"id": "software_engineering", "label": "Software Engineering"}

    result = scorer._score_domain_aware(
        projects,
        role_keywords=["python"],
        domain_profile=domain_profile,
    )

    eval_item = result["projectEvaluation"][0]
    verif = eval_item["linkVerification"]

    assert verif["status"] == "PRIVATE_REPO"
    assert verif["badgeType"] == "warning"
    assert verif["isWorking"] is False
    assert verif["evidenceScore"] == 2.5
    assert "Private" in verif["title"]
    assert "Make public" in verif["actionableFix"]
    assert eval_item["score"] <= 21.0


def test_deployment_sleeping_edge_case(scorer):
    projects = [
        {
            "title": "React Ecommerce",
            "description": "Full stack ecommerce store",
            "highlights": ["Built responsive UI", "Integrated Stripe"],
            "deployment": {
                "present": True,
                "working": False,
                "url": "https://ecommerce-demo.onrender.com",
                "status_code": 503,
                "is_sleeping_or_timeout": True,
            },
        }
    ]
    domain_profile = {"id": "software_engineering", "label": "Software Engineering"}

    result = scorer._score_domain_aware(
        projects,
        role_keywords=["react"],
        domain_profile=domain_profile,
    )

    eval_item = result["projectEvaluation"][0]
    verif = eval_item["linkVerification"]

    assert verif["status"] == "DEPLOYMENT_SLEEPING"
    assert verif["badgeType"] == "warning"
    assert verif["isWorking"] is False
    assert verif["evidenceScore"] == 4.0
    assert "Sleeping" in verif["title"]
    assert "wake up" in verif["actionableFix"].lower()


def test_broken_404_edge_case(scorer):
    projects = [
        {
            "title": "Portfolio Website",
            "description": "Personal website",
            "projectUrl": "https://broken-link-domain-1234.com/doesnotexist",
            "repository": {
                "present": False,
                "working": False,
                "status_code": 404,
            },
        }
    ]
    domain_profile = {"id": "software_engineering", "label": "Software Engineering"}

    result = scorer._score_domain_aware(
        projects,
        role_keywords=["developer"],
        domain_profile=domain_profile,
    )

    eval_item = result["projectEvaluation"][0]
    verif = eval_item["linkVerification"]

    assert verif["status"] == "BROKEN_404"
    assert verif["badgeType"] == "error"
    assert verif["isWorking"] is False
    assert verif["evidenceScore"] == 1.5
    assert "404" in verif["title"]


def test_generic_root_url_edge_case(scorer):
    projects = [
        {
            "title": "Social App",
            "description": "Social networking tool",
            "projectUrl": "https://github.com",
        }
    ]
    domain_profile = {"id": "software_engineering", "label": "Software Engineering"}

    result = scorer._score_domain_aware(
        projects,
        role_keywords=["developer"],
        domain_profile=domain_profile,
    )

    eval_item = result["projectEvaluation"][0]
    verif = eval_item["linkVerification"]

    assert verif["status"] == "GENERIC_ROOT_URL"
    assert verif["badgeType"] == "warning"
    assert verif["evidenceScore"] == 1.0


def test_invalid_format_edge_case(scorer):
    projects = [
        {
            "title": "Mobile App",
            "description": "Flutter app",
            "projectUrl": "not_a_valid_url_at_all",
        }
    ]
    domain_profile = {"id": "software_engineering", "label": "Software Engineering"}

    result = scorer._score_domain_aware(
        projects,
        role_keywords=["developer"],
        domain_profile=domain_profile,
    )

    eval_item = result["projectEvaluation"][0]
    verif = eval_item["linkVerification"]

    assert verif["status"] == "INVALID_FORMAT"
    assert verif["badgeType"] == "error"
    assert verif["evidenceScore"] == 1.0


def test_explicit_link_requirements_auto_enables_for_tech():
    from src.services.ai_resume.bulk.domain_profile import explicit_link_requirements

    # Tech role: Data Analyst
    cats = explicit_link_requirements(
        role_name="Data Analyst",
        role_description="Analyze business metrics using SQL and Python",
        evaluation_intent="",
        required_skills=["SQL", "Python"],
    )
    assert "project_evidence" in cats

    # Non-tech role: HR Generalist without link keywords
    non_tech_cats = explicit_link_requirements(
        role_name="HR Specialist",
        role_description="Manage recruitment and employee relations",
        evaluation_intent="",
        required_skills=["Communication", "Payroll"],
    )
    assert "project_evidence" not in non_tech_cats
    assert len(non_tech_cats) == 0

    # Non-tech role but explicit require_project_links=True
    forced_cats = explicit_link_requirements(
        role_name="Operations Associate",
        role_description="Logistics",
        evaluation_intent="",
        required_skills=[],
        require_project_links=True,
    )
    assert "project_evidence" in forced_cats


def test_preassigned_project_url_binds_repository_metadata(scorer):
    from src.services.ai_resume.scoring.project_mapper import ProjectLinkMapper

    mapper = ProjectLinkMapper()
    projects = [
        {
            "title": "AI Sales Performance BI Dashboard",
            "projectUrl": "https://github.com/Harshu1707/AI_Sales_Performance_BI_Dashboard",
            "description": "Developed an AI-powered Sales Performance Dashboard to analyze sales data.",
            "highlights": ["Built interactive dashboards to visualize key performance metrics."],
        }
    ]
    validated_links = {
        "links": {
            "https://github.com/Harshu1707/AI_Sales_Performance_BI_Dashboard": {
                "url": "https://github.com/Harshu1707/AI_Sales_Performance_BI_Dashboard",
                "valid": True,
                "status_code": 200,
                "category": "repository",
                "platform": "github",
            }
        }
    }

    mapped = mapper.map_links_to_projects(projects, validated_links)
    assert mapped[0].get("repository") is not None
    assert mapped[0]["repository"]["working"] is True
    assert mapped[0]["repository"]["status_code"] == 200

    domain_profile = {"id": "data_analytics", "label": "Data and Analytics", "is_tech": True}
    eval_res = scorer._score_domain_aware(mapped, role_keywords=["python", "sql"], domain_profile=domain_profile)
    verif = eval_res["projectEvaluation"][0]["linkVerification"]
    assert verif["status"] == "VERIFIED_LIVE"
    assert verif["statusCode"] == 200
    assert verif["isWorking"] is True
    assert verif["evidenceScore"] == 7.0

