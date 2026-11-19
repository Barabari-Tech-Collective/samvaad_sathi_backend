from __future__ import annotations
import re
from typing import Set, List


class SkillNormalizer:
    """
    Production-grade Skill Normalizer.
    Converts common technology variations into canonical IDs.
    """

    def __init__(self):
        self.cleanup_pattern = re.compile(r"[^\w\s\-\#\.\+\/]")

        self.alias_matrix = {
            # --- API / PROTOCOLS ---
            "rest": "rest",
            "rest api": "rest",
            "rest apis": "rest",
            "restful api": "rest",
            "restful apis": "rest",
            "restful": "rest",
            "graphql": "graphql",
            "soap": "soap",
            "websocket": "websockets",
            "websockets": "websockets",
            "ws": "websockets",
            # --- LANGUAGES ---
            "javascript": "javascript",
            "js": "javascript",
            "ecmascript": "javascript",
            "es6": "javascript",
            "es6+": "javascript",
            "typescript": "typescript",
            "ts": "typescript",
            "python": "python",
            "py": "python",
            "java": "java",
            "c++": "cpp",
            "cpp": "cpp",
            "cplusplus": "cpp",
            "c#": "csharp",
            "csharp": "csharp",
            "golang": "go",
            "go": "go",
            "ruby": "ruby",
            "rust": "rust",
            "php": "php",
            # --- FRONTEND ---
            "react": "react",
            "reactjs": "react",
            "react.js": "react",
            "react js": "react",
            "nextjs": "nextjs",
            "next.js": "nextjs",
            "next js": "nextjs",
            "next": "nextjs",
            "vue": "vue",
            "vuejs": "vue",
            "vue.js": "vue",
            "angular": "angular",
            "angularjs": "angular",
            "angular.js": "angular",
            "svelte": "svelte",
            "svelte.js": "svelte",
            "sveltekit": "sveltekit",
            "tailwind": "tailwindcss",
            "tailwind css": "tailwindcss",
            "tailwindcss": "tailwindcss",
            "bootstrap": "bootstrap",
            "bootstrap5": "bootstrap",
            "html": "html",
            "html5": "html",
            "css": "css",
            "css3": "css",
            "sass": "sass",
            "scss": "sass",
            # --- BACKEND ---
            "node": "nodejs",
            "nodejs": "nodejs",
            "node.js": "nodejs",
            "node js": "nodejs",
            "express": "express",
            "expressjs": "express",
            "express.js": "express",
            "django": "django",
            "fastapi": "fastapi",
            "fast api": "fastapi",
            "flask": "flask",
            "spring": "spring",
            "springboot": "springboot",
            "spring boot": "springboot",
            # --- DATABASES ---
            "postgres": "postgresql",
            "postgresql": "postgresql",
            "postgres db": "postgresql",
            "mongo": "mongodb",
            "mongodb": "mongodb",
            "mongo db": "mongodb",
            "mysql": "mysql",
            "my sql": "mysql",
            "sqlite": "sqlite",
            "sqlite3": "sqlite",
            "sql": "sql",
            "redis": "redis",
            "oracle": "oracle",
            "oracledb": "oracle",
            "dynamodb": "dynamodb",
            "dynamo db": "dynamodb",
            "cassandra": "cassandra",
            # --- CLOUD / DEVOPS ---
            "aws": "aws",
            "amazon web services": "aws",
            "gcp": "gcp",
            "google cloud": "gcp",
            "google cloud platform": "gcp",
            "azure": "azure",
            "microsoft azure": "azure",
            "docker": "docker",
            "docker containers": "docker",
            "kubernetes": "kubernetes",
            "k8s": "kubernetes",
            "ci/cd": "cicd",
            "cicd": "cicd",
            "continuous integration": "cicd",
            "continuous delivery": "cicd",
            "continuous deployment": "cicd",
            "ci cd": "cicd",
            "git": "git",
            "github": "github",
            "gitlab": "gitlab",
            "bitbucket": "bitbucket",
            "jenkins": "jenkins",
            "terraform": "terraform",
            "ansible": "ansible",
            # --- TOOLS & METHODOLOGIES ---
            "figma": "figma",
            "adobe xd": "adobexd",
            "xd": "adobexd",
            "postman": "postman",
            "jwt": "jwt",
            "jwt authentication": "jwt",
            "agile": "agile",
            "agile development": "agile",
            # --- UI/UX & DESIGN ---
            "ui/ux": "uiux",
            "ui/ux design": "uiux",
            "ui design": "uidesign",
            "ux design": "uxdesign",
            "user interface": "uidesign",
            "user experience": "uxdesign",
            "wireframing": "wireframing",
            "prototyping": "prototyping",
            "graphic design": "graphicdesign",
            "visual design": "visualdesign",
            "framer": "framer",
            "sketch": "sketch",
            "invision": "invision",
            "web design": "webdesign",
            "website design": "webdesign",
            # --- AI / ML / DATA / ANALYTICS ---
            "openai": "openai",
            "langchain": "langchain",
            "tensorflow": "tensorflow",
            "pytorch": "pytorch",
            "pandas": "pandas",
            "numpy": "numpy",
            "matplotlib": "matplotlib",
            "seaborn": "seaborn",
            "power bi": "powerbi",
            "powerbi": "powerbi",
            "tableau": "tableau",
            "microsoft excel": "excel",
            "ms excel": "excel",
            "msexcel": "excel",
            "excel": "excel",
            "advanced excel": "advanced_excel",
            "microsoft excel (advanced)": "advanced_excel",
            "excel (advanced)": "advanced_excel",
            "excel advanced": "advanced_excel",
            "ms excel (basic)": "excel",
            "excel (basic)": "excel",
            "data analysis": "data_analysis",
            "data analytics": "data_analysis",
            "data cleaning": "data_cleaning",
            "data preprocessing": "datapreprocessing",
            "data wrangling": "data_wrangling",
            "data munging": "data_wrangling",
            "data modeling": "data_modeling",
            "data modelling": "data_modeling",
            "data reporting": "data_reporting",
            "data visualization": "data_visualization",
            "data visualisation": "data_visualization",
            "data interpretation": "data_interpretation",
            "data mining": "data_mining",
            "etl": "etl",
            "extract transform load": "etl",
            "business intelligence": "bi",
            "business intelligence (bi)": "bi",
            "bi": "bi",
            "dashboard development": "dashboards",
            "dashboards": "dashboards",
            "dashboard": "dashboards",
            "exploratory data analysis": "eda",
            "exploratory data analysis (eda)": "eda",
            "eda": "eda",
            "statistical analysis": "statistical_analysis",
            "statistics": "statistical_analysis",
            "scikit-learn": "scikitlearn",
            "scikit learn": "scikitlearn",
            "sklearn": "scikitlearn",
            "opencv": "opencv",
            "open cv": "opencv",
            "deep learning": "deeplearning",
            "machine learning": "machinelearning",
            "computer vision": "computervision",
            "nlp": "nlp",
            "natural language processing": "nlp",
            "cnn": "cnn",
            "lstm": "lstm",
            "model evaluation": "modelevaluation",
            "streamlit": "streamlit",
            "spacy": "spacy",
            "microsoft powerpoint": "powerpoint",
            "powerpoint": "powerpoint",
            "ppt": "powerpoint",
            "ms powerpoint": "powerpoint",
            # --- BUSINESS / PRODUCT / AGILE ---
            "product management": "product_management",
            "product manager": "product_management",
            "project management": "project_management",
            "project manager": "project_management",
            "business analysis": "business_analysis",
            "business analyst": "business_analysis",
            "data science": "data_science",
            "data scientist": "data_science",
            "data engineering": "data_engineering",
            "data engineer": "data_engineering",
            "software engineering": "software_engineering",
            "software engineer": "software_engineer",
            "devops": "devops",
            "devops engineer": "devops",
            "qa": "qa",
            "quality assurance": "qa",
            "scrum": "scrum",
            "kanban": "kanban",
            "jira": "jira",
            "confluence": "confluence",
            "market research": "market_research",
            "user stories": "user_stories",
            "stakeholder management": "stakeholder_management",
            "crm": "crm",
            "salesforce": "salesforce",
            "seo": "seo",
            "digital marketing": "digital_marketing",
            # --- UNIVERSAL / ANALYTICAL / SOFT SKILLS ---
            "problem solving": "problem_solving",
            "problem-solving": "problem_solving",
            "critical thinking": "critical_thinking",
            "analytical thinking": "analytical_thinking",
            "analytical skills": "analytical_thinking",
            "logical reasoning": "logical_reasoning",
            "attention to detail": "attention_to_detail",
            "communication skills": "communication",
            "communication": "communication",
            "team collaboration": "teamwork",
            "teamwork": "teamwork",
            "collaboration": "teamwork",
            "adaptability": "adaptability",
        }

        self.display_names = {
            "rest": "REST APIs",
            "websockets": "WebSockets",
            "graphql": "GraphQL",
            "soap": "SOAP",
            "javascript": "JavaScript",
            "typescript": "TypeScript",
            "python": "Python",
            "java": "Java",
            "cpp": "C++",
            "csharp": "C#",
            "go": "Go",
            "ruby": "Ruby",
            "rust": "Rust",
            "php": "PHP",
            "react": "React.js",
            "nextjs": "Next.js",
            "vue": "Vue.js",
            "angular": "Angular",
            "svelte": "Svelte",
            "sveltekit": "SvelteKit",
            "tailwindcss": "Tailwind CSS",
            "bootstrap": "Bootstrap",
            "html": "HTML5",
            "css": "CSS3",
            "sass": "Sass",
            "nodejs": "Node.js",
            "express": "Express.js",
            "django": "Django",
            "fastapi": "FastAPI",
            "flask": "Flask",
            "spring": "Spring",
            "springboot": "Spring Boot",
            "postgresql": "PostgreSQL",
            "mongodb": "MongoDB",
            "mysql": "MySQL",
            "sqlite": "SQLite",
            "sql": "SQL",
            "redis": "Redis",
            "oracle": "Oracle",
            "dynamodb": "DynamoDB",
            "cassandra": "Cassandra",
            "aws": "AWS",
            "gcp": "GCP",
            "azure": "Azure",
            "docker": "Docker",
            "kubernetes": "Kubernetes",
            "cicd": "CI/CD",
            "git": "Git",
            "github": "GitHub",
            "gitlab": "GitLab",
            "bitbucket": "Bitbucket",
            "jenkins": "Jenkins",
            "terraform": "Terraform",
            "ansible": "Ansible",
            "figma": "Figma",
            "adobexd": "Adobe XD",
            "postman": "Postman",
            "jwt": "JWT",
            "agile": "Agile",
            "uiux": "UI/UX Design",
            "uidesign": "UI Design",
            "uxdesign": "UX Design",
            "wireframing": "Wireframing",
            "prototyping": "Prototyping",
            "graphicdesign": "Graphic Design",
            "visualdesign": "Visual Design",
            "framer": "Framer",
            "sketch": "Sketch",
            "invision": "InVision",
            "webdesign": "Web Design",
            "openai": "OpenAI",
            "langchain": "LangChain",
            "tensorflow": "TensorFlow",
            "pytorch": "PyTorch",
            "pandas": "Pandas",
            "numpy": "NumPy",
            "powerbi": "Power BI",
            "tableau": "Tableau",
            "excel": "Microsoft Excel",
            "scikitlearn": "Scikit-learn",
            "opencv": "OpenCV",
            "deeplearning": "Deep Learning",
            "machinelearning": "Machine Learning",
            "computervision": "Computer Vision",
            "nlp": "NLP",
            "cnn": "CNN",
            "lstm": "LSTM",
            "datapreprocessing": "Data Preprocessing",
            "modelevaluation": "Model Evaluation",
            "streamlit": "Streamlit",
            "spacy": "spaCy",
            "matplotlib": "Matplotlib",
            "seaborn": "Seaborn",
            "advanced_excel": "Advanced Excel",
            "data_analysis": "Data Analysis",
            "data_cleaning": "Data Cleaning",
            "data_wrangling": "Data Wrangling",
            "data_modeling": "Data Modeling",
            "data_reporting": "Data Reporting",
            "data_visualization": "Data Visualization",
            "data_interpretation": "Data Interpretation",
            "data_mining": "Data Mining",
            "dashboards": "Dashboard Development",
            "etl": "ETL",
            "eda": "Exploratory Data Analysis (EDA)",
            "statistical_analysis": "Statistical Analysis",
            "bi": "Business Intelligence (BI)",
            "powerpoint": "Microsoft PowerPoint",
            "product_management": "Product Management",
            "project_management": "Project Management",
            "business_analysis": "Business Analysis",
            "data_science": "Data Science",
            "data_engineering": "Data Engineering",
            "software_engineering": "Software Engineering",
            "devops": "DevOps",
            "qa": "QA / Quality Assurance",
            "scrum": "Scrum",
            "kanban": "Kanban",
            "jira": "Jira",
            "confluence": "Confluence",
            "market_research": "Market Research",
            "user_stories": "User Stories",
            "stakeholder_management": "Stakeholder Management",
            "crm": "CRM",
            "salesforce": "Salesforce",
            "seo": "SEO",
            "digital_marketing": "Digital Marketing",
            "problem_solving": "Problem Solving",
            "critical_thinking": "Critical Thinking",
            "analytical_thinking": "Analytical Thinking",
            "logical_reasoning": "Logical Reasoning",
            "attention_to_detail": "Attention to Detail",
            "communication": "Communication Skills",
            "teamwork": "Team Collaboration",
            "adaptability": "Adaptability",
        }

        self.related_skill_groups = [
            {"react", "angular", "vue", "svelte", "sveltekit"},
            {"express", "django", "fastapi", "flask", "spring", "springboot"},
            {
                "postgresql",
                "mysql",
                "mongodb",
                "sqlite",
                "sql",
                "redis",
                "oracle",
                "dynamodb",
                "cassandra",
            },
            {"rest", "graphql", "soap", "websockets"},
            {"aws", "azure", "gcp"},
            {"docker", "kubernetes", "cicd", "terraform", "ansible"},
            {
                "javascript",
                "typescript",
                "python",
                "java",
                "go",
                "cpp",
                "csharp",
                "ruby",
                "rust",
                "php",
            },
            {"tensorflow", "pytorch", "scikitlearn"},
            {"machinelearning", "deeplearning", "cnn", "lstm"},
            {"computervision", "opencv"},
            {"powerbi", "tableau", "excel", "advanced_excel", "bi"},
            {"pandas", "numpy", "matplotlib", "seaborn", "python"},
            {
                "data_analysis",
                "data_cleaning",
                "data_wrangling",
                "datapreprocessing",
                "eda",
                "statistical_analysis",
                "data_visualization",
                "data_interpretation",
                "data_modeling",
                "data_reporting",
                "dashboards",
            },
            {
                "problem_solving",
                "critical_thinking",
                "analytical_thinking",
                "logical_reasoning",
            },
            {
                "uiux",
                "uidesign",
                "uxdesign",
                "wireframing",
                "prototyping",
                "figma",
                "adobexd",
                "sketch",
                "invision",
                "framer",
            },
        ]

    def normalize(self, raw_string: str) -> str:
        if not raw_string or not isinstance(raw_string, str):
            return ""

        clean = raw_string.strip().lower()
        clean = re.sub(r"\(.*?\)", "", clean).strip()

        if clean in self.alias_matrix:
            return self.alias_matrix[clean]

        clean_processed = re.sub(r"\brestful\b", "rest", clean)
        clean_processed = re.sub(r"\bapis\b", "api", clean_processed)
        clean_processed = re.sub(r"[\.\-\_]", " ", clean_processed).strip()

        if clean_processed in self.alias_matrix:
            return self.alias_matrix[clean_processed]

        mutated = clean.replace(" ", "").replace(".", "").replace("-", "")
        if mutated in self.alias_matrix:
            return self.alias_matrix[mutated]

        return clean

    def normalize_tokens(self, raw_string: str) -> List[str]:
        """
        Splits compound strings, strips qualifiers, and returns all canonical IDs represented.
        Handles real-world candidate expressions across all roles:
        - 'Programming & Data Analysis' -> ['data_analysis']
        - 'Microsoft Excel (advanced)' -> ['excel', 'advanced_excel']
        - 'Power BI / Tableau' -> ['powerbi', 'tableau']
        - 'Exploratory Data Analysis (EDA)' -> ['eda']
        """
        if not raw_string or not isinstance(raw_string, str):
            return []

        canonicals: Set[str] = set()

        raw_lower = raw_string.lower().strip()

        # 1. Direct normalization
        direct = self.normalize(raw_lower)
        if direct and direct in self.alias_matrix.values():
            canonicals.add(direct)

        # 2. Check qualifier patterns like (advanced) or (basic)
        if "advanced" in raw_lower:
            if "excel" in raw_lower:
                canonicals.add("advanced_excel")
                canonicals.add("excel")
            if "python" in raw_lower:
                canonicals.add("python")
            if "sql" in raw_lower:
                canonicals.add("sql")
        if "basic" in raw_lower and "excel" in raw_lower:
            canonicals.add("excel")

        # 3. Split by common delimiters (&, /, comma, ;, and, •, |)
        sub_parts = re.split(r"[,/;&•|\n]|\band\b", raw_string, flags=re.IGNORECASE)
        for part in sub_parts:
            part_clean = part.strip()
            if part_clean and len(part_clean) > 1:
                part_norm = self.normalize(part_clean)
                if part_norm and part_norm in self.alias_matrix.values():
                    canonicals.add(part_norm)

        return list(canonicals)

    def get_related_skills(self, canonical_skill: str) -> Set[str]:
        for group in self.related_skill_groups:
            if canonical_skill in group:
                return group - {canonical_skill}
        return set()

    def get_display_name(self, canonical_token: str) -> str:
        return self.display_names.get(canonical_token, canonical_token.title())
