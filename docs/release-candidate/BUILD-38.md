# Build 38 — Jev release gate and integrity evidence hardening

21 October 2026. Candidate implementation; no database migration or production deployment.

Jev now requires explicit ATS rollout approval: pinned model, digest-matching reviewed evaluation artifact, matching task/policy, unexpired evidence, stable batch cohort and a passing domain:experience segment before SDK invocation. Default rollout is zero; a key alone no longer enables Jev. Unapproved cohorts retain deterministic results and manual-review routing. Interview generation, speech providers and scoring have not been switched to Jev.

The offline evaluation CLI compares deidentified paired human labels and provider predictions, checks overall and declared-segment review recall/false-review rate, semantic score error, coverage and minimum sample counts, and emits a bounded checksum-pinnable report. Failed predictions remain in coverage. It makes no provider calls and refuses to overwrite reviewed evidence. Thresholds are initial release criteria; no real labelled evaluation or empirical calibration has been claimed.

The ATS adapter now rejects incomplete/unknown/wrong-type task results, invalid choices/probabilities/confidence, NaN/infinity and boolean/out-of-range ordinal scores. Invalid results fall back to manual review. SDK exception bodies are excluded from logs. Candidate-supplied context is explicitly untrusted data.

Integrity records now identify their policy and unverified browser provenance, explicitly carry no automated penalty or verdict, and reject conflicting reuse of an event ID. The unused punitive termination helper was removed. Existing owner, practice exclusion, terminal-state and bounded-journal protections remain. Historical records are unchanged. A complete scoped human decision/appeal workflow is still outstanding; this build does not implement or enable an integrity classifier.

## Verification

- Full backend regression: 1,340 passed, 614 warnings, with fresh isolated local PostgreSQL 15.17 databases. Production/CI use Python 3.12 and PostgreSQL 17; this Mac used Python 3.14.8.
- Initial focused Jev/ATS regression: 99 passed, 127 warnings, including the additional domain gate and CLI checksum/overwrite tests. Exact backend commit da95bc8 passed GitHub CI on Python 3.12/PostgreSQL 17: 1,342 tests, 611 warnings. The final two additional domain-gate cases explain the difference from the earlier full local count.
- Includes real SQL/HTTP browser-signal retry/conflict and non-punitive state assertions, SDK transport/schema failures, score sanitization, cohort stability, model/digest/task/time mismatches, subgroup regression, missing labels and failed prediction coverage.
- Early local migration runs failed due to disposable role/schema ownership setup; aligning the disposable role to CI resolved those setup errors. No production database was accessed.

Read JEV-AND-INTEGRITY.md for the exact operator sequence, operational impact of default-off behavior and remaining evaluation/reviewer/appeal gates. Migration head remains a5b6c7d8e9f0. Google Meet link-out, no staging, deferred AWS migration and pending historical staff mappings remain unchanged.

## Unscorable evidence follow-up

Review-required ambiguous cases can carry a null expected score, so reviewers do not invent gold scores. MAE and prediction coverage use only score-labelled cases, with explicit minimum label counts. Missing predictions on labelled cases still fail coverage; missing required fields or oversized output artifacts are rejected. Final focused regression: 100 passed. Exact final-commit GitHub results are recorded in the planning verification evidence.
