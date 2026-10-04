"""
Syllabus data definitions for interview question generation.

This module contains the static data structures used for generating interview questions
across different roles, difficulties, and categories.
"""
from __future__ import annotations

from typing import Dict, List

# Canonical roles supported by the system
CANONICAL_ROLES: List[str] = [
    "React Developer",
    "Node JS Developer", 
    "Express JS Developer",
    "MERN Stack Developer",
    "UI Developer",
    "JavaScript Developer",
]

# Map common track keywords to canonical roles
ROLE_ALIASES: Dict[str, str] = {
    "react": "React Developer",
    "frontend": "UI Developer",
    "ui": "UI Developer",
    "javascript": "JavaScript Developer",
    "js": "JavaScript Developer",
    "mern": "MERN Stack Developer",
    "fullstack": "MERN Stack Developer",
    "full stack": "MERN Stack Developer",
    "full-stack": "MERN Stack Developer",
    "node": "Node JS Developer",
    "express": "Express JS Developer",
}

# Behavioral topics that apply across all roles
BEHAVIORAL_TOPICS: List[str] = [
    "Teamwork and collaboration",
    "Conflict resolution",
    "Problem-solving and debugging approaches",
    "Adaptability and quick learning",
    "Ownership and taking initiative",
    "Handling mistakes and learning from feedback",
    "Work ethic, time management, and prioritization",
    "Communication with technical and non-technical peers",
    "Coping with tight deadlines or pressure",
    "Receiving and incorporating feedback",
]

# Question archetypes for different categories
ARCHETYPES: Dict[str, List[str]] = {
    "tech": [
        "Concept understanding (define, explain with example)",
        "Why/How/Trade-offs",
        "Debug/fix a broken snippet or scenario",
        "Design a component/module/system",
        "Predict output/behavior (code or scenario)",
        "Optimize for performance or memory",
        "Security or edge-case analysis",
    ],
    "tech_allied": [
        "Tooling/setup decisions and rationale in your project",
        "Testing strategy and coverage in your project",
        "CI/CD and release process in your project",
        "Monitoring/observability, logs/metrics/traces in your project",
        "Security and compliance considerations in your project",
        "Data modeling and migrations in your project",
    ],
    "behavioral": [
        "STAR: situation-task-action-result",
        "Teamwork & collaboration specifics",
        "Conflict resolution specifics",
        "Ownership and initiative",
        "Handling mistakes & feedback",
        "Communication with non-technical peers",
        "Working under pressure/time constraints",
    ],
}

# Depth guidelines for different difficulty levels
DEPTH_GUIDELINES: Dict[str, str] = {
    "easy": "Test basic understanding, definitions, and simple syntax. Focus on fundamental concepts without complex implementations or edge cases.",
    "medium": "Test fundamentals with practical application; include small working examples and common use cases with moderate scope.",
    "hard": "Cover implementation details and trade-offs; include constraints, alternatives, edge cases, and deeper architectural considerations.",
    "expert": "Address scaling, performance optimization, failure modes, security vulnerabilities, and advanced patterns; require nuanced trade-offs with multiple constraints.",
}

# Tech keywords for resume parsing
TECH_KEYWORDS: List[str] = [
    # Languages & Backend Frameworks
    "python", "django", "fastapi", "flask", "java", "spring", "springboot", "c++", "c#", ".net", "dotnet", "golang", "go", "ruby", "rails", "sql",
    # Frontend/UI
    "react", "redux", "next.js", "nextjs", "vite", "webpack", "babel", "typescript", 
    "tailwind", "mui", "ant design", "sass", "styled-components", "rtl", "jest", 
    "cypress", "playwright",
    # Backend/Node
    "node", "express", "nestjs", "koa", "fastify", "graphql", "rest", "websocket", 
    "socket.io",
    # Databases/Cache/Queues
    "mongodb", "mongoose", "postgres", "mysql", "redis", "kafka", "rabbitmq",
    # DevOps/Cloud
    "docker", "kubernetes", "k8s", "aws", "azure", "gcp", "terraform", "github actions", 
    "ci/cd", "jenkins",
    # Security/Auth
    "oauth", "oauth2", "oidc", "jwt", "helmet", "cors",
    # Observability
    "prometheus", "grafana", "opentelemetry", "sentry",
]
