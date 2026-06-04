import fastapi

from src.api.routes.users import router as users_router
from src.api.routes.resume import router as resume_router
from src.api.routes.interviews import router as interviews_router
from src.api.routes.interviews_v2 import router as interviews_v2_router
from src.api.routes.audio import router as audio_router
from src.api.routes.analysis import router as analysis_router
from src.api.routes.report import router as report_router
from src.api.routes.auth_cognito import router as cognito_router
from src.api.routes.summary_report import router as summary_report_router
from src.api.routes.summary_report_v2 import router as summary_report_v2_router
from src.api.routes.tts import router as tts_router
from src.api.routes.speech_pacing import router as speech_pacing_router
from src.api.routes.analytics import router as analytics_router
from src.api.routes.analytics_v2 import router as analytics_v2_router
from src.api.routes.job_profiles import router as job_profiles_router
from src.api.routes.ai_resume import router as ai_resume_router
from src.api.routes.resume_builder import router as resume_builder_router

router = fastapi.APIRouter()

# Health check endpoint for ECS/Load Balancer
@router.get("/health", status_code=200)
async def health_check():
    return {"status": "healthy", "service": "samvaad-sathi-backend"}

router.include_router(router=users_router)
router.include_router(router=resume_router)
router.include_router(router=interviews_router)
router.include_router(router=interviews_v2_router)
router.include_router(router=audio_router)
router.include_router(router=analysis_router)
router.include_router(router=report_router)
router.include_router(router=summary_report_router)
router.include_router(router=summary_report_v2_router)
router.include_router(router=cognito_router)
router.include_router(router=tts_router)
router.include_router(router=speech_pacing_router)
router.include_router(router=analytics_router)
router.include_router(router=analytics_v2_router)
router.include_router(router=job_profiles_router)
router.include_router(router=ai_resume_router)
router.include_router(router=resume_builder_router)