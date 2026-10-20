"""Checks on the build and release machinery.

None of this can build a real installer here, but the parts that break silently — a renamed file,
a path the workflow expects that nothing produces, a .gitignore rule swallowing a tracked file —
are all checkable cheaply.
"""
import os
import re
import sys
import unittest

try:
    import yaml
except ImportError:                                       # pragma: no cover - dev dependency
    yaml = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packaging"))

PROJECT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def read(*parts):
    with open(os.path.join(PROJECT, *parts), encoding="utf-8") as handle:
        return handle.read()


class FilesPresentTests(unittest.TestCase):
    def test_everything_the_build_needs_exists(self):
        for path in ("negscan_app.py",
                     "requirements.txt", "requirements-dev.txt",
                     "packaging/build.py", "packaging/negscan.spec", "packaging/negscan.iss",
                     "packaging/linux/install.sh", "packaging/linux/negscan.desktop",
                     "packaging/linux/99-negscan.rules",
                     "assets/icon.svg", "assets/icon.png", "assets/Negscan.icns",
                     ".github/workflows/ci.yml", ".github/workflows/release.yml"):
            with self.subTest(path=path):
                self.assertTrue(os.path.isfile(os.path.join(PROJECT, path)), f"missing {path}")

    def test_install_script_is_executable(self):
        path = os.path.join(PROJECT, "packaging", "linux", "install.sh")
        self.assertTrue(os.access(path, os.X_OK), "install.sh must ship with its +x bit")

    def test_spec_is_not_swallowed_by_gitignore(self):
        """.gitignore has *.spec for PyInstaller's own output; ours must be exempted."""
        gitignore = read(".gitignore")
        self.assertIn("*.spec", gitignore)
        self.assertIn("!packaging/*.spec", gitignore,
                      "packaging/negscan.spec would not be committed")


class BuildScriptTests(unittest.TestCase):
    def test_imports_without_running(self):
        import build
        self.assertTrue(callable(build.main))

    def test_artifact_names_carry_platform_and_arch(self):
        import build
        self.assertIn(build.arch_tag(), ("x86_64", "arm64"))
        self.assertIn(build.platform_tag(), ("macOS", "windows", "linux"))

    def test_release_dir_is_inside_dist(self):
        import build
        self.assertEqual(build.RELEASE.parent, build.DIST)


class UdevRuleTests(unittest.TestCase):
    def test_rule_matches_the_device_the_driver_opens(self):
        from scanner_app import usb_scanner
        rule = read("packaging", "linux", "99-negscan.rules")
        self.assertIn(f'{{idVendor}}=="{usb_scanner.VID:04x}"', rule)
        self.assertIn(f'{{idProduct}}=="{usb_scanner.PID:04x}"', rule)

    def test_rule_grants_access_without_root(self):
        rule = read("packaging", "linux", "99-negscan.rules")
        self.assertIn("uaccess", rule, "without uaccess the scanner needs root")

    def test_rule_targets_the_device_not_its_interfaces(self):
        rule = read("packaging", "linux", "99-negscan.rules")
        self.assertIn('ENV{DEVTYPE}=="usb_device"', rule)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.ci = read(".github", "workflows", "ci.yml")
        self.release = read(".github", "workflows", "release.yml")

    def test_release_builds_every_platform_we_claim_to_support(self):
        for runner in ("macos-13", "macos-14", "windows-latest", "ubuntu-"):
            with self.subTest(runner=runner):
                self.assertIn(runner, self.release)

    def test_release_runs_the_build_script(self):
        self.assertIn("packaging/build.py", self.release)

    def test_release_uploads_what_the_build_script_writes(self):
        import build
        relative = os.path.relpath(build.RELEASE, PROJECT).replace(os.sep, "/")
        self.assertIn(f"{relative}/*", self.release,
                      "the upload path and the build output directory have drifted apart")

    def test_both_workflows_run_the_tests(self):
        for name, text in (("ci", self.ci), ("release", self.release)):
            with self.subTest(workflow=name):
                self.assertIn("unittest discover", text)

    def test_ci_sets_offscreen_qt(self):
        self.assertIn("QT_QPA_PLATFORM: offscreen", self.ci)

    @unittest.skipIf(yaml is None, "pyyaml is not installed")
    def test_every_job_is_gated_on_the_repository_owner(self):
        """Anyone can open a pull request on a public repo; nobody else should start a build."""
        for name in ("ci", "release"):
            jobs = yaml.safe_load(read(".github", "workflows", f"{name}.yml"))["jobs"]
            self.assertTrue(jobs, f"found no jobs in {name}.yml")
            for job_name, job in jobs.items():
                with self.subTest(workflow=name, job=job_name):
                    self.assertIn("github.actor == github.repository_owner", job.get("if", ""),
                                  f"{name}.yml job {job_name!r} is not owner-gated")

    @unittest.skipIf(yaml is None, "pyyaml is not installed")
    def test_workflows_default_to_read_only_permissions(self):
        for name in ("ci", "release"):
            with self.subTest(workflow=name):
                document = yaml.safe_load(read(".github", "workflows", f"{name}.yml"))
                self.assertEqual(document.get("permissions"), {"contents": "read"})

    @unittest.skipIf(yaml is None, "pyyaml is not installed")
    def test_only_the_publish_job_may_write(self):
        """Write access is what creates the release; no build job should hold it."""
        jobs = yaml.safe_load(read(".github", "workflows", "release.yml"))["jobs"]
        writers = [n for n, j in jobs.items()
                   if (j.get("permissions") or {}).get("contents") == "write"]
        self.assertEqual(writers, ["release"])

    def test_release_is_tag_triggered(self):
        self.assertIn('tags: ["v*"]', self.release)

    def test_version_flows_from_the_tag_into_the_build(self):
        self.assertIn("GITHUB_REF_NAME#v", self.release)
        self.assertIn("--version ${{ steps.version.outputs.version }}", self.release)


class VersionTests(unittest.TestCase):
    def test_app_version_is_a_release_number(self):
        import negscan_app
        self.assertRegex(negscan_app.__version__, r"^\d+\.\d+\.\d+$")

    def test_inno_setup_output_name_matches_what_the_release_notes_promise(self):
        iss = read("packaging", "negscan.iss")
        match = re.search(r"OutputBaseFilename=(\S+)", iss)
        self.assertIsNotNone(match)
        self.assertTrue(match.group(1).endswith("-setup"), match.group(1))


if __name__ == "__main__":
    unittest.main()
