"""Synthetic local staff matrix; never copies or changes production assignments."""
import asyncio,json,ssl
from pathlib import Path
import httpx,psycopg2
from psycopg2.extras import Json
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
ACTORS=[('program','PROGRAM_MANAGER','team member','Program'),('placement','PLACEMENT_COM','team member','Placement'),('mentor','FACILITATOR','facilitator','Program'),('custom','FACILITATOR','team member','Program')]
def seed():
 p=json.loads((S/'product.json').read_text());assert p['POSTGRES_HOST']=='127.0.0.1' and str(p['POSTGRES_PORT'])=='15432' and p['POSTGRES_DB']=='samvaad_local'
 with psycopg2.connect(host='127.0.0.1',port=15432,dbname='samvaad_local',user=p['POSTGRES_USERNAME'],password=p['POSTGRES_PASSWORD']) as db:
  with db.cursor() as q:
   q.execute("INSERT INTO team(name) SELECT 'B123 synthetic staff' WHERE NOT EXISTS(SELECT 1 FROM team WHERE name='B123 synthetic staff')")
   for actor,role,membership,purpose in ACTORS:
    # These synthetic accounts can survive a local password-reset test.  Keep
    # their hash in sync with the known facilitator fixture on every run, or a
    # previous local reset makes a role-matrix failure look like an RBAC bug.
    q.execute('''INSERT INTO barabari_edtech_auth.users_identity(unique_id,first_name,last_name,email,password_hash,is_active,is_deleted,email_verified,student_profile_saved,role_id)
     SELECT %s,%s,'Acceptance',%s,u.password_hash,true,false,true,true,r.role_id FROM barabari_edtech_auth.users_identity u CROSS JOIN barabari_edtech_auth.roles r WHERE u.email='facilitator@example.com' AND r.role_code=%s
     ON CONFLICT(unique_id) DO UPDATE SET first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,email=EXCLUDED.email,password_hash=EXCLUDED.password_hash,is_active=true,is_deleted=false,email_verified=true,student_profile_saved=true,role_id=EXCLUDED.role_id,must_set_password=false''',('local-b123-'+actor,actor,'b123-'+actor+'@example.com',role))
    q.execute('''INSERT INTO barabari_edtech_auth.product_permissions(user_id,product_id) SELECT u.user_id,p.product_id FROM barabari_edtech_auth.users_identity u CROSS JOIN barabari_edtech_auth.products p WHERE u.unique_id=%s ON CONFLICT DO NOTHING''',('local-b123-'+actor,))
    q.execute('''INSERT INTO "user"(email,name,password_hash,central_identity_id,is_onboarded,is_admin,is_super_admin,university,feature_permissions) VALUES(%s,%s,'!central-sso-only',%s,true,false,false,'Local Test College',%s) ON CONFLICT(central_identity_id) DO NOTHING''',('b123-'+actor+'@example.com',actor,'local-b123-'+actor,Json({'course_workspace':['read']})))
    q.execute('''INSERT INTO team_member(team_id,user_id,access_role,course_ids,colleges,domain,purposes) SELECT t.id,u.id,%s,%s,%s,%s,%s FROM team t CROSS JOIN "user" u WHERE t.name='B123 synthetic staff' AND u.central_identity_id=%s ON CONFLICT(team_id,user_id) DO NOTHING''',(membership,Json(['1']),Json(['Local Test College']),purpose,Json([purpose]),'local-b123-'+actor))
async def main():
 seed();secret=json.loads((S/'local-secrets.json').read_text());checks=[];base='https://127.0.0.1:3442'
 async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=40) as c:
  for actor,role,_,_ in ACTORS:
   c.cookies.clear();h={'Origin':base,'X-Barabari-Request':'1'}
   login=await c.post(base+'/api/public/v1/login',json={'email':'b123-'+actor+'@example.com','password':secret['password']},headers=h);assert login.status_code==200,(actor,'login',login.status_code)
   state=(await c.get(base+'/api/session')).json();assert state['role']==role
   h.update({'X-Barabari-CSRF':state['csrf'],'X-Barabari-Account':state['uniqueId']})
   for path,expected in [('/api/samvaad/v2/course-workspace/courses/1/students',200),('/api/samvaad/v2/course-workspace/courses/2/students',403),('/api/samvaad/v2/feature-access/people',403),('/api/samvaad/v2/human-interviews/courses/1',403)]:
    r=await c.get(base+path,headers=h);assert r.status_code==expected,(actor,path,r.status_code)
    checks.append({'actor':actor,'path':path,'status':r.status_code})
   page=await c.get(base+'/dashboard/samvaad/courses');assert page.status_code==200,(actor,'page',page.status_code)
   checks.append({'actor':actor,'path':'shared course page','status':200})
 (S/'B123-STAFF-ACCEPTANCE.json').write_text(json.dumps({'checks':checks,'scope':'synthetic central roles plus product memberships; HTTP protocol, not rendered UI'},indent=2));print(f'{len(checks)} expanded staff checks passed')
asyncio.run(main())
