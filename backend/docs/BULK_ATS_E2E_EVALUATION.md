# Bulk ATS End-to-End Evaluation

Date: 2026-10-15

## Outcome

The previously disconnected Bulk ATS path is now implemented across the frontend, API, worker, scoring pipeline, Jev, and database schema. The configured Jev key was verified with one live request using synthetic candidate data; all eight expected typed answers were returned.

The automated code-level evaluation passes. The strengthening pass adds optional evaluation intent, JD-quality analysis, evidence-backed confidence, a Jev/DeepSeek provider split, OCR/document hardening, fail-closed malware scanning, per-recruiter Google Drive OAuth, retention enforcement, audit events, outcome capture, and calibration/fairness/load-test tooling. A true deployed-stack run still requires a reachable test database, Redis/arq worker, S3 bucket, DeepSeek secret, Google OAuth client credentials, and running OCR/ClamAV services. Those services are not running locally, so this review does not claim that a hosted 100-resume batch or hiring accuracy has been validated.

## Root causes found and fixed

1. **No frontend feature:** there was no `/bulk-ats` page or browser-to-API integration. The recruiter workflow, upload/import UI, polling, result table, candidate drawer, notes, and rerun action are now present.
2. **Jev was not configured:** the local ignored environment now contains the supplied key. A synthetic live smoke test reached Jev and returned the complete answer schema.
3. **Batches had no usable role rubric:** batches created in the UI carried only a role name, leaving required skills empty and weakening every match score. The batch can now store an optional evaluation intent, optional Job Description, and required/preferred skills, backed by Alembic migrations. Intent supplies project or screening context to Jev and DeepSeek without directly changing score arithmetic; missing context remains visible in candidate warnings.
4. **Normal Google Drive links could not work:** all redirects were rejected, and a Drive `/file/d/.../view` URL became an invalid filename. Drive share links are now converted to a download URL, each redirect is security-checked, and the returned bytes are signature-validated as PDF/DOCX.
5. **No candidate brief or final metric evidence:** the candidate detail exposed only the aggregate score and raw Jev answers. It now includes the five category scores, weights, weighted points, scoring sources, Jev confidence, strongest signals, review gaps, warnings, and recommendation.
6. **Empty/scanned resumes could be sent into AI structuring:** a document with no machine-readable text could produce an unreliable result. It now becomes `needs_review` with an OCR/manual-review warning and no fabricated score.
7. **Jev transport failures could fail the whole candidate:** all provider/network/time-out errors now degrade to deterministic scoring plus manual review.
8. **Queue failures were not terminal:** `queued: false` left a candidate permanently pending. Queue failure now becomes a terminal, retriable candidate failure.
9. **Batch status never completed:** candidate processing now derives `draft`, `processing`, `completed`, or `failed` from terminal candidate states.
10. **Spreadsheet IDs could crash imports:** numeric Excel candidate IDs are normalized safely.
11. **Cross-recruiter data exposure:** non-admin recruiters are restricted to batches they created; admins retain global access.
12. **Sensitive candidate data was printed:** phone/email debug output was removed from deterministic scoring.
13. **No JD quality control:** each batch now stores a deterministic completeness/safety analysis and warns on vague or potentially discriminatory criteria.
14. **Scores lacked evidence and uncertainty:** every category now carries source evidence, category confidence, overall confidence and a displayed score interval. Confidence affects review priority, never score arithmetic.
15. **Feedback was coupled to scoring:** Jev evaluates; backend code owns weights; DeepSeek receives only a compact non-PII scorecard and writes a cached, 350-token-capped brief with deterministic fallback.
16. **No outcome feedback loop:** recruiter disposition, decision reason and interview/hiring outcome are persisted for offline calibration.
17. **Weak document/privacy controls:** encrypted PDFs, malformed/encrypted/expanding DOCX files, optional OCR, optional ClamAV scanning, retention dates, S3-first deletion and append-only audit events are implemented.
18. **No reproducible validation harness:** scripts now calculate ranking/calibration/fairness metrics from recruiter-labelled CSV data and exercise the hosted 100-resume lifecycle with p50/p95 latency.

## Implemented workflow

1. Authorized recruiter or placement staff opens `/bulk-ats`.
2. Recruiter creates a batch with an optional evaluation intent, optional Job Description, optional required/preferred skills, and fresher/experienced weighting. The role name remains required.
3. Recruiter uploads up to 100 PDF/DOCX files or imports up to 100 CSV/XLSX rows containing public Drive links, user-authorized private Drive links, or HTTPS links.
4. Each candidate is validated, deduplicated, stored, and queued independently.
5. Worker downloads or reads the resume, parses it, validates links, runs deterministic metrics, calls Jev, and persists a terminal result.
6. Dashboard polls while work is active and stops when every candidate is completed, needs review, or failed.
7. Recruiter reviews the brief, category evidence, warnings, Jev confidence, original Drive link, and can save notes or rerun the candidate.

## Evaluation matrix

| Area | Result | Evidence |
| --- | --- | --- |
| User flow and corner cases | Pass | Mermaid flow plus acceptance rules in `BULK_ATS_USER_FLOW.md` |
| Frontend API wiring | Pass | `/bulk-ats` generated in the production build |
| Role rubric, evaluation intent and JD quality | Pass | Optional intent, description and skills plus quality/safety analysis persisted; migration head `b8d5f9a3c722` |
| 100-resume request | Pass at contract level | Client and API reject more than 100; per-file size/type/empty checks |
| Public Google Drive links | Pass with controlled HTTP tests | Share-link conversion, redirect validation, PDF/DOCX signature detection, private-page rejection |
| Private Google Drive links | Pass at code and controlled HTTP level | Read-only per-recruiter OAuth, signed state plus matching HttpOnly cookie, encrypted refresh token, ephemeral worker access token, revocation/disconnect path |
| CSV/XLSX import | Pass at contract level | Row cap, numeric IDs, URL validation, per-row skips, deduplication |
| Candidate failure isolation | Pass | Every resume is queued and failed independently |
| Document coverage | Pass with deployment dependency | Encrypted/malformed checks, DOCX expansion guard, multilingual text, opt-in PyMuPDF/Tesseract OCR; OCR language packs remain an ops dependency |
| Metric evidence and uncertainty | Pass | Category source evidence, confidence factors and score interval persisted and rendered; review-priority sorting included |
| Jev + DeepSeek split | Pass at code level | Jev receives optional intent for semantic evaluation; backend scores; DeepSeek receives intent plus compact non-PII score data and only writes cached feedback; provider live calls remain a deployment gate |
| Candidate feedback loop | Pass | Recruiter decision, reason and interview/hiring outcome persisted independently of score |
| Privacy, audit and retention | Pass at code level | PII-minimised Jev copy, audit events, encrypted per-recruiter Drive refresh credentials, S3-first delete and daily retention purge |
| Calibration/fairness toolkit | Pass at tooling level | `scripts/bulk_ats_evaluate.py` reports MAE, Pearson/Spearman, precision/recall@K, group FNR and four-fifths ratio |
| 100-resume lifecycle harness | Pass at tooling level | `scripts/bulk_ats_load_test.py` creates/uploads/polls and reports batch time plus p50/p95 candidate latency |
| Jev SDK mapping | Pass | Official `typesafe-sdk` typed response coverage |
| Live Jev request | Pass | Synthetic request returned 8/8 non-null answers using `JevClient` |
| Jev outage fallback | Pass | Transport failure falls back to deterministic/manual-review mode |
| Queue outage behavior | Pass | Candidate becomes failed instead of hanging |
| Batch lifecycle | Pass | Mixed and all-failed terminal states covered |
| Recruiter ownership | Pass | Non-admin cross-owner access returns 404 |
| Frontend lint/type/build | Pass | ESLint, TypeScript, and Next.js webpack production build passed |
| Backend regression suite | Pass | 484 tests passed with 56 environment-dependent skips; focused Bulk ATS suite has 28 tests |
| Deployed DB + Redis + worker + S3 lifecycle | Not run locally | Required services are not running in this workspace |

## Release gates

1. Apply Alembic migrations through `b8d5f9a3c722` in the target environment.
2. Add the Jev key to the API and worker secret stores. The local ignored `.env` is configured, but it does not configure a hosted deployment.
3. Configure `REDIS_URL` or split Redis settings and run `src.worker.main.WorkerSettings` as a separate worker service.
4. Confirm S3 read/write permission for `Samvaad-Saathi/bulk-ats-resumes/`.
5. Grant a non-production test account the recruiter or placement-staff role.
6. Install the English and Hindi Tesseract language packs and configure a reachable ClamAV daemon. Production is configured to fail closed when the malware scanner is unavailable.
7. Run one PDF, one DOCX, one public Drive link, one private Drive link, one duplicate, one encrypted/malformed file, one scanned resume, and a batch of 100 small synthetic resumes through the hosted UI or load-test harness.
8. Verify every candidate terminates, the batch terminates, Jev/DeepSeek requests appear in provider logs, outcomes/audit events persist, retention purge works, and rerun transitions back through the queue.
9. Collect 300-500 representative anonymized resumes with independent multi-recruiter labels and run `scripts/bulk_ats_evaluate.py`; accuracy and fairness are not release claims until these results meet agreed thresholds.

## External validation and integration gates

- Calibration and fairness instrumentation is implemented, but accuracy/fairness cannot be certified without representative, independently labelled recruiter data. The system must not auto-reject candidates.
- Hosted capacity, rate-limit behaviour, queue depth, provider failure rate, peak memory and real cost per candidate require the deployed 100-resume load run; a local unit suite cannot establish them.
- OCR and malware integration are implemented in Docker Compose and the EC2/systemd installer. Production still must provision adequate memory or a remote ClamAV host and verify signature updates.
- Private Google Drive ingestion now uses per-recruiter, user-consented read-only OAuth with encrypted refresh tokens and ephemeral access tokens. Production must supply its Google Cloud OAuth client, exact callback URI and encryption key.
- Both local API keys are configured. Jev was live-verified previously; the final synthetic DeepSeek request fell back correctly because this execution environment could not connect to `api.deepseek.com`, so DeepSeek still needs a hosted-network smoke test.
- Bulk CSV export and batch-level retry-all are useful recruiter conveniences but are not required for scoring correctness or the safeguards in this pass.
