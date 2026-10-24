"""Static checks of the CI/CD, Docker and deployment configuration, plus env validation."""

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import config

try:
    import yaml
except ImportError:  # PyYAML is a dev dependency (requirements-dev.txt)
    yaml = None

ROOT = Path(__file__).parent


def _bash_works(path):
    """True only if the executable really starts and runs a command (rejects the broken WSL launcher)."""
    try:
        result = subprocess.run([path, "-c", "echo ok"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "ok"


def find_bash():
    """Return a working Bash executable path, or None. Prefers Git Bash on Windows."""
    candidates = []
    if os.name == "nt":
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
            base = os.environ.get(var)
            if base:
                candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
                candidates.append(os.path.join(base, "Programs", "Git", "bin", "bash.exe"))
        candidates.append(r"C:\Program Files\Git\bin\bash.exe")
    found = shutil.which("bash")
    if found and not (os.name == "nt" and "system32" in found.lower()):
        candidates.append(found)
    for candidate in dict.fromkeys(candidates):
        if os.path.isfile(candidate) and _bash_works(candidate):
            return candidate
    return None


BASH = find_bash()


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


@unittest.skipIf(yaml is None, "PyYAML not installed")
class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = yaml.safe_load(read(".github/workflows/ci-cd.yml"))
        cls.jobs = cls.wf["jobs"]
        cls.text = read(".github/workflows/ci-cd.yml")

    def test_triggers(self):
        triggers = self.wf[True] if True in self.wf else self.wf["on"]
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertEqual(triggers["pull_request"]["branches"], ["main"])
        self.assertIn("workflow_dispatch", triggers)

    def test_default_permissions_are_read_only(self):
        self.assertEqual(self.wf["permissions"], {"contents": "read"})

    def test_only_publish_can_write_packages(self):
        for name, job in self.jobs.items():
            perms = job.get("permissions", {})
            if name == "publish":
                self.assertEqual(perms.get("packages"), "write")
            else:
                self.assertNotEqual(perms.get("packages"), "write", name)
            self.assertNotIn("write-all", str(perms))

    def test_job_dependency_chain(self):
        needs = {n: set(j["needs"] if isinstance(j.get("needs"), list) else [j["needs"]] if j.get("needs") else [])
                 for n, j in self.jobs.items()}
        self.assertEqual(needs["validate"], set())
        self.assertIn("validate", needs["test"])
        self.assertTrue({"validate", "test"} <= needs["docker-build"])
        self.assertIn("docker-build", needs["publish"])
        self.assertIn("publish", needs["deploy"])

    def test_publish_and_deploy_only_run_for_pushes_to_main(self):
        for name in ("publish", "deploy", "deploy-not-configured"):
            condition = self.jobs[name]["if"]
            self.assertIn("github.event_name == 'push'", condition, name)
            self.assertIn("github.ref == 'refs/heads/main'", condition, name)

    def test_deploy_requires_explicit_enablement(self):
        self.assertIn("vars.DEPLOY_ENABLED == 'true'", self.jobs["deploy"]["if"])
        self.assertIn("vars.DEPLOY_ENABLED != 'true'", self.jobs["deploy-not-configured"]["if"])

    def test_not_configured_job_does_not_claim_deployment(self):
        steps = " ".join(str(s.get("run", "")) for s in self.jobs["deploy-not-configured"]["steps"])
        self.assertIn("NO server was updated", steps)
        self.assertNotIn("Deployed", steps)

    def test_latest_and_sha_tags_are_pushed(self):
        run = " ".join(str(s.get("run", "")) for s in self.jobs["publish"]["steps"])
        self.assertIn(':$GITHUB_SHA"', run)
        self.assertIn(':latest"', run)

    def test_tests_report_junit_and_coverage(self):
        run = " ".join(str(s.get("run", "")) for s in self.jobs["test"]["steps"])
        self.assertIn("--junitxml", run)
        self.assertIn("--cov", run)

    def test_validation_checks_present(self):
        run = " ".join(str(s.get("run", "")) for s in self.jobs["validate"]["steps"])
        for expected in ("compileall", "ruff check", "docker compose config"):
            self.assertIn(expected, run)

    def test_deploy_uses_strict_host_key_checking_and_secrets(self):
        self.assertIn("StrictHostKeyChecking yes", self.text)
        self.assertNotIn("StrictHostKeyChecking no", self.text)
        self.assertNotIn("ssh-keyscan", self.text)
        for secret in ("DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_SSH_KEY", "DEPLOY_KNOWN_HOSTS"):
            self.assertIn("secrets.%s" % secret, self.text)

    def test_no_hardcoded_credentials(self):
        patterns = (
            r"ghp_[A-Za-z0-9]{20,}",
            r"github_pat_[A-Za-z0-9_]{20,}",
            r"BEGIN [A-Z ]*PRIVATE KEY",
            r"AKIA[0-9A-Z]{16}",
        )
        for pattern in patterns:
            self.assertIsNone(re.search(pattern, self.text), pattern)

    def test_no_pull_request_target_trigger(self):
        self.assertNotIn("pull_request_target", self.text)


class DeployScriptTests(unittest.TestCase):
    def test_script_never_deletes_the_data_volume(self):
        script = "\n".join(
            line for line in read("deploy/remote-deploy.sh").splitlines() if not line.lstrip().startswith("#")
        )
        for forbidden in ("down -v", "--volumes", "volume rm", "volume prune", "system prune", "rm -rf"):
            self.assertNotIn(forbidden, script)

    def test_script_rolls_back_and_waits_for_health(self):
        script = read("deploy/remote-deploy.sh")
        self.assertIn("Rolling back", script)
        self.assertIn("State.Health.Status", script)
        self.assertIn("set -euo pipefail", script)

    @unittest.skipUnless(BASH, "no usable Bash executable found (install Git for Windows)")
    def test_shell_scripts_have_valid_syntax(self):
        for name in ("deploy/remote-deploy.sh", "docker-entrypoint.sh", "scripts/docker-smoke.sh"):
            result = subprocess.run([BASH, "-n", str(ROOT / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "%s: %s" % (name, result.stderr))

    @unittest.skipUnless(BASH, "no usable Bash executable found (install Git for Windows)")
    def test_deploy_script_requires_an_image_argument(self):
        result = subprocess.run([BASH, str(ROOT / "deploy/remote-deploy.sh")], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage", result.stderr)


class DockerConfigTests(unittest.TestCase):
    def test_dockerfile_runs_gunicorn_as_non_root_with_healthcheck(self):
        text = read("Dockerfile")
        self.assertIn("gunicorn", text)
        self.assertIn("HEALTHCHECK", text)
        self.assertIn("/health", text)
        self.assertIn("--uid 10001", text)
        self.assertIn("DB_PATH=/data/dashboard.db", text)

    def test_entrypoint_drops_privileges_and_never_removes_data(self):
        text = read("docker-entrypoint.sh")
        self.assertIn("setpriv --reuid=app", text)
        self.assertNotIn("rm ", text)

    def test_dockerfile_copies_every_runtime_module(self):
        text = read("Dockerfile")
        for module in ("app.py", "config.py", "github_client.py", "pipeline.py"):
            self.assertIn(module, text)
        self.assertIn("COPY templates", text)
        self.assertIn("COPY static", text)

    def test_runtime_modules_do_not_import_unlisted_local_files(self):
        local = {p.stem for p in ROOT.glob("*.py") if not p.name.startswith("test_")}
        shipped = {"app", "config", "github_client", "pipeline"}
        for module in shipped:
            for name in re.findall(r"^(?:import|from) (\w+)", read(module + ".py"), re.M):
                if name in local:
                    self.assertIn(name, shipped, "%s imports %s which the image does not contain" % (module, name))

    def test_dockerignore_keeps_secrets_and_data_out_of_the_image(self):
        text = read(".dockerignore")
        for entry in (".git", "*.db"):
            self.assertIn(entry, text)

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_compose_persists_sqlite_and_takes_config_from_environment(self):
        service = yaml.safe_load(read("docker-compose.yml"))["services"]["dashboard"]
        self.assertIn("dashboard-data:/data", service["volumes"])
        self.assertEqual(service["environment"]["DB_PATH"], "/data/dashboard.db")
        for name in ("GITHUB_REPO", "GITHUB_TOKEN", "DEMO_MODE"):
            self.assertIn("${%s" % name, service["environment"][name])
        self.assertIn("no-new-privileges:true", service["security_opt"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["restart"], "unless-stopped")

    def test_compose_has_no_hardcoded_token(self):
        self.assertNotRegex(read("docker-compose.yml"), r"GITHUB_TOKEN:\s*[\"']?(ghp_|github_pat_)")

    def test_requirements_are_pinned(self):
        for line in read("requirements.txt").split():
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==\d")


class EnvironmentValidationTests(unittest.TestCase):
    def check(self, **env):
        base = {"GITHUB_REPO": "", "GITHUB_TOKEN": "", "DEMO_MODE": "", "GITHUB_TIMEOUT": "", "GITHUB_CACHE_TTL": ""}
        base.update(env)
        with mock.patch.dict(os.environ, base):
            return config.problems()

    def test_clean_configuration_has_no_problems(self):
        clean = self.check(GITHUB_REPO="24106085-wq/devops-dashboard", GITHUB_TOKEN="tok", DEMO_MODE="true")
        self.assertEqual(clean, [])
        self.assertEqual(self.check(), [])

    def test_reports_each_invalid_setting_without_leaking_values(self):
        found = self.check(GITHUB_REPO="bad repo", GITHUB_TOKEN="has space", DEMO_MODE="maybe",
                           GITHUB_TIMEOUT="soon", GITHUB_CACHE_TTL="x")
        self.assertEqual(len(found), 5)
        self.assertFalse(any("has space" in p or "bad repo" in p for p in found))

    def test_numeric_settings_are_clamped(self):
        with mock.patch.dict(os.environ, {"GITHUB_TIMEOUT": "9999", "GITHUB_CACHE_TTL": "-5"}):
            self.assertEqual(config.github_timeout(), 30.0)
            self.assertEqual(config.cache_ttl(), 0.0)
        with mock.patch.dict(os.environ, {"GITHUB_TIMEOUT": "nan", "GITHUB_CACHE_TTL": "junk"}):
            self.assertEqual(config.github_timeout(), config.DEFAULT_TIMEOUT)
            self.assertEqual(config.cache_ttl(), config.DEFAULT_CACHE_TTL)


if __name__ == "__main__":
    unittest.main()
