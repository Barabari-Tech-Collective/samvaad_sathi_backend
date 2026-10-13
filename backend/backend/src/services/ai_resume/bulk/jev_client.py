"""Pluggable Jev (TypeSafe) client for Bulk ATS semantic judgment calls.

Jev is scoped to exactly the questions a deterministic rule can't answer -
skills_score, education_quality, experience_relevance, project_quality,
project_complexity, project_tier, resume_hygiene, needs_manual_review (see
QUESTIONS below). Parsing, link reachability and the final weighted score
never go through Jev - those stay in parser_service.py / link_requirements.py
/ scoring_weights.py so a Jev outage or schema change can't break them.

JevProvider is the seam: get_jev_provider() returns JevClient when
JEV_API_BASE_URL/JEV_API_KEY are configured, otherwise StubJevProvider so
Bulk ATS keeps working (candidates land in needs_review) before Jev is wired
up or during a Jev outage.

The exact Jev request/response wire format (endpoint path, typed
Choice/Score/Noul answer schema) was not available when this was written -
JevClient's _call_jev() has a best-effort shape (a single request per resume
carrying all QUESTIONS, returning {question_id: {"type", "value",
"confidence"}}) that **must be verified against real Jev docs/a sample call
before enabling JEV_API_BASE_URL in any real environment**. If the actual
contract differs, only _call_jev() needs to change - everything above it
(QUESTIONS, JevAnswer, callers in pipeline.py) is shape-stable.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from src.config.manager import settings

logger = logging.getLogger(__name__)

QuestionType = Literal["choice", "score", "noul"]


@dataclass(frozen=True)
class JevQuestion:
    id: str
    type: QuestionType
    prompt: str
    # For "choice" questions only: the allowed answer values.
    choices: tuple[str, ...] | None = None


@dataclass(frozen=True)
class JevAnswer:
    question_id: str
    type: QuestionType
    value: Any
    confidence: float | None = None


# The atomic questions Bulk ATS asks per resume. Kept here (not per-call) so
# pipeline.py and both providers agree on ids without re-typing them.
QUESTIONS: tuple[JevQuestion, ...] = (
    JevQuestion("skills_score", "score", "Score (0-100) how well the candidate's demonstrated skills match the role's required and preferred skills, weighting skills shown via projects/experience over bare keyword mentions."),
    JevQuestion("education_quality", "choice", "Rate the relevance and completeness of the candidate's education for this role.", ("Strong", "Adequate", "Weak", "Missing")),
    JevQuestion("experience_relevance", "score", "Score (0-100) how relevant the candidate's work/internship experience is to this role, independent of raw years."),
    JevQuestion("project_quality", "score", "Score (0-100) the overall quality of the candidate's listed projects: clarity, described contribution, outcome/metrics, and grammar/hygiene."),
    JevQuestion("project_complexity", "score", "Score (0-100) the technical/analytical complexity of the candidate's most substantial project, relative to this role."),
    JevQuestion("project_tier", "choice", "Classify the candidate's strongest project for this role.", ("Good", "Average", "Poor")),
    JevQuestion("resume_hygiene", "score", "Score (0-100) the resume's writing quality: grammar, clarity, consistent formatting, no filler."),
    JevQuestion("needs_manual_review", "noul", "True if resume content is incomplete, contradictory, or otherwise too ambiguous for automated scoring to be trusted."),
)


class JevProvider(abc.ABC):
    @abc.abstractmethod
    async def evaluate(
        self,
        *,
        resume_text: str,
        parsed_resume_json: dict,
        role_name: str,
        role_description: str,
        required_skills: list[str],
        preferred_skills: list[str],
    ) -> dict[str, JevAnswer]:
        """One call per resume; returns every question in QUESTIONS, keyed by id."""
        raise NotImplementedError


class StubJevProvider(JevProvider):
    """Deterministic, Jev-shaped placeholder used when Jev isn't configured.

    Always answers needs_manual_review=True so candidates route to manual
    review instead of silently getting a confident-looking score from a
    provider that was never actually called.
    """

    async def evaluate(self, **_kwargs) -> dict[str, JevAnswer]:
        answers: dict[str, JevAnswer] = {}
        for q in QUESTIONS:
            if q.type == "noul":
                value: Any = True
            elif q.type == "score":
                value = None
            else:
                value = None
            answers[q.id] = JevAnswer(question_id=q.id, type=q.type, value=value, confidence=0.0)
        return answers


class JevClient(JevProvider):
    """Real Jev (TypeSafe) HTTP client. See module docstring - verify the
    wire format in _call_jev() before relying on this in production."""

    def __init__(self, base_url: str, api_key: str, timeout: float):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def evaluate(
        self,
        *,
        resume_text: str,
        parsed_resume_json: dict,
        role_name: str,
        role_description: str,
        required_skills: list[str],
        preferred_skills: list[str],
    ) -> dict[str, JevAnswer]:
        payload = {
            "context": {
                "role_name": role_name,
                "role_description": role_description,
                "required_skills": required_skills,
                "preferred_skills": preferred_skills,
                "resume_text": resume_text,
                "parsed_resume": parsed_resume_json,
            },
            "questions": [
                {"id": q.id, "type": q.type, "prompt": q.prompt, **({"choices": list(q.choices)} if q.choices else {})}
                for q in QUESTIONS
            ],
        }
        try:
            raw = await self._call_jev(payload)
        except Exception:
            logger.exception("Jev call failed; falling back to needs_manual_review for this resume")
            return await StubJevProvider().evaluate()

        answers: dict[str, JevAnswer] = {}
        for q in QUESTIONS:
            item = raw.get(q.id) or {}
            answers[q.id] = JevAnswer(
                question_id=q.id,
                type=q.type,
                value=item.get("value"),
                confidence=item.get("confidence"),
            )
        return answers

    async def _call_jev(self, payload: dict) -> dict[str, dict]:
        """Best-effort wire format - see module docstring. Adjust the path,
        headers and response parsing here once Jev's real contract is known."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/evaluate",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("answers", data)


def get_jev_provider() -> JevProvider:
    if settings.JEV_API_BASE_URL and settings.JEV_API_KEY:
        return JevClient(
            base_url=settings.JEV_API_BASE_URL,
            api_key=settings.JEV_API_KEY,
            timeout=settings.JEV_TIMEOUT_SECONDS,
        )
    return StubJevProvider()
