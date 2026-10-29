"""Export only approved production provider settings, sealed to a local ephemeral key.

SSM command/output never contain plaintext secrets. Production services are not restarted.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import time
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

STATE=Path(__file__).resolve().parents[3] / "samvaad-local-test"
KEYS="OPENAI_API_KEY OPENAI_MODEL LLM_PROVIDER DEEPSEEK_API_KEY DEEPSEEK_BASE_URL DEEPSEEK_MODEL GROQ_API_KEY GROQ_BASE_URL GROQ_WHISPER_MODEL STT_PROVIDER ELEVENLABS_API_KEY ELEVENLABS_VOICE_ID JEV_API_KEY JEV_API_BASE_URL JEV_MODEL".split()


def aws(*args):
    result=subprocess.run(["aws","--profile","samvaad-login","--region","ap-south-1","--output","json",*args],text=True,capture_output=True,check=True)
    return json.loads(result.stdout)


def main():
    if aws("sts","get-caller-identity")["Account"]!="351405418851":
        raise SystemExit("Wrong source account")
    private=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    public=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    script=f'''import os,sys,json,base64,secrets,subprocess
from pathlib import Path
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
try:
 keys={KEYS!r}
 pid=int(subprocess.check_output(['systemctl','show','samvaad_sathi_backend.service','--property=MainPID','--value'],text=True).strip())
 if pid<=0: raise RuntimeError('source inactive')
 for entry in Path('/proc/'+str(pid)+'/environ').read_bytes().split(b'\\0'):
  key,sep,value=entry.partition(b'=')
  if sep and key.decode() in keys: os.environ[key.decode()]=value.decode()
 os.chdir('/home/ubuntu/apps/samvaad_sathi_backend/backend/backend')
 sys.path.insert(0,os.getcwd())
 from src.config.manager import settings
 data={{key:str(getattr(settings,key,'')) for key in keys}}
 key=AESGCM.generate_key(bit_length=256); nonce=secrets.token_bytes(12)
 public=serialization.load_pem_public_key({public!r}.encode())
 wrapped=public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
 payload=AESGCM(key).encrypt(nonce,json.dumps(data).encode(),b'samvaad-local-providers-v1')
 print(json.dumps({{name:base64.b64encode(value).decode() for name,value in [('wrapped',wrapped),('nonce',nonce),('payload',payload)]}}))
except Exception as error:
 print('provider_export_failed:'+type(error).__name__)
 sys.exit(1)
'''
    # Script embeds a public key and allowlist only; shell never sees a secret value.
    command="/home/ubuntu/apps/samvaad_sathi_backend/venv/bin/python - <<'LOCAL_PROVIDER_EXPORT'\n"+script+"\nLOCAL_PROVIDER_EXPORT"
    result=aws("ssm","send-command","--instance-ids","i-051d97f973abe0ff0","--document-name","AWS-RunShellScript","--comment","Read-only provider configuration sealed for authorized local testing","--parameters",json.dumps({"commands":[command]}))
    command_id=result["Command"]["CommandId"]
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        time.sleep(2)
        try:
            result=aws("ssm","get-command-invocation","--command-id",command_id,"--instance-id","i-051d97f973abe0ff0")
        except subprocess.CalledProcessError:
            continue
        if result["Status"] in {"Pending","InProgress","Delayed"}:
            continue
        if result["Status"]!="Success":
            raise SystemExit("Read-only provider export failed; no plaintext output displayed")
        output=result["StandardOutputContent"]
        envelope=json.loads(next(line for line in output.splitlines() if line.startswith('{"wrapped"')))
        raw={name:base64.b64decode(value) for name,value in envelope.items()}
        key=private.decrypt(raw["wrapped"],padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
        values=json.loads(AESGCM(key).decrypt(raw["nonce"],raw["payload"],b'samvaad-local-providers-v1'))
        if set(values)!=set(KEYS) or not all(isinstance(v,str) for v in values.values()):
            raise SystemExit("Unexpected provider schema")
        fd=os.open(STATE / "production-providers.json",os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,"w") as target:
            json.dump(values,target)
        evidence={"accountId":"351405418851","instanceId":"i-051d97f973abe0ff0","commandId":command_id,"keys":KEYS,"scope":"provider settings only; encrypted in SSM output; no service restart"}
        (STATE / "provider-source.json").write_text(json.dumps(evidence,indent=2))
        print("Production provider configuration saved privately; no values displayed")
        return
    raise SystemExit("Provider export timed out; retry with a new local key")


if __name__=="__main__":
    main()
