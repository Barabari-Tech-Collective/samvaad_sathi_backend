"""Read/mutate synthetic local identities only; never prints credentials or tokens."""
import asyncio,json,ssl
from pathlib import Path
import httpx
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
AUTH='https://localhost:8441/barabari-auth/api/auth';API='https://localhost:8440/api'
async def main():
 secrets=json.loads((S/'local-secrets.json').read_text());checks=[]
 async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=30) as c:
  for name in ['student','facilitator','admin','superadmin']:
   route='student-login' if name=='student' else 'login'
   response=await c.post(AUTH+'/public/v1/'+route,json={'email':name+'@example.com','password':secrets['password']})
   assert response.status_code==200,(name,'login',response.status_code)
   token=response.json()['data']['accessToken'];c.cookies.clear()
   headers={'Authorization':'Bearer '+token}
   me=await c.get(API+'/auth/profile',headers=headers)
   checks.append({'account':name,'check':'product profile','status':me.status_code})
   assert me.status_code==200,(name,'profile',me.status_code)
   assert not any(k in me.json().get('authorizedUser',{}) for k in ['token','refreshToken'])
   wrong=await c.post(AUTH+'/public/v1/'+('login' if name=='student' else 'student-login'),json={'email':name+'@example.com','password':secrets['password']})
   assert wrong.status_code==403,(name,'cross role',wrong.status_code)
   checks.append({'account':name,'check':'opposite login denied','status':wrong.status_code})
   surface='home' if name=='student' else 'staff';origin='https://localhost:3441' if name=='student' else 'https://127.0.0.1:3442'
   machine={'X-Browser-Session-Key':secrets['browserSessionKey']}
   body={'token':token,'surface':surface,'origin':origin}
   created=await c.post(AUTH+'/internal/v2/browser-sessions/establish',json=body,headers=machine)
   assert created.status_code==200,(name,'establish',created.status_code)
   data=created.json();body={'handle':data['handle'],'surface':surface,'origin':origin}
   inspected=await c.post(AUTH+'/internal/v2/browser-sessions/inspect',json=body,headers=machine)
   assert inspected.status_code==200
   checks.append({'account':name,'check':'database-backed browser session','status':200})
   denial=await c.post(AUTH+'/internal/v2/browser-sessions/inspect',json={**body,'surface':'staff' if name=='student' else 'home','origin':'https://127.0.0.1:3442' if name=='student' else 'https://localhost:3441'},headers=machine)
   assert denial.status_code==401
   revoked=await c.post(AUTH+'/public/v2/logout-all',headers=headers)
   assert revoked.status_code==200
   retry=await c.post(AUTH+'/public/v2/logout-all',headers=headers)
   assert retry.status_code==200
   denied=await c.get(API+'/auth/profile',headers=headers)
   assert denied.status_code in [401,403],(name,'revocation',denied.status_code)
   checks.append({'account':name,'check':'revocation reaches product and retry confirms','status':denied.status_code})
 (S/'SESSION-ACCEPTANCE.json').write_text(json.dumps({'checks':checks,'passed':len(checks)},indent=2))
 print(f'{len(checks)} database-backed HTTPS checks passed')
asyncio.run(main())
