#!/usr/bin/env python3
"""Exercise release metadata and publication ordering without contacting GitHub."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


spec = importlib.util.spec_from_file_location("ci_release", Path(__file__).with_name("release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "dist").mkdir()
        self.package = self.root / "dist" / "synthetic-module.zip"
        self.payload = b"synthetic reviewed library: never an installable module"
        self.payload_hash = hashlib.sha256(self.payload).hexdigest()
        self.write_package(self.payload_hash)
        self.env = {
            "GITHUB_REPOSITORY": "fixture-owner/fixture-repo",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ID": "123456",
            "GITHUB_RUN_NUMBER": "7",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        self.now = datetime(2026, 9, 28, 12, 34, 56, tzinfo=timezone.utc)
        # A future code change must not accidentally execute gh in these tests.
        process_patch = patch.object(release.subprocess, "run", side_effect=AssertionError("unexpected subprocess"))
        self.run = process_patch.start()
        self.addCleanup(process_patch.stop)

    def write_package(self, reviewed_hash):
        with zipfile.ZipFile(self.package, "w") as archive:
            archive.writestr("module.prop", "id=synthetic\nversion=fixture-v2\nversionCode=2\n")
            archive.writestr("static-review.json", json.dumps({
                "patched_sha256": reviewed_hash,
                "firmware_fingerprint": "synthetic-firmware",
            }))
            archive.writestr("payload/libbluetooth_qti.so", self.payload)

    def prepare(self, env=None):
        return release.prepare(self.root, self.env if env is None else env, self.now)

    def test_reruns_and_new_runs_have_distinct_tags_at_same_commit_and_time(self):
        original = self.prepare()
        retry = self.prepare(dict(self.env, GITHUB_RUN_ATTEMPT="2"))
        separate_run = self.prepare(dict(self.env, GITHUB_RUN_ID="123457", GITHUB_RUN_NUMBER="8"))
        self.assertEqual(len({original["tag"], retry["tag"], separate_run["tag"]}), 3)
        self.assertEqual(original["tag"], "build-20260928T123456Z-123456-1")
        for info in (original, retry, separate_run):
            self.assertEqual(info["commit"], self.env["GITHUB_SHA"])
            self.assertEqual(datetime.fromisoformat(info["built_at_utc"].replace("Z", "+00:00")), self.now)

    def test_checksums_match_actual_package_and_build_info(self):
        info = self.prepare()
        hashes = dict(line.split("  ", 1)[::-1] for line in
                      (self.root / "dist" / "SHA256SUMS.txt").read_text().splitlines())
        self.assertEqual(set(hashes), {self.package.name, "build-info.json"})
        for name, expected in hashes.items():
            self.assertEqual(hashlib.sha256((self.root / "dist" / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(info["package_sha256"], hashes[self.package.name])
        self.assertEqual(info["payload_sha256"], self.payload_hash)
        self.assertEqual(info["module_version_code"], 2)
        self.assertFalse(info["device_tested_by_this_ci_run"])
        self.assertEqual(json.loads((self.root / "dist" / "build-info.json").read_text()), info)

    def test_prepare_rejects_payload_not_matching_review(self):
        self.write_package("0" * 64)
        with self.assertRaisesRegex(ValueError, "reviewed hash"):
            self.prepare()
        self.assertFalse((self.root / "dist" / "build-info.json").exists())

    def test_prepare_requires_exactly_one_package(self):
        extra = self.root / "dist" / "stale-build.zip"
        extra.write_bytes(self.package.read_bytes())
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.prepare()
        extra.unlink()
        self.package.unlink()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.prepare()

    def test_context_rejects_non_main_refs(self):
        for ref in ("refs/heads/feature", "refs/tags/v2", "refs/pull/42/merge", ""):
            with self.subTest(ref=ref):
                with self.assertRaisesRegex(ValueError, "Only builds of main"):
                    release.context(dict(self.env, GITHUB_REF=ref))

    def test_context_rejects_invalid_workflow_identity(self):
        for key, value in (("GITHUB_REPOSITORY", "../bad/repo"), ("GITHUB_SHA", "a" * 39),
                           ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_NUMBER", "-1"),
                           ("GITHUB_RUN_ATTEMPT", "1; echo bad")):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    release.context(dict(self.env, **{key: value}))

    def test_publish_refuses_changed_package_before_any_github_call(self):
        self.prepare()
        with self.package.open("ab") as output:
            output.write(b"unexpected change")
        with patch.object(release, "api") as api:
            with self.assertRaisesRegex(ValueError, "package has changed"):
                release.publish(self.root, self.env)
        api.assert_not_called()
        self.run.assert_not_called()

    def test_publish_refuses_different_workflow_context_before_any_github_call(self):
        self.prepare()
        for key, value in (("GITHUB_REPOSITORY", "other-owner/other-repo"), ("GITHUB_SHA", "b" * 40),
                           ("GITHUB_RUN_ID", "123457"), ("GITHUB_RUN_ATTEMPT", "2")):
            with self.subTest(key=key), patch.object(release, "api") as api:
                with self.assertRaisesRegex(ValueError, "another workflow run"):
                    release.publish(self.root, dict(self.env, **{key: value}))
                api.assert_not_called()
                self.run.assert_not_called()

    def test_publish_uploads_draft_before_publishing_and_preserves_tag_date(self):
        info = self.prepare()
        events = []

        def api(endpoint, payload):
            events.append(("api", endpoint, payload))
            return {"sha": "c" * 40}

        def run(command, **kwargs):
            self.assertTrue(kwargs["check"])
            events.append(("gh", command))
            return subprocess.CompletedProcess(command, 0)

        self.run.side_effect = run
        summary = self.root / "summary.md"
        with patch.object(release, "api", side_effect=api), patch("sys.stdout", new_callable=io.StringIO):
            release.publish(self.root, dict(self.env, GITHUB_STEP_SUMMARY=str(summary)))
        self.assertEqual([event[0] for event in events], ["api", "api", "gh", "gh"])
        self.assertTrue(events[0][1].endswith("/git/tags"))
        self.assertEqual(events[0][2]["object"], self.env["GITHUB_SHA"])
        self.assertEqual(datetime.fromisoformat(events[0][2]["tagger"]["date"].replace("Z", "+00:00")), self.now)
        self.assertTrue(events[1][1].endswith("/git/refs"))
        self.assertEqual(events[1][2], {"ref": "refs/tags/" + info["tag"], "sha": "c" * 40})
        create, publish = events[2][1], events[3][1]
        self.assertEqual(create[:4], ["gh", "release", "create", info["tag"]])
        self.assertIn("--verify-tag", create)
        self.assertIn("--draft", create)
        self.assertEqual(create[-3:], [str(self.package), str(self.root / "dist" / "SHA256SUMS.txt"),
                                     str(self.root / "dist" / "build-info.json")])
        self.assertEqual(publish, ["gh", "release", "edit", info["tag"], "--repo",
                                  self.env["GITHUB_REPOSITORY"], "--draft=false", "--latest"])
        self.assertIn(info["tag"], summary.read_text())
        self.assertIn(info["package_sha256"], summary.read_text())

    def test_failed_draft_create_or_upload_never_publishes(self):
        self.prepare()
        self.run.side_effect = subprocess.CalledProcessError(1, ["gh", "release", "create"])
        summary = self.root / "summary.md"
        with patch.object(release, "api", return_value={"sha": "c" * 40}):
            with self.assertRaises(subprocess.CalledProcessError):
                release.publish(self.root, dict(self.env, GITHUB_STEP_SUMMARY=str(summary)))
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args[0][:3], ["gh", "release", "create"])
        self.assertFalse(summary.exists())

    def test_existing_tag_ref_error_never_creates_or_publishes_release(self):
        self.prepare()
        with patch.object(release, "api", side_effect=[{"sha": "c" * 40},
                          subprocess.CalledProcessError(1, ["gh", "api", "git/refs"])]):
            with self.assertRaises(subprocess.CalledProcessError):
                release.publish(self.root, self.env)
        self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
