"""Exercise the running local stack with synthetic identities. No production records.

Creates clearly named local templates; --providers also makes small metered AI/TTS calls.
"""
import argparse
import json
from pathlib import Path
import time
import httpx

STATE = Path(__file__).resolve().parents[3] / "samvaad-local-test"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", action="store_true")
    args = parser.parse_args()
    password = json.loads((STATE / "local-secrets.json").read_text())["password"]
    results = []
    def check(label, value):
        results.append({"check":label,"passed":bool(value)})
    with httpx.Client(timeout=60, trust_env=False) as client:
        tokens = {}
        for name in ("student","facilitator","admin","superadmin"):
            route = "student-login" if name=="student" else "login"
            response = client.post("http://localhost:8081/barabari-auth/api/auth/public/v1/"+route,json={"email":name+"@example.com","password":password})
            check(name+" password login",response.status_code==200)
            if response.status_code!=200:
                continue
            tokens[name] = response.json()["data"]["accessToken"]
            response = client.get("http://localhost:8000/api/me",headers={"Authorization":"Bearer "+tokens[name]})
            check(name+" central identity and product bridge",response.status_code==200)
            response = client.get("http://localhost:8000/api/v2/course-workspace/me",headers={"Authorization":"Bearer "+tokens[name]})
            check(name+" workspace authorization",response.status_code==(403 if name=="student" else 200))
        def api(method,path,actor="admin",**kwargs):
            return client.request(method,"http://localhost:8000/api"+path,headers={"Authorization":"Bearer "+tokens[actor]},**kwargs)
        response = api("POST","/v2/course-workspace/enrollments/sync-from-sampark")
        check("real local Sampark enrollment sync",response.status_code==200)
        check("facilitator course 1 roster",api("GET","/v2/course-workspace/courses/1/students",actor="facilitator").status_code==200)
        check("facilitator course 2 denied",api("GET","/v2/course-workspace/courses/2/students",actor="facilitator").status_code==403)
        import boto3
        config=json.loads((STATE / "product.json").read_text())
        storage=boto3.client("s3",region_name="ap-south-1",endpoint_url="http://127.0.0.1:19000",aws_access_key_id="samvaad-local",aws_secret_access_key=config["AWS_SECRET_ACCESS_KEY"])
        bucket=config["AWS_S3_BUCKET_NAME"]
        for key,anonymous_status in (("local-acceptance/private.txt",403),("Samvaad-Saathi/tts-audio/local-acceptance.txt",200)):
            storage.put_object(Bucket=bucket,Key=key,Body=b"synthetic local acceptance data")
            body=storage.get_object(Bucket=bucket,Key=key)["Body"]
            try:
                check("authorized storage read "+key,body.read()==b"synthetic local acceptance data")
            finally:
                body.close()
            response=client.get(f"http://127.0.0.1:19000/{bucket}/{key}")
            check("anonymous storage policy "+key,response.status_code==anonymous_status)
        for mode, base in (("practice","practice-templates"),("assessment","assessment-roles")):
            title=f"Local acceptance {mode} {int(time.time())}"
            response=api("POST","/v2/"+base,json={"job_name":title,"job_description":"Local software engineering interview for end-to-end testing.","authoring_mode":"manual","questions_per_interview":2,"skills":["Python"]})
            check(mode+" manual draft creation",response.status_code==201)
            if response.status_code!=201:
                continue
            profile=response.json()["id"]
            question_path=f"/v2/practice-templates/{profile}/questions" if mode=="practice" else f"/v2/job-profiles/{profile}/questions"
            for text in ("Explain a project you built and one technical decision you made.","Describe how you would debug a slow API endpoint."):
                response=api("POST",question_path,json={"question":text,"level":1,"difficulty":"easy","expected_answer":"Explain context, concrete actions, tradeoffs and a measurable outcome."})
                check(mode+" manual question save",response.status_code==201)
            response=api("PUT",f"/v2/{base}/{profile}",json={"job_name":title+" edited"})
            check(mode+" draft edit",response.status_code==200)
            response=api("POST",f"/v2/{base}/{profile}/submit")
            check(mode+" submit",response.status_code==200)
            response=api("PATCH",f"/v2/{base}/{profile}/review",json={"status":"published"})
            check(mode+" publish",response.status_code==200)
            if response.status_code==200:
                response=api("PUT",f"/v2/{base}/{profile}",json={"job_name":"Published change must fail"})
                check(mode+" publication immutable",response.status_code==409)
        if args.providers:
            response=api("POST","/tts/convert",actor="student",json={"text":"Hi."})
            check("live ElevenLabs audio",response.status_code==200 and len(response.content)>100 and response.headers.get("X-TTS-Status") in {"success","cached"})
            response=api("POST","/v2/assessment-roles",json={"job_name":"Local AI provider test","job_description":"Junior Python developer interview. Ask concise technical questions about functions and APIs.","authoring_mode":"ai","questions_per_interview":1,"skills":["Python"]})
            if response.status_code==201:
                profile=response.json()["id"]
                response=api("POST",f"/v2/job-profiles/{profile}/questions/generate",json={"levels":[{"level":1,"count":1}]})
                check("AI generation enqueued",response.status_code==202)
                if response.status_code==202:
                    deadline=time.monotonic()+90
                    count=0
                    while time.monotonic()<deadline:
                        response=api("GET",f"/v2/job-profiles/{profile}/questions")
                        count=response.json().get("total_questions",response.json().get("totalQuestions",0)) if response.status_code==200 else 0
                        if count: break
                        time.sleep(2)
                    check("live AI generation completed by local worker",count==1)
        for path in ("/practice/hub","/v2/integrity/me/cases","/v2/human-interviews/me"):
            check("student "+path,api("GET",path,actor="student").status_code==200)
    report={"scope":"running local services, synthetic identities; no production records","checks":results,"passed":sum(x["passed"] for x in results),"failed":sum(not x["passed"] for x in results),"providerCallsRequested":args.providers,"pending":["physical mobile and native VoiceOver","full microphone/camera and every interview journey","external notification and Google OAuth/Drive integration"]}
    (STATE / "ACCEPTANCE.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return 1 if report["failed"] else 0


if __name__=="__main__":
    raise SystemExit(main())
