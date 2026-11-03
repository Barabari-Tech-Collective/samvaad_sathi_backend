"""Exercise real BFF grant issuance and direct-upload CORS without AI calls."""
import asyncio,json,ssl
from pathlib import Path
import httpx
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
async def main():
 secret=json.loads((S/'local-secrets.json').read_text());base='https://127.0.0.1:3442';api='https://localhost:8440'
 bid=json.loads((S/'UPLOAD-GRANT-ACCEPTANCE.json').read_text())['synthetic_batch_id'];path=f'/api/bulk-ats/batches/{bid}/resumes'
 async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=45) as c:
  h={'Origin':base,'X-Barabari-Request':'1'}
  login=await c.post(base+'/api/public/v1/login',json={'email':'admin@example.com','password':secret['password']},headers=h);assert login.status_code==200
  state=(await c.get(base+'/api/session')).json();h.update({'X-Barabari-CSRF':state['csrf'],'X-Barabari-Account':state['uniqueId']})
  g=await c.post(base+'/api/samvaad/auth/upload-grant',json={'path':path,'method':'POST'},headers=h)
  assert g.status_code==200,('BFF grant',g.status_code)
  value=g.json()['grant'];assert value.startswith('upload_grant_') and 'accessToken' not in g.json()
  preflight=await c.options(api+path,headers={'Origin':base,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,requestid'})
  assert preflight.status_code==200 and preflight.headers['access-control-allow-origin']==base
  c.cookies.clear()
  upload=await c.post(api+path,headers={'Origin':base,'Authorization':'Bearer '+value},files={'files':('synthetic.pdf',b'%PDF-'+b'x'*(10*1024*1024),'application/pdf')})
  assert upload.status_code==202 and 'file too large' in upload.text
  assert upload.headers['access-control-allow-origin']==base
 (S/'UPLOAD-BFF-ACCEPTANCE.json').write_text(json.dumps({'cookie_BFF_grant':True,'direct_CORS_preflight':True,'bounded_large_multipart':True,'general_browser_bearer':False},indent=2));print('BFF grant, direct CORS and large upload validation passed')
asyncio.run(main())
