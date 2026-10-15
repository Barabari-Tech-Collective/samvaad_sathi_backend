# Bulk ATS production deployment checklist

Date: 2026-10-15

## Pre-deploy

- [ ] Create a Google Cloud OAuth 2.0 **Web application** client and enable the Google Drive API.
- [ ] Register the exact callback: `https://<backend-host>/api/bulk-ats/google-drive/callback`.
- [ ] Configure `GOOGLE_DRIVE_OAUTH_CLIENT_ID`, `GOOGLE_DRIVE_OAUTH_CLIENT_SECRET`, `GOOGLE_DRIVE_OAUTH_REDIRECT_URI`, and `GOOGLE_DRIVE_OAUTH_FRONTEND_RETURN_URL` in the production secret store.
- [ ] Generate a dedicated Fernet key and configure `GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY` plus key ID. Never replace the key until stored credentials have been re-encrypted or disconnected.
- [ ] Keep the requested Google scope at `drive.readonly`; complete Google's OAuth consent-screen/testing-to-production requirements.
- [ ] Apply Alembic migrations through `b8d5f9a3c722` and verify there is one migration head.
- [ ] Choose document-service deployment:
  - Docker Compose: use the included `clamav` service; API and worker images include Tesseract English/Hindi packs.
  - EC2/systemd with at least 4 GB RAM: run `deploy/install-bulk-ats-document-services.sh` as root.
  - Smaller host: run the installer with `INSTALL_LOCAL_CLAMAV=false` and configure a remote ClamAV daemon. Do not run local ClamAV alongside this application on the documented 2 GB host.
- [ ] Configure `BULK_ATS_OCR_ENABLED=True`, `BULK_ATS_OCR_LANGUAGE=eng+hin`, `BULK_ATS_CLAMAV_HOST`, `BULK_ATS_CLAMAV_PORT=3310`, and `BULK_ATS_CLAMAV_FAIL_CLOSED=True`.
- [ ] Back up the database and record the current application/worker image IDs.
- [ ] Confirm frontend and backend origins are HTTPS and allowed by CORS.

Google recommends the server-side authorization-code flow for offline access, exact redirect-URI matching, non-guessable state, secure refresh-token storage, and handling revocation. This implementation follows those properties and keeps access tokens ephemeral:

- <https://developers.google.com/identity/protocols/oauth2/web-server>
- <https://developers.google.com/identity/protocols/oauth2/resources/best-practices>

The official ClamAV container includes its own daemon health check and signature startup delay:

- <https://github.com/Cisco-Talos/clamav/blob/main/README.Docker.md>

## Deploy

- [ ] Deploy database migration first.
- [ ] Start ClamAV and wait until its container/service is healthy.
- [ ] Verify `tesseract --list-langs` includes `eng` and `hin` on both API and worker hosts.
- [ ] Start API and worker with the same OAuth encryption key and Google client secrets.
- [ ] Deploy the frontend.
- [ ] Confirm the Bulk ATS header shows Jev, OCR, malware scan and Private Drive as ready.

## Smoke tests

- [ ] Connect a recruiter Google account and confirm only read-only Drive consent is requested.
- [ ] Import one private Drive PDF owned by that account and confirm it completes.
- [ ] Import a private file the account cannot access and confirm only that candidate fails.
- [ ] Disconnect Drive, rerun the private candidate and confirm it requests reconnection without exposing a token.
- [ ] Upload a standard PDF, a Hindi/English scanned PDF, a DOCX, an encrypted PDF and a malformed DOCX.
- [ ] Use the EICAR test file in an isolated non-production batch and confirm fail-closed malware rejection. Never use a live malicious sample.
- [ ] Restart the worker and verify previously connected Drive access still works, proving encrypted refresh-token persistence.
- [ ] Run `scripts/bulk_ats_load_test.py` with 100 unique synthetic resumes and inspect p50/p95, terminal counts and provider fallbacks.

## Monitoring and rollback triggers

Monitor for at least 30 minutes after the first production batch:

- Roll back if candidate terminal failure exceeds 5% excluding intentionally invalid fixtures.
- Roll back if the worker crashes/restarts repeatedly or host memory exceeds 90% for five minutes.
- Disable private Drive ingestion if OAuth callback or refresh failures exceed 2%.
- Stop new uploads if ClamAV is unhealthy; fail-closed behavior should prevent unscanned storage.
- Disable OCR temporarily if candidate p95 exceeds the agreed batch SLA or worker memory becomes unsafe; scanned resumes will route to manual review.

Rollback order:

1. Disable new Bulk ATS uploads at the ingress/feature-access layer.
2. Restore the previous API/worker/frontend images.
3. Keep migration `b8d5f9a3c722` in place during application rollback; it is additive and harmless to the previous version.
4. Do not drop encrypted OAuth credentials until the incident is understood. Revoke and delete them only if credential exposure is suspected.
