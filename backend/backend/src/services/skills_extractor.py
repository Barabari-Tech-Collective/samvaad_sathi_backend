import re
from typing import List

# Predefined skills list (keyword-based)
SKILL_KEYWORDS = [
    "React", "Next.js", "JavaScript", "TypeScript", "Node.js", "Express.js", 
    "Python", "FastAPI", "SQL", "PostgreSQL", "MongoDB", "HTML", "CSS", 
    "Tailwind CSS", "Git", "Docker", "AWS", "REST APIs", "GraphQL",
    "Java", "Spring Boot", "C++", "C#", "Django", "Flask", "Angular", "Vue.js",
    "Redux", "Material UI", "Bootstrap", "Prisma", "TypeORM", "Drizzle",
    "Kubernetes", "Azure", "GCP", "Firebase", "Redis", "Elasticsearch",
    "Unit Testing", "Jest", "Cypress", "Agile", "Scrum", "CI/CD"
]

def extract_skills_from_text(text: str) -> List[str]:
    """
    Simple keyword-based skill extraction.
    Matches predefined skills from the text in a case-insensitive manner.
    """
    if not text:
        return []
    
    matched_skills = []
    text_lower = text.lower()
    
    for skill in SKILL_KEYWORDS:
        # Use regex to match skill as a whole word to avoid partial matches
        # (e.g., 'Java' matching in 'JavaScript')
        # We escape the skill name for regex safety (though our list is safe)
        escaped_skill = re.escape(skill.lower())
        
        # Pattern looks for the skill with word boundaries
        # \b handles most cases, but we also handle special characters like '.' in Next.js
        pattern = rf"\b{escaped_skill}\b"
        
        # Special handling for skills with symbols like .js, #, etc.
        if any(c in skill for c in [".", "#", "+"]):
            # If it has special chars, we check for it directly but ensured by whitespace/punctuation
            if f" {skill.lower()}" in f" {text_lower} " or f"\n{skill.lower()}" in f"\n{text_lower}\n":
                if skill not in matched_skills:
                    matched_skills.append(skill)
                continue

        if re.search(pattern, text_lower):
            if skill not in matched_skills:
                matched_skills.append(skill)
                
    return matched_skills
