"""Reset concurrency and revocation against a synthetic account and local mail capture."""
import asyncio,json,ssl
from pathlib import Path
import httpx,redis,psycopg2
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
async def main():
 p=json.loads((S/'product.json').read_text());assert p['POSTGRES_DB']=='samvaad_local' and p['POSTGRES_HOST']=='127.0.0.1'
 secret=json.loads((S/'local-secrets.json').read_text());email='b123-custom@example.com';base='https://127.0.0.1:3442';auth='https://localhost:8441/barabari-auth/api/auth';r=redis.Redis(host='127.0.0.1',port=16379,decode_responses=True)
 db=psycopg2.connect(host='127.0.0.1',port=15432,dbname='samvaad_local',user=p['POSTGRES_USERNAME'],password=p['POSTGRES_PASSWORD'])
 with db.cursor() as q:q.execute('SELECT password_hash FROM barabari_edtech_auth.users_identity WHERE email=%s',(email,));original=q.fetchone()[0]
 try:
  async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=30) as c:
   h={'Origin':base,'X-Barabari-Request':'1'}
   login=await c.post(base+'/api/public/v1/login',json={'email':email,'password':secret['password']},headers=h);assert login.status_code==200
   state=(await c.get(base+'/api/session')).json();h.update({'X-Barabari-CSRF':state['csrf'],'X-Barabari-Account':state['uniqueId']})
   req=await c.post(base+'/api/public/v1/forgot-password/request',json={'email':email},headers=h);assert req.status_code==200,req.status_code
   code=r.get('PWD_RESET_CODE-'+email);assert code
   body={'email':email,'code':code,'newPassword':'B123Testing!123','confirmPassword':'B123Testing!123'}
   # The race reaches real Redis and real database; direct public endpoint avoids
   # the second BFF session read masking the code-consumption race.
   outcomes=await asyncio.gather(*[c.post(auth+'/public/v1/forgot-password/reset',json=body) for _ in range(2)])
   assert sum(x.status_code==200 for x in outcomes)==1,[x.status_code for x in outcomes]
   assert (await c.get(base+'/api/session')).json()['authenticated'] is False
   replay=await c.post(auth+'/public/v1/forgot-password/reset',json=body);assert replay.status_code!=200
   login=await c.post(auth+'/public/v1/login',json={'email':email,'password':body['newPassword']});assert login.status_code==200
   (S/'B123-RECOVERY-ACCEPTANCE.json').write_text(json.dumps({'local_reset_request':True,'one_concurrent_winner':True,'old_session_revoked':True,'replay_denied':True,'new_password_works':True},indent=2))
   print('5 live reset/recovery checks passed; synthetic password restored')
 finally:
  with db.cursor() as q:q.execute('UPDATE barabari_edtech_auth.users_identity SET password_hash=%s WHERE email=%s',(original,email))
  db.commit();db.close()
asyncio.run(main())
