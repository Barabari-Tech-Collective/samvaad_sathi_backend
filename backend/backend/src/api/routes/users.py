import datetime
import fastapi
import pydantic
import sqlalchemy

from src.api.dependencies.repository import get_repository
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.admin import (
    calculate_user_permissions,
    extract_memberships_from_result,
    get_user_active_memberships,
    is_admin_user,
    is_bulk_ats_user,
    is_super_admin_user,
)
from src.api.dependencies.session import get_async_session
from src.api.dependencies.rate_limit import anonymous_rate_limiter
from src.config.manager import settings
from src.models.db.user import User
from src.models.db.user_resume import UserResume
from src.models.schemas.user import (
    UserCreate,
    UserLogin,
    UserInResponse,
    UserWithToken,
    UserProfileUpdate,
    UserProfileOut,
)
from src.repository.crud.user import UserCRUDRepository
from src.repository.crud.session import SessionCRUDRepository
from src.repository.crud.summary_report import SummaryReportCRUDRepository
from src.securities.authorizations.jwt import jwt_generator
from src.utilities.exceptions.database import EntityAlreadyExists, EntityDoesNotExist
from src.utilities.exceptions.password import PasswordDoesNotMatch
from src.utilities.exceptions.http.exc_400 import http_exc_400_credentials_bad_signin_request

from src.models.db.user import TargetPositionEnum
from src.services.analytics_events import track_analytics_event


router = fastapi.APIRouter(prefix="", tags=["users"])


@router.post(
    path="/users",
    name="users:register",
    response_model=UserInResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
)
async def register_user(
    payload: UserCreate,
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
    session_repo: SessionCRUDRepository = fastapi.Depends(get_repository(repo_type=SessionCRUDRepository)),
    _rate_limit: None = fastapi.Depends(
        anonymous_rate_limiter(
            key_prefix="signup",
            limit=settings.RATE_LIMIT_SIGNUP_PER_HOUR,
            window_seconds=3600,
        )
    ),
) -> UserInResponse:
    try:
        user = await user_repo.create_user(email=payload.email, password=payload.password, name=payload.name)
    except EntityAlreadyExists:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_400_BAD_REQUEST, detail="Email already exists")

    user_id = user.id
    user_email = user.email
    user_name = user.name
    user_created_at = user.created_at
    user_is_onboarded = user.is_onboarded if hasattr(user, "is_onboarded") else False

    token = jwt_generator.generate_access_token_for_user(user=user)
    refresh = await session_repo.create_session(user_id=user_id, expiry_minutes=settings.REFRESH_TOKEN_EXPIRY_MINUTES)
    await track_analytics_event(
        session_repo.async_session,
        event_type="user_signup",
        user_id=user_id,
        event_data={"source": "users.register"},
    )

    return UserInResponse(
        user_id=user_id,
        authorized_user=UserWithToken(token=token, 
                                    refresh_token=refresh.token,
                                    email=user_email,
                                    name=user_name,
                                    created_at=user_created_at,
                                    is_onboarded=user_is_onboarded,
                                    is_admin=is_admin_user(user),
                                    is_super_admin=is_super_admin_user(user),
                                    degree=None,
                                    university=None,
                                    target_position=None,
                                    years_experience=None,
                                    total_attempts=0,
                                    has_resume_text=bool(getattr(user, "resume_text", None)),
                                    skills=user.skills.get("items", []) if isinstance(getattr(user, "skills", None), dict) else [],
                                    company=None),
    )


def _extract_primary_membership_and_claims(user: User, memberships: list) -> dict:
    """Extract primary team membership details, calculate permissions, and build JWT claims.

    Handles multi-team setups deterministically by extracting the primary membership
    from memberships[0], while aggregating cross-team permissions via calculate_user_permissions.
    Safely unpacks SQLAlchemy Row objects, tuples, or scalar TeamMember records.
    """
    first_item = memberships[0] if memberships else None
    member = None
    primary_team = None
    if first_item is not None:
        if isinstance(first_item, (tuple, list)) or hasattr(first_item, "__getitem__"):
            try:
                member = first_item[0]
                primary_team = first_item[1] if len(first_item) > 1 else None
            except Exception:
                member = first_item
                primary_team = getattr(member, "team", None)
        else:
            member = first_item
            primary_team = getattr(member, "team", None)

    perms = calculate_user_permissions(user, memberships)
    access_role = getattr(member, "access_role", None) if member else None
    colleges = perms["colleges"]
    domain = getattr(member, "domain", None) if member else None
    purposes = getattr(member, "purposes", None) if member and isinstance(getattr(member, "purposes", None), list) else None
    is_admin_flag = is_admin_user(user)
    is_super_admin_flag = is_super_admin_user(user)
    can_access_ats = perms["can_access_ats"]
    can_create_roles = perms["can_create_roles"]
    can_edit = perms["can_edit"]
    is_view_only = perms["is_view_only"]
    is_team_lead = perms["is_team_lead"]
    effective_role = access_role or getattr(user, "_token_role", None) or getattr(user, "role", None)

    can_access_dashboard = perms.get("can_access_dashboard", False)
    can_access_students = perms.get("can_access_students", False)
    can_access_colleges = perms.get("can_access_colleges", False)
    can_access_interviews = perms.get("can_access_interviews", False)
    can_access_practice_interview = perms.get("can_access_practice_interview", False)

    role_claim = (
        "SUPER_ADMIN" if is_super_admin_flag
        else ("ADMIN" if is_admin_flag
        else (str(effective_role).upper() if effective_role else "STUDENT"))
    )

    extra_claims = {
        "role": role_claim,
        "access_role": access_role or (str(effective_role).lower() if effective_role and str(effective_role).lower() != "student" else ""),
    }

    team_name = getattr(primary_team, "name", None) if primary_team else None
    if is_super_admin_flag:
        display_role = "super admin"
        display_access_role = access_role or "super admin"
    elif is_admin_flag:
        display_role = "admin"
        display_access_role = access_role or "admin"
    else:
        display_role = access_role or getattr(user, "role", None) or (str(effective_role).lower() if effective_role else "student")
        display_access_role = access_role or (str(effective_role).lower() if effective_role and str(effective_role).lower() != "student" else None)

    return {
        "member": member,
        "primary_team": primary_team,
        "perms": perms,
        "access_role": display_access_role,
        "role": display_role,
        "colleges": colleges,
        "domain": domain,
        "purposes": purposes,
        "is_admin": is_admin_flag,
        "is_super_admin": is_super_admin_flag,
        "can_access_ats": can_access_ats,
        "can_create_roles": can_create_roles,
        "can_edit": can_edit,
        "is_view_only": is_view_only,
        "is_team_lead": is_team_lead,
        "can_access_dashboard": can_access_dashboard,
        "can_access_students": can_access_students,
        "can_access_colleges": can_access_colleges,
        "can_access_interviews": can_access_interviews,
        "can_access_practice_interview": can_access_practice_interview,
        "effective_role": effective_role,
        "role_claim": role_claim,
        "extra_claims": extra_claims,
        "team_name": team_name,
    }


@router.post(
    path="/login",
    name="users:login",
    response_model=UserInResponse,
    status_code=fastapi.status.HTTP_202_ACCEPTED,
)
async def login_user(
    payload: UserLogin,
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
    session_repo: SessionCRUDRepository = fastapi.Depends(get_repository(repo_type=SessionCRUDRepository)),
    _rate_limit: None = fastapi.Depends(
        anonymous_rate_limiter(
            key_prefix="login",
            limit=settings.RATE_LIMIT_LOGIN_PER_MINUTE,
            window_seconds=60,
        )
    ),
    session=fastapi.Depends(get_async_session),
) -> UserInResponse:
    try:
        user = await user_repo.verify_password(email=payload.email.strip().lower(), password=payload.password)
    except (EntityDoesNotExist, PasswordDoesNotMatch):
        raise await http_exc_400_credentials_bad_signin_request()

    user_id = user.id
    user_email = user.email
    user_name = user.name
    user_created_at = user.created_at
    user_is_onboarded = user.is_onboarded if hasattr(user, "is_onboarded") else False

    memberships = await get_user_active_memberships(user.id, session)
    claims_data = _extract_primary_membership_and_claims(user, memberships)

    token = jwt_generator.generate_access_token_for_user(
        user=user,
        extra_claims=claims_data["extra_claims"],
    )
    refresh = await session_repo.create_session(user_id=user_id, expiry_minutes=settings.REFRESH_TOKEN_EXPIRY_MINUTES)

    return UserInResponse(
        user_id=user_id,
        authorized_user=UserWithToken(
            token=token, 
            refresh_token=refresh.token,
            email=user_email,
            name=user_name,
            created_at=user_created_at,
            is_onboarded=user_is_onboarded,
            is_admin=claims_data["is_admin"],
            is_super_admin=claims_data["is_super_admin"],
            access_role=claims_data["access_role"],
            role=claims_data["role"],
            is_team_lead=claims_data["is_team_lead"],
            colleges=claims_data["colleges"],
            domain=claims_data["domain"],
            purposes=claims_data["purposes"],
            can_create_roles=claims_data["can_create_roles"],
            can_access_ats=claims_data["can_access_ats"],
            can_edit=claims_data["can_edit"],
            is_view_only=claims_data["is_view_only"],
            can_access_dashboard=claims_data.get("can_access_dashboard", False),
            can_access_students=claims_data.get("can_access_students", False),
            can_access_colleges=claims_data.get("can_access_colleges", False),
            can_access_interviews=claims_data.get("can_access_interviews", False),
            can_access_practice_interview=claims_data.get("can_access_practice_interview", False),
            team_name=claims_data["team_name"],
            can_use_bulk_ats=is_bulk_ats_user(user),
            degree=None,
            university=None,
            target_position=None,
            years_experience=None,
            total_attempts=0,
            has_resume_text=bool(getattr(user, "resume_text", None)),
            skills=user.skills.get("items", []) if isinstance(getattr(user, "skills", None), dict) else [],
            company=None,
        ),
    )

# How long a refresh token stays usable after being rotated away. Long enough to cover
# a page that fires several requests at once and refreshes more than once; short enough
# that a leaked token is not useful for long.
REFRESH_ROTATION_GRACE_SECONDS = 15


class TokenRefreshResponse(pydantic.BaseModel):
    accessToken: str
    refreshToken: str

@router.post(
    path="/refresh",
    name="users:refresh",
    response_model=TokenRefreshResponse,
    status_code=fastapi.status.HTTP_200_OK,
)
async def refresh_access_token(
    refresh_token: str = fastapi.Form(...),
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
    session_repo: SessionCRUDRepository = fastapi.Depends(get_repository(repo_type=SessionCRUDRepository)),
    _rate_limit: None = fastapi.Depends(
        anonymous_rate_limiter(
            key_prefix="refresh",
            limit=settings.RATE_LIMIT_LOGIN_PER_MINUTE,
            window_seconds=60,
        )
    ),
):
    # Validate refresh token exists and is not expired
    session = await session_repo.get_session_by_token(token=refresh_token)
    if not session or session.expiry < datetime.datetime.now(datetime.timezone.utc):
        raise fastapi.HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await user_repo.get_user_by_id(user_id=session.user_id)
    if not user:
        raise fastapi.HTTPException(status_code=401, detail="User not found")

    # Rotate the refresh token, leaving the replaced one briefly valid so that requests
    # already in flight with it still succeed rather than racing into a logout.
    new_refresh = await session_repo.create_session(
        user_id=user.id, expiry_minutes=settings.REFRESH_TOKEN_EXPIRY_MINUTES
    )
    await session_repo.expire_session_soon(session=session, grace_seconds=REFRESH_ROTATION_GRACE_SECONDS)
    # Superseded tokens are expired rather than deleted, so without this the table only
    # ever grows - and get_session_by_token above pays for it on every refresh.
    await session_repo.delete_expired_sessions(user_id=user.id)

    new_access = jwt_generator.generate_access_token_for_user(user=user)
    return {"accessToken": new_access, "refreshToken": new_refresh.token}


@router.get(
    path="/me",
    name="users:me",
    response_model=UserInResponse,
    status_code=fastapi.status.HTTP_200_OK,
)
async def get_me(
    current_user=fastapi.Depends(get_current_user),
    summary_repo: SummaryReportCRUDRepository = fastapi.Depends(get_repository(repo_type=SummaryReportCRUDRepository)),
    session=fastapi.Depends(get_async_session),
) -> UserInResponse:
    # Get total interview attempts count
    total_attempts = await summary_repo.count_by_user(user_id=current_user.id)

    # Fetch all user resumes to avoid multiple queries
    resumes_stmt = (
        sqlalchemy.select(UserResume.id, UserResume.filename, UserResume.source)
        .where(UserResume.user_id == current_user.id)
        .order_by(UserResume.created_at.desc(), UserResume.id.desc())
    )
    resumes_result = await session.execute(resumes_stmt)
    resumes = resumes_result.all()

    onboarding_resume_filename: str | None = None
    ats_resume_id: int | None = None
    ats_resume_filename: str | None = None

    for resume in resumes:
        if resume.source == "onboarding" and onboarding_resume_filename is None:
            onboarding_resume_filename = resume.filename
        elif resume.source == "ats_final" and ats_resume_filename is None:
            ats_resume_id = resume.id
            ats_resume_filename = resume.filename
            
        if onboarding_resume_filename and ats_resume_filename:
            break

    memberships = await get_user_active_memberships(current_user.id, session)
    claims_data = _extract_primary_membership_and_claims(current_user, memberships)

    token = jwt_generator.generate_access_token_for_user(
        user=current_user,
        extra_claims=claims_data["extra_claims"],
    )

    return UserInResponse(
        user_id=current_user.id,
        authorized_user=UserWithToken(
            token=token,
            refresh_token=None,
            email=current_user.email,
            name=current_user.name,
            created_at=current_user.created_at,
            is_onboarded=getattr(current_user, 'is_onboarded', False),
            is_admin=claims_data["is_admin"],
            is_super_admin=claims_data["is_super_admin"],
            access_role=claims_data["access_role"],
            role=claims_data["role"],
            is_team_lead=claims_data["is_team_lead"],
            colleges=claims_data["colleges"],
            domain=claims_data["domain"],
            purposes=claims_data["purposes"],
            can_create_roles=claims_data["can_create_roles"],
            can_access_ats=claims_data["can_access_ats"],
            can_edit=claims_data["can_edit"],
            is_view_only=claims_data["is_view_only"],
            can_access_dashboard=claims_data.get("can_access_dashboard", False),
            can_access_students=claims_data.get("can_access_students", False),
            can_access_colleges=claims_data.get("can_access_colleges", False),
            can_access_interviews=claims_data.get("can_access_interviews", False),
            can_access_practice_interview=claims_data.get("can_access_practice_interview", False),
            team_name=claims_data["team_name"],
            can_use_bulk_ats=is_bulk_ats_user(current_user),
            degree=current_user.degree,
            university=current_user.university,
            target_position=current_user.target_position,
            years_experience=current_user.years_experience,
            has_resume=bool(getattr(current_user, 'resume_text', None)),
            total_attempts=total_attempts,
            has_resume_text=bool(getattr(current_user, "resume_text", None)),
            onboarding_resume_filename=onboarding_resume_filename,
            ats_resume_filename=ats_resume_filename,
            ats_resume_id=ats_resume_id,
            skills=current_user.skills.get("items", []) if isinstance(getattr(current_user, "skills", None), dict) else [],
            company=current_user.company,
        ),
    )

@router.put(
    path="/users/profile",
    name="users:update-profile",
    response_model=UserProfileOut,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Update authenticated user's profile fields",
    description=(
        "Allows an authenticated user to update additional profile fields such as degree, university, "
        "target position, and years of experience. Only the provided fields are updated."
    ),
)
async def update_profile(
    profile_update: UserProfileUpdate,
    current_user=fastapi.Depends(get_current_user),
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
) -> UserProfileOut:
    previous_target_position = getattr(current_user, "target_position", None)
    # Persist updates via repository
    updated = await user_repo.update_user_profile(
        user_id=current_user.id,
        degree=profile_update.degree,
        university=profile_update.university,
        target_position=profile_update.target_position,
        years_experience=profile_update.years_experience,
    )

    # Mark onboarding complete when profile endpoint is used successfully
    if hasattr(updated, 'is_onboarded') and not updated.is_onboarded:
        await user_repo.set_onboarded(user_id=updated.id, value=True)
        updated = await user_repo.get_user_by_id(user_id=updated.id)

    if profile_update.target_position and profile_update.target_position != previous_target_position:
        await track_analytics_event(
            user_repo.async_session,
            event_type="role_selected",
            user_id=updated.id,
            event_data={
                "target_position": profile_update.target_position,
                "source": "users.profile",
            },
        )

    return UserProfileOut(
        user_id=updated.id,
        email=updated.email,
        name=updated.name,
        degree=updated.degree,
        university=updated.university,
        target_position=updated.target_position,
        years_experience=updated.years_experience,
        has_resume_text=bool(getattr(updated, "resume_text", None)),
        skills=updated.skills.get("items", []) if isinstance(getattr(updated, "skills", None), dict) else [],
    )


