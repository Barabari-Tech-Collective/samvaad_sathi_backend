"""Single-use, exact-operation upload grants; never expose general credentials."""
import hashlib,json,re,secrets
from cryptography.fernet import Fernet,MultiFernet,InvalidToken
from fastapi import HTTPException
from src.config.manager import settings
from src.repository.redis_client import async_redis
PREFIX='upload_grant_'
TTL=60

def cipher():
    if not settings.PRODUCT_COOKIE_API_ENABLED or not settings.CENTRAL_IDENTITY_CHECK_ENABLED:
        raise HTTPException(503,'Secure uploads are not enabled.')
    try:return MultiFernet([Fernet(k.strip()) for k in settings.PRODUCT_SESSION_ENCRYPTION_KEYS.split(',') if k.strip()])
    except (ValueError,TypeError):raise HTTPException(503,'Upload grant storage is unavailable.') from None

def key(value):return 'scoped-upload:'+hashlib.sha256(value.encode()).hexdigest()
def allowed(path,method):return method=='POST' and isinstance(path,str) and re.fullmatch(r'/api/bulk-ats/batches/[1-9][0-9]*/resumes',path) is not None

async def issue(token,path,method):
    if not allowed(path,method):raise HTTPException(403,'Unsupported upload operation.')
    value=PREFIX+secrets.token_urlsafe(32)
    payload=cipher().encrypt(json.dumps({'token':token,'path':path,'method':method}).encode())
    await async_redis.client.set(key(value),payload,ex=TTL)
    return value

async def consume(value,path,method):
    if not isinstance(value,str) or not re.fullmatch(r'upload_grant_[A-Za-z0-9_-]{43}',value):raise HTTPException(401,'Invalid upload grant.')
    encrypted=await async_redis.client.eval("local v=redis.call('GET',KEYS[1]); if v then redis.call('DEL',KEYS[1]); end; return v",1,key(value))
    if not encrypted:raise HTTPException(401,'Upload grant expired or already used. Retry the upload.')
    try:
        data=json.loads(cipher().decrypt(encrypted.encode() if isinstance(encrypted,str) else encrypted,ttl=TTL))
        if data['path']!=path or data['method']!=method or not allowed(path,method):raise ValueError()
        return data['token']
    except (InvalidToken,ValueError,KeyError,TypeError):raise HTTPException(403,'Upload grant does not match this operation.') from None
