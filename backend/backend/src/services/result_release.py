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


def rescale(part: dict, pct: int) -> dict:
    """Move a score block to a new percentage, keeping its points and average consistent."""
    out = dict(part)
    out["percentage"] = pct
    if "averagePct" in out:
        out["averagePct"] = pct
    if isinstance(out.get("maxScore"), (int, float)):
        out["score"] = round(pct / 100 * out["maxScore"])
    if isinstance(out.get("maxAverage"), (int, float)):
        out["average"] = round(pct / 100 * out["maxAverage"], 2)
    return out


def current_scores(report_json: dict | None) -> dict | None:
    summary = (report_json or {}).get("scoreSummary") or {}
    speech = (summary.get("speechAndStructure") or {}).get("percentage")
    if speech is None:
        return None
    return {
        "knowledge": (summary.get("knowledgeCompetence") or {}).get("percentage"),
        "speech": speech,
    }


# Speech rating bands, using the same five labels the AI is told to choose from.
RATING_BANDS = (
    (85, "Excellent", "Excellent delivery"),
    (70, "Good", "Good delivery"),
    (50, "Average", "Average delivery"),
    (30, "Needs-Improvement", "Delivery needs improvement"),
    (0, "Poor", "Delivery needs a lot of practice"),
)


def rating_for(pct: int) -> tuple[str, str]:
    return next((emoji, title) for floor, emoji, title in RATING_BANDS if pct >= floor)


def corrected_report(report_json: dict, knowledge: int | None, speech: int, note: str | None) -> dict:
    """The report with corrected headline percentages; per-criterion detail is unchanged.
    If the speech score moves into another rating band, the rating label follows it."""
    out = dict(report_json)
    feedback = out.get("speechFluencyFeedback")
    if isinstance(feedback, dict):
        emoji, title = rating_for(speech)
        if feedback.get("ratingEmoji") != emoji:
            out["speechFluencyFeedback"] = {
                **feedback,
                "ratingEmoji": emoji,
                "ratingTitle": title,
                "ratingDescription": f"Updated after review: speech and structure {speech}%.",
            }
    summary = dict(out["scoreSummary"])
    summary["speechAndStructure"] = rescale(summary["speechAndStructure"], speech)
    if knowledge is not None and summary.get("knowledgeCompetence"):
        summary["knowledgeCompetence"] = rescale(summary["knowledgeCompetence"], knowledge)
    out["scoreSummary"] = summary
    if note:
        out["uncertaintyNotice"] = f"Your score was updated after review. {note}"
    return out
