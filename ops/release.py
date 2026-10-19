"""Release the checked-out commit after recorded testing; never deploy branch tips blindly.

Run only from release.yml. Configuration belongs to GitHub environments, not source.
"""

import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

# Load only the sibling file from this tested checkout. This supports both direct
# script execution and test imports without depending on the caller's sys.path.
_probe_spec = importlib.util.spec_from_file_location(
    "samvaad_public_smoke", Path(__file__).with_name("staging_smoke.py")
)
public_smoke = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(public_smoke)


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment configuration: {name}")
    return value


def command(*args):
    return json.loads(subprocess.check_output(args, text=True))


def wait_for(fetch, status, success, failure, timeout=1500):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        value = fetch()
        current = status(value)
        if current != previous:
            print(f"Deployment state: {current}", flush=True)
            previous = current
        if current in success:
            return value
        if current in failure:
            raise RuntimeError(f"Deployment failed: {current}")
        time.sleep(15)
    raise RuntimeError(
        "Deployment polling timed out; inspect the provider before retrying"
    )


def verify_aws_destination():
    """Read-only identity gate before any release or deployment mutation."""
    expected = required("AWS_EXPECTED_ACCOUNT_ID")
    region = required("AWS_REGION")
    if not re.fullmatch(r"[0-9]{12}", expected):
        raise RuntimeError("AWS_EXPECTED_ACCOUNT_ID must contain exactly 12 digits")
    identity = command(
        "aws", "sts", "get-caller-identity", "--region", region, "--output", "json"
    )
    if identity.get("Account") != expected:
        raise RuntimeError("AWS account mismatch; no release action was started")
    return expected


def verify_production_source(repository, sha):
    repo = command("gh", "api", f"repos/{repository}")
    default_branch = repo["default_branch"]
    if required("GITHUB_REF_NAME") != default_branch:
        raise RuntimeError("Production releases must run from the default branch")
    source = command(
        "gh", "api", f"repos/{repository}/commits/{quote(default_branch, safe='')}"
    )
    if source["sha"] != sha:
        raise RuntimeError("The tested commit is not the current default-branch tip")


def amplify_deploy(sha, repository):
    app, branch = required("AMPLIFY_APP_ID"), required("AMPLIFY_BRANCH")
    # RELEASE uses the latest source branch. Require its tip to equal the tested SHA.
    source = command(
        "gh", "api", f"repos/{repository}/commits/{quote(branch, safe='')}"
    )
    if source["sha"] != sha:
        raise RuntimeError(
            "Amplify source branch differs from the tested production commit"
        )
    settings = command(
        "aws",
        "amplify",
        "get-branch",
        "--app-id",
        app,
        "--branch-name",
        branch,
        "--output",
        "json",
    )
    if settings["branch"].get("enableAutoBuild"):
        raise RuntimeError(
            "Disable Amplify auto-build before enabling gated production releases"
        )
    job = command(
        "aws",
        "amplify",
        "start-job",
        "--app-id",
        app,
        "--branch-name",
        branch,
        "--job-type",
        "RELEASE",
        "--commit-id",
        sha,
        "--output",
        "json",
    )["jobSummary"]
    result = wait_for(
        lambda: command(
            "aws",
            "amplify",
            "get-job",
            "--app-id",
            app,
            "--branch-name",
            branch,
            "--job-id",
            job["jobId"],
            "--output",
            "json",
        ),
        lambda value: value["job"]["summary"]["status"],
        {"SUCCEED"},
        {"FAILED", "CANCELLED"},
    )
    if result["job"]["summary"]["commitId"] != sha:
        raise RuntimeError(
            "Amplify commit mismatch: inspect and restore the previous verified release"
        )


def ec2_deploy(sha):
    instance = required("EC2_INSTANCE_ID")
    if not re.fullmatch(r"i-[0-9a-f]+", instance):
        raise RuntimeError("Invalid EC2 instance ID")
    if required("EC2_RELEASE_CONFIGURED") != "true":
        raise RuntimeError("Install and verify the EC2 release runner first")
    result = command(
        "aws",
        "ssm",
        "send-command",
        "--instance-ids",
        instance,
        "--document-name",
        "SamvaadProductionRelease",
        "--parameters",
        json.dumps(
            {
                "releaseSha": [sha],
            }
        ),
        "--output",
        "json",
    )
    command_id = result["Command"]["CommandId"]
    time.sleep(5)  # SSM command registration is eventually consistent.
    fetch = lambda: command(
        "aws",
        "ssm",
        "get-command-invocation",
        "--command-id",
        command_id,
        "--instance-id",
        instance,
        "--output",
        "json",
    )
    try:
        invocation = wait_for(
            fetch,
            lambda value: value["Status"],
            {"Success"},
            {"Failed", "Cancelled", "TimedOut"},
        )
    except BaseException:
        invocation = fetch()
        print(invocation.get("StandardOutputContent", "")[-12_000:])
        print(invocation.get("StandardErrorContent", "")[-12_000:])
        raise
    print(invocation.get("StandardOutputContent", "")[-12_000:])


def verify_database_backup_reference():
    reference = required("DATABASE_BACKUP_REFERENCE")
    if len(reference) < 12 or reference.lower() in {"none", "n/a", "not applicable"}:
        raise RuntimeError(
            "Provide the latest verified managed-database backup ID and timestamp"
        )
    return reference


def smoke():
    origin, path = public_smoke.validate_health_url(os.environ.get("HEALTH_URL", ""))
    last_error = "health_not_verified"
    for attempt in range(6):
        try:
            health = public_smoke.fetch_json(
                origin,
                path,
                timeout=30,
                max_bytes=64 * 1024,
                user_agent="Samvaad-Release-Health-Check/1.0",
            )
            public_smoke.validate_health(health)
            return
        except public_smoke.SmokeError as error:
            last_error = str(error)
        if attempt < 5:
            time.sleep(10)
    raise RuntimeError(
        f"Hosted smoke check failed ({last_error}); keep the release blocked and inspect rollback"
    )


def main():
    sha, repository = required("GITHUB_SHA"), required("GITHUB_REPOSITORY")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Invalid release commit")
    if len(required("TEST_EVIDENCE")) < 20:
        raise RuntimeError(
            "Provide a meaningful test record including tester, date, and results"
        )
    # Reject an unsafe or malformed target before AWS calls or deployment writes.
    public_smoke.validate_health_url(os.environ.get("HEALTH_URL", ""))
    backup_reference = None
    if required("PRODUCTION_PROVIDER") == "ec2":
        backup_reference = verify_database_backup_reference()
    verify_aws_destination()
    verify_production_source(repository, sha)
    payload = {
        "ref": sha,
        "environment": "production",
        "task": "samvaad-release",
        "auto_merge": False,
        "required_contexts": [],
        "description": "Verified feature release",
        "payload": {
            "test_evidence": required("TEST_EVIDENCE"),
            "database_backup_reference": backup_reference,
        },
    }
    deployment = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{repository}/deployments",
                "--input",
                "-",
            ],
            input=json.dumps(payload),
            text=True,
        )
    )
    status_path = f"repos/{repository}/deployments/{deployment['id']}/statuses"

    def mark(state):
        command(
            "gh",
            "api",
            "--method",
            "POST",
            status_path,
            "-f",
            f"state={state}",
            "-f",
            f"environment_url={required('HEALTH_URL')}",
        )

    mark("in_progress")
    try:
        if required("PRODUCTION_PROVIDER") == "amplify":
            amplify_deploy(sha, repository)
        elif required("PRODUCTION_PROVIDER") == "ec2":
            ec2_deploy(sha)
        else:
            raise RuntimeError("Unsupported production provider")
        smoke()
    except BaseException:
        mark("failure")
        raise
    mark("success")
    print(
        "Production deployment and health check passed. Candidate journey acceptance is still required."
    )


if __name__ == "__main__":
    main()
