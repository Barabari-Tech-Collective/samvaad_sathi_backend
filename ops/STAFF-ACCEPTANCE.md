# Scoped staff acceptance — isolated staging only

This is a manually invoked baseline for scoped facilitator/placement test accounts whose course-workspace reads are enabled. It checks the deployed API through real central sign-in tokens. It does not create accounts, grant roles, mint tokens, publish interviews or change infrastructure. Authentication middleware can bind/synchronize identities and enrollments even on GET; GET-only is not a guarantee of no database side effects.

## Prepare the actual environment

Select and independently verify an isolated staging deployment and its database, central-auth and Sampark source configuration. The manifest's `isolated_staging` label is an operator declaration, not automated proof of resource isolation. Never substitute the production URL or use production student records. Existing production staff assignments, including seven unresolved historical mappings, must not be guessed to make tests pass.

Provision approved staging identities through the normal central identity/admin workflows. Choose a non-admin facilitator or placement account with explicit course assignments and permission to read those courses. Use a separate unassigned course for each negative case. Copy `examples/staff-acceptance.json` to an operator-controlled file and replace its invalid example origin, user ID, courses and exact personal permissions with verified staging values. No token belongs in the manifest or Git.

Supply each existing short-lived central sign-in token through its named `SAMVAAD_ACCEPTANCE_*_TOKEN` environment variable using the approved secret mechanism. The runner does not log tokens, refresh expired sessions, or use the local development token-minting helper. Different actors require distinct user IDs, labels and tokens. Null permissions mean the API's legacy role-default policy; `{}` means all personal features are revoked and cannot pass this positive-course profile. Disabled-feature, unassigned-staff, student, admin and super-admin profiles need separate acceptance scenarios.

From the repository root:

```sh
python ops/staff_acceptance.py /path/to/verified-staff-manifest.json --report /tmp/staff-acceptance-evidence.json
```

The runner uses the application's default `/api` prefix. Each actor makes six baseline reads (three before and three after the scope checks), one GET per assigned course and one GET per forbidden course. All requests use the exact configured HTTPS origin, reject redirects and bypass environment proxies; only the fixed staff identity, course scope, personal permissions and course-roster route families are reachable. The response limit is 1 MiB per request and socket timeout is 15 seconds. There are no automatic retries. All actor settings/tokens and a 200-request budget are validated before any request. This is not a load tester or strict wall-clock deadline.

## Interpreting evidence

A pass requires all of the following for every actor:

- Exact expected local user ID, `central_sso` identity source and no administrator authority.
- Exact assigned-course set, no global access, central course catalogue and default-deny declaration.
- Exact personal permission policy, a valid policy version, and no administrator bypass.
- Successful assigned-course roster reads with matching course ID and bounded result shape. An empty roster is valid; this check does not assert student inclusion/exclusion.
- Explicit HTTP 403 on every unassigned-course roster. HTTP 401, 404, 5xx, redirect, malformed body or transport failure cannot count as denial evidence.
- The identity, course-scope and permission response snapshots remain equal at the end. Expiry or access changes invalidate the run. This is not an atomic snapshot and cannot rule out changes that occur and revert between requests.

The JSON report contains UTC time, target origin, manifest SHA-256, actor labels and bounded pass/failure codes. It omits tokens, user IDs, course IDs, roster data and response bodies. Preserve the verified nonsecret manifest alongside the report in controlled acceptance records so its hash can be checked. Missing/malformed/oversized input, missing tokens or report-write failure exits nonzero. The report cannot overwrite the manifest. Do not upload credentials or sensitive fixtures as CI artifacts.

## Still required

A successful run is only this scoped staff baseline. It does not prove student enrollment freshness/revocation, college/cohort/purpose filtering of individual records, consent withdrawal, actual exports or mutation capabilities, real interview/question/audio/receipt journeys, browser sessions, human/video/provider behavior, representative load or rollback. Use dedicated known-record fixtures and additional scenarios for those gates. No live acceptance run has been performed merely by adding this tool or passing its synthetic tests.
