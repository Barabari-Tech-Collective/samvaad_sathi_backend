"""HTTPS protocol acceptance using real cookies; not rendered browser acceptance."""
import asyncio,json,ssl
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import httpx
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
async def main():
 secret=json.loads((S/'local-secrets.json').read_text());home='https://localhost:3441';api='https://localhost:3440/api/proxy';checks=[]
 async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=30) as c:
  r=await c.get(api+'/auth/sso/login',params={'return_to':'/profile'})
  assert r.status_code==307
  authorize=r.headers['location']
  cold=await c.get(authorize)
  assert cold.status_code in [302,307] and '/student-login' in cold.headers['location'],('cold',cold.status_code)
  checks.append('cold entry goes to central student login')
  login=await c.post(home+'/api/public/v1/student-login',json={'email':'student@example.com','password':secret['password']},headers={'Origin':home,'X-Barabari-Request':'1'})
  assert login.status_code==200
  r=await c.get(authorize)
  assert r.status_code in [302,307],('authorize',r.status_code)
  callback=r.headers['location'];assert callback.startswith(api+'/auth/sso/callback?')
  r=await c.get(callback)
  assert r.status_code in [302,307],('callback',r.status_code)
  dest=r.headers['location'];ticket=parse_qs(urlsplit(dest).fragment).get('handoff',[''])[0]
  assert len(ticket)==43,('handoff missing',urlsplit(dest).path)
  h={'Origin':'https://localhost:3440'}
  complete=await c.post(api+'/auth/session/complete',json={'ticket':ticket},headers=h)
  assert complete.status_code==200,('complete',complete.status_code)
  body=complete.json();assert body['return_to']=='/profile' and body['authenticated']
  checks.append('warm central session completes PKCE handoff and preserves destination')
  status=await c.get(api+'/auth/session',headers=h)
  assert status.status_code==200 and status.json()['authenticated']
  checks.append('product session cookie authenticated')
  for path in ['v2/graduation/me', 'v2/practice-dashboard/consents/me', 'v2/human-interviews/me']:
   feature=await c.get(api+'/'+path,headers={**h,'X-Session-Principal':status.json()['principal']})
   assert feature.status_code==200,('feature read',path,feature.status_code)
   checks.append('authenticated profile feature read: '+path)

  replay=await c.post(api+'/auth/session/complete',json={'ticket':ticket},headers={**h,'X-CSRF-Token':body['csrf_token']})
  assert replay.status_code==401
  checks.append('handoff replay rejected')
 (S/'HANDOFF-ACCEPTANCE.json').write_text(json.dumps({'passed':checks,'scope':'HTTP protocol; no rendered browser or SameSite simulation'},indent=2));print(str(len(checks))+' real HTTPS handoff/profile checks passed')
asyncio.run(main())
