import re
from typing import Set


class SkillNormalizer:
    """
    Production-grade Skill Normalizer.
    Converts common technology variations into canonical IDs.
    """

    def __init__(self):
        self.cleanup_pattern = re.compile(r'[^\w\s\-\#\.\+\/]')

        self.alias_matrix = {
            # --- API / PROTOCOLS ---
            "rest": "rest", "rest api": "rest", "rest apis": "rest",
            "restful api": "rest", "restful apis": "rest", "restful": "rest",
            "graphql": "graphql", "soap": "soap",
            "websocket": "websockets", "websockets": "websockets", "ws": "websockets",

            # --- LANGUAGES ---
            "javascript": "javascript", "js": "javascript", "ecmascript": "javascript",
            "es6": "javascript", "es6+": "javascript",
            "typescript": "typescript", "ts": "typescript",
            "python": "python", "py": "python",
            "java": "java",
            "c++": "cpp", "cpp": "cpp", "cplusplus": "cpp",
            "c#": "csharp", "csharp": "csharp",
            "golang": "go", "go": "go",
            "ruby": "ruby", "rust": "rust", "php": "php",

            # --- FRONTEND ---
            "react": "react", "reactjs": "react", "react.js": "react", "react js": "react",
            "nextjs": "nextjs", "next.js": "nextjs", "next js": "nextjs", "next": "nextjs",
            "vue": "vue", "vuejs": "vue", "vue.js": "vue",
            "angular": "angular", "angularjs": "angular", "angular.js": "angular",
            "svelte": "svelte", "svelte.js": "svelte", "sveltekit": "sveltekit",
            "tailwind": "tailwindcss", "tailwind css": "tailwindcss", "tailwindcss": "tailwindcss",
            "bootstrap": "bootstrap", "bootstrap5": "bootstrap",
            "html": "html", "html5": "html",
            "css": "css", "css3": "css",
            "sass": "sass", "scss": "sass",

            # --- BACKEND ---
            "node": "nodejs", "nodejs": "nodejs", "node.js": "nodejs", "node js": "nodejs",
            "express": "express", "expressjs": "express", "express.js": "express",
            "django": "django", "fastapi": "fastapi", "fast api": "fastapi", "flask": "flask",
            "spring": "spring", "springboot": "springboot", "spring boot": "springboot",

            # --- DATABASES ---
            "postgres": "postgresql", "postgresql": "postgresql", "postgres db": "postgresql",
            "mongo": "mongodb", "mongodb": "mongodb", "mongo db": "mongodb",
            "mysql": "mysql", "my sql": "mysql",
            "sqlite": "sqlite", "sqlite3": "sqlite", "redis": "redis",
            "oracle": "oracle", "oracledb": "oracle",
            "dynamodb": "dynamodb", "dynamo db": "dynamodb", "cassandra": "cassandra",

            # --- CLOUD / DEVOPS ---
            "aws": "aws", "amazon web services": "aws",
            "gcp": "gcp", "google cloud": "gcp", "google cloud platform": "gcp",
            "azure": "azure", "microsoft azure": "azure",
            "docker": "docker", "docker containers": "docker",
            "kubernetes": "kubernetes", "k8s": "kubernetes",
            "ci/cd": "cicd", "cicd": "cicd", "continuous integration": "cicd",
            "continuous delivery": "cicd", "continuous deployment": "cicd", "ci cd": "cicd",
            "git": "git", "github": "github", "gitlab": "gitlab", "bitbucket": "bitbucket",
            "jenkins": "jenkins", "terraform": "terraform", "ansible": "ansible",

            # --- TOOLS & METHODOLOGIES ---
            "figma": "figma", "adobe xd": "adobexd", "xd": "adobexd", "postman": "postman",
            "jwt": "jwt", "jwt authentication": "jwt",
            "agile": "agile", "agile development": "agile",

            # --- UI/UX & DESIGN ---
            "ui/ux": "uiux", "ui/ux design": "uiux", "ui design": "uidesign", "ux design": "uxdesign",
            "user interface": "uidesign", "user experience": "uxdesign", "wireframing": "wireframing",
            "prototyping": "prototyping", "graphic design": "graphicdesign", "visual design": "visualdesign",
            "framer": "framer", "sketch": "sketch", "invision": "invision", "web design": "webdesign", "website design": "webdesign",

            # --- AI / ML / DATA ---
            "openai": "openai", "langchain": "langchain",
            "tensorflow": "tensorflow", "pytorch": "pytorch",
            "pandas": "pandas", "numpy": "numpy"
        }

        self.display_names = {
            "rest": "REST APIs", "websockets": "WebSockets", "graphql": "GraphQL", "soap": "SOAP",
            "javascript": "JavaScript", "typescript": "TypeScript", "python": "Python", "java": "Java",
            "cpp": "C++", "csharp": "C#", "go": "Go", "ruby": "Ruby", "rust": "Rust", "php": "PHP",
            "react": "React.js", "nextjs": "Next.js", "vue": "Vue.js", "angular": "Angular",
            "svelte": "Svelte", "sveltekit": "SvelteKit", "tailwindcss": "Tailwind CSS",
            "bootstrap": "Bootstrap", "html": "HTML5", "css": "CSS3", "sass": "Sass",
            "nodejs": "Node.js", "express": "Express.js", "django": "Django", "fastapi": "FastAPI",
            "flask": "Flask", "spring": "Spring", "springboot": "Spring Boot",
            "postgresql": "PostgreSQL", "mongodb": "MongoDB", "mysql": "MySQL", "sqlite": "SQLite",
            "redis": "Redis", "oracle": "Oracle", "dynamodb": "DynamoDB", "cassandra": "Cassandra",
            "aws": "AWS", "gcp": "GCP", "azure": "Azure", "docker": "Docker", "kubernetes": "Kubernetes",
            "cicd": "CI/CD", "git": "Git", "github": "GitHub", "gitlab": "GitLab", "bitbucket": "Bitbucket",
            "jenkins": "Jenkins", "terraform": "Terraform", "ansible": "Ansible",
            "figma": "Figma", "adobexd": "Adobe XD", "postman": "Postman", "jwt": "JWT", "agile": "Agile",
            "uiux": "UI/UX Design", "uidesign": "UI Design", "uxdesign": "UX Design", "wireframing": "Wireframing",
            "prototyping": "Prototyping", "graphicdesign": "Graphic Design", "visualdesign": "Visual Design",
            "framer": "Framer", "sketch": "Sketch", "invision": "InVision", "webdesign": "Web Design",
            "openai": "OpenAI", "langchain": "LangChain", "tensorflow": "TensorFlow",
            "pytorch": "PyTorch", "pandas": "Pandas", "numpy": "NumPy"
        }

        self.related_skill_groups = [
            {"react", "angular", "vue", "svelte", "sveltekit"},
            {"express", "django", "fastapi", "flask", "spring", "springboot"},
            {"postgresql", "mysql", "mongodb", "sqlite", "redis", "oracle", "dynamodb", "cassandra"},
            {"rest", "graphql", "soap", "websockets"},
            {"aws", "azure", "gcp"},
            {"docker", "kubernetes", "cicd", "terraform", "ansible"},
            {"javascript", "typescript", "python", "java", "go", "cpp", "csharp", "ruby", "rust", "php"},
            {"uiux", "uidesign", "uxdesign", "wireframing", "prototyping", "figma", "adobexd", "sketch", "invision", "framer"}
        ]

    def normalize(self, raw_string: str) -> str:
        if not raw_string or not isinstance(raw_string, str):
            return ""

        clean = raw_string.strip().lower()
        clean = re.sub(r'\(.*?\)', '', clean).strip()

        if clean in self.alias_matrix:
            return self.alias_matrix[clean]

        clean_processed = re.sub(r'\brestful\b', 'rest', clean)
        clean_processed = re.sub(r'\bapis\b', 'api', clean_processed)
        clean_processed = re.sub(r'[\.\-\_]', ' ', clean_processed).strip()

        if clean_processed in self.alias_matrix:
            return self.alias_matrix[clean_processed]

        mutated = clean.replace(" ", "").replace(".", "").replace("-", "")
        if mutated in self.alias_matrix:
            return self.alias_matrix[mutated]

        return clean

    def get_related_skills(self, canonical_skill: str) -> Set[str]:
        for group in self.related_skill_groups:
            if canonical_skill in group:
                return group - {canonical_skill}
        return set()

    def get_display_name(self, canonical_token: str) -> str:
        return self.display_names.get(canonical_token, canonical_token.title())