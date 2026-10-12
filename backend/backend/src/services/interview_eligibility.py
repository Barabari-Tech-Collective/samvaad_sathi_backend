"""Which interviews a student is allowed to start, and at which level.

A facilitator-curated interview is an assessment: the student is scored against
a question set that a facilitator wrote and published for one domain at one
level. That only means something if the questions actually came from there.

The platform used to break that in several places at once. A student could pick
any of the four difficulties because the picker was hardcoded, creation only
checked that the job profile existed, and when the requested level turned out to
have no published questions the generator quietly fell through to the LLM - so a
student was assessed against a facilitator's profile on questions the
facilitator had never seen, with nothing in the response or the report saying
so. A draft profile was equally interviewable, because the student path never
looked at status.

The rule this module exists to enforce: for an interview attached to a job
profile, a student may only be served questions that facilitator published, for
that profile, at that level. Never an AI substitute, never a different level,
never unpublished content. Free practice - an interview with no job profile - is
unaffected; there, generated questions are the product.

The threshold lives here rather than in the client so that the picker, interview
creation and question generation cannot disagree about what "available" means.
"""

from src.models.db.job_profile import JobProfileStatus

#: Fewest published questions a level needs before a student may sit it.
#: Below this the level is not offered at all, rather than served short or
#: topped up with generated questions: an assessment whose length varies by
#: student cannot be compared across students, and topping up would put
#: unapproved questions back into a scored interview.
MINIMUM_QUESTIONS_PER_LEVEL = 5

#: Difficulty as the student and the interview row spell it, to the integer
#: `job_profile_question.level` the facilitator's questions are filed under.
#: Previously inlined as a bare dict in the generator with a `.get(x, 2)`
#: default, which silently turned an unrecognised difficulty into medium.
DIFFICULTY_TO_LEVEL = {"easy": 1, "medium": 2, "hard": 3, "expert": 4}

LEVEL_TO_DIFFICULTY = {level: difficulty for difficulty, level in DIFFICULTY_TO_LEVEL.items()}

#: Ordered easiest-first, for pickers and messages.
DIFFICULTIES = tuple(DIFFICULTY_TO_LEVEL)


def is_valid_difficulty(difficulty: str | None) -> bool:
    return difficulty in DIFFICULTY_TO_LEVEL


def level_for_difficulty(difficulty: str) -> int:
    """Raises rather than defaulting: guessing a level mis-files an assessment."""
    try:
        return DIFFICULTY_TO_LEVEL[difficulty]
    except KeyError:
        raise ValueError(
            f"Unknown difficulty {difficulty!r}; expected one of {', '.join(DIFFICULTIES)}"
        ) from None


def is_profile_interviewable(job_profile) -> bool:
    """Only a published profile may be sat by a student.

    `draft`, `under_review` and `changes_requested` are all states where a
    facilitator or admin has not agreed the content is ready - `changes_requested`
    especially, where a reviewer has actively asked for edits.
    """
    return job_profile.status == JobProfileStatus.PUBLISHED


def published_question_counts(job_profile) -> dict[str, int]:
    """How many questions the facilitator published, per difficulty.

    Reads the `JobProfile` count properties, which aggregate the eagerly loaded
    `questions` relationship, so this needs no extra query.
    """
    return {
        "easy": job_profile.easy_questions,
        "medium": job_profile.medium_questions,
        "hard": job_profile.hard_questions,
        "expert": job_profile.expert_questions,
    }


def available_difficulties(job_profile) -> list[str]:
    """The difficulties a student may actually start, easiest first.

    Empty for a profile that is not published, or one whose levels are all too
    sparse - in which case the profile should not be offered to students at all
    and the facilitator needs to be told it is incomplete.
    """
    if not is_profile_interviewable(job_profile):
        return []
    counts = published_question_counts(job_profile)
    return [
        difficulty
        for difficulty in DIFFICULTIES
        if counts[difficulty] >= MINIMUM_QUESTIONS_PER_LEVEL
    ]


def unavailable_reason(job_profile, difficulty: str) -> str | None:
    """Why this difficulty cannot be started, or None if it can.

    The string is shown to students, so it says what to do next instead of
    naming a status or a count the student has no control over.
    """
    if not is_valid_difficulty(difficulty):
        return f"Choose a difficulty: {', '.join(DIFFICULTIES)}."

    if not is_profile_interviewable(job_profile):
        return (
            "This role is not open for interviews yet. "
            "Your facilitator is still preparing it."
        )

    available = available_difficulties(job_profile)
    if difficulty in available:
        return None

    if available:
        return (
            f"The {difficulty} level is not ready for this role yet. "
            f"Available now: {', '.join(available)}."
        )
    return (
        "This role has no interview levels ready yet. "
        "Your facilitator needs to add more questions before it can be started."
    )


def select_published_questions(questions, *, difficulty: str, count: int, offset: int = 0):
    """Pick `count` of the facilitator's questions for one level.

    Walks the level's questions from `offset`, so a student retaking the same
    profile and level sees a different slice rather than the same opening five.
    Returns an empty list when the level is too sparse, which callers must treat
    as a refusal - never as a cue to generate.
    """
    level = level_for_difficulty(difficulty)
    level_questions = [question for question in questions if question.level == level]
    if len(level_questions) < MINIMUM_QUESTIONS_PER_LEVEL:
        return []

    total = len(level_questions)
    return [level_questions[(offset + index) % total] for index in range(min(count, total))]
