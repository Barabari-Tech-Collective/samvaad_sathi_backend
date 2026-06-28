import json

def build_ats_analysis_prompt(
    resume_text: str,
    target_role: str,
    experience_level: str,
    job_description: str,
    deterministic_report: dict,
) -> str:
    """
    Builds the complete production ATS optimization prompt.
    Forces OpenAI to return separate, individual feedback blocks for every project.
    """
    role_lower = target_role.lower()
    is_fresher = any(k in experience_level.lower() for k in ["fresher", "intern", "entry", "0 years"])

    if any(k in role_lower for k in ["design", "ui", "ux", "graphics", "product designer"]):
        role_track_type = "DESIGN / CREATIVE TRACK"
        link_validation_instruction = "- Focus on visual portfolios (Behance, Dribbble, Figma)."
    else:
        role_track_type = "TECHNICAL / ENGINEERING TRACK"
        link_validation_instruction = "- Focus on active code bases (GitHub) and live cloud deployments."

    exact_total_score = deterministic_report["atsScore"]
    exact_breakdown = deterministic_report["scoreBreakdown"]

    # Extract target project list computed by Python to construct an explicit target template structure
    python_projects = deterministic_report.get("deterministicMetrics", {}).get("projectModule", {}).get("projectEvaluation", [])
    
    project_json_schema_builder = []
    for proj in python_projects:
        name = proj["projectName"]
        score = proj["score"]
        gaps_str = ", ".join(proj["detectedGaps"]) if proj["detectedGaps"] else "None"
        project_json_schema_builder.append(
            f'{{\n      "projectName": "{name}",\n      "feedback": "Write unique, deep engineering critique specifically for the \'{name}\' project. Address why it scored {score}/40 based on these verified issues: {gaps_str}."\n    }}'
        )
    
    schema_projects_block = ",\n    ".join(project_json_schema_builder)

    return f"""
You are an expert ATS (Applicant Tracking System) optimizer and premium recruiter.
Your objective is to provide professional natural language summaries, granular feedback, and roadmap recommendations for a candidate based on their resume and a target job description.

CRITICAL ARCHITECTURAL REQUIREMENT:
A deterministic Python engine has already analyzed the technical elements of this resume and calculated the EXACT numerical scores. You are strictly FORBIDDEN from altering, guessing, or recalculating these numbers. You must map them directly into your JSON output fields as specified below.

TARGET SYSTEM SCORES TO INJECT (USE THESE EXACT NUMBERS):
- MASTER ATS SCORE: {exact_total_score}
- skillsMatch BREAKDOWN: {exact_breakdown['skillsMatch']}
- experienceMatch BREAKDOWN: {exact_breakdown['experienceMatch']}
- formattingScore BREAKDOWN: {exact_breakdown['formattingScore']}
- keywordDensity BREAKDOWN: {exact_breakdown['keywordDensity']}

TARGET EVALUATION PARAMETERS:
- CANDIDATE TARGET ROLE: {target_role}
- EXPECTED EXPERIENCE LEVEL: {experience_level}
- EVALUATION TRACK: {role_track_type}

{link_validation_instruction}

DETAILED PYTHON METRIC ANALYSIS LOGS FOR YOUR REFERENCE CONTEXT:
{json.dumps(deterministic_report)}

Return response in EXACT clean valid JSON format matching the schema below without markdown formatting wrappers or triple backticks.

{{
  "atsScore": {exact_total_score},
  "summary": "High-level professional explanation detailing how their profile maps to the core role specifications, explicitly justifying why they received their pre-computed score of {exact_total_score}/100.",
  "scoreBreakdown": {{
    "skillsMatch": {exact_breakdown['skillsMatch']},
    "experienceMatch": {exact_breakdown['experienceMatch']},
    "formattingScore": {exact_breakdown['formattingScore']},
    "keywordDensity": {exact_breakdown['keywordDensity']}
  }},
  "skillsAnalysis": {{
    "strongSkills": ["Extracted_Skill_1", "Extracted_Skill_2"],
    "missingSkills": ["Missing_Skill_1", "Missing_Skill_2"],
    "deprioritizedSkills": ["Irrelevant_Skill_1"]
  }},
  "experienceEvaluation": {{
    "rating": "Fresher profile configuration applied. Scoring focus shifted heavily onto project metrics and academic builds.",
    "feedback": "Deep natural language analysis explaining their occupational history or project depth relative to the JD requirements."
  }},
  "educationEvaluation": {{
    "educationExplanation": "Write natural language feedback explaining layout gaps or structural advice regarding academic records here."
  }},
  "projectEvaluation": [
    {schema_projects_block}
  ],
  "suggestedProject": {{
    "title": "Generated_Project_Idea_Title",
    "description": "Why this project would help them get the job.",
    "difficulty": "Intermediate",
    "tags": ["Node.js", "React"]
  }},
  "finalRecommendations": [
    "Actionable recommendation 1 based on their resume",
    "Actionable recommendation 2 based on their resume",
    "Actionable recommendation 3 based on their resume"
  ],
  "hygieneCheck": {{
    "grammarIssues": ["List any weak phrases, stylistic errors, or grammatical bugs detected in their text"],
    "hasLinkedIn": {str(deterministic_report['hygieneCheck']['hasLinkedIn']).lower()},
    "linkedInWorking": {str(deterministic_report['hygieneCheck']['linkedInWorking']).lower()},
    "hasGithub": {str(deterministic_report['hygieneCheck']['hasGithub']).lower()},
    "githubWorking": {str(deterministic_report['hygieneCheck']['githubWorking']).lower()},
    "hasPortfolio": {str(deterministic_report['hygieneCheck']['hasPortfolio']).lower()},
    "portfolioWorking": {str(deterministic_report['hygieneCheck']['portfolioWorking']).lower()},
    "hasPhone": {str(deterministic_report['hygieneCheck']['hasPhone']).lower()},
    "hasEmail": {str(deterministic_report['hygieneCheck']['hasEmail']).lower()}
  }}
}}

JOB DESCRIPTION SPECIFICATION:
{job_description}

RESUME TEXT DATA TO EVALUATE:
{resume_text}
"""
def build_structuring_prompt(
    resume_text: str,
    analysis_result: dict,
) -> str:
    """
    Builds a prompt that instructs the LLM to structure the raw resume text into the exact JSON 
    schema required by the resume templates, applying any keyword suggestions from the analysis.
    """
    return f"""
You are an expert resume formatter. Your job is to take raw, extracted text from a candidate's uploaded resume, 
and convert it into a perfectly clean, structured JSON object that will be fed directly into a resume template builder.

You will also be provided with an ATS analysis result. Use the feedback and keywords from the analysis to lightly 
optimize the structured data (e.g., inject missing keywords into the summary or bullet points where appropriate).

REQUIREMENTS:
1. Extract the candidate's name, email, phone, location, and links (LinkedIn, GitHub) for the header.
2. Craft a professional summary based on their experience.
3. Extract all skills into a flat array of strings.
4. Extract work experience into an array of objects. Each must have: title, duration, company, and an array of bullet points (highlights).
5. Extract projects into an array of objects. Each must have: title, duration, description, and an array of bullet points (highlights).
6. Extract education into an array of objects. Each must have: degree, institution, duration.
7. Return ONLY valid JSON matching the schema exactly. No markdown wrappers.

JSON SCHEMA EXPECTED:
{{
  "header": {{
    "fullName": "...",
    "email": "...",
    "phone": "...",
    "location": "...",
    "linkedin": "...",
    "github": "..."
  }},
  "summary": "...",
  "skills": ["...", "..."],
  "experience": [
    {{
      "title": "...",
      "company": "...",
      "duration": "...",
      "highlights": ["...", "..."]
    }}
  ],
  "projects": [
    {{
      "title": "...",
      "duration": "...",
      "description": "...",
      "highlights": ["...", "..."]
    }}
  ],
  "education": [
    {{
      "degree": "...",
      "institution": "...",
      "duration": "..."
    }}
  ]
}}

ATS ANALYSIS RESULTS TO INCORPORATE:
{analysis_result}

RAW RESUME TEXT:
{resume_text}
"""