from types import SimpleNamespace as Item
from datetime import date, timedelta
import pytest
from fastapi import HTTPException
from src.api.routes.graduation import Change
from src.services.graduation import transition, project, allowed


def actor(id=1, admin=False, operations=None):
    return Item(id=id,is_admin=admin,is_super_admin=False,role=None,feature_permissions={"graduation":operations or ["read","verify_course","verify_placement"]})


def record():
    return Item(student_id=9,course_id="1",version=0,state="draft",course_evidence=None,placement_evidence=None,approval=None)


def change(action,version=0,**kw):
    values=dict(expected_version=version,action=action,reason="Checked source records",evidence_reference="Verified source reference")
    if action=="verify_placement":values.update(employer="Example Ltd",job_title="Engineer",joined_on=date.today())
    return Change(**(values|kw))


def ready():
    row=record();transition(row,change('verify_course'),actor());transition(row,change('verify_placement',1),actor(2));return row


def test_full_lifecycle_and_private_projection():
    row=ready();assert row.state=='ready'
    transition(row,change('approve',2),actor(3,True));assert row.state=='graduated'
    public=project(row);assert public['graduated_at'] and 'placement_evidence' not in public and 'approval' not in public
    transition(row,change('reopen',3),actor(3,True));assert row.state=='draft' and not row.course_evidence and not row.approval


@pytest.mark.parametrize('action',['approve','reopen'])
def test_personal_grant_cannot_turn_staff_into_admin(action):
    assert not allowed(actor(operations=['read',action]),action)


def test_verifier_cannot_approve_own_evidence():
    with pytest.raises(HTTPException) as exc:transition(ready(),change('approve',2),actor(1,True))
    assert exc.value.status_code==409


def test_incomplete_not_approved():
    with pytest.raises(HTTPException):transition(record(),change('approve'),actor(3,True))


def test_self_verification_denied():
    with pytest.raises(HTTPException) as exc:transition(record(),change('verify_course'),actor(9))
    assert exc.value.status_code==403


def test_stale_write_does_not_mutate():
    row=ready()
    with pytest.raises(HTTPException):transition(row,change('verify_course',0),actor())
    assert row.version==2 and row.state=='ready'


def test_graduated_evidence_locked():
    row=ready();transition(row,change('approve',2),actor(3,True))
    with pytest.raises(HTTPException):transition(row,change('verify_course',3),actor())
    assert row.state=='graduated'


def test_null_legacy_permissions_do_not_grant_new_feature():
    user=actor();user.feature_permissions=None
    assert not allowed(user,'read')


@pytest.mark.parametrize('kwargs',[{'reason':'          '},{'evidence_reference':None},{'joined_on':date.today()+timedelta(days=1)},{'employer':None},{'job_title':None},{'expected_version':True}])
def test_invalid_evidence_rejected(kwargs):
    with pytest.raises(ValueError):change('verify_placement',**kwargs)
