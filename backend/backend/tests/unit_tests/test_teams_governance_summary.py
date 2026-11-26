import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.models.schemas.teams_v2 import (
    TeamResponse,
    TeamMemberResponse,
    TeamsSummaryResponse,
    RoleDistribution,
    TeamSummaryItem,
)
from src.repository.crud.team_v2 import (
    TeamCRUDRepository,
    categorize_role,
    format_access_role_display,
)
from src.api.routes import teams_v2


def test_categorize_role_mapping():
    assert categorize_role("creator") == "creator"
    assert categorize_role("lead") == "creator"
    assert categorize_role("Team Lead") == "creator"
    assert categorize_role("Sr Facilitator") == "sr_facilitator"
    assert categorize_role("senior facilitator") == "sr_facilitator"
    assert categorize_role("Jr Facilitator") == "jr_facilitator"
    assert categorize_role("junior facilitator") == "jr_facilitator"
    assert categorize_role("Team Member") == "team_member"
    assert categorize_role("member") == "team_member"
    assert categorize_role("recruiter") == "team_member"
    assert categorize_role("placement staff") == "team_member"


def test_format_access_role_display():
    assert format_access_role_display("creator") == "Creator"
    assert format_access_role_display("lead") == "Creator"
    assert format_access_role_display("team lead") == "Creator"
    assert format_access_role_display("sr facilitator") == "Sr Facilitator"
    assert format_access_role_display("senior facilitator") == "Sr Facilitator"
    assert format_access_role_display("jr facilitator") == "Jr Facilitator"
    assert format_access_role_display("junior facilitator") == "Jr Facilitator"
    assert format_access_role_display("member") == "Team Member"
    assert format_access_role_display("team member") == "Team Member"


def test_teams_summary_response_serialization():
    now = datetime.datetime.now(datetime.timezone.utc)
    summary = TeamsSummaryResponse(
        total_teams=2,
        total_active_members=5,
        role_distribution=RoleDistribution(
            creator=2,
            sr_facilitator=1,
            jr_facilitator=1,
            team_member=1,
        ),
        teams=[
            TeamSummaryItem(
                id=1,
                name="Program Team",
                lead_name="Akshay Srinivasan",
                lead_email="akshay@example.com",
                purposes=["Create interview", "Check student progress"],
                member_count=3,
                created_by_name="Super Admin",
                created_at=now,
            ),
            TeamSummaryItem(
                id=2,
                name="Placement Team",
                lead_name="Sindhura Ammaneni",
                lead_email="sindhura@example.com",
                purposes=["Bulk ATS"],
                member_count=2,
                created_by_name="Super Admin",
                created_at=now,
            ),
        ],
    )

    dump_by_alias = summary.model_dump(by_alias=True)
    # Check that both camelCase and snake_case keys are present
    assert dump_by_alias["total_teams"] == 2
    assert dump_by_alias["totalTeams"] == 2
    assert dump_by_alias["total_active_members"] == 5
    assert dump_by_alias["totalActiveMembers"] == 5
    assert dump_by_alias["role_distribution"]["creator"] == 2
    assert dump_by_alias["role_distribution"]["sr_facilitator"] == 1
    assert dump_by_alias["role_distribution"]["jr_facilitator"] == 1
    assert dump_by_alias["role_distribution"]["team_member"] == 1

    team1 = dump_by_alias["teams"][0]
    assert team1["id"] == 1
    assert team1["name"] == "Program Team"
    assert team1["lead_name"] == "Akshay Srinivasan"
    assert team1["leadName"] == "Akshay Srinivasan"
    assert team1["lead_email"] == "akshay@example.com"
    assert team1["member_count"] == 3
    assert team1["memberCount"] == 3
    assert team1["created_by_name"] == "Super Admin"
    assert team1["createdByName"] == "Super Admin"
    assert team1["purposes"] == ["Create interview", "Check student progress"]


def test_team_response_contains_governance_fields():
    now = datetime.datetime.now(datetime.timezone.utc)
    team_res = TeamResponse(
        id=10,
        name="Tech Team",
        description="Engineering",
        status="active",
        member_count=4,
        created_at=now,
        purposes=["Create interview", "Bulk ATS"],
        created_by_name="Super Admin",
    )
    data = team_res.model_dump(by_alias=True)
    assert data["purposes"] == ["Create interview", "Bulk ATS"]
    assert data["created_by_name"] == "Super Admin"
    assert data["createdByName"] == "Super Admin"
    assert data["member_count"] == 4
    assert data["memberCount"] == 4
    assert "created_at" in data
    assert "createdAt" in data


def test_team_member_response_contains_governance_fields():
    now = datetime.datetime.now(datetime.timezone.utc)
    member_res = TeamMemberResponse(
        id=1,
        user_id=101,
        team_id=10,
        name="Priya Sharma",
        email="priya@example.com",
        access_role="Sr Facilitator",
        purposes=["Check student progress"],
        colleges=["Govt College", "City Tech"],
        domain="IT",
        status="active",
        created_at=now,
        created_by_name="Super Admin",
    )
    data = member_res.model_dump(by_alias=True)
    assert data["access_role"] == "Sr Facilitator"
    assert data["accessRole"] == "Sr Facilitator"
    assert data["purposes"] == ["Check student progress"]
    assert data["colleges"] == ["Govt College", "City Tech"]
    assert data["domain"] == "IT"
    assert data["created_by_name"] == "Super Admin"
    assert data["createdByName"] == "Super Admin"
    assert data["status"] == "active"
    assert "created_at" in data
    assert "createdAt" in data


from fastapi import FastAPI
from src.api.routes import teams_v2


def test_teams_summary_endpoint_logic():
    now = datetime.datetime.now(datetime.timezone.utc)
    mock_summary_data = {
        "total_teams": 2,
        "totalTeams": 2,
        "total_active_members": 8,
        "totalActiveMembers": 8,
        "role_distribution": {
            "creator": 2,
            "sr_facilitator": 2,
            "jr_facilitator": 2,
            "team_member": 2,
        },
        "roleDistribution": {
            "creator": 2,
            "sr_facilitator": 2,
            "jr_facilitator": 2,
            "team_member": 2,
        },
        "teams": [
            {
                "id": 1,
                "name": "Program Team",
                "lead_name": "Akshay Srinivasan",
                "leadName": "Akshay Srinivasan",
                "lead_email": "akshay@example.com",
                "leadEmail": "akshay@example.com",
                "purposes": ["Create interview", "Check student progress"],
                "member_count": 5,
                "memberCount": 5,
                "created_by_name": "Super Admin",
                "createdByName": "Super Admin",
                "created_at": now,
                "createdAt": now,
            }
        ],
    }

    res = TeamsSummaryResponse(**mock_summary_data)
    assert res.total_teams == 2
    assert res.total_active_members == 8
    assert res.role_distribution.creator == 2
    assert res.role_distribution.sr_facilitator == 2
    assert len(res.teams) == 1
    assert res.teams[0].lead_name == "Akshay Srinivasan"
    assert res.teams[0].purposes == ["Create interview", "Check student progress"]
    assert res.teams[0].member_count == 5
    assert res.teams[0].created_by_name == "Super Admin"

    # Verify serialization has both camelCase and snake_case
    data = res.model_dump(by_alias=True)
    assert data["total_teams"] == 2
    assert data["totalTeams"] == 2
    assert data["total_active_members"] == 8
    assert data["totalActiveMembers"] == 8
    assert data["role_distribution"]["creator"] == 2
    assert data["role_distribution"]["sr_facilitator"] == 2
    assert data["teams"][0]["lead_name"] == "Akshay Srinivasan"
    assert data["teams"][0]["leadName"] == "Akshay Srinivasan"
    assert data["teams"][0]["member_count"] == 5
    assert data["teams"][0]["memberCount"] == 5
    assert data["teams"][0]["created_by_name"] == "Super Admin"
    assert data["teams"][0]["createdByName"] == "Super Admin"
