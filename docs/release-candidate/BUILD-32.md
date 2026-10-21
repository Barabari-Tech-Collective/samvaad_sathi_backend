# Build 32 — host-local liveness and rollback verification

20 October 2026. Backend release candidate only. No production invocation, AWS changes, migration execution, grants or frontend changes.

## Implemented

The EC2 script previously accepted any successful curl response and checked both systemd services in one invocation. A multi-unit `systemctl is-active` succeeds if at least one unit is active, so a stopped worker could escape the gate. Rollback also printed a healthy message even when earlier recovery steps had failed.

New `ops/local_health.py` validates a direct HTTP loopback target: exact `127.0.0.1`, explicit nonzero valid port, and `/health` under a valid optional API prefix. Credentials, alternate hosts, query/fragment delimiters, ambiguous paths and whitespace fail. It shares the existing health contract and bounded JSON reader, uses a 5-second socket timeout and 64 KiB body limit, refuses redirects, and explicitly disables environment proxies. `--validate-only` makes no request.

`ops/ec2-release.sh` fetches the exact target commit, extracts both probe files with `git show` into a private temporary directory, and validates the target before checkout/install/migrations/restarts. Extracting from the target supports deployed commits that predate the helper files. The snapshot survives checkout and rollback and is cleaned by an EXIT trap. The existing lock and tracked-cleanliness checks remain before fetching.

Both candidate and rollback readiness require separate successful API and worker service checks followed by the exact backend health contract. Each readiness wait makes at most 12 attempts with 11 five-second waits; there is no final extra sleep. The existing hosted release gate remains independent.

Rollback disables recursive ERR handling, restores the prior commit, attempts dependency installation and restarts, and verifies readiness with the retained probe. Failed checkout stops recovery with an urgent manual-recovery message. Failed dependencies or restart prevent a healthy rollback claim even if the endpoint later responds. A recovered application does not turn the failed release into a successful exit.

Migration-head discovery now captures and checks the Alembic command before parsing its output. A partial output followed by failure cannot be mistaken for a valid head. Errors inside command substitutions propagate to the owning shell; rollback runs once there. Database downgrade remains deliberately manual, with the expand-only compatibility warning retained.

CI syntax checks now include the local probe.

## Verification

**125 targeted tests passed**, including **29 new local-health/release-shell cases**. Prior hosted release, public staging and migration-safety cases remain included. Black, Bash syntax, Python 3.13 compilation and diff checks passed. Local pytest uses the existing Python 3.14.8 environment; new-commit Python 3.12 CI is still required.

The shell fixture runs the actual script with temporary checkouts and synthetic Git/systemd/pip/Python-command adapters. Only the lock file location is substituted; macOS Bash 3 receives a fixture shim for the missing mapfile builtin, while Linux CI uses its real builtin. Probe validation and health-contract logic run; network health responses are synthetic. It verifies successful release, wrong-service responses, candidate-only/still-stopped workers, old commits deleting probe files, failed rollback checkout/dependencies/restart, cleanup, failed candidate dependencies, failed Alembic heads and migration execution, single rollback ownership, failure exit status and rejection before candidate mutation.

Other tests exercise disallowed loopback lookalikes and credentials, port/path validation, no-network preflight, disabled environment proxies, bounded responses and the local user agent. No live systemd, real Git checkout, pip installation, database or AWS operations ran.

## Rollout limits and remaining gates

This is static service liveness plus systemd process state, not proof of queue processing, database health, release SHA identity, provider availability or authenticated student/staff journeys. Socket timeouts and attempt counts do not establish a strict end-to-end deadline against every network condition. A host-local successful release can still fail the later hosted check; that later failure requires operator inspection, as recorded in Build 31.

The target commit must contain both helper files. A missing file fails before application mutation; do not remove the check to force a deployment. The production SSM runner must execute the reviewed script/version as part of approved adoption; this build did not alter or verify the installed SSM document. The existing backup, migration compatibility and source/destination gates remain required. Historical releases without these probe files require an operator-reviewed recovery path rather than blindly invoking the new script with such a target.

Build 31's host-local body-validation follow-up is now implemented in the candidate. Isolated authenticated staging, approved mappings for seven historical staff memberships, enrollment/revocation, real provider/device/screen-reader/load acceptance and complete visual review remain open. AWS relocation remains deferred.

Build 32 is the sixteenth batch against the post-Build-16 estimate, not proof that the pilot is ready. Next: prepare and execute isolated authenticated/source acceptance where an approved staging environment is available, then resolve findings before rollout.
