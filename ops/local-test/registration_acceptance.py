"""Real local Home-cookie registration journey; no browser rendering claimed.

Creates a distinct synthetic example.com learner, retained for inspection in the
isolated database. Verification codes are read from local Redis, never printed.
"""
import asyncio, json, secrets, ssl, time
from pathlib import Path
import httpx, redis
S = Path(__file__).resolve().parents[3] / 'samvaad-local-test'

async def main():
    config = json.loads((S / 'product.json').read_text())
    assert config['POSTGRES_HOST'] == '127.0.0.1' and config['POSTGRES_DB'] == 'samvaad_local'
    base = 'https://localhost:3441'
    suffix = str(time.time_ns())
    email = 'b123-registration-' + suffix + '@example.com'
    password = 'TestA1!' + secrets.token_hex(5)
    checks = []
    async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(S/'cert.pem')), timeout=30) as c:
        h = {'Origin':base, 'X-Barabari-Request':'1'}
        response = await c.post(base+'/api/public/v1/student-account', headers=h, json={'email':email,'password':password,'confirmPassword':password})
        assert response.status_code == 201, ('create', response.status_code)
        assert not any(k in response.json().get('data',{}) for k in ('accessToken','refreshToken'))
        checks.append('new account creates cookie session without browser bearer tokens')
        session = (await c.get(base+'/api/session')).json()
        assert session['authenticated'] and session['role'] == 'STUDENT'
        h.update({'X-Barabari-CSRF':session['csrf'],'X-Barabari-Account':session['uniqueId']})
        prefix = base + '/api/student/v1/me/'
        bootstrap = (await c.get(prefix+'bootstrap',headers=h)).json()['data']
        assert bootstrap['nextStep'] == 'VERIFY_EMAIL'
        checks.append('unverified account resumes verification')
        premature = await c.put(prefix+'product-interests',headers=h,json={'requestedProductUniqueIds':[]})
        assert premature.status_code in (400,403)
        checks.append('unverified account cannot skip verification')
        r = redis.Redis(host='127.0.0.1',port=16379,decode_responses=True)
        code = r.get('EMAIL_VERIFY_CODE-'+session['uniqueId']); assert code
        wrong = '000000' if code != '000000' else '999999'
        denied = await c.post(prefix+'verify-email-code',headers=h,json={'code':wrong})
        assert denied.status_code == 400
        checks.append('incorrect verification code rejected')
        verified = await c.post(prefix+'verify-email-code',headers=h,json={'code':code})
        assert verified.status_code == 200
        assert verified.json()['data']['nextStep'] == 'PRODUCT_INTERESTS'
        checks.append('correct verification advances onboarding')
        selected = await c.put(prefix+'product-interests',headers=h,json={'requestedProductUniqueIds':[]})
        assert selected.status_code == 200 and selected.json()['data']['nextStep'] == 'COMMON_PROFILE'
        checks.append('Samvaad-only learner advances without unavailable products')
        profile = {'firstName':'Local','lastName':'Acceptance','phone':'+199'+suffix[-9:],'city':'Test City','state':'Maharashtra','gender':'Prefer not to say','SourceOfReferral':'Local acceptance','institutionName':'Local Test College','highestLevelOfEducation':'Undergraduate','ugProgramType':'Full time','ugCourse':'Computer Science','graduationYear':'2026'}
        incomplete = await c.put(prefix+'profile',headers=h,json={**profile,'city':''})
        assert incomplete.status_code == 400
        checks.append('incomplete common profile rejected')
        saved = await c.put(prefix+'profile',headers=h,json=profile)
        assert saved.status_code == 200 and saved.json()['data']['nextStep'] == 'COMPLETE', ('profile',saved.status_code)
        checks.append('complete common profile finishes central onboarding')
        version = saved.json()['data']['profile']['version']
        edited = await c.put(prefix+'profile',headers=h,json={**profile,'city':'Updated Test City'})
        assert edited.status_code == 200 and edited.json()['data']['profile']['version'] == version+1
        checks.append('completed learner can update profile with a new version')
        assert (await c.get(base+'/products',headers=h)).status_code == 200
        assert (await c.get(base+'/dashboard',headers=h)).status_code == 404
        checks.append('Home products accessible while staff surface stays unavailable')
        logged = await c.post(base+'/api/owner/v1/logout-all',headers=h,json={})
        assert logged.status_code == 200 and not (await c.get(base+'/api/session')).json()['authenticated']
        checks.append('new learner global logout invalidates Home session')
    (S/'B123-REGISTRATION-ACCEPTANCE.json').write_text(json.dumps({'checks':checks,'synthetic_email':email,'scope':'local HTTPS protocol, not browser UI'},indent=2))
    print(f'{len(checks)} local registration/onboarding checks passed')

asyncio.run(main())
