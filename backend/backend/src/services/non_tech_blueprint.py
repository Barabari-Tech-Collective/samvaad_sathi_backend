from __future__ import annotations

import random


NON_TECH_BLUEPRINT_VERSION = "v1"
NON_TECH_FIXED_DIFFICULTY = "medium"

NON_TECH_CATEGORIES: list[dict[str, str]] = [
    {
        "key": "self",
        "label": "Self Introduction",
        "color": "green",
        "outcome": "Assess self-awareness, personal narrative clarity, and professional intent.",
    },
    {
        "key": "behavioral",
        "label": "Behavioral",
        "color": "dark_green",
        "outcome": "Assess adaptability, collaboration, feedback response, and ownership in real situations.",
    },
    {
        "key": "productivity",
        "label": "Productivity",
        "color": "pink",
        "outcome": "Assess prioritization, time-management, and sustained motivation under workload.",
    },
    {
        "key": "company_candidate",
        "label": "Company and Candidate",
        "color": "yellow",
        "outcome": "Assess role-fit intent, professionalism, and quality of candidate-side inquiry.",
    },
    {
        "key": "general",
        "label": "General",
        "color": "purple",
        "outcome": "Assess spontaneity, communication flow, and practical articulation in everyday contexts.",
    },
]


def non_tech_category_keys() -> list[str]:
    return [item["key"] for item in NON_TECH_CATEGORIES]


def non_tech_category_labels() -> dict[str, str]:
    return {item["key"]: item["label"] for item in NON_TECH_CATEGORIES}


def _with_role(question: str, role_name: str) -> str:
    return question.replace("{role}", role_name)


def _with_company(question: str, company_name: str | None) -> str:
    company = (company_name or "our company").strip() or "our company"
    return question.replace("{company}", company)


def build_non_tech_question_bank(*, role_name: str, company_name: str | None) -> dict[str, list[str]]:
    normalized_role = (role_name or "this role").strip() or "this role"
    templates: dict[str, list[str]] = {
        "self": [
            "Please introduce yourself and tell us about your background.",
            "How would your teachers, classmates, or friends describe you?",
            "What are your career goals at the beginning of your professional journey?",
            "What is one area you are actively working to improve?",
            "What are your greatest strengths as a fresher entering {role}?",
            "What motivates you to consistently perform well?",
            "Where do you see yourself professionally in the next five years?",
            "Which personal value guides most of your professional decisions and why?",
            "What type of work environment helps you do your best work?",
            "What does success in your first year of {role} mean to you?",
            "What is one habit from college life that will help you in {role}?",
            "How have your priorities changed from your first year of college to now?",
            "What kind of feedback do you find most useful for your growth?",
            "What makes you different from other freshers applying for {role}?",
            "What is one misconception people have about you that you would like to correct?",
            "What achievement are you most proud of and why does it matter to you?",
            "Which skill are you trying to build right now and what is your approach?",
            "How do you usually prepare before an important interview or presentation?",
            "How would you summarize your personal brand in three words?",
            "What kind of contribution do you hope to make as a new member in {role}?",
        ],
        "behavioral": [
            "Can you describe a challenge you faced and how you overcame it?",
            "How do you adapt when learning something completely new?",
            "If you make a mistake at work, how would you handle it?",
            "Describe a situation where you adjusted your ideas after listening to others.",
            "Do you prefer working alone or in a group, and why?",
            "Tell us about a time you worked on a college group project.",
            "How do you respond when someone gives you constructive feedback?",
            "Describe a disagreement in a team and how you helped move things forward.",
            "Tell me about a time you had to take responsibility without being asked.",
            "How do you react when priorities suddenly change?",
            "Describe an instance where you had to communicate bad news clearly and calmly.",
            "Tell us about a decision you made with limited information.",
            "Share a moment where you supported a teammate who was struggling.",
            "How have you handled a situation where your first approach failed?",
            "Describe a time when you had to persuade others to consider your idea.",
            "Tell us about a time when you learned from a failure and improved quickly.",
            "How do you usually build trust with new team members?",
            "Describe a situation where you balanced quality and speed under pressure.",
            "Tell us about a time you proactively solved a problem before it escalated.",
            "When you receive unclear instructions, how do you seek clarity?",
        ],
        "productivity": [
            "How do you handle pressure when you have multiple deadlines?",
            "How do you organize your tasks when you have many responsibilities?",
            "How do you manage your time effectively during busy periods?",
            "How do you stay motivated during repetitive or routine tasks?",
            "What system do you use to prioritize urgent versus important tasks?",
            "How do you protect focus time when there are frequent interruptions?",
            "How do you plan your week when your workload is unpredictable?",
            "Tell us about a period when you had to deliver under tight timelines.",
            "How do you track progress on long-running tasks?",
            "What do you do first when your to-do list becomes overwhelming?",
            "How do you balance learning new skills with ongoing deliverables?",
            "How do you recover your productivity after a setback?",
            "How do you decide what to delegate and what to own personally?",
            "What does a productive day look like for you?",
            "How do you maintain quality while working quickly?",
            "What steps do you take to avoid procrastination?",
            "How do you prepare for high-pressure meetings or reviews?",
            "How do you handle context-switching across multiple workstreams?",
            "How do you evaluate whether your process is improving over time?",
            "How do you sustain energy and consistency during busy weeks?",
        ],
        "company_candidate": [
            "Why should we hire you over other candidates for {role}?",
            "What inspired you to apply for this {role} role?",
            "Why would you like to start your career with {company}?",
            "What does professionalism mean to you in the workplace?",
            "How do your strengths align with what this role requires?",
            "What are you expecting from your first manager in this role?",
            "What would make this role meaningful for you over the next year?",
            "How do you plan to learn quickly during your onboarding period?",
            "What concerns would you want clarified before joining a team?",
            "How do you evaluate whether a company culture is right for you?",
            "What questions would you like to ask us about the role or company?",
            "What kind of impact would you like to make in your first 90 days?",
            "How would you represent our company values in day-to-day work?",
            "What kind of projects in {role} excite you the most and why?",
            "What support would help you perform at your best in this role?",
            "How would you respond if role expectations changed after joining?",
            "What does long-term growth look like for you within {company}?",
            "How do you balance candidate expectations with company realities?",
            "Which parts of this job description best match your strengths?",
            "If selected, how would you prepare before your first working day?",
        ],
        "general": [
            "If you could live in any city in the world for one year, which city would you choose and why?",
            "Can you describe your daily routine from morning to night?",
            "What is one book, movie, or series you enjoyed recently, and what did you like about it?",
            "If you had an extra hour every day, how would you use it?",
            "Tell me about a memorable day from your college life.",
            "If a foreign visitor came to your hometown, what three places would you recommend and why?",
            "What skill would you like to learn this year, and what interests you about it?",
            "Describe your best friend without mentioning their name.",
            "If you were given ₹10,000 to organize an event for students, what kind of event would you plan?",
            "What is one everyday problem you wish someone would solve better?",
            "How would you explain your hometown culture to someone visiting for the first time?",
            "What personal routine helps you stay calm and focused?",
            "If you had to teach one concept to school students, what would it be and why?",
            "What is one decision you made recently that you are proud of?",
            "Describe a recent conversation that changed your perspective.",
            "If you had to host a student event tomorrow, how would you structure it?",
            "What small habit has had the biggest positive effect on your life?",
            "If you could improve one public service in your city, what would it be?",
            "How would you spend a completely free day to recharge yourself?",
            "What is one value you would never compromise on, and why?",
        ],
    }

    bank: dict[str, list[str]] = {}
    for key, questions in templates.items():
        prepared: list[str] = []
        for text in questions:
            with_role = _with_role(text, normalized_role)
            with_company = _with_company(with_role, company_name)
            prepared.append(with_company)
        bank[key] = prepared
    return bank


def select_non_tech_interview_questions(
    *,
    role_name: str,
    company_name: str | None,
    seed: str,
    past_interview_count: int = 0,
) -> list[dict[str, str]]:
    bank = build_non_tech_question_bank(role_name=role_name, company_name=company_name)
    labels = non_tech_category_labels()

    selected: list[dict[str, str]] = []
    for category in non_tech_category_keys():
        bucket = bank.get(category, [])
        if len(bucket) < 20:
            raise ValueError(f"Question bank for category '{category}' must contain at least 20 questions")
        
        idx = past_interview_count % len(bucket)
        question_text = bucket[idx]
        
        selected.append(
            {
                "text": question_text,
                "topic": labels.get(category, category),
                "category": category,
            }
        )
    return selected
