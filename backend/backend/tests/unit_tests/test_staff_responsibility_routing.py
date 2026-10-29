from types import SimpleNamespace as NS
from src.api.routes.practice_dashboard import resolve_dashboard_role

def user():
 return NS(is_admin=False,is_super_admin=False,email='synthetic@test',_token_source='central',_token_role='FACILITATOR')
def member(purposes=None,domain=None,name='Generic team',role='team member'):
 return (NS(access_role=role,purposes=purposes,domain=domain),NS(name=name))
def test_program_membership_enables_scoped_coaching_persona():
 assert resolve_dashboard_role(user(),[member(['Program'])])=='facilitator'
def test_placement_membership_does_not_depend_on_team_name():
 assert resolve_dashboard_role(user(),[member(['Placement'])])=='placements'
def test_explicit_program_not_overridden_by_placement_display_name():
 assert resolve_dashboard_role(user(),[member(['Program'],name='Placement Team')])=='facilitator'
def test_unknown_custom_label_does_not_grant_reporting():
 assert resolve_dashboard_role(user(),[member(['Unclassified'])]) is None
def test_domain_compatibility():
 assert resolve_dashboard_role(user(),[member(domain='Partnership')])=='placements'
def test_staff_title_alone_is_not_scoped_responsibility():
 assert resolve_dashboard_role(user(),[]) is None

def test_dual_responsibility_retains_both_task_contexts():
 item=member(['Program','Placement'])
 assert resolve_dashboard_role(user(),[item])=='placements'
 assert resolve_dashboard_role(user(),[item],preferred='facilitator')=='facilitator'
def test_preference_cannot_create_missing_responsibility():
 assert resolve_dashboard_role(user(),[member(['Placement'])],preferred='facilitator')=='placements'
