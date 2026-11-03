"""Idempotent synthetic accounts and scope, guarded to the dedicated local database."""
import json
from pathlib import Path
import sys
import psycopg2
from psycopg2.extras import Json

state = Path(sys.argv[1])
config = json.loads(state.read_text())
if config.get("POSTGRES_HOST") != "127.0.0.1" or config.get("POSTGRES_PORT") != "15432" or config.get("POSTGRES_DB") != "samvaad_local":
    raise SystemExit("Dedicated local database required")
with psycopg2.connect(host="127.0.0.1",port=15432,dbname="samvaad_local",user="samvaad_local",password=config["POSTGRES_PASSWORD"]) as connection:
    with connection.cursor() as sql:
        for index, name in enumerate(("student", "facilitator", "admin", "superadmin"), 1):
            sql.execute('INSERT INTO "user"(email,name,password_hash,central_identity_id,sampark_student_unique_id,is_onboarded,is_admin,is_super_admin,role,university,enrolled_course_ids,enrollment_source,enrollment_observed_at) VALUES(%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(central_identity_id) DO UPDATE SET email=EXCLUDED.email',
                        (name+"@example.com", name.title()+" Local", "!central-sso-only", "local-"+name, "local-student" if name=="student" else None, name in ("admin","superadmin"), name=="superadmin", None if name in ("student","facilitator") else "admin", "Local Test College", Json(["1"]), "sampark"))
        sql.execute("INSERT INTO team(name) SELECT 'Local Test Team' WHERE NOT EXISTS(SELECT 1 FROM team WHERE name='Local Test Team')")
        sql.execute('INSERT INTO team_member(team_id,user_id,access_role,course_ids,colleges) SELECT t.id,u.id,\'facilitator\',%s,%s FROM team t CROSS JOIN "user" u WHERE t.name=\'Local Test Team\' AND u.email=\'facilitator@example.com\' ON CONFLICT(team_id,user_id) DO NOTHING', (Json(["1"]),Json(["Local Test College"])))
        sql.execute('UPDATE "user" SET feature_permissions=%s WHERE email=\'facilitator@example.com\' AND feature_permissions IS NULL', (Json({"course_workspace":["read","update","export"],"interview_authoring":["read","create","update","generate","publish"],"practice_coaching":["read","create","update"],"practice_support":["read"],"human_interviews":["read","create","update"],"integrity_review":["read","create","update","publish","resolve_appeal"],"ai_evaluation":["read"]}),))
local = json.loads((state.parent / "local-secrets.json").read_text())
guide = state.parent / "TESTING.md"
guide.write_text(f"""# Samvaad local test workspace

Student: http://localhost:3000

Staff dashboard: http://localhost:3001/login

API documentation: http://localhost:8000/docs

Use `localhost` consistently for login and app URLs; do not mix it with `127.0.0.1` because browser cookies belong to hosts.

| Account | Email |
|---|---|
| Student | student@example.com |
| Facilitator (course 1, Local Test College) | facilitator@example.com |
| Admin | admin@example.com |
| Super admin | superadmin@example.com |

All four local accounts use this generated password: `{local['password']}`

Use separate browser profiles/private windows when comparing roles. Student and staff apps have separate token cookie names but share central SSO cookies on localhost.

These are synthetic records in a dedicated PostgreSQL 17 database. API and worker use the production AI/speech settings retrieved securely from the verified Samvaad host when production-providers.json is present; only the approved provider fields are imported. Real AI/speech requests may use account quota. Jev rollout gates remain unchanged. No production database, object bucket or queue is connected. Verification and reset emails are captured locally at http://localhost:18025. External WhatsApp notifications and Google OAuth/Drive callbacks are not configured.

Start by signing in as admin and authoring one manual practice template and one assessment template. Set question count, edit questions, submit and publish using the review workflow. Try draft edits, immutable publication, student practice/assessment routing and facilitator course restrictions. Then try AI authoring, mic permission denial/recovery, voice answer upload/transcription, reconnect, submission/report, companion hide/mute, Google Meet scheduling, role feature revocation and integrity review/appeal. Create additional test users or staff through the existing admin flows as needed.

The production ElevenLabs account has very little quota remaining (last observed: 12 credits). A minimal cached speech sample works; longer question playback can hit quota limits. DeepSeek and Groq accept the verified production credentials. No credits were purchased or subscription changed.

Start.command, Stop.command and Status.command in this folder control this local stack. Stop preserves test data and volumes. Use the source README for terminal commands.

This setup is a testing workspace, not evidence that every journey or external integration passed. See ACCEPTANCE.json, SSO-ACCEPTANCE.json, VOICE-ACCEPTANCE.json and regression-tests.log for checks actually executed. Automated browser access is disabled in Work settings; manual browser acceptance is yours to run. Physical mobile and native VoiceOver remain pending.
""")
guide.chmod(0o600)
