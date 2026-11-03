"""Concurrent pacing creation against isolated local HTTPS and PostgreSQL."""
import asyncio, json, ssl
from pathlib import Path
from uuid import uuid4
import httpx, psycopg2
S = Path(__file__).resolve().parents[3] / 'samvaad-local-test'

async def main():
    config = json.loads((S/'product.json').read_text())
    assert config['POSTGRES_HOST'] == '127.0.0.1' and config['POSTGRES_DB'] == 'samvaad_local'
    secret = json.loads((S/'local-secrets.json').read_text())
    async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')),timeout=45) as client:
        for attempt in range(30):
            try:
                ready = await client.get('https://localhost:8440/api/health')
                if ready.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            await asyncio.sleep(1)
        else:
            raise AssertionError('Local API did not become ready')
        auth = 'https://localhost:8441/barabari-auth/api/auth/public/v1/'
        identities = {}
        for name, route in [('student','student-login'), ('facilitator','login')]:
            response = await client.post(auth+route, json={'email':name+'@example.com','password':secret['password']})
            assert response.status_code == 200
            identities[name] = {'Authorization':'Bearer '+response.json()['data']['accessToken']}
        client.cookies.clear()
        url = 'https://localhost:8440/api/pacing-practice/session'
        key = str(uuid4()); body = {'level':1,'clientRequestId':key}
        responses = await asyncio.gather(*[client.post(url,json=body,headers=identities['student']) for _ in range(20)])
        assert all(r.status_code == 201 for r in responses), [r.status_code for r in responses]
        ids = {r.json()['sessionId'] for r in responses}
        prompts = {r.json()['promptText'] for r in responses}
        assert len(ids) == len(prompts) == 1
        replay = await client.post(url,json=body,headers=identities['student'])
        assert replay.status_code == 201 and replay.json()['sessionId'] in ids
        mismatch = await client.post(url,json={**body,'level':2},headers=identities['student'])
        assert mismatch.status_code == 409
        other = await client.post(url,json=body,headers=identities['facilitator'])
        assert other.status_code == 201 and other.json()['sessionId'] not in ids
        denied = await client.get(url+'/'+str(other.json()['sessionId']),headers=identities['student'])
        assert denied.status_code == 404
        # Hold a real database row reservation; the HTTP submit must fail before
        # decoding or calling paid transcription providers.
        db = psycopg2.connect(host='127.0.0.1',port=15432,dbname='samvaad_local',user=config['POSTGRES_USERNAME'],password=config['POSTGRES_PASSWORD'])
        try:
            with db.cursor() as cursor:
                cursor.execute('SELECT id FROM pacing_practice_session WHERE id=%s FOR UPDATE', (next(iter(ids)),))
            busy = await client.post(url+'/'+str(next(iter(ids)))+'/submit', headers=identities['student'], files={'file':('test.wav',b'not-a-real-recording','audio/wav')})
            assert busy.status_code == 409, ('busy submission', busy.status_code)
        finally:
            db.rollback(); db.close()
        (S/'PACING-RECEIPT-ACCEPTANCE.json').write_text(json.dumps({'concurrent_requests':20,'one_session_one_prompt':True,'response_loss_replay':True,'payload_conflict_denied':True,'key_scoped_per_account':True,'other_account_receipt_denied':True,'concurrent_submission_denied_before_provider':True},indent=2))
        print('20 concurrent creates produced one session; replay, conflict and account isolation passed')

asyncio.run(main())
