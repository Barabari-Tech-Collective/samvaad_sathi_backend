import pytest
from src.services.ai_resume.bulk.evidence import build_category_evidence
from src.services.ai_resume.scoring.skill_normalizer import SkillNormalizer
from src.services.ai_resume.scoring.skills_scorer import SkillsScorer


def test_vyshnavi_excel_and_data_analysis_skills_matched():
    """Verifies that Microsoft Excel, Advanced Excel, and Data Analysis are matched correctly."""
    resume_text = """
    VYSHNAVI LAKKARSU
    PROFESSIONAL SUMMARY
    Detail-oriented Electronics and Communication Engineering student with hands-on experience in Python-based data analysis,
    data preprocessing, and machine learning model development through academic projects. Skilled in collecting, cleaning, and
    interpreting data to generate actionable insights and support data-driven decision-making.
    TECHNICAL SKILLS
    Programming & Data Analysis: Python (data cleaning, data preprocessing, exploratory data analysis), C, HTML/CSS
    Data & Analytics Concepts: Data Collection, Data Cleaning, Statistical Analysis, Exploratory Data Analysis (EDA)
    Tools: VS Code, Google Colab, MS Excel (basic), MS Office Suite
    Currently Building: SQL, Microsoft Excel (advanced), Power BI / Tableau for data visualization and reporting
    ADDITIONAL TRAINING
    Data Science Training (3 Months) — United Way of Hyderabad: Trained in data analysis fundamentals, data interpretation.
    KEY SKILLS
    Analytical Thinking | Problem-Solving | Communication Skills
    """
    parsed_resume_json = {
        "skills": [
            "Python",
            "SQL",
            "Power BI",
            "Tableau",
            "Data Visualization",
            "Data Cleaning",
            "Exploratory Data Analysis (EDA)",
            "Statistical Analysis",
            "Analytical Thinking",
            "MS Excel (basic)",
            "Microsoft Excel (advanced)",
        ]
    }
    required_skills = [
        "SQL",
        "Python",
        "Microsoft Excel",
        "Advanced Excel",
        "Power BI",
        "Tableau",
        "Data Visualization",
        "Data Cleaning",
        "Data Wrangling",
        "Data Analysis",
        "Exploratory Data Analysis (EDA)",
        "Statistical Analysis",
        "Pandas",
        "NumPy",
        "Matplotlib",
        "Seaborn",
        "MySQL",
        "PostgreSQL",
        "Problem Solving",
        "Communication Skills",
    ]

    evidence = build_category_evidence(
        resume_text=resume_text,
        parsed_resume_json=parsed_resume_json,
        required_skills=required_skills,
        preferred_skills=[],
        link_report={},
        jev_report={},
    )
    skills_evidence = evidence.get("skills", [])
    matched_text = next((item["text"] for item in skills_evidence if "Matched:" in item["text"]), "")
    not_evidenced_text = next((item["text"] for item in skills_evidence if "Not evidenced:" in item["text"]), "")

    # Verify Microsoft Excel, Advanced Excel, and Data Analysis are MATCHED
    assert "Microsoft Excel" in matched_text
    assert "Advanced Excel" in matched_text
    assert "Data Analysis" in matched_text
    assert "Problem Solving" in matched_text
    assert "Communication Skills" in matched_text

    # Verify truly missing skills are NOT evidenced
    assert "Pandas" in not_evidenced_text
    assert "NumPy" in not_evidenced_text
    assert "MySQL" in not_evidenced_text
    assert "PostgreSQL" in not_evidenced_text
    assert "Data Wrangling" in not_evidenced_text


def test_software_engineering_role_skills():
    """Verifies multi-role coverage: Software Engineering & DevOps."""
    resume_text = """
    Full-stack developer with 3 years of experience in JavaScript/TypeScript, React.js, and Node.js.
    Designed and deployed microservices on AWS (Amazon Web Services) with Docker and Kubernetes (K8s).
    Built CI/CD pipelines using GitHub Actions. Experience with PostgreSQL and MongoDB.
    """
    parsed_skills = ["React.js", "Node.js", "Docker", "K8s", "Postgres"]
    required_skills = [
        "React",
        "TypeScript",
        "Node.js",
        "Docker",
        "Kubernetes",
        "AWS",
        "CI/CD",
        "PostgreSQL",
        "Go",  # Missing
        "Rust",  # Missing
    ]
    evidence = build_category_evidence(
        resume_text=resume_text,
        parsed_resume_json={"skills": parsed_skills},
        required_skills=required_skills,
        preferred_skills=[],
        link_report={},
        jev_report={},
    )
    matched_text = next(item["text"] for item in evidence["skills"] if "Matched:" in item["text"])
    not_evidenced_text = next(item["text"] for item in evidence["skills"] if "Not evidenced:" in item["text"])

    assert "React" in matched_text
    assert "TypeScript" in matched_text
    assert "Docker" in matched_text
    assert "Kubernetes" in matched_text
    assert "AWS" in matched_text
    assert "CI/CD" in matched_text
    assert "PostgreSQL" in matched_text

    assert "Go" in not_evidenced_text
    assert "Rust" in not_evidenced_text


def test_product_management_and_business_role_skills():
    """Verifies multi-role coverage: Product Management, Agile & Business."""
    resume_text = """
    Senior Product Manager with expertise in Agile & Scrum methodologies.
    Led cross-functional teams using Jira and Confluence for sprint planning and user stories.
    Experienced in Market Research, Stakeholder Management, and CRM tools like Salesforce.
    Strong problem-solving and critical thinking abilities.
    """
    parsed_skills = ["Agile", "Jira", "Scrum"]
    required_skills = [
        "Product Management",
        "Scrum",
        "Jira",
        "Confluence",
        "Market Research",
        "Stakeholder Management",
        "Salesforce",
        "Critical Thinking",
        "Financial Modeling",  # Missing
    ]
    evidence = build_category_evidence(
        resume_text=resume_text,
        parsed_resume_json={"skills": parsed_skills},
        required_skills=required_skills,
        preferred_skills=[],
        link_report={},
        jev_report={},
    )
    matched_text = next(item["text"] for item in evidence["skills"] if "Matched:" in item["text"])
    not_evidenced_text = next(item["text"] for item in evidence["skills"] if "Not evidenced:" in item["text"])

    assert "Product Management" in matched_text
    assert "Scrum" in matched_text
    assert "Jira" in matched_text
    assert "Confluence" in matched_text
    assert "Market Research" in matched_text
    assert "Stakeholder Management" in matched_text
    assert "Salesforce" in matched_text
    assert "Critical Thinking" in matched_text

    assert "Financial Modeling" in not_evidenced_text


def test_skills_scorer_deterministic_score_with_expanded_tokens():
    """Verifies that SkillsScorer properly gives points for qualified skills."""
    scorer = SkillsScorer()
    parsed_skills = ["MS Excel (basic)", "Microsoft Excel (advanced)", "Programming & Data Analysis"]
    jd_skills = ["Microsoft Excel", "Advanced Excel", "Data Analysis", "Python"]
    raw_resume = "Skilled in Python-based data analysis and advanced excel."
    report = scorer.score_skills(
        resume_skills=parsed_skills,
        jd_skills=jd_skills,
        raw_resume_text=raw_resume,
    )
    analysis = report["skillsAnalysis"]
    strong = analysis["strongSkills"]
    # All 4 skills should be recognized
    assert "Microsoft Excel" in strong
    assert "Advanced Excel" in strong
    assert "Data Analysis" in strong
    assert "Python" in strong
    assert report["totalScore"] == 25.0
