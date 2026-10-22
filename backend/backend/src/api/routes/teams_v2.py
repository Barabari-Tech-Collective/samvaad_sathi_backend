import fastapi
import datetime
from typing import List
from sqlalchemy.exc import IntegrityError

from src.api.dependencies.admin import (
    get_current_admin_user,
    get_current_super_admin_user,
    is_super_admin_user,
    is_team_authorized_user,
    check_team_permission,
)
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.models.schemas.teams_v2 import (
    TeamResponse,
    TeamCreate,
    TeamMemberResponse,
    TeamMemberCreate,
    TeamMemberUpdate,
    TeamMemberRecycleBinResponse,
)
from src.repository.crud.team_v2 import TeamCRUDRepository, TeamDeleteStatus
from src.models.db.user import User
from src.utilities.exceptions.database import EntityDoesNotExist, EntityAlreadyExists
from src.services.central_auth_service import CentralAuthProvisioningError, CentralAuthReconciliationError

router = fastapi.APIRouter(prefix="/v2/teams", tags=["teams-v2"])

@router.get(
    path="",
    name="teams:get_all",
    response_model=List[TeamResponse],
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get all teams",
)
async def get_all_teams(
    status: str = fastapi.Query("active", description="Status of the team (active/deleted)"),
    current_user: User = fastapi.Depends(get_current_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
) -> List[TeamResponse]:
    # Prevent query-parameter status bypass: non-super-admins cannot inspect deleted teams
    if status != "active" and not is_super_admin_user(current_user):
        status = "active"
    teams = await team_repo.get_all_teams(status=status)
    return [TeamResponse(**t) for t in teams]

@router.get(
    path="/recycle-bin",
    name="teams:get_team_recycle_bin",
    response_model=List[TeamResponse],
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get deleted teams for recycle bin",
)
async def get_team_recycle_bin(
    current_user: User = fastapi.Depends(get_current_super_admin_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
) -> List[TeamResponse]:
    teams = await team_repo.get_all_teams(status="deleted")
    return [TeamResponse(**t) for t in teams]

@router.post(
    path="",
    name="teams:create",
    response_model=TeamResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Create a new team",
)
async def create_team(
    team_data: TeamCreate,
    current_user: User = fastapi.Depends(get_current_super_admin_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
) -> TeamResponse:
    team = await team_repo.create_team(team_data)
    return TeamResponse(**team)

@router.delete(
    path="/{team_id}",
    name="teams:delete",
    status_code=fastapi.status.HTTP_204_NO_CONTENT,
    summary="Delete a team (move to recycle bin)",
)
async def delete_team(
    team_id: int,
    current_user: User = fastapi.Depends(get_current_super_admin_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
):
    result = await team_repo.delete_team(team_id)
    if result == TeamDeleteStatus.HAS_MEMBERS or result == "has_members":
        raise fastapi.HTTPException(status_code=400, detail="Cannot delete team. Please remove or transfer all active members first.")
    elif result == TeamDeleteStatus.NOT_FOUND or result == "not_found":
        raise fastapi.HTTPException(status_code=404, detail="Team not found")
    
    return None

@router.patch(
    path="/{team_id}/restore",
    name="teams:restore_team",
    status_code=fastapi.status.HTTP_200_OK,
    summary="Restore team from recycle bin",
)
async def restore_team(
    team_id: int,
    current_user: User = fastapi.Depends(get_current_super_admin_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
):
    success = await team_repo.restore_team(team_id)
    if not success:
        raise fastapi.HTTPException(status_code=404, detail="Team not found")
    return {"message": "Team restored successfully"}

@router.delete(
    path="/{team_id}/permanent",
    name="teams:permanent_delete_team",
    status_code=fastapi.status.HTTP_204_NO_CONTENT,
    summary="Permanently delete team",
)
async def permanent_delete_team(
    team_id: int,
    current_user: User = fastapi.Depends(get_current_super_admin_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
):
    success = await team_repo.permanent_delete_team(team_id)
    if not success:
        raise fastapi.HTTPException(status_code=404, detail="Team not found")
    return None

@router.get(
    path="/{team_id}/members",
    name="teams:get_active_members",
    response_model=List[TeamMemberResponse],
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get active team members",
)
async def get_active_members(
    team_id: int,
    status: str = fastapi.Query("active", description="Status of the member (active/deleted)"),
    current_user: User = fastapi.Depends(get_current_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
) -> List[TeamMemberResponse]:
    # Prevent query-parameter status bypass: viewing non-active/deleted members requires lead permission
    require_lead = (status != "active")
    if not await check_team_permission(current_user, team_id, team_repo.async_session, require_lead=require_lead):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Team Lead or Administrator access required to view non-active members."
            if require_lead
            else "You do not have access to view members of this team.",
        )
    members = await team_repo.get_team_members(team_id, status)
    return [TeamMemberResponse(**m) for m in members]

@router.post(
    path="/{team_id}/members",
    name="teams:add_member",
    response_model=TeamMemberResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Add a new member to a team",
)
async def add_member(
    team_id: int,
    member_data: TeamMemberCreate,
    current_user: User = fastapi.Depends(get_current_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
) -> TeamMemberResponse:
    if not await check_team_permission(current_user, team_id, team_repo.async_session, require_lead=True):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Team Lead or Administrator access required to manage members.",
        )
    try:
        member = await team_repo.add_team_member(team_id, member_data)
        return TeamMemberResponse(**member)
    except EntityDoesNotExist:
        raise fastapi.HTTPException(status_code=404, detail="Team not found")
    except EntityAlreadyExists:
        raise fastapi.HTTPException(status_code=400, detail="User is already an active member of this team.")
    except CentralAuthReconciliationError as e:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_409_CONFLICT, detail=str(e))
    except CentralAuthProvisioningError as e:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except ValueError as e:
        raise fastapi.HTTPException(status_code=400, detail=str(e))
    except IntegrityError as e:
        error_msg = str(e.orig) if hasattr(e, "orig") else str(e)
        if "unique constraint" in error_msg.lower() or "duplicate key" in error_msg.lower():
            if "team_member" in error_msg.lower() or "team_id" in error_msg.lower():
                raise fastapi.HTTPException(status_code=400, detail="User is already a member of this team.")
            raise fastapi.HTTPException(status_code=400, detail="The email is already registered or member already exists.")
        raise fastapi.HTTPException(status_code=400, detail="Database integrity error occurred.")

@router.get(
    path="/{team_id}/recycle-bin",
    name="teams:get_recycle_bin",
    response_model=List[TeamMemberRecycleBinResponse],
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get deleted members for recycle bin",
)
async def get_recycle_bin(
    team_id: int,
    current_user: User = fastapi.Depends(get_current_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
) -> List[TeamMemberRecycleBinResponse]:
    if not await check_team_permission(current_user, team_id, team_repo.async_session, require_lead=True):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Team Lead or Administrator access required to view recycle bin.",
        )
    members = await team_repo.get_team_members(team_id, status='deleted')
    return [TeamMemberRecycleBinResponse(**m) for m in members]

@router.delete(
    path="/{team_id}/members/{member_id}",
    name="teams:soft_delete_member",
    status_code=fastapi.status.HTTP_204_NO_CONTENT,
    summary="Move member to recycle bin (soft delete)",
)
async def soft_delete_member(
    team_id: int,
    member_id: int,
    current_user: User = fastapi.Depends(get_current_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
):
    if not await check_team_permission(current_user, team_id, team_repo.async_session, require_lead=True):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Team Lead or Administrator access required to manage members.",
        )
    success = await team_repo.soft_delete_member(team_id, member_id)
    if not success:
        raise fastapi.HTTPException(status_code=404, detail="Member not found")
    return None

@router.patch(
    path="/{team_id}/members/{member_id}",
    name="teams:update_member",
    response_model=TeamMemberResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Update an existing team member",
)
async def update_member(
    team_id: int,
    member_id: int,
    member_data: TeamMemberUpdate,
    current_user: User = fastapi.Depends(get_current_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
) -> TeamMemberResponse:
    if not await check_team_permission(current_user, team_id, team_repo.async_session, require_lead=True):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Team Lead or Administrator access required to manage members.",
        )
    try:
        updated_member = await team_repo.update_team_member(
            team_id=team_id,
            member_id=member_id,
            member_data=member_data,
            caller_user=current_user,
        )
        if not updated_member:
            raise fastapi.HTTPException(status_code=404, detail="Member not found")
        return TeamMemberResponse(**updated_member)
    except CentralAuthReconciliationError as e:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_409_CONFLICT, detail=str(e))
    except CentralAuthProvisioningError as e:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except ValueError as e:
        raise fastapi.HTTPException(status_code=400, detail=str(e))
    except IntegrityError as e:
        error_msg = str(e.orig) if hasattr(e, "orig") else str(e)
        if "unique constraint" in error_msg.lower() or "duplicate key" in error_msg.lower():
            raise fastapi.HTTPException(status_code=400, detail="The email is already registered to another user.")
        raise fastapi.HTTPException(status_code=400, detail="Database integrity error occurred.")

@router.patch(
    path="/{team_id}/members/{member_id}/restore",
    name="teams:restore_member",
    status_code=fastapi.status.HTTP_200_OK,
    summary="Restore member from recycle bin",
)
async def restore_member(
    team_id: int,
    member_id: int,
    current_user: User = fastapi.Depends(get_current_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
):
    if not await check_team_permission(current_user, team_id, team_repo.async_session, require_lead=True):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Team Lead or Administrator access required to manage members.",
        )
    success = await team_repo.restore_member(team_id, member_id)
    if not success:
        raise fastapi.HTTPException(status_code=404, detail="Member not found")
    return {"message": "Member restored successfully"}

@router.delete(
    path="/{team_id}/members/{member_id}/permanent",
    name="teams:permanent_delete_member",
    status_code=fastapi.status.HTTP_204_NO_CONTENT,
    summary="Permanently delete member",
)
async def permanent_delete_member(
    team_id: int,
    member_id: int,
    current_user: User = fastapi.Depends(get_current_user),
    team_repo: TeamCRUDRepository = fastapi.Depends(get_repository(repo_type=TeamCRUDRepository)),
):
    if not await check_team_permission(current_user, team_id, team_repo.async_session, require_lead=True):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Team Lead or Administrator access required to manage members.",
        )
    success = await team_repo.permanent_delete_member(team_id, member_id)
    if not success:
        raise fastapi.HTTPException(status_code=404, detail="Member not found")
    return None
