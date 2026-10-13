"""Release the checked-out commit after recorded testing; never deploy branch tips blindly.

Run only from release.yml. Configuration belongs to GitHub environments, not source.
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import quote


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
    raise RuntimeError("Deployment polling timed out; inspect the provider before retrying")


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
    source = command("gh", "api", f"repos/{repository}/commits/{quote(branch, safe='')}")
    if source["sha"] != sha:
        raise RuntimeError("Amplify source branch differs from the tested production commit")
    settings = command("aws", "amplify", "get-branch", "--app-id", app, "--branch-name", branch, "--output", "json")
    if settings["branch"].get("enableAutoBuild"):
        raise RuntimeError("Disable Amplify auto-build before enabling gated production releases")
    job = command("aws", "amplify", "start-job", "--app-id", app, "--branch-name", branch,
                  "--job-type", "RELEASE", "--commit-id", sha, "--output", "json")["jobSummary"]
    result = wait_for(lambda: command("aws", "amplify", "get-job", "--app-id", app, "--branch-name", branch,
                                     "--job-id", job["jobId"], "--output", "json"),
                      lambda value: value["job"]["summary"]["status"], {"SUCCEED"}, {"FAILED", "CANCELLED"})
    if result["job"]["summary"]["commitId"] != sha:
        raise RuntimeError("Amplify commit mismatch: inspect and restore the previous verified release")


def ec2_deploy(sha):
    instance = required("EC2_INSTANCE_ID")
    if not re.fullmatch(r"i-[0-9a-f]+", instance):
        raise RuntimeError("Invalid EC2 instance ID")
    if required("EC2_RELEASE_CONFIGURED") != "true":
        raise RuntimeError("Install and verify the EC2 release runner first")
    result = command("aws", "ssm", "send-command", "--instance-ids", instance,
                     "--document-name", "AWS-RunShellScript", "--parameters",
                     json.dumps({"commands": [f"/usr/local/sbin/samvaad-release {sha}"], "executionTimeout": ["1200"]}),
                     "--output", "json")
    command_id = result["Command"]["CommandId"]
    time.sleep(5)  # SSM command registration is eventually consistent.
    wait_for(lambda: command("aws", "ssm", "get-command-invocation", "--command-id", command_id,
                             "--instance-id", instance, "--output", "json"),
             lambda value: value["Status"], {"Success"}, {"Failed", "Cancelled", "TimedOut"})


def smoke():
    url = required("HEALTH_URL")
    if not url.startswith("https://"):
        raise RuntimeError("Hosted health checks require HTTPS")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        if attempt < 5:
            time.sleep(10)
    raise RuntimeError("Hosted smoke check failed; keep the release blocked and inspect rollback")


def main():
    sha, repository = required("GITHUB_SHA"), required("GITHUB_REPOSITORY")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Invalid release commit")
    if len(required("TEST_EVIDENCE")) < 20:
        raise RuntimeError("Provide a meaningful test record including tester, date, and results")
    verify_production_source(repository, sha)
    payload = {"ref": sha, "environment": "production", "task": "samvaad-release", "auto_merge": False,
               "required_contexts": [], "description": "Verified feature release", "payload": {"test_evidence": required("TEST_EVIDENCE")}}
    deployment = json.loads(subprocess.check_output(["gh", "api", "--method", "POST", f"repos/{repository}/deployments", "--input", "-"], input=json.dumps(payload), text=True))
    status_path = f"repos/{repository}/deployments/{deployment['id']}/statuses"
    def mark(state):
        command("gh", "api", "--method", "POST", status_path, "-f", f"state={state}", "-f", f"environment_url={required('HEALTH_URL')}")
    mark("in_progress")
    try:
        if required("PRODUCTION_PROVIDER") == "amplify": amplify_deploy(sha, repository)
        elif required("PRODUCTION_PROVIDER") == "ec2": ec2_deploy(sha)
        else: raise RuntimeError("Unsupported production provider")
        smoke()
    except BaseException:
        mark("failure")
        raise
    mark("success")
    print("Production deployment and health check passed. Candidate journey acceptance is still required.")


if __name__ == "__main__":
    main()
