"""Simulate an unavailable security marker without removing revocation records."""
import asyncio,json,ssl
from pathlib import Path
import httpx,redis
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
async def main():
 a=json.loads((S/'auth.json').read_text());secret=json.loads((S/'local-secrets.json').read_text())
 r=redis.Redis(host='127.0.0.1',port=16379,decode_responses=True);marker=r.get('AUTH_SECURITY_MARKER')
 assert marker and marker==a['SESSION_REVOCATION_STORE_MARKER']
 async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=30) as c:
  base='https://localhost:8441/barabari-auth/api/auth'
  login=await c.post(base+'/public/v1/login',json={'email':'admin@example.com','password':secret['password']});assert login.status_code==200
  h={'Authorization':'Bearer '+login.json()['data']['accessToken']};c.cookies.clear()
  try:
   r.set('AUTH_SECURITY_MARKER','local-acceptance-mismatch')
   denied=await c.post(base+'/public/v2/logout-all',headers=h)
   assert denied.status_code>=500,denied.status_code
  finally:
   # Restore the known original marker; no revocation keys were removed.
   r.set('AUTH_SECURITY_MARKER',marker)
  restored=await c.post(base+'/public/v2/logout-all',headers=h);assert restored.status_code==200
 (S/'REVOCATION-LOSS-ACCEPTANCE.json').write_text(json.dumps({'mismatched_marker_fails_closed':True,'original_marker_restored':True,'revocation_records_untouched':True},indent=2));print('Security-state mismatch fails closed; intact local store restored')
asyncio.run(main())
