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

Return response in EXACT clean valid JSON format matching the schema below without any markdown formatting wrappers.
IMPORTANT: You MUST extract the actual skills, project names, and URLs from the resume text. DO NOT output the placeholder names or fake URLs from the schema blueprint below. If a project does not have a URL in the resume, leave projectUrl as an empty string "".

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
    "strongSkills": ["Extracted_Skill_1", "Extracted_Skill_2", "Extracted_Skill_3"],
    "missingSkills": ["Missing_Skill_1", "Missing_Skill_2"],
    "deprioritizedSkills": ["Irrelevant_Skill_1"]
  }},
  "experienceEvaluation": {{
    "rating": "Good_Or_Bad_Or_Average",
    "feedback": "Specific feedback on how well their experience matches the job description."
  }},
  "projectEvaluation": [
    {{
      "projectName": "Extracted_Project_Name",
      "rating": "Good_Or_Needs_Improvement",
      "feedback": "Specific feedback for this project based on job description.",
      "projectUrl": "EXTRACTED_URL_OR_EMPTY_STRING"
    }}
  ],
  "suggestedProject": {{
    "title": "Generated_Project_Idea_Title",
    "description": "Why this project would help them get the job.",
    "difficulty": "Beginner_Or_Intermediate_Or_Advanced",
    "tags": ["Generated_Tag_1", "Generated_Tag_2"]
  }},
  "finalRecommendations": [
    "Actionable recommendation 1 based on their resume",
    "Actionable recommendation 2 based on their resume"
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