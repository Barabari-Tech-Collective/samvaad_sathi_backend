import asyncio,json,ssl
from pathlib import Path
import httpx
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
async def main():
 secret=json.loads((S/'local-secrets.json').read_text());root='https://localhost:8440/api';results=[]
 async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=45) as c:
  login=await c.post('https://localhost:8441/barabari-auth/api/auth/public/v1/login',json={'email':'admin@example.com','password':secret['password']});assert login.status_code==200
  h={'Authorization':'Bearer '+login.json()['data']['accessToken']};c.cookies.clear()
  batch=await c.post(root+'/bulk-ats/batches',data={'name':'Local scoped upload acceptance','role_name':'Python developer','role_description':'Develop Python APIs with clear tests, reliable database queries and practical communication skills.','required_skills':'Python, SQL'},headers=h)
  assert batch.status_code in [200,201],(batch.status_code,batch.text[:200]);bid=batch.json()['id'];path=f'/api/bulk-ats/batches/{bid}/resumes'
  async def grant():
   r=await c.post(root+'/auth/upload-grant',json={'path':path,'method':'POST'},headers=h)
   assert r.status_code==200,(r.status_code,r.text[:100]);return {'Authorization':'Bearer '+r.json()['grant']}
  g=await grant()
  # Slightly over the file limit: transport reaches authenticated file validation,
  # which must reject without storage or AI work.
  r=await c.post('https://localhost:8440'+path,files={'files':('synthetic.pdf',b'%PDF-'+b'x'*(10*1024*1024),'application/pdf')},headers=g)
  assert r.status_code==202,(r.status_code,r.text[:150]);assert 'file too large' in r.text
  results.append('10 MB multipart transport reaches bounded file validation')
  replay=await c.post('https://localhost:8440'+path,files={'files':('tiny.pdf',b'%PDF-test','application/pdf')},headers=g)
  assert replay.status_code==401;results.append('grant replay rejected')
  g=await grant()
  wrong=await c.get(root+'/auth/profile',headers=g);assert wrong.status_code==403;results.append('grant cannot read profile')
  g=await grant()
  revoke=await c.post('https://localhost:8441/barabari-auth/api/auth/public/v2/logout-all',headers=h);assert revoke.status_code==200
  denied=await c.post('https://localhost:8440'+path,files={'files':('tiny.pdf',b'%PDF-test','application/pdf')},headers=g)
  assert denied.status_code in [401,403];results.append('revocation after grant issuance enforced')
 (S/'UPLOAD-GRANT-ACCEPTANCE.json').write_text(json.dumps({'passed':results,'synthetic_batch_id':bid},indent=2));print('4 live scoped upload checks passed; no AI requests made')
asyncio.run(main())
