"""Bounded real-auth, live local API simulation; no AI/media or mutation calls."""
import asyncio
import ssl
import json
from pathlib import Path
import time
import httpx

STATE=Path(__file__).resolve().parents[3]/'samvaad-local-test'

async def main():
    config=json.loads((STATE/'product.json').read_text())
    if (config.get('POSTGRES_HOST'),config.get('POSTGRES_DB'),config.get('REDIS_PORT')) != ('127.0.0.1','samvaad_local','16379'):
        raise RuntimeError('Dedicated local environment required')
    password=json.loads((STATE/'local-secrets.json').read_text())['password']
    tokens={}
    async with httpx.AsyncClient(trust_env=False,timeout=30,verify=ssl.create_default_context(cafile=str(STATE/"cert.pem"))) as client:
        for actor in ('student','facilitator','admin','superadmin'):
            route='student-login' if actor=='student' else 'login'
            response=await client.post('http://127.0.0.1:8081/barabari-auth/api/auth/public/v1/'+route,json={'email':actor+'@example.com','password':password})
            if response.status_code != 200:
                raise RuntimeError('Local sign-in failed: '+actor)
            tokens[actor]=response.json()['data']['accessToken']
        cases=[]
        for actor in tokens:
            cases.extend([(actor,'/auth/profile',200),(actor,'/v2/course-workspace/me',403 if actor=='student' else 200),
                (actor,'/v2/feature-access/people',200 if actor in ('admin','superadmin') else 403)])
        cases.extend([('student','/practice/hub',200),('student','/v2/integrity/me/cases',200),
            ('student','/v2/human-interviews/me',200),('facilitator','/v2/course-workspace/courses/1/students',200),
            ('facilitator','/v2/course-workspace/courses/2/students',403),('anonymous','/auth/profile',403)])
        waves=[]
        for concurrency in (4,12):
            gate=asyncio.Semaphore(concurrency)
            async def one(case):
                actor,path,expected=case
                async with gate:
                    start=time.monotonic()
                    try:
                        response=await client.get('https://localhost:8440/api'+path,headers={'Authorization':'Bearer '+tokens[actor]} if actor!='anonymous' else {})
                        status=response.status_code
                        correct=status==expected
                        if path=='/auth/profile' and status==200:
                            data=response.json()
                            correct=correct and data.get('authorizedUser',{}).get('email')==actor+'@example.com'
                        return {'actor':actor,'path':path,'expected':expected,'status':status,'passed':correct,'ms':round((time.monotonic()-start)*1000,1)}
                    except httpx.HTTPError:
                        return {'actor':actor,'path':path,'expected':expected,'status':None,'passed':False,'ms':round((time.monotonic()-start)*1000,1)}
            rows=await asyncio.gather(*(one(case) for _ in range(3) for case in cases))
            times=sorted(row['ms'] for row in rows)
            waves.append({'concurrency':concurrency,'requests':len(rows),'passed':sum(r['passed'] for r in rows),'p95Ms':times[int(.95*(len(times)-1))],'maxMs':max(times),'failures':[r for r in rows if not r['passed']]})
    report={'scope':'Four real local identities, bounded read-only HTTP simulation; not browser, media, full journey or production capacity acceptance','waves':waves}
    Path('/tmp/samvaad-b21-concurrency.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    if any(w['failures'] for w in waves):
        raise RuntimeError('Concurrent acceptance had failures')

if __name__=='__main__':
    asyncio.run(main())
