def build_ats_analysis_prompt(
    resume_text: str,
    target_role: str,
    experience_level: str,
    job_description: str,
) -> str:
    """
    Builds structured ATS analysis prompt strictly tracking candidate inputs.
    Dynamically adapts based on the target_role value.
    """
    
    # 1. Dynamically classify role type for custom tracking logic
    role_lower = target_role.lower()
    if any(k in role_lower for k in ["design", "ui", "ux", "graphics", "product designer"]):
        role_track_type = "DESIGN / CREATIVE TRACK"
        special_instructions = """
        - Prioritize visual portfolio evaluation, Behance/Dribbble/Figma links, and case studies.
        - Do NOT penalize the user for missing GitHub repositories.
        - Ensure project evaluation searches for design file/live preview links.
        """
    else:
        role_track_type = "TECHNICAL / ENGINEERING TRACK"
        special_instructions = """
        - Prioritize technical stack alignment, frameworks, and system architectures.
        - Ensure project evaluation explicitly checks for active live links or GitHub repository links.
        - Penalize the profile metrics if open-source/code repository links are entirely missing.
        """

    return f"""
You are an expert ATS (Applicant Tracking System) optimizer and premium executive tech recruiter.
Analyze the candidate's resume text against the provided job description objectively.

TARGET EVALUATION PARAMETERS:
- CANDIDATE TARGET ROLE: {target_role}
- EXPECTED EXPERIENCE LEVEL: {experience_level}
- EVALUATION TRACK: {role_track_type}

TRACK INSTRUCTIONS:
{special_instructions}

SCORE TIERS FOR VALIDATION:
- EXCELLENT MATCH (Core skills aligned + metrics included): 85 - 98.
- AVERAGE MATCH (Skills present but lacks optimization/keywords): 60 - 84.
- WEAK MATCH (Massive skill or context gaps): 10 - 59.

Return response in EXACT clean valid JSON format matching the schema below without any markdown formatting wrappers:

{{
  "atsScore": 75,
  "summary": "High-level summary of match capability.",
  "scoreBreakdown": {{
    "skillsMatch": 80,
    "experienceMatch": 70,
    "formattingScore": 85,
    "keywordDensity": 65
  }},
  "skillsAnalysis": {{
    "strongSkills": ["React", "TypeScript"],
    "missingSkills": ["Docker", "AWS"],
    "deprioritizedSkills": ["jQuery"]
  }},
  "experienceEvaluation": {{
    "rating": "Good",
    "feedback": "Years of experience match the job description criteria, but bullet points could use more quantifiable business metrics."
  }},
  "projectEvaluation": [
    {{
      "projectName": "E-commerce React App",
      "rating": "Good",
      "feedback": "Clean component breakdown structure. Include backend scaling parameters.",
      "projectUrl": "https://github.com/user/project"
    }}
  ],
  "suggestedProject": {{
    "title": "Real-time collaborative dashboard with WebSockets",
    "description": "Aligns perfectly with frontend data streaming needs.",
    "difficulty": "Intermediate",
    "tags": ["React", "WebSockets"]
  }},
  "finalRecommendations": [
    "Add measurable achievements with numbers",
    "Include live project links in header"
  ],
  "hygieneCheck": {{
    "grammarIssues": [],
    "hasLinkedIn": true,
    "hasGithub": true,
    "hasPortfolio": false,
    "hasPhone": true,
    "hasEmail": true
  }}
}}

JOB DESCRIPTION SPECIFICATION:
{job_description}

RESUME TEXT DATA TO EVALUATE:
{resume_text}
"""