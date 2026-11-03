import asyncio,json,ssl
from pathlib import Path
import httpx
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'
async def main():
 secret=json.loads((S/'local-secrets.json').read_text());root='https://localhost:8440/api';tokens={}
 async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=30) as c:
  for actor in ['admin','facilitator']:
   r=await c.post('https://localhost:8441/barabari-auth/api/auth/public/v1/login',json={'email':actor+'@example.com','password':secret['password']});assert r.status_code==200
   tokens[actor]={'Authorization':'Bearer '+r.json()['data']['accessToken']}
  c.cookies.clear()
  people=await c.get(root+'/v2/feature-access/people',headers=tokens['admin']);assert people.status_code==200
  rows=people.json();rows=rows if isinstance(rows,list) else rows.get('items',rows.get('people',[]))
  me=await c.get(root+'/auth/profile',headers=tokens['facilitator']);assert me.status_code==200
  person=next(x for x in rows if x['id']==me.json()['userId'])
  original=person['permissions'];version=person['version'];uid=person['id']
  # Restore NULL legacy policy via the isolated DB after the API test; API deliberately accepts only explicit objects.
  try:
   put=await c.put(root+f'/v2/feature-access/people/{uid}',json={'expected_version':version,'permissions':{},'reason':'Local acceptance temporary revocation'},headers=tokens['admin']);assert put.status_code==200,put.text[:150]
   denied=await c.get(root+'/v2/course-workspace/courses/1/students',headers=tokens['facilitator']);assert denied.status_code==403
   stale=await c.put(root+f'/v2/feature-access/people/{uid}',json={'expected_version':version,'permissions':{},'reason':'Local stale version acceptance'},headers=tokens['admin']);assert stale.status_code==409
   granted=await c.put(root+f'/v2/feature-access/people/{uid}',json={'expected_version':version+1,'permissions':{'course_workspace':['read']},'reason':'Local acceptance restore scoped read'},headers=tokens['admin']);assert granted.status_code==200
   assert (await c.get(root+'/v2/course-workspace/courses/1/students',headers=tokens['facilitator'])).status_code==200
   assert (await c.get(root+'/v2/course-workspace/courses/2/students',headers=tokens['facilitator'])).status_code==403
  finally:
   import psycopg2
   config=json.loads((S/'product.json').read_text())
   assert config['POSTGRES_DB']=='samvaad_local' and config['POSTGRES_HOST']=='127.0.0.1'
   with psycopg2.connect(host='127.0.0.1',port=15432,dbname='samvaad_local',user=config['POSTGRES_USERNAME'],password=config['POSTGRES_PASSWORD']) as db:
    with db.cursor() as cur:cur.execute('UPDATE "user" SET feature_permissions=%s::jsonb, feature_permissions_version=feature_permissions_version+1 WHERE id=%s AND email=%s',(json.dumps(original) if original is not None else None,uid,'facilitator@example.com'))
 (S/'PERMISSION-MUTATION-ACCEPTANCE.json').write_text(json.dumps({'revocation_immediate':True,'stale_version_denied':True,'read_restored':True,'cross_course_still_denied':True,'original_policy_restored':True},indent=2))
 print('Live permission revocation/version/course checks passed')
asyncio.run(main())
