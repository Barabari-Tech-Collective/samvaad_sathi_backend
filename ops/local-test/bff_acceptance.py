import asyncio,json,ssl
from pathlib import Path
import httpx
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
async def main():
 secret=json.loads((S/'local-secrets.json').read_text());checks=[]
 for name in ['student','facilitator','admin','superadmin']:
  base='https://localhost:3441' if name=='student' else 'https://127.0.0.1:3442'
  async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=45,base_url=base) as c:
   h={'Origin':base,'X-Barabari-Request':'1'}
   response=await c.post('/api/public/v1/'+('student-login' if name=='student' else 'login'),json={'email':name+'@example.com','password':secret['password']},headers=h)
   assert response.status_code==200,(name,'login',response.status_code,response.text[:150])
   assert 'accessToken' not in response.json().get('data',{})
   status=await c.get('/api/session');assert status.status_code==200,(name,status.status_code)
   data=status.json();assert data['authenticated']
   h.update({'X-Barabari-CSRF':data['csrf'],'X-Barabari-Account':data['uniqueId']})
   profile=await c.get('/api/samvaad/me',headers=h)
   assert profile.status_code==200,(name,'profile',profile.status_code,profile.text[:150])
   assert 'token' not in profile.json().get('authorizedUser',{})
   page=await c.get('/products' if name=='student' else '/dashboard')
   assert page.status_code in [200,307],(name,'page',page.status_code)
   stale=await c.get('/api/samvaad/me',headers={**h,'X-Barabari-Account':'wrong'})
   assert stale.status_code==409
   logged=await c.post('/api/owner/v1/logout-all',json={},headers=h)
   assert logged.status_code==200,(name,'logout',logged.status_code)
   assert (await c.get('/api/session')).json()['authenticated'] is False
   checks.append({'role':name,'login_profile_route_stale_account_logout':'passed'})
 (S/'BFF-ACCEPTANCE.json').write_text(json.dumps(checks,indent=2));print('4 compiled HTTPS role journeys passed')
asyncio.run(main())
