"""B7 HTTP/SQL acceptance, isolated local identities and evidence only."""
import asyncio, json, ssl
from pathlib import Path
from datetime import date
import httpx, psycopg2
S=Path(__file__).resolve().parents[3]/'samvaad-local-test'

async def main():
    config=json.loads((S/'product.json').read_text());secret=json.loads((S/'local-secrets.json').read_text())
    assert config['POSTGRES_HOST']=='127.0.0.1' and config['POSTGRES_DB']=='samvaad_local'
    db=psycopg2.connect(host='127.0.0.1',port=15432,dbname='samvaad_local',user=config['POSTGRES_USERNAME'],password=config['POSTGRES_PASSWORD'])
    originals={};checks=[]
    try:
        with db.cursor() as q:
            for name in ['b123-program','b123-placement']:
                q.execute('SELECT id,feature_permissions FROM "user" WHERE email=%s',(name+'@example.com',));uid,policy=q.fetchone();originals[uid]=policy
                op='verify_course' if name.endswith('program') else 'verify_placement'
                q.execute('UPDATE "user" SET feature_permissions=%s::jsonb WHERE id=%s',(json.dumps({'graduation':['read',op,'approve'],'course_workspace':['read']}),uid))
            q.execute('SELECT id FROM "user" WHERE email=%s',('student@example.com',));student=q.fetchone()[0]
        db.commit()
        async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=40) as c:
            for _ in range(30):
                try:
                    if (await c.get('https://localhost:8440/api/health')).status_code==200:break
                except httpx.TransportError:pass
                await asyncio.sleep(1)
            tokens={}
            for name in ['student','b123-program','b123-placement','admin']:
                r=await c.post('https://localhost:8441/barabari-auth/api/auth/public/v1/'+('student-login' if name=='student' else 'login'),json={'email':name+'@example.com','password':secret['password']})
                assert r.status_code==200,(name,r.status_code)
                tokens[name]={'Authorization':'Bearer '+r.json()['data']['accessToken']}
            c.cookies.clear()
            sync=await c.post('https://localhost:8440/api/v2/course-workspace/enrollments/sync-from-sampark',headers=tokens['admin'])
            assert sync.status_code==200,('local enrollment sync',sync.status_code)
            root='https://localhost:8440/api/v2/graduation';url=root+f'/courses/1/students/{student}'
            async def get(name='admin'):
                r=await c.get(url,headers=tokens[name]);assert r.status_code==200,('detail',name,r.status_code);return r.json()
            async def post(name,action,version,expected=200,**kw):
                body={'action':action,'expected_version':version,'reason':'Isolated B7 acceptance verification',**kw}
                r=await c.post(url,headers=tokens[name],json=body);assert r.status_code==expected,(action,name,r.status_code,r.text[:100]);checks.append(f'{name} {action}: {expected}');return r
            roster=await c.get(root+'/courses/1/students',headers=tokens['b123-program'])
            assert roster.status_code==200 and any(x['student_id']==student for x in roster.json()['items']);checks.append('assigned learner visible without practice sharing consent')
            data=await get();version=data['record']['version']
            if data['record']['state']=='graduated':
                await post('admin','reopen',version);version+=1
            await post('student','verify_course',version,403,evidence_reference='Local evidence only')
            r=await c.get(root+f'/courses/2/students/{student}',headers=tokens['b123-program']);assert r.status_code in (403,404);checks.append('other course denied')
            await post('b123-program','verify_course',version,evidence_reference='Local course completion certificate reviewed');version+=1
            await post('b123-program','verify_placement',version,403,evidence_reference='Local placement confirmation',employer='Local Example',job_title='Engineer',joined_on=date.today().isoformat())
            await post('admin','approve',version,409)
            await post('b123-placement','verify_placement',version,evidence_reference='Local employer joining confirmation reviewed',employer='Local Example',job_title='Engineer',joined_on=date.today().isoformat());version+=1
            await post('b123-placement','approve',version,403)
            await post('admin','approve',version-1,409)
            body={'action':'approve','expected_version':version,'reason':'Independent review of both local verification records'}
            raced=await asyncio.gather(*[c.post(url,headers=tokens['admin'],json=body) for _ in range(2)])
            assert sorted(r.status_code for r in raced)==[200,409];checks.append('competing approvals: one winner');version+=1
            r=await c.get(root+'/me',headers=tokens['student']);assert r.status_code==200
            item=next(x for x in r.json()['items'] if x['course_id']=='1');assert item['state']=='graduated' and 'placement_evidence' not in item and 'approval' not in item;checks.append('learner sees approval without staff evidence')
            await post('b123-program','verify_course',version,409,evidence_reference='Local replacement evidence')
            with db.cursor() as q:
                try:
                    q.execute('UPDATE graduation_event SET reason=%s WHERE student_id=%s',('tamper',student))
                    raise AssertionError('audit mutation unexpectedly allowed')
                except psycopg2.errors.RaiseException:db.rollback()
            checks.append('database audit edits denied')
            await post('admin','reopen',version);version+=1
            with db.cursor() as q:
                q.execute('UPDATE "user" SET feature_permissions=%s::jsonb WHERE email=%s', ('{}','b123-program@example.com'))
            db.commit()
            revoked=await c.get(url,headers=tokens['b123-program']);assert revoked.status_code==403;checks.append('personal permission revocation immediately denies read')
            final=await get();assert final['record']['state']=='draft' and not final['record']['course_verified'] and not final['record']['placement_verified'];assert len(final['history'])>=4;checks.append('reopen clears current verification and preserves history')
        (S/'B7-GRADUATION-ACCEPTANCE.json').write_text(json.dumps({'checks':checks,'count':len(checks),'scope':'synthetic local learner, real API/database; no browser acceptance'},indent=2))
        print(f'{len(checks)} graduation acceptance checks passed')
    finally:
        db.rollback()
        with db.cursor() as q:
            for uid,policy in originals.items():q.execute('UPDATE "user" SET feature_permissions=%s::jsonb WHERE id=%s',(json.dumps(policy) if policy is not None else None,uid))
        db.commit();db.close()

asyncio.run(main())
