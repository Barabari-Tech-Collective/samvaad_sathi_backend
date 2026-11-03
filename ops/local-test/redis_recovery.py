"""Validate durability on the dedicated local Redis container only."""
import json,subprocess,uuid,time
import manage
local=json.loads((manage.STATE/'local-secrets.json').read_text())
env=manage.clean_env()|{'LOCAL_DB_PASSWORD':local['db'],'LOCAL_BROKER_PASSWORD':local['broker'],'LOCAL_STORAGE_PASSWORD':local['storage'],'LOCAL_STORAGE_MASTER_KEY':local['storageMasterKey']}
manage.run(manage.DOCKER+['up','-d','redis'],env,'redis-durability-config')
base=['docker','--context','colima-samvaad-local','exec','samvaad-local-test-redis-1','redis-cli','--raw']
def command(*args):return subprocess.check_output(base+list(args),text=True).strip()
key='acceptance-revocation-'+str(uuid.uuid4())
for _ in range(60):
 if command('PING')=='PONG': break
 time.sleep(.2)
assert command('SET',key,'revoked','EX','600')=='OK'
subprocess.run(['docker','--context','colima-samvaad-local','restart','samvaad-local-test-redis-1'],check=True,stdout=subprocess.DEVNULL)
for _ in range(30):
 try:
  if command('PING')=='PONG':break
 except subprocess.CalledProcessError:pass
 time.sleep(.2)
assert command('GET',key)=='revoked'
config=command('CONFIG','GET','appendonly','appendfsync','maxmemory-policy').splitlines();values=dict(zip(config[::2],config[1::2]))
assert values=={'appendonly':'yes','appendfsync':'always','maxmemory-policy':'noeviction'},values
command('DEL',key)
(manage.STATE/'REDIS-RECOVERY.json').write_text(json.dumps({'restart_retained_revocation':True,'config':values,'scope':'isolated local container; no production failover claim'},indent=2))
print('Local Redis restart retained revocation; AOF always + noeviction verified')
