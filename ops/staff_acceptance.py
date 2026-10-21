"""GET-only isolated-staging staff authorization acceptance. Never mints tokens.

Usage: python ops/staff_acceptance.py manifest.json --report evidence.json
Tokens are read from named environment variables, never the manifest or report.
Authentication middleware may synchronize identity/enrollment even on GETs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote

_spec = importlib.util.spec_from_file_location(
    "public_smoke", Path(__file__).with_name("staging_smoke.py")
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


class AcceptanceError(RuntimeError):
    pass


def fail(code):
    raise AcceptanceError(code)


def ids(value):
    if (
        not isinstance(value, list)
        or len(value) > 100
        or any(
            not isinstance(item, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", item)
            or item in {".", ".."}
            for item in value
        )
        or len(value) != len(set(value))
    ):
        fail("invalid_course_ids")
    return sorted(value)


def permissions(value):
    if value is None:
        return None
    if not isinstance(value, dict) or len(value) > 100:
        fail("invalid_permissions")
    result = {}
    for feature, operations in value.items():
        if not isinstance(feature, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,79}", feature
        ):
            fail("invalid_permissions")
        if (
            not isinstance(operations, list)
            or len(operations) > 30
            or any(
                not isinstance(item, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", item)
                for item in operations
            )
            or len(set(operations)) != len(operations)
        ):
            fail("invalid_permissions")
        result[feature] = sorted(operations)
    return result


def validate_manifest(value, environ):
    if not isinstance(value, dict) or set(value) != {
        "version",
        "environment",
        "origin",
        "actors",
    }:
        fail("invalid_manifest")
    if (
        type(value["version"]) is not int
        or value["version"] != 1
        or value["environment"] != "isolated_staging"
    ):
        fail("isolated_staging_manifest_required")
    try:
        origin = probe.validate_origin(value["origin"])
    except (probe.SmokeError, TypeError, AttributeError):
        fail("invalid_staging_origin")
    actors = value["actors"]
    if not isinstance(actors, list) or not 1 <= len(actors) <= 20:
        fail("invalid_actors")
    validated, labels, users, tokens = [], set(), set(), set()
    for actor in actors:
        required = {
            "label",
            "token_env",
            "user_id",
            "course_ids",
            "forbidden_course_ids",
            "permissions",
        }
        if not isinstance(actor, dict) or set(actor) != required:
            fail("invalid_actor")
        label, env_name, user_id = actor["label"], actor["token_env"], actor["user_id"]
        if (
            not isinstance(label, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", label)
            or label in labels
        ):
            fail("invalid_actor_label")
        if type(user_id) is not int or user_id <= 0 or user_id in users:
            fail("invalid_actor_identity")
        if not isinstance(env_name, str) or not re.fullmatch(
            r"SAMVAAD_ACCEPTANCE_[A-Z0-9_]+_TOKEN", env_name
        ):
            fail("invalid_token_environment_name")
        token = environ.get(env_name)
        if (
            not isinstance(token, str)
            or not 20 <= len(token) <= 16384
            or not all(33 <= ord(c) <= 126 for c in token)
            or token in tokens
        ):
            fail("missing_invalid_or_reused_actor_token")
        courses, forbidden = ids(actor["course_ids"]), ids(
            actor["forbidden_course_ids"]
        )
        if not courses or not forbidden or set(courses) & set(forbidden):
            fail("positive_and_negative_course_scope_required")
        validated.append(
            {
                **actor,
                "course_ids": courses,
                "forbidden_course_ids": forbidden,
                "permissions": permissions(actor["permissions"]),
                "token": token,
            }
        )
        labels.add(label)
        users.add(user_id)
        tokens.add(token)
    if (
        sum(
            6 + len(a["course_ids"]) + len(a["forbidden_course_ids"]) for a in validated
        )
        > 200
    ):
        fail("acceptance_request_budget_exceeded")
    return origin, validated


def request(origin, path, token):
    try:
        return 200, probe.fetch_json(
            origin,
            path,
            timeout=15,
            max_bytes=1024 * 1024,
            user_agent="Samvaad-Staff-Acceptance/1.0",
            bearer_token=token,
            use_proxy=False,
        )
    except probe.SmokeError as error:
        match = re.fullmatch(r"http_([0-9]{3})", str(error))
        if match:
            return int(match[1]), None
        raise AcceptanceError("transport_or_response_failed") from None


def check_actor(origin, actor):
    def read(path):
        status, body = request(origin, "/api/v2/" + path, actor["token"])
        if status != 200 or not isinstance(body, dict):
            fail("authenticated_baseline_failed")
        return body

    def baseline():
        identity = read("staff-access/me")
        if (
            type(identity.get("user_id")) is not int
            or identity["user_id"] != actor["user_id"]
            or identity.get("identity_source") != "central_sso"
            or identity.get("administrator") is not False
        ):
            fail("central_scoped_staff_identity_mismatch")
        scope = read("course-workspace/me")
        if (
            scope.get("global_access") is not False
            or scope.get("catalogue_source") != "central_course_ids"
            or scope.get("unassigned_access") != "denied"
            or ids(scope.get("course_ids")) != actor["course_ids"]
        ):
            fail("course_scope_mismatch")
        access = read("feature-access/me")
        if (
            access.get("administrator") is not False
            or "permissions" not in access
            or permissions(access["permissions"]) != actor["permissions"]
            or type(access.get("version")) is not int
            or access["version"] < 0
        ):
            fail("personal_permissions_mismatch")
        return identity, scope, access

    initial = baseline()
    for course in actor["course_ids"]:
        roster = read(
            "course-workspace/courses/" + quote(course, safe="") + "/students?limit=1"
        )
        if (
            roster.get("course_id") != course
            or not isinstance(roster.get("items"), list)
            or len(roster["items"]) > 1
            or type(roster.get("total")) is not int
            or roster["total"] < len(roster["items"])
        ):
            fail("positive_course_read_contract_failed")
    for course in actor["forbidden_course_ids"]:
        status, _ = request(
            origin,
            "/api/v2/course-workspace/courses/"
            + quote(course, safe="")
            + "/students?limit=1",
            actor["token"],
        )
        if status != 403:
            fail("forbidden_course_did_not_return_403")
    if baseline() != initial:
        fail("access_changed_during_acceptance")


def run(manifest, environ):
    origin, actors = validate_manifest(
        manifest, environ
    )  # Validate every secret/config before any request.
    results = []
    for actor in actors:
        try:
            check_actor(origin, actor)
            results.append({"actor": actor["label"], "passed": True})
        except AcceptanceError as error:
            results.append(
                {"actor": actor["label"], "passed": False, "error": str(error)}
            )
    return {
        "target_origin": origin,
        "passed": all(item["passed"] for item in results),
        "actors": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.manifest.resolve() == args.report.resolve():
        print(
            "Staff acceptance failed: report_must_not_overwrite_manifest",
            file=sys.stderr,
        )
        return 1
    report = {
        "check": "scoped_staff_baseline",
        "environment": "operator_declared_isolated_staging",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "coverage": "central_identity_course_scope_personal_permissions_foreign_course_denial",
        "enrollment_revocation_and_journeys": "not_checked",
    }
    try:
        with args.manifest.open("rb") as source:
            payload = source.read(256 * 1024 + 1)
        if len(payload) > 256 * 1024:
            fail("manifest_too_large")
        report["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
        report.update(run(json.loads(payload), os.environ))
    except (OSError, ValueError, RecursionError):
        report["error"] = "manifest_read_failed"
    except AcceptanceError as error:
        report["error"] = str(error)
    try:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    except OSError:
        print("Staff acceptance failed: report_write_failed", file=sys.stderr)
        return 1
    print(json.dumps(report), file=sys.stdout if report["passed"] else sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
