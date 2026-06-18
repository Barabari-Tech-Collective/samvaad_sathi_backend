def build_ats_analysis_prompt(
    resume_text: str,
    target_role: str,
    experience_level: str,
    job_description: str,
) -> str:
    """
    Builds structured ATS analysis prompt strictly tracking candidate inputs.
    Enforces deep parsing metrics for Education (Institution, Duration, Percentage/CGPA).
    """
    role_lower = target_role.lower()
    is_fresher = any(k in experience_level.lower() for k in ["fresher", "intern", "entry", "0 years"])

    # 1. Handle Dynamic Link Tracking Rules
    if any(k in role_lower for k in ["design", "ui", "ux", "graphics", "product designer"]):
        role_track_type = "DESIGN / CREATIVE TRACK"
        link_validation_instruction = """
        - CORE LINK MATRIX (40% of Total Score): Search strictly for visual portfolio links (Behance, Dribbble, Figma, or Personal Portfolios). 
        - Heavily deduct points if no design file showcase or live preview links are structurally stated. Do not penalize for missing GitHub.
        """
    else:
        role_track_type = "TECHNICAL / ENGINEERING TRACK"
        link_validation_instruction = """
        - CORE LINK MATRIX (40% of Total Score): Search strictly for active code repositories or live deployments (GitHub, GitLab, Vercel, Netlify).
        - Deduct points significantly if active functional workspace strings are missing.
        """

    # 2. Dynamic Fresher UI Rule Hook
    if is_fresher:
        experience_rubric_instruction = """
        - FRESHER PROFILE DETECTED: The user has no formal corporate job history.
        - CRITICAL RULE: Assess their academic projects, open-source work, or bootcamps. 
        - Assign this combined Project quality rating score directly to the 'experienceMatch' breakdown node so the frontend score cards render beautifully.
        """
    else:
        experience_rubric_instruction = """
        - EXPERIENCED PROFILE DETECTED: Evaluate corporate roles, industry timeline durability, frameworks scaled, and real-world milestones.
        """

    return f"""
You are an expert ATS (Applicant Tracking System) optimizer and premium recruiter.
Analyze the candidate's resume text against the job description strictly according to the weighted rubric below.

TARGET EVALUATION PARAMETERS:
- CANDIDATE TARGET ROLE: {target_role}
- EXPECTED EXPERIENCE LEVEL: {experience_level}
- EVALUATION TRACK: {role_track_type}

STRICT SCORING WEIGHT DISTRIBUTION MATRICES (10/40/10/40):
1. MATCHING SKILLS (10%): Evaluation of tech keywords against the Job Description. Maps to 'skillsMatch'.
2. WORKING LINKS & DEPLOYMENTS (40%): Presence of active, valid clickable hyperlinked URLs. Maps to 'workingLinksAndDeployments'.
3. EDUCATION VALIDATION (10%): Academic timeline tracking, institutions, and CGPA/percentages. Maps to 'educationValidation'.
4. PROJECTS & WORK DESCRIPTIONS (40%): Structural validation of descriptions containing metrics and frameworks. Maps to 'projectsAndExperienceDescription'.

{link_validation_instruction}
{experience_rubric_instruction}

EDUCATION STRUCTURAL VERIFICATION RULES:
- Scrutinize the resume text for academic background credentials.
- You must explicitly check for:
  1. University Name / College Name
  2. Duration / Graduation Timeline (e.g., 2021 - 2025)
  3. Performance Score (Percentage or CGPA metric)
- If ANY of these three parameters are missing or incomplete, flag it clearly inside the 'educationEvaluation' feedback object and lower the 'educationValidation' score block.

Return response in EXACT clean valid JSON format matching the schema below without markdown formatting wrappers.
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
  "educationEvaluation": {{
    "hasInstitution": true,
    "hasDuration": false,
    "hasScore": false,
    "rating": "Needs_Improvement",
    "feedback": "CRITICAL CRITERIA MISSING: Your graduation timeline and academic scores (Percentage/CGPA) are completely missing from the education layout block. Recruiters favor candidates with explicit score breakdowns."
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
    "Actionable recommendation 2 based on their resume",
    "Actionable recommendation 3 based on their resume"
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