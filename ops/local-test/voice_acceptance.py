"""Synthetic voice upload through actual local interview/storage/worker/Groq flow."""
import json
from pathlib import Path
import time
import uuid
import io
import subprocess
import tempfile
import wave
import httpx
import psycopg2

STATE=Path(__file__).resolve().parents[3] / "samvaad-local-test"


def main():
    cfg=json.loads((STATE / "product.json").read_text())
    password=json.loads((STATE / "local-secrets.json").read_text())["password"]
    report={"syntheticAudioOnly":True,"steps":{},"browserMicrophoneTest":False}
    def record(label,response,expected):
        report["steps"][label]={"status":response.status_code,"passed":response.status_code==expected}
        if response.status_code!=expected:
            if response.status_code in {400,403,409,422}:
                report["steps"][label]["detail"]=response.json().get("detail")
            raise RuntimeError(label+" failed with HTTP "+str(response.status_code))
    try:
        with psycopg2.connect(host="127.0.0.1",port=15432,user="samvaad_local",password=cfg["POSTGRES_PASSWORD"],dbname="samvaad_local") as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM job_profile WHERE interview_mode='practice' AND status='published' ORDER BY id DESC LIMIT 1")
                profile=cursor.fetchone()[0]
        with httpx.Client(timeout=60,trust_env=False) as client:
            response=client.post("http://localhost:8081/barabari-auth/api/auth/public/v1/student-login",json={"email":"student@example.com","password":password})
            record("login",response,200)
            client.headers["Authorization"]="Bearer "+response.json()["data"]["accessToken"]
            def api(method,path,**kwargs):
                return client.request(method,"http://localhost:8000/api"+path,**kwargs)
            response=api("POST","/tts/convert",json={"text":"Hi."})
            record("cached synthetic speech",response,200)
            if len(response.content)<100:
                raise RuntimeError("Synthetic speech unavailable")
            audio=response.content
            # Use macOS speech for the longer synthetic answer, avoiding extra
            # ElevenLabs quota. The cached API response above tests ElevenLabs.
            # This is transport/provider evidence, not human answer calibration.
            with tempfile.TemporaryDirectory(dir=STATE) as folder:
                source=Path(folder)/"sample.aiff"
                decoded=Path(folder)/"sample.wav"
                text="In my last project I built a Python API for a library. I used PostgreSQL to store records, added indexes for common queries, and measured response time before and after the change. I chose a simple design so the team could maintain it. I also added error handling and tested requests with missing fields."
                subprocess.run(["/usr/bin/say","-o",str(source),text],check=True,capture_output=True)
                subprocess.run(["/usr/bin/afconvert","-f","WAVE","-d","LEI16",str(source),str(decoded)],check=True,capture_output=True)
                audio=decoded.read_bytes()
            report["syntheticAnswerSource"]="macOS speech; ElevenLabs checked separately using cached sample"
            response=api("POST","/v2/interviews/create",json={"track":"Python","difficulty":"easy","course_id":"1","job_profile_id":profile,"client_contract":"fixed-round-v1"})
            record("curated practice start",response,201)
            interview=response.json()["interviewId"]
            response=api("POST","/v2/interviews/generate-questions",json={"interview_id":interview,"use_resume":False})
            record("published question selection",response,201)
            data=response.json()
            report["exactCreatorQuestionCount"]=data.get("count")==2
            question=data["questionIds"][0]
            response=api("POST","/interviews/session/claim",json={"interviewId":interview,"force":True})
            record("session writer lease",response,200)
            lease=response.json()["leaseToken"]
            response=api("POST","/interviews/begin",json={"interviewId":interview})
            record("begin prepared interview",response,200)
            response=api("POST","/interviews/question-attempts",json={"interviewId":interview,"questionId":question,"clientAttemptId":str(uuid.uuid4()),"sessionLeaseToken":lease})
            record("question attempt",response,201)
            attempt=response.json()["questionAttemptId"]
            response=api("POST","/transcribe-whisper",data={"question_attempt_id":str(attempt),"language":"en","session_lease_token":lease},files={"file":("synthetic.wav",audio,"audio/wav")})
            record("durable audio upload",response,202)
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                response=api("GET",f"/audio/attempts/{attempt}/transcription")
                if response.status_code!=200:
                    record("transcription status",response,200)
                data=response.json()
                if data.get("isTerminal") or data.get("is_terminal"):
                    report["transcription"]={key:data.get(key) for key in ("status","saved","wordCount","evidenceStatus","evidenceReason")}
                    break
                time.sleep(2)
            report["passed"]=report.get("exactCreatorQuestionCount",False) and report.get("transcription",{}).get("saved") is True and report.get("transcription",{}).get("status")=="transcribed"
    except Exception as error:
        report["passed"]=False
        report["errorType"]=type(error).__name__
        # No credential, token, raw provider exception or transcript is logged.
    (STATE / "VOICE-ACCEPTANCE.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return 0 if report["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
