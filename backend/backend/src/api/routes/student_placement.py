"""The learner's own course and batch choice (see services/student_placement.py)."""

import fastapi
from pydantic import BaseModel, Field

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.services import student_placement as service

router = fastapi.APIRouter(prefix="/v2/placement", tags=["student-placement"])


class Choice(BaseModel):
    batch_keys: list[str] = Field(min_length=1, max_length=service.MAX_CHOICES)


@router.get("/me")
async def mine(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """What this learner has chosen, and whether they still need to choose."""
    response.headers["Cache-Control"] = "no-store"
    return await service.overview(session, user)


@router.get("/options")
async def options(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """The courses and batches the program team has opened for choosing."""
    response.headers["Cache-Control"] = "no-store"
    await service.require_student(session, user)  # learners only
    items, stale = await service.open_batches()
    return {
        "items": [
            {
                "batch_key": i.key,
                "batch_name": i.batchName,
                "course_id": i.course,
                "course_name": i.courseName,
                "label": i.label(),
            }
            for i in items
        ],
        "stale": stale,
    }


@router.put("/me")
async def choose(
    body: Choice,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """Replace this learner's choices with exactly these batches."""
    response.headers["Cache-Control"] = "no-store"
    return await service.replace(session, user, body.batch_keys)
