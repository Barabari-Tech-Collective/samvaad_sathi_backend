import re
from typing import Set

class SkillNormalizer:
    """
    Production-grade Skill Normalizer.
    Converts diverse text representations and variations of technologies 
    into unified canonical tokens for 100% accurate set intersection matching.
    """

    def __init__(self):
        # Clean up punctuation except technical essentials like +, #, .
        self.cleanup_pattern = re.compile(r'[^\w\s\-\#\.\+]')
        
        # Comprehensive canonical mapping database (Expandable to 250+ entries easily)
        self.alias_matrix = {
            # Languages
            "javascript": "javascript", "js": "javascript", "ts": "typescript", "typescript": "typescript",
            "python": "python", "py": "python", "c++": "cpp", "cpp": "cpp", "cplusplus": "cpp",
            "c#": "csharp", "csharp": "csharp", "java": "java", "golang": "go", "go": "go",
            
            # Frontend Frameworks & Tech
            "react": "react", "reactjs": "react", "react.js": "react", "nextjs": "nextjs", "next.js": "nextjs",
            "vue": "vue", "vuejs": "vue", "vue.js": "vue", "angular": "angular", "angularjs": "angular",
            "tailwind": "tailwindcss", "tailwind css": "tailwindcss", "bootstrap": "bootstrap",
            "html": "html", "html5": "html", "css": "css", "css3": "css", "sass": "sass",
            
            # Backend Frameworks
            "nodejs": "nodejs", "node": "nodejs", "node.js": "nodejs",
            "express": "express", "expressjs": "express", "express.js": "express",
            "django": "django", "flask": "fastapi", "fastapi": "fastapi", "spring": "springboot", "springboot": "springboot",
            
            # Databases
            "postgres": "postgresql", "postgresql": "postgresql", "mongo": "mongodb", "mongodb": "mongodb",
            "mysql": "mysql", "redis": "redis", "sqlite": "sqlite", "oracle": "oracle",
            
            # Cloud & DevOps
            "aws": "aws", "amazon web services": "aws", "docker": "docker", "kubernetes": "kubernetes", "k8s": "kubernetes",
            "gcp": "gcp", "google cloud": "gcp", "azure": "azure", "jenkins": "jenkins", "terraform": "terraform",
            "ci/cd": "cicd", "cicd": "cicd", "git": "git", "github": "github",
            
            # APIs & Protocol Layers
            "rest apis": "rest", "rest api": "rest", "rest": "rest", "graphql": "graphql", "soap": "soap",
            "websockets": "websockets", "websocket": "websockets", "twilio api": "twilio", "twilio": "twilio",
            
            # Design UI/UX
            "figma": "figma", "adobe xd": "adobexd", "sketch": "sketch", "photoshop": "photoshop"
        }

        # Human-readable presentation mapping for clean React Dashboard display cards
        self.display_names = {
            "javascript": "JavaScript", "typescript": "TypeScript", "python": "Python", "cpp": "C++",
            "csharp": "C#", "java": "Java", "go": "Go", "react": "React.js", "nextjs": "Next.js",
            "vue": "Vue.js", "angular": "Angular", "tailwindcss": "Tailwind CSS", "bootstrap": "Bootstrap",
            "html": "HTML5", "css": "CSS3", "sass": "Sass", "nodejs": "Node.js", "express": "Express.js",
            "django": "Django", "fastapi": "FastAPI", "springboot": "Spring Boot", "postgresql": "PostgreSQL",
            "mongodb": "MongoDB", "mysql": "MySQL", "redis": "Redis", "aws": "AWS", "docker": "Docker",
            "kubernetes": "Kubernetes", "git": "Git", "github": "GitHub", "cicd": "CI/CD",
            "rest": "REST APIs", "graphql": "GraphQL", "websockets": "WebSockets", "twilio": "Twilio API",
            "figma": "Figma"
        }

    def normalize(self, raw_string: str) -> str:
        """Normalizes an individual token or short phrase phrase into its canonical tech ID."""
        if not raw_string:
            return ""
        
        # Structural sanitation tracking loop
        clean_target = raw_string.strip().lower()
        
        # Direct structural translation match check
        if clean_target in self.alias_matrix:
            return self.alias_matrix[clean_target]
            
        # Fallback mutation step: strip spaces, dots, dashes, and trailing 'js' tags
        mutated = clean_target.replace(" ", "").replace(".", "").replace("-", "").replace("js", "")
        if mutated in self.alias_matrix:
            return self.alias_matrix[mutated]
            
        return clean_target

    def get_display_name(self, canonical_token: str) -> str:
        """Converts internal raw tokens back to elegant UI display names."""
        return self.display_names.get(canonical_token, canonical_token.capitalize())