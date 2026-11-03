from src.models.db.job_profile import JobProfile
from src.models.db.job_profile_question import JobProfileQuestion


def test_question_reassignment_keeps_both_relationships_consistent():
    first, second = JobProfile(), JobProfile()
    question = JobProfileQuestion()
    first.questions.append(question)
    assert question.job_profile is first
    question.job_profile = second
    assert question not in first.questions
    assert question in second.questions
