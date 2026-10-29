"""Configure/restart only the existing isolated local stack for HTTPS session acceptance."""
import json, os, signal, subprocess, sys
from pathlib import Path
import manage
from cryptography.fernet import Fernet
S=manage.STATE; D=manage.DESKTOP; H=manage.HERE
local=json.loads((S/'local-secrets.json').read_text())
# Existing secrets stay local; only random candidate secrets are created.
import secrets
local.setdefault('browserSessionKey',secrets.token_urlsafe(48))
local.setdefault('productSessionKey',Fernet.generate_key().decode())
manage.private_json('local-secrets.json',local)
home='https://localhost:3441';staff='https://127.0.0.1:3442';student='https://localhost:3440';api='https://localhost:8440';auth='https://localhost:8441'
a=json.loads((S/'auth.json').read_text());p=json.loads((S/'product.json').read_text())
a.update(HOME_SESSION_ORIGIN=home,STAFF_SESSION_ORIGIN=staff,BROWSER_SESSION_BFF_KEY=local['browserSessionKey'],SSO_CENTRAL_AUTHORIZE_URL=home+'/api/public/v2/authorize',SSO_CENTRAL_LOGIN_URL=home+'/student-login',SSO_LOGIN_PAGE_URL=home+'/student-login',SSO_ALLOWED_REDIRECT_URIS=p['SAMPARK_PRODUCT_UNIQUE_ID']+'='+student+'/api/proxy/auth/sso/callback',NODE_EXTRA_CA_CERTS=str(S/'cert.pem'))
p.update(PRODUCT_SESSION_ENABLED='True',PRODUCT_COOKIE_API_ENABLED='True',SSO_V2_ENABLED='True',SSO_TRANSACTION_ENABLED='True',PRODUCT_SESSION_ENCRYPTION_KEYS=local['productSessionKey'],AUTH_SERVICE_PUBLIC_BASE_URL=home,SSO_REDIRECT_URI=student+'/api/proxy/auth/sso/callback',COGNITO_POST_LOGIN_REDIRECT_URL=student+'/auth/signup',COGNITO_POST_LOGOUT_REDIRECT_URL=student+'/auth/signup',ALLOWED_ORIGINS=json.dumps([student,home,staff]),LOCAL_TLS_KEY=str(S/'key.pem'),LOCAL_TLS_CERT=str(S/'cert.pem'))
manage.private_json('auth.json',a);manage.private_json('product.json',p)
env=manage.clean_env()|{'NODE_EXTRA_CA_CERTS':str(S/'cert.pem')}
student_env=env|{'NEXT_PUBLIC_CENTRAL_HOME_ORIGIN':home,'NEXT_PUBLIC_API_BASE_URL':student+'/api/proxy','NEXT_PUBLIC_APP_URL':student,'SAMVAAD_API_UPSTREAM_URL':api+'/api','AUTH_BASE_URL':auth,'NEXT_PUBLIC_AUTH_MODE':'server-session','NEXT_PUBLIC_POSTHOG_KEY':''}
staff_env=env|{'NEXT_PUBLIC_CENTRAL_HOME_ORIGIN':home,'NEXT_PUBLIC_AUTH_MODE':'server-session','NEXT_PUBLIC_API_URL':'/api','AUTH_SERVICE_ORIGIN':auth,'SAATHI_SERVICE_ORIGIN':auth,'NOTIFICATION_SERVICE_ORIGIN':auth,'SAMVAAD_SERVICE_ORIGIN':api,'HOME_SESSION_ORIGIN':home,'STAFF_SESSION_ORIGIN':staff,'BROWSER_SESSION_BFF_KEY':local['browserSessionKey'],'NEXT_PUBLIC_SAMVAAD_API_URL':api+'/api','NEXT_PUBLIC_ADMIN_BASE_URL':staff,'NEXT_PUBLIC_SAMVAAD_SAATHI_URL':student,'NEXT_PUBLIC_GOOGLE_OAUTH_CLIENT_ID':''}
manage.private_json('student-session-env.json',student_env);manage.private_json('dashboard-session-env.json',staff_env)
if len(sys.argv)>1 and sys.argv[1]=='configure':
 print('HTTPS candidate configuration prepared; secrets not printed.');sys.exit()
processes=json.loads((S/'processes.json').read_text())
for label in ['auth-service','api','student','dashboard','dashboard-staff']:
 pid=processes.get(label)
 if pid:
  command=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
  if str(H) in command or 'node_modules/next/dist/bin/next' in command:
   os.killpg(pid,signal.SIGTERM)
import time;time.sleep(2)
manage.launch(['node',H/'central.cjs',S/'auth.json',D/'Sampark-Saathi-Backend','auth-service','serve'],env,'auth-service')
manage.launch([sys.executable,H/'product.py',S/'product.json','api'],env,'api')
for label in ['student', 'dashboard']:
 manage.launch(['node', H/'next_https.cjs', label], env, label)
print('Compiled HTTPS services started: Home 3441, staff 3442, student 3440, API 8440. Build both frontends before starting.')
