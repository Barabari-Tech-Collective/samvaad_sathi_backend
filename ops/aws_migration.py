"""Read-only, account-bound migration discovery. Never provisions or cuts over.

Standard library only; run `python3 ops/aws_migration.py --help`.
Reports contain infrastructure identifiers, not credentials or application data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone


# Fixed commands and field projections prevent configuration/env/secret dumps.
# CLI auto-pagination stays enabled: do not silently inventory only the first page.
QUERIES = {
    "compute": ("ec2", "describe-instances", "Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType,vpc:VpcId,subnet:SubnetId,profile:IamInstanceProfile.Arn,groups:SecurityGroups[].GroupId,volumes:BlockDeviceMappings[].Ebs.VolumeId}"),
    "volumes": ("ec2", "describe-volumes", "Volumes[].{id:VolumeId,sizeGiB:Size,encrypted:Encrypted,kmsKey:KmsKeyId,state:State}"),
    "network": ("ec2", "describe-vpcs", "Vpcs[].{id:VpcId,cidr:CidrBlock,isDefault:IsDefault}"),
    "subnets": ("ec2", "describe-subnets", "Subnets[].{id:SubnetId,vpc:VpcId,zone:AvailabilityZone,cidr:CidrBlock,publicIp:MapPublicIpOnLaunch}"),
    "loadBalancers": ("elbv2", "describe-load-balancers", "LoadBalancers[].{arn:LoadBalancerArn,dns:DNSName,scheme:Scheme,type:Type,vpc:VpcId}"),
    "ssm": ("ssm", "describe-instance-information", "InstanceInformationList[].{id:InstanceId,status:PingStatus,platform:PlatformName,version:PlatformVersion}"),
    "amplify": ("amplify", "list-apps", "apps[].{id:appId,name:name,defaultDomain:defaultDomain,repository:repository,platform:platform}"),
    "buckets": ("s3api", "list-buckets", "Buckets[].{name:Name,created:CreationDate}"),
    "databases": ("rds", "describe-db-instances", "DBInstances[].{id:DBInstanceIdentifier,engine:Engine,version:EngineVersion,status:DBInstanceStatus,endpoint:Endpoint.Address,encrypted:StorageEncrypted,kmsKey:KmsKeyId,public:PubliclyAccessible}"),
    "clusters": ("rds", "describe-db-clusters", "DBClusters[].{id:DBClusterIdentifier,engine:Engine,version:EngineVersion,status:Status,endpoint:Endpoint,encrypted:StorageEncrypted,kmsKey:KmsKeyId}"),
    "redis": ("elasticache", "describe-cache-clusters", "CacheClusters[].{id:CacheClusterId,engine:Engine,version:EngineVersion,status:CacheClusterStatus}"),
    "containers": ("ecs", "list-clusters", "clusterArns"),
    "dns": ("route53", "list-hosted-zones", "HostedZones[].{id:Id,name:Name,private:Config.PrivateZone}"),
    "certificates": ("acm", "list-certificates", "CertificateSummaryList[].{arn:CertificateArn,domain:DomainName,status:Status}"),
    "stacks": ("cloudformation", "describe-stacks", "Stacks[].{id:StackId,name:StackName,status:StackStatus}"),
}


def account_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{12}", value):
        raise ValueError("Account ID must contain exactly 12 digits")
    return value


def aws(profile: str, region: str, *args: str):
    if not profile or profile.startswith("-") or not re.fullmatch(r"[\w.@+-]+", profile):
        raise ValueError("Use an explicit named CLI profile")
    if region != "ap-south-1":
        raise ValueError("This migration is approved for ap-south-1 only")
    env = os.environ.copy()
    # Explicit ambient credentials/endpoints must not silently override the profile.
    for name in list(env):
        if name in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_CA_BUNDLE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"} or name.startswith("AWS_ENDPOINT_URL"):
            env.pop(name, None)
    env.update(AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off", AWS_IGNORE_CONFIGURED_ENDPOINT_URLS="true", AWS_MAX_ATTEMPTS="2")
    result = subprocess.run(
        ["aws", "--profile", profile, "--region", region, "--output", "json", "--cli-connect-timeout", "5", "--cli-read-timeout", "15", *args],
        text=True, capture_output=True, timeout=90, env=env, check=False,
    )
    if result.returncode:
        # Do not persist raw stderr: plugins may print credentials/config.
        category = "access_denied" if "AccessDenied" in result.stderr or "UnauthorizedOperation" in result.stderr else "authentication_or_command_failed"
        raise RuntimeError(category)
    return json.loads(result.stdout)


def inventory(profile: str, expected_account: str, region: str, call=aws):
    account_id(expected_account)
    identity = call(profile, region, "sts", "get-caller-identity")
    if identity.get("Account") != expected_account:
        raise ValueError("Wrong AWS account; discovery stopped before resource queries")
    report = {
        "schemaVersion": 1, "kind": "discovery_only_not_cutover_evidence",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "accountId": expected_account, "profile": profile, "region": region,
        "resources": {}, "errors": {},
        "limitations": ["Regional discovery covers Mumbai only; buckets and hosted zones are account-wide.", "External PostgreSQL/auth hosts, object contents, secrets, live service configuration and shared consumers need separate verification.", "Resource presence does not prove ownership, deployment version, backup validity or migration readiness."],
    }
    for name, (service, operation, query) in QUERIES.items():
        try:
            report["resources"][name] = call(profile, region, service, operation, "--query", query)
        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            report["errors"][name] = type(exc).__name__ if not isinstance(exc, RuntimeError) else str(exc)
    report["discoveryComplete"] = not report["errors"]
    return report


def validate_manifest(data):
    """Validate inventory inputs only. Cannot authorize a migration/cutover."""
    errors = []
    for side in ("source", "destination"):
        value = data.get(side, {})
        if not isinstance(value, dict):
            errors.append(f"{side} must be an object")
            continue
        try:
            account_id(value.get("accountId"))
        except ValueError:
            errors.append(f"{side}.accountId required (12 digits)")
        if not isinstance(value.get("profile"), str) or not re.fullmatch(r"[\w.@+-]+", value.get("profile", "")) or value.get("profile", "").startswith("-"):
            errors.append(f"{side}.profile required")
        if value.get("region") != "ap-south-1":
            errors.append(f"{side}.region must be ap-south-1")
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    destination = data.get("destination") if isinstance(data.get("destination"), dict) else {}
    if source.get("accountId") == destination.get("accountId"):
        errors.append("Source and destination accounts must differ")
    scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    for field in ("awsResources", "applicationDatabase", "authenticationDatabaseAndService", "keepPublicDomains"):
        if scope.get(field) is not True:
            errors.append(f"scope.{field} must be true")
    return errors


def write_private(path: Path, data):
    # Exclusive create: reports never silently replace earlier evidence or symlinks.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("inventory", help="Read-only metadata discovery; no secrets or application records")
    discover.add_argument("--profile", required=True)
    discover.add_argument("--expected-account", required=True)
    discover.add_argument("--region", default="ap-south-1")
    discover.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("validate-manifest", help="Check account/scope inputs; NOT a cutover readiness check")
    check.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate-manifest":
            data = json.loads(args.path.read_text())
            if not isinstance(data, dict):
                raise ValueError("Manifest must be a JSON object")
            errors = validate_manifest(data)
            print(json.dumps({"inputValid": not errors, "cutoverReady": False, "errors": errors}, indent=2))
            return 1 if errors else 0
        if args.output.exists() or not args.output.parent.is_dir():
            raise ValueError("Output must be a new file in an existing directory")
        report = inventory(args.profile, args.expected_account, args.region)
        write_private(args.output, report)
        print(json.dumps({"saved": str(args.output), "discoveryComplete": report["discoveryComplete"], "failedSections": sorted(report["errors"])}))
        return 0 if report["discoveryComplete"] else 2
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Stopped: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
