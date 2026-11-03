# Local cross-service acceptance

This opt-in harness runs the actual central password-login and identity-bridge controllers, actual Sampark enrollment snapshot controller/SQL, and the full Samvaad API against newly initialized local PostgreSQL databases, local Redis and verified local TLS. All identities, passwords, keys, courses, colleges and consent are fixtures. It does not use existing databases, restored production records, AWS or provider accounts.

Prerequisites: installed application Python dependencies (including psycopg2), both central Node services' installed dependencies, a trusted Sampark backend checkout, PostgreSQL server tools usable by a non-root user, Redis, Node, Git and OpenSSL. PostgreSQL 15 was used locally; this does not replace a PostgreSQL 17 hosted rehearsal. Run without Python optimization because assertions are acceptance gates.

```sh
.venv/bin/python ops/acceptance/run_local.py \
  --sampark-checkout /absolute/path/to/Sampark-Saathi-Backend \
  --pg-bin /absolute/path/to/postgresql/bin \
  --report /tmp/samvaad-local-source-acceptance.json
```

The harness creates random database names and selects unused loopback ports. A port-selection race fails startup; it does not attach to an existing database. PostgreSQL is initialized in a private temporary directory and binds only to 127.0.0.1. Authentication is trust-based for this disposable cluster, which contains only synthetic records. It must not be exposed remotely or reused for deployment. The fixture uses a freshly generated local TLS certificate and trusts that certificate explicitly; HTTPS verification remains enabled.

Central/core dotenv file loading is disabled in the fixture process. The Python fixture similarly disables python-decouple and Pydantic dotenv loading. Child service environments are explicitly constructed instead of inheriting credentials. No auth, authorization, repository, sync or SQL business logic is mocked. Central routers run in a dedicated fixture Express mount, not the complete production gateway/index; brokers, notifications, Eureka, OAuth, providers and room services are not started or verified. Configuration isolation hooks exist only in fixture entrypoints and must never be used as production entrypoints.

Checks include central password login, Build 33's staff acceptance runner, positive/negative course scope, college/consent isolation, source enrollment revoke/restore, stale-enrollment denial and fresh recovery, personal-feature revocation, consent withdrawal/restoration, staff membership revoke/restore, central active-account and product-entitlement revoke/restore using the same issued token. The harness seeds and changes only its own disposable fixture records to trigger these states; it does not administer real users.

Children and the PostgreSQL cluster are stopped in a finally block, including failures. Temporary TLS keys/certificates and the manifest are deleted. Synthetic database files and private logs remain under the reported temporary directory for diagnosis; there is no ongoing server or hosting cost. No token or fixture password is written into the report. Force-killing the parent or losing power can bypass cleanup: stop only the processes belonging to that fixture directory, never unrelated local services. Do not upload private logs without review.

A passing report records scenario names, PostgreSQL version and source commits. An uncommitted product worktree is identified so a report is not mistaken for exact-commit acceptance. This is local cross-service integration evidence, **not hosted staging, browser, physical-device, provider, representative-load or production acceptance**. Run the corresponding actual-environment gates before rollout.
