# Persistent local test stack

Runs the current working copies of the Samvaad API and ARQ worker, central auth and Sampark services, student app and integrated dashboard. It uses PostgreSQL 17, Redis 7, RabbitMQ 4 and RustFS S3-compatible storage in a dedicated `colima-samvaad-local` Docker context. All application and container ports bind to loopback. Central server-to-server auth uses a verified local certificate; public CA roots remain trusted for real provider calls.

Only AI/speech keys and model settings are imported. On 21 October these were securely retrieved from the verified source host's effective settings; the launcher prefers the private `production-providers.json` file when present and otherwise reads `backend/backend/.env`. The export script verifies account identity, restricts retrieval to an allowlist, seals output with an ephemeral RSA/AES-GCM key before SSM transfer, and saves plaintext only in the private local workspace. Saved production database, identity keys, Redis, bucket, SMTP, OAuth and monitoring configuration are not imported. Existing environment files are never edited. This is a DEV runtime with real provider credentials, not a byte-for-byte copy of production configuration. Jev rollout gates remain unchanged. Provider tests use real account quota.

```sh
# From the backend repository; dependencies must already be installed.
colima start --profile samvaad-local --cpu 2 --memory 3 --disk 12
.venv/bin/python ops/local-test/manage.py start
.venv/bin/python ops/local-test/manage.py status
.venv/bin/python ops/local-test/acceptance.py
# Optional: one small real speech/AI call, using the approved provider credentials.
.venv/bin/python ops/local-test/acceptance.py --providers
.venv/bin/python ops/local-test/manage.py stop
```

On this Mac an incompatible user Docker Compose plugin is bypassed by calling the installed `/opt/homebrew/bin/docker-compose` executable directly. Container routing is explicitly pinned to the dedicated context. The original Colima VM/disk is not modified. `stop` preserves local test volumes/data; it does not remove containers/volumes or shut down the VM.

Open student `http://localhost:3000` and staff `http://localhost:3001/login`. Local credentials, private runtime configuration, logs and actual acceptance results are in the sibling `samvaad-local-test/` directory (directory mode `0700`). Read `TESTING.md` there for logins and scenarios. Do not commit/share that directory. Use `localhost` consistently and separate browser profiles/private windows when comparing roles.

The launcher seeds only explicitly named local accounts. Manual acceptance creates additional clearly named local templates; repeated runs retain those templates for inspection. Database migration commands are guarded to the dedicated local database. Application source remains editable; restart the stack after backend/central changes. Frontends run Next dev servers. This is an interactive test workspace, not production build/load acceptance.

Verification/reset email is captured in the loopback-only Mailpit inbox at `http://localhost:18025`, using test SMTP credentials and no external relay. Its setup follows the [official Mailpit Docker documentation](https://mailpit.axllent.org/docs/install/docker/).

Not configured here: external notifications/WhatsApp, Google OAuth/Drive callbacks, another product's CodeGuru service, in-platform media infrastructure, or an approved empirical Jev rollout. Human appointments retain the initial external Google Meet flow. Native screen-reader and physical-mobile acceptance remain pending. A passing API smoke report does not establish microphone/camera/browser usability or cover every workflow.

Storage supports real uploads, reads and presigned links locally; only synthesized question-audio prefix is public. This does not prove AWS IAM/KMS/retention behavior. Local endpoint configuration is explicit, loopback-only, requires credentials and is rejected outside `ENVIRONMENT=DEV`. RustFS setup follows its [official Docker documentation](https://github.com/rustfs/docs.rustfs.com/blob/main/content/en/installation/container/docker.md).

The synthetic voice smoke test uses a longer macOS-generated answer (no further ElevenLabs quota) through the actual practice session, writer lease, begin step, encrypted upload and worker transcription. It is not a microphone, camera or human-answer quality test. Regression checks cover mono/stereo WAV duration, sample widths, RIFF chunks, minimum/maximum durations and empty clips.

### Résumé operation HTTP acceptance

After applying local migrations and restarting the local API, run `.venv/bin/python ops/local-test/product.py ../samvaad-local-test/product.json resume-http` from the backend repository. This uses real local sign-in and PostgreSQL with a process-isolated synthetic extractor, then checks the running API can read/replay the completed test receipt. It does not call AI or upload to storage. Only its generated operation records are cleaned up. The local central-auth and API services must be running; credentials are read privately from the existing local test state.

### Guarded local runtime activation and cron acceptance

Run `.venv/bin/python ops/local-test/product.py ../samvaad-local-test/product.json resume-runtime` while local manual testing is idle. This checks dedicated local configuration, recorded API/worker PIDs and pending work before gracefully restarting those two services. It verifies live authenticated delivery status and real worker expiry cleanup using one synthetic expired résumé job, then deletes only that fixture. No AI or object upload is invoked. The preflight is a point-in-time check, not a maintenance lock; do not submit new jobs during this command. It refuses mismatched processes or pending provider work instead of killing unrelated processes or clearing queues. If a later check fails, already restarted services remain on the candidate. Logs may contain private runtime diagnostics; do not publish them.

### Real local résumé storage acceptance

Run `.venv/bin/python ops/local-test/product.py ../samvaad-local-test/product.json resume-objects` while local work is idle. The command verifies the exact loopback S3 endpoint and dedicated bucket/queue, temporarily stops only the recorded worker, creates a synthetic account, verifies actual bytes/privacy/dedup, and tests missing-bucket retry/exhaustion. It deletes only that account and its object prefix and relaunches the worker in cleanup. No AI or external AWS calls occur. This does not cover process crashes, real AWS policies or browser interactions. Completed arq cron markers are ignored only when their jobs have left the queue; active jobs still block the test.

The resume-objects runner also launches a dedicated synthetic child and deliberately exits it immediately after its local object PUT, before registry insertion. It verifies durable pending bytes survive and a retry registers the same stable key. The internal resume-crash-upload mode refuses non-loopback storage or accounts/payloads outside the test fixture. This is a real process interruption at one specific boundary, not full crash/partition coverage. Terminal orphan cleanup and production bucket version retention remain open.

### Cross-product and concurrency acceptance

` .venv/bin/python ops/local-test/integration_suite.py ` creates/uses only localhost:15432/ss_count_tests, using the dedicated local credentials privately. Fixtures rebuild that database schema. Never run two copies concurrently or point these fixtures at interactive/production data. Optional pytest filter arguments are accepted. Real SQL tests often inject auth identity and mock providers; they are not complete browser tests.

`.venv/bin/python ops/local-test/concurrent_acceptance.py` signs in the four seeded local identities and runs 108 GET requests at concurrency 4 and 12. It validates actor identity, scope and expected denial. It makes no AI/media calls and no explicit business mutations, though auth GETs may refresh identity/enrollment. This is bounded smoke concurrency, not a production capacity test. Evidence is written without tokens to /tmp/samvaad-b21-concurrency.json.
