"""Offline tests: python3 -m unittest discover -s ops -p test_aws_migration.py"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import aws_migration as migration


class MigrationTests(unittest.TestCase):
    def manifest(self):
        return {
            "source": {"accountId": "351405418851", "profile": "source", "region": "ap-south-1"},
            "destination": {"accountId": "123456789012", "profile": "target", "region": "ap-south-1"},
            "scope": {key: True for key in ("awsResources", "applicationDatabase", "authenticationDatabaseAndService", "keepPublicDomains")},
        }

    def test_wrong_account_stops_before_resource_reads(self):
        calls = []
        def call(*args):
            calls.append(args)
            return {"Account": "123456789012"}
        with self.assertRaisesRegex(ValueError, "Wrong AWS account"):
            migration.inventory("source", "351405418851", "ap-south-1", call)
        self.assertEqual(len(calls), 1)

    def test_inaccessible_resource_is_not_reported_as_empty(self):
        def call(profile, region, service, operation, *args):
            if service == "sts":
                return {"Account": "351405418851"}
            if service == "rds":
                raise RuntimeError("access_denied")
            return []
        report = migration.inventory("source", "351405418851", "ap-south-1", call)
        self.assertFalse(report["discoveryComplete"])
        self.assertIn("databases", report["errors"])
        self.assertNotIn("databases", report["resources"])
        self.assertEqual(report["resources"]["compute"], [])

    def test_manifest_scope_same_account_and_region(self):
        data = self.manifest()
        self.assertEqual(migration.validate_manifest(data), [])
        data["destination"]["accountId"] = data["source"]["accountId"]
        data["destination"]["region"] = "us-east-1"
        data["scope"]["authenticationDatabaseAndService"] = False
        self.assertEqual(len(migration.validate_manifest(data)), 3)

    def test_malformed_and_missing_inputs_fail_closed(self):
        for data in ({}, {"source": None, "destination": [], "scope": False}):
            self.assertTrue(migration.validate_manifest(data))
        for value in (None, "", "123", 123456789012):
            with self.assertRaises(ValueError):
                migration.account_id(value)

    def test_private_evidence_cannot_overwrite_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "inventory.json"
            migration.write_private(target, {"safe": True})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(target.read_text()), {"safe": True})
            with self.assertRaises(FileExistsError):
                migration.write_private(target, {})
            alias = Path(folder) / "alias"
            alias.symlink_to(target)
            with self.assertRaises(FileExistsError):
                migration.write_private(alias, {})

    def test_cli_pins_profile_and_strips_ambient_credentials_and_endpoints(self):
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "test-only", "AWS_ENDPOINT_URL": "https://invalid.test", "AWS_CONFIG_FILE": "/tmp/untrusted-config"}), patch("aws_migration.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "{}"
            migration.aws("source", "ap-south-1", "sts", "get-caller-identity")
            args, kwargs = run.call_args
            self.assertIn("source", args[0])
            self.assertNotIn("AWS_ACCESS_KEY_ID", kwargs["env"])
            self.assertNotIn("AWS_ENDPOINT_URL", kwargs["env"])
            self.assertNotIn("AWS_CONFIG_FILE", kwargs["env"])
            self.assertNotIn("--no-paginate", args[0])

    def test_invalid_profile_and_region_do_not_spawn_process(self):
        with patch("aws_migration.subprocess.run") as run:
            for profile, region in (("--debug", "ap-south-1"), ("source", "us-east-1")):
                with self.assertRaises(ValueError):
                    migration.aws(profile, region, "sts", "get-caller-identity")
            run.assert_not_called()

    def test_raw_cli_errors_are_not_exposed(self):
        with patch("aws_migration.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "AccessDenied secret-test-marker"
            with self.assertRaisesRegex(RuntimeError, "^access_denied$"):
                migration.aws("source", "ap-south-1", "sts", "get-caller-identity")


if __name__ == "__main__":
    unittest.main()
