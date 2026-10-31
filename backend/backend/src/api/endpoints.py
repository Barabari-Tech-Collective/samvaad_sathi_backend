import fastapi

from src.api.routes.users import router as users_router
from src.api.routes.resume import router as resume_router
from src.api.routes.interviews import router as interviews_router
from src.api.routes.interviews_v2 import router as interviews_v2_router
from src.api.routes.audio import router as audio_router
from src.api.routes.analysis import router as analysis_router
from src.api.routes.report import router as report_router

# LEGACY - COGNITO, kept for rollback during the Sampark Saathi SSO migration. Uncomment
# and re-add cognito_router below (plus the frontend's commented-out counterparts in
# endpoints.ts/auth-provider.tsx) if SSO needs to be reverted.
# from src.api.routes.auth_cognito import router as cognito_router
from src.api.routes.auth_sso import router as sso_router
from src.api.routes.internal_admin import router as internal_admin_router
from src.api.routes.summary_report import router as summary_report_router
from src.api.routes.summary_report_v2 import router as summary_report_v2_router
from src.api.routes.tts import router as tts_router
from src.api.routes.speech_pacing import router as speech_pacing_router
from src.api.routes.job_profiles_v2 import router as job_profiles_v2_router
from src.api.routes.analytics import router as analytics_router
from src.api.routes.analytics_v2 import router as analytics_v2_router
from src.api.routes.ai_resume import router as ai_resume_router
from src.api.routes.resume_builder import router as resume_builder_router
from src.api.routes.bulk_ats import router as bulk_ats_router
from src.api.routes.teams_v2 import router as teams_v2_router
from src.api.routes.practice_dashboard import router as practice_dashboard_router
from src.api.routes.course_workspace import router as course_workspace_router

from src.api.dependencies.features import feature_gate
from src.api.routes.feature_access import router as feature_access_router

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
# router.include_router(router=cognito_router)  # LEGACY - COGNITO, kept for rollback
router.include_router(router=sso_router)
router.include_router(router=internal_admin_router)
router.include_router(router=tts_router)
router.include_router(router=speech_pacing_router)
router.include_router(router=job_profiles_v2_router, dependencies=[fastapi.Depends(feature_gate("interview_authoring"))])
router.include_router(router=analytics_router, dependencies=[fastapi.Depends(feature_gate("analytics"))])
router.include_router(router=analytics_v2_router, dependencies=[fastapi.Depends(feature_gate("analytics"))])
router.include_router(router=ai_resume_router)
router.include_router(router=resume_builder_router)
router.include_router(router=bulk_ats_router, dependencies=[fastapi.Depends(feature_gate("bulk_ats"))])
router.include_router(router=teams_v2_router, dependencies=[fastapi.Depends(feature_gate("teams"))])
router.include_router(router=practice_dashboard_router, dependencies=[fastapi.Depends(feature_gate("practice_coaching"))])
router.include_router(router=course_workspace_router, dependencies=[fastapi.Depends(feature_gate("course_workspace"))])
from src.api.routes.cohorts import router as cohorts_router
router.include_router(router=cohorts_router, dependencies=[fastapi.Depends(feature_gate("course_workspace"))])
# //working on staging

router.include_router(router=feature_access_router)

from src.api.routes.staff_access import router as staff_access_router
router.include_router(router=staff_access_router)

from src.api.routes.coaching_assignments import router as coaching_assignments_router
router.include_router(router=coaching_assignments_router)
from src.api.routes.mock_assignments import router as mock_assignments_router
router.include_router(router=mock_assignments_router)
from src.api.routes.learning_plans import router as learning_plans_router
router.include_router(router=learning_plans_router)
from src.api.routes.student_home import router as student_home_router
router.include_router(router=student_home_router)

from src.api.routes.course_shortlists import router as course_shortlists_router
router.include_router(router=course_shortlists_router)

from src.api.routes.human_interviews import router as human_interviews_router
router.include_router(router=human_interviews_router)
from src.api.routes.human_scorecards import router as human_scorecards_router
router.include_router(router=human_scorecards_router)
from src.api.routes.notifications import router as notifications_router
router.include_router(router=notifications_router)
from src.api.routes.operations import router as operations_router
router.include_router(router=operations_router)
from src.api.routes.profile_context import router as profile_context_router
router.include_router(router=profile_context_router)
from src.api.routes.feedback_issues import router as feedback_issues_router
router.include_router(router=feedback_issues_router)
from src.api.routes.accommodations import router as accommodations_router
router.include_router(router=accommodations_router)
from src.api.routes.staff_tasks import router as staff_tasks_router
router.include_router(router=staff_tasks_router)
from src.api.routes.opportunity_selection import router as opportunity_selection_router
router.include_router(router=opportunity_selection_router)
from src.api.routes.opportunities import router as opportunities_router
router.include_router(router=opportunities_router)
from src.api.routes.learners import router as learners_router
router.include_router(router=learners_router)
from src.api.routes.assessment_records import router as assessment_records_router
router.include_router(router=assessment_records_router)

from src.api.routes.integrity_review import router as integrity_review_router
router.include_router(router=integrity_review_router)

from src.api.routes.product_session import router as product_session_router
router.include_router(router=product_session_router)

from src.api.routes.graduation import router as graduation_router
router.include_router(graduation_router)

# Keep this last: unmounts v1 routes that no frontend uses (see legacy_routes.py).
from src.api.legacy_routes import drop_retired_routes

drop_retired_routes(router)
from src.api.routes.assessment_assignments import router as assessment_assignments_router
router.include_router(router=assessment_assignments_router)
from src.api.routes.privacy_notice import router as privacy_notice_router
router.include_router(router=privacy_notice_router)
from src.api.routes.readiness import router as readiness_router
router.include_router(router=readiness_router)
from src.api.routes.opportunity_handoff import router as opportunity_handoff_router
router.include_router(router=opportunity_handoff_router)
