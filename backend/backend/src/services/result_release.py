"""Whether a student may see an AI assessment result yet (architecture pack F16).

A template can ask for results to be reviewed before release. That rule is frozen onto each
sitting when it starts. Until someone releases a held result, the student's own report
endpoints answer with a "needs review" envelope that carries no scores or feedback. The
student app already shows that status as "Feedback needs review". Scoring itself is not
delayed; staff see the full result on the assessment record.
"""

HELD_NOTICE = (
    "Your assessment was submitted and is being reviewed. Your result and feedback will "
    "appear here once they are released."
)
HELD_DETAIL = "This assessment result is being reviewed and has not been released yet."


def withheld(interview) -> bool:
    return (
        getattr(interview, "interview_mode", None) == "assessment"
        and getattr(interview, "result_release", "immediate") == "review"
        and getattr(interview, "result_released_at", None) is None
    )


def held_report(report: dict) -> dict:
    """The parts of a V2 report a student may see while it is held: who and when, no result."""
    return {
        "reportId": report.get("reportId") or "held",
        "reportVersion": report.get("reportVersion", "practice-report-v1"),
        "reportStatus": "needs_review",
        "generatedAt": report.get("generatedAt"),
        "candidateInfo": report["candidateInfo"],
        "questionAnalysis": [],
        "uncertaintyNotice": HELD_NOTICE,
    }
