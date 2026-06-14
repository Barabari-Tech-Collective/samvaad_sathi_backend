DEFAULT_TEMPLATE_ID = "default_ats_001"

DEFAULT_TEMPLATE_METADATA = {
    "templateId": DEFAULT_TEMPLATE_ID,
    "name": "ATS Standard Professional",
    "description": "Clean single-column ATS-optimized blueprint for tech and non-tech tracks.",
    "previewImage": "https://cdn.samvaadsaathi.in/templates/ats_clean_preview.png", # Placeholder
    "tags": ["ATS Friendly", "Clean", "Recommended"],
    "sections": ["header", "summary", "skills", "experience", "projects", "education"]
}

DEFAULT_TEMPLATE_DETAIL = {
    "templateId": DEFAULT_TEMPLATE_ID,
    "name": "ATS Standard Professional",
    "structure": {
        "sections": ["header", "summary", "skills", "experience", "projects", "education"]
    },
    "sampleData": {
        "header": {
            "fullName": "Ananya Sharma",
            "email": "ananya.sharma@example.com",
            "phone": "+91 98765 43210",
            "linkedin": "linkedin.com/in/ananyasharma",
            "github": "github.com/ananyasharma"
        },
        "summary": "Ambitious Software Engineer with experience building clean FastAPI and React web systems.",
        "skills": ["Python", "FastAPI", "PostgreSQL", "React", "Docker"],
        "experience": [
            {
                "company": "Tech Solutions Corp",
                "role": "Backend Intern",
                "duration": "June 2024 - Present",
                "highlights": ["Optimized SQL queries reducing latency by 20%.", "Built 5+ REST endpoints."]
            }
        ],
        "projects": [
            {
                "title": "E-Commerce Gateway",
                "description": "Microservice setup dealing with transactional loads utilizing FastAPI/AsyncPG."
            }
        ],
        "education": [
            {
                "institution": "ABC Institute of Technology",
                "degree": "B.Tech in Computer Science",
                "year": "2025"
            }
        ]
    }
}