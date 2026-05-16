# Syllabus Service

The syllabus service maps interview tracks to role-aware topic banks and question ratios. It is used by V1 and V2 interview question generation.

## Files

- `syllabus_service.py`: current service implementation, role derivation, topic lookup, category ratio logic, and resume-skill topic extraction
- `syllabus.py`: compatibility wrapper that preserves older function-style imports
- `syllabus_data.py`: role aliases, constants, and smaller static data
- `syllabus_content.py`: larger topic banks
- `syllabus_examples.py`: local examples for manual checks

## Current Usage

New code should use the service object:

```python
from src.services.syllabus import derive_role
from src.services.syllabus_service import syllabus_service

role = derive_role("react developer")
topics = syllabus_service.get_topics_for_role(role=role, difficulty="medium")
ratio = syllabus_service.compute_question_ratio(
    years_experience=2.0,
    has_resume_text=True,
    has_skills=True,
)
```

Older imports from `src.services.syllabus` are still available for compatibility.

## Runtime Behavior

- Easy difficulty can use static questions.
- Medium, hard, and expert flows use syllabus topics as LLM context.
- Resume text and extracted skills influence `tech_allied` topics.
- Invalid or unknown roles fall back to general role/topic defaults.
- The service keeps in-memory caches for repeated topic lookups.

## Rules

- Prefer `syllabus_service` in new code.
- Keep static content in `syllabus_data.py` or `syllabus_content.py`; do not bury large topic lists inside route handlers.
- Preserve the compatibility wrapper until all older imports are removed.
- Update interview smoke tests when changing topic ratios or role derivation.
