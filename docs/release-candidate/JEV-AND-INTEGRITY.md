# Jev task evaluation and integrity review release policy

Build 38, 21 October 2026. Candidate implementation; no live Jev request, production rollout or calibration claim.

## Implemented provider boundary

Only bulk_ats_semantics_v1 is supported by this gate. Jev remains a typed semantic ATS judgment adapter, not an interchangeable chat, question-generation, speech or interview-scoring endpoint. Existing interview generation providers remain unchanged. Fixed question schema, complete response validation, finite ordinal score/confidence checks and explicit choice/probability checks reject missing, extra, wrong-type, NaN, infinity and boolean-as-score values. Invalid/failed responses become deterministic-only ATS results requiring manual review; no fabricated semantic score is substituted. Candidate content is explicitly treated as data, not provider instructions. Never treat Noul truth probability as calibration confidence.

The worker chooses a stable batch cohort using the batch ID. A key alone no longer enables Jev. The initial rollout percentage is zero. All of these must match before a provider call is selected:

- JEV_API_KEY configured through the existing secret mechanism.
- JEV_MODEL explicitly pinned to the evaluated model (avoid floating aliases).
- JEV_BULK_ATS_EVALUATION_PATH points to a readable evaluation artifact, at most 64 KiB.
- JEV_BULK_ATS_EVALUATION_SHA256 matches the exact reviewed file bytes.
- JEV_BULK_ATS_ROLLOUT_PERCENT is an explicit integer 1–100; 0 is the kill switch.
- Matching task/policy/model; an evaluated domain:experience segment for each candidate; passing overall and segment gates; evaluation not future-dated or expired, at most 30 days of validity.

This intentionally changes key-only ATS installations: until evidence is approved they use the existing deterministic/manual-review path. Assess this operational impact before release. Changing a model, task rubric or endpoint requires a new review; bump the task/policy version whenever schema/rubric semantics change. Reload/restart the API and worker processes to apply changed environment settings. This gate reads their current configuration per candidate; an already running provider call is not cancelled by a flag change. The artifact is operator-approved evidence, not a signed attestation or proof that labels are correct.

## Produce evaluation evidence without production calls

Use an independently reviewed, deidentified, held-out JSON array of paired cases. Each case has opaque id, predeclared segment (domain profile ID plus experience mode, e.g. software_engineering:fresher), boolean expectedReview/predictedReview and percentage expectedScore/predictedScore. The semantic score is the consistently defined semantic-task aggregate, not the final weighted ATS score. Preserve failed predictions as null. Labels must be supplied by reviewers, not the evaluated model. No resume text, names, email, API keys or protected traits belong in the artifact.

```json
[{"id":"opaque-example-01","segment":"software_engineering:fresher","expectedReview":true,"predictedReview":true,"expectedScore":50,"predictedScore":null}]
```

The single example above cannot pass. Run:

```sh
.venv/bin/python ops/evaluate_jev.py paired-cases.json --model PINNED_MODEL --dataset-id REVIEWED_DATASET --output evaluation.json
```

Exit 0 means initial gates pass, 1 means gates fail, 2 means invalid input or output already exists. Output includes the artifact SHA256 and failure reasons, not raw cases. The tool never calls Jev and never overwrites an existing evidence file. Keep original reviewed labels/predictions in restricted evaluation storage for reproducibility.

Initial configurable-by-code release criteria: at least 50 cases overall, 20 per declared segment, at least 10 positive and 10 negative review labels per segment, recall >= .95, false-review rate <= .20, semantic score MAE <= 12.5 percentage points and prediction coverage >= .98. These are initial gates, not statistically proven calibration. Use representative domain, experience, language, answer quality, ambiguity, injection, outage and accessibility conditions; predeclare segments before evaluating. Sample size, confidence intervals, annotator agreement and fairness review are still required before expansion.

Operator sequence: review paired cases and provider/model provenance; inspect failed cases and all segments; checksum the output; install the same artifact on API/worker releases; start at a small explicitly approved cohort; monitor failures/latency/cost/manual-review burden; increase only after review. Reverting percent to 0 stops new Jev selection. Never remove deterministic parsing, explicit rubrics or manual review to improve an evaluation metric.

## Integrity boundary and appeal acceptance

Browser signals are unverified client observations. They cannot detect every nearby second device, prove cheating or justify an automatic score reduction/termination. New records carry integrity-observation-v1, unverified_client_observation, automaticPenalty=false and verdict=null. Retry IDs are idempotent; conflicting reuse returns 409. Journals stay bounded at 100 events. Ownership, assessment-only applicability and terminal-session protections remain in force. The unused punitive termination helper has been removed. Historical terminated rows have not been automatically reclassified.

Still required before any adverse integrity decision: scoped reviewer case queue, evidence provenance and reliability, a recorded human reason, learner notification with reviewable evidence and redaction, technical/accessibility explanations, bounded appeal submission, independent appeal adjudicator, immutable decision history, optimistic concurrency/idempotency, deadlines and explicit re-open/overturn behavior. Raw event counts, gaze, accent, disability, typing patterns or AI confidence alone must never decide guilt. Review prioritization must be evaluated against labelled technical incidents and independently reviewed cases; threshold changes require versioned evaluation. No integrity classifier is enabled by this build. This document does not claim that the remaining appeal workflow is implemented.

Human/video remains Google Meet in a separate tab initially. Build 36 appointments are implemented; admission, attendance and media/reconnect are controlled by Google/host, not inferred from clicking a link. Structured scorecards, validated camera/media capabilities, additional interviewer orchestration and explicit AI/human handover remain separate gates. Build 35 Saathi is practice-only; a neutral assessment face is still pending. Publication history/section coverage/adaptive reservations and deeper reviewer/recovery acceptance also remain open.
