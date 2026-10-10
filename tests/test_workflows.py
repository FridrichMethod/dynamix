"""Static and behavioral checks of .github/workflows: triggers, release hold, permissions, pinning, no write paths.

The release hold (06-validation-and-release.md, Gate 5) must hold by construction: ordinary pushes and pull requests
only test and upload CI artifacts; Pages deploys only from a manual dispatch of main whose release_sha input and the
repository variable RELEASE_APPROVED_SHA both name the dispatched commit (unset variable = closed); no workflow has a
schedule, pushes, tags, releases or opens pull requests. Workflows are parsed with the stdlib YAML-subset reader in
tests/test_workflows_yaml.py; the deploy guard is also evaluated, and the shell guard executed, for each scenario.
"""

import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_workflows_yaml import parse_yaml  # noqa: E402

WORKFLOWS = ROOT / ".github/workflows"
EXPECTED_WORKFLOWS = {"tests.yml", "pages.yml", "check-sources.yml"}
GUARD = ("github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && "
         "inputs.release_sha == github.sha && vars.RELEASE_APPROVED_SHA == github.sha")
GUARD_ENV = {"EVENT_NAME": "${{ github.event_name }}", "REF": "${{ github.ref }}", "SHA": "${{ github.sha }}",
             "RELEASE_SHA": "${{ inputs.release_sha }}", "APPROVED_SHA": "${{ vars.RELEASE_APPROVED_SHA }}"}
ALLOWED_ACTIONS = {"actions/checkout", "actions/setup-python", "actions/upload-artifact",
                   "actions/upload-pages-artifact", "actions/deploy-pages"}
PAGES_ONLY_ACTIONS = {"actions/upload-pages-artifact", "actions/deploy-pages", "actions/configure-pages"}
USES_LINE = re.compile(r"^\s*(?:- )?uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"^([\w.-]+/[\w.-]+)@([0-9a-f]{40})$")
VERSION_COMMENT = re.compile(r"^\s+# v\d+(?:\.\d+){1,2}\s*$")
FORBIDDEN_COMMANDS = {
    "git push": r"\bgit\s+push\b", "git tag": r"\bgit\s+tag\b", "git commit": r"\bgit\s+commit\b",
    "git remote": r"\bgit\s+remote\b", "git config": r"\bgit\s+config\b", "gh (any GitHub CLI call)": r"\bgh\s+\w",
    "curl write method": r"\bcurl\b[^\n]*(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)",
    "swallowed failure": r"\|\|\s*(?:true|:|echo)\b", "token": r"GITHUB_TOKEN|GH_TOKEN|github\.token|secrets\.",
}
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
APPROVED = {"github.event_name": "workflow_dispatch", "github.ref": "refs/heads/main", "github.sha": SHA,
            "inputs.release_sha": SHA, "vars.RELEASE_APPROVED_SHA": SHA}
DENIED = {  # each must neither satisfy the deploy condition nor pass the shell guard
    "push to main": {**APPROVED, "github.event_name": "push", "inputs.release_sha": ""},
    "pull request": {**APPROVED, "github.event_name": "pull_request", "github.ref": "refs/pull/1/merge",
                     "inputs.release_sha": ""},
    "schedule": {**APPROVED, "github.event_name": "schedule", "inputs.release_sha": ""},
    "dispatch on another branch": {**APPROVED, "github.ref": "refs/heads/feature"},
    "dispatch on a tag": {**APPROVED, "github.ref": "refs/tags/v1.0.0"},
    "wrong release_sha": {**APPROVED, "inputs.release_sha": OTHER},
    "variable unset (release hold)": {k: v for k, v in APPROVED.items() if k != "vars.RELEASE_APPROVED_SHA"},
    "variable empty": {**APPROVED, "vars.RELEASE_APPROVED_SHA": ""},
    "variable names another commit": {**APPROVED, "vars.RELEASE_APPROVED_SHA": OTHER},
    "short SHAs": {**APPROVED, "inputs.release_sha": SHA[:7], "vars.RELEASE_APPROVED_SHA": SHA[:7]},
}


def load(name: str) -> dict:
    return parse_yaml((WORKFLOWS / name).read_text(encoding="utf-8"))


def all_workflows() -> dict[str, dict]:
    return {name: load(name) for name in sorted(EXPECTED_WORKFLOWS)}


def steps_of(job: dict) -> list[dict]:
    return job.get("steps") or []


def uses_in(workflow: dict) -> list[tuple[str, str]]:
    """``(job id, uses)`` for every job-level (reusable workflow) and step-level ``uses``."""
    found = []
    for job_id, job in workflow["jobs"].items():
        if "uses" in job:
            found.append((job_id, job["uses"]))
        found += [(job_id, step["uses"]) for step in steps_of(job) if "uses" in step]
    return found


def run_scripts(workflow: dict) -> list[str]:
    return [step["run"] for job in workflow["jobs"].values() for step in steps_of(job) if "run" in step]


def action_name(uses: str) -> str:
    return uses.split("@", 1)[0]


def evaluate(expression: str, context: dict) -> bool:
    """GitHub expression semantics for the guard's grammar: ``&&`` of ``==`` terms comparing context values or
    single-quoted literals. A missing value is ``''``; string comparison ignores case, as in GitHub Actions."""
    def operand(text: str) -> str:
        text = text.strip()
        literal = re.fullmatch(r"'([^']*)'", text)
        if literal:
            return literal.group(1)
        if not re.fullmatch(r"[A-Za-z_][\w.]*", text):
            raise ValueError(f"unsupported operand {text!r}")
        return context.get(text, "")

    terms = expression.split("&&")
    for term in terms:
        left, operator, right = term.partition("==")
        if not operator or "!" in term or "||" in term:
            raise ValueError(f"unsupported term {term!r}")
        if operand(left).casefold() != operand(right).casefold():
            return False
    return True


def resolve_env(env: dict, context: dict) -> dict:
    resolved = {}
    for name, value in env.items():
        match = re.fullmatch(r"\$\{\{\s*([\w.]+)\s*\}\}", value)
        if not match:
            raise ValueError(f"unexpected env value {value!r}")
        resolved[name] = context.get(match.group(1), "")
    return resolved


class InventoryAndTriggerTests(unittest.TestCase):
    def test_only_reviewed_workflows_exist(self):
        files = {path.name for path in WORKFLOWS.iterdir() if path.suffix in (".yml", ".yaml")}
        self.assertEqual(files, EXPECTED_WORKFLOWS, "a new workflow needs release-hold review and tests here")

    def test_triggers(self):
        workflows = all_workflows()
        self.assertEqual(workflows["tests.yml"]["on"], {"push": None, "pull_request": None, "workflow_call": None})
        self.assertEqual(workflows["check-sources.yml"]["on"], {"workflow_dispatch": None})
        pages_on = workflows["pages.yml"]["on"]
        self.assertEqual(list(pages_on), ["workflow_dispatch"])
        self.assertEqual(pages_on["workflow_dispatch"],
                         {"inputs": {"release_sha": {"description": pages_on["workflow_dispatch"]["inputs"][
                             "release_sha"]["description"], "required": "true", "type": "string"}}})

    def test_no_schedule_or_privileged_triggers_anywhere(self):
        for name, workflow in all_workflows().items():
            with self.subTest(workflow=name):
                self.assertFalse(set(workflow["on"]) & {"schedule", "pull_request_target", "workflow_run",
                                                        "repository_dispatch", "release", "issue_comment"})
                text = (WORKFLOWS / name).read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"^\s*(?:schedule|cron)\s*:", text, re.MULTILINE))


class PermissionTests(unittest.TestCase):
    def test_top_level_permissions(self):
        workflows = all_workflows()
        self.assertEqual(workflows["tests.yml"]["permissions"], {"contents": "read"})
        self.assertEqual(workflows["check-sources.yml"]["permissions"], {"contents": "read"})
        self.assertEqual(workflows["pages.yml"]["permissions"], {})

    def test_every_job_declares_minimal_permissions(self):
        for name, workflow in all_workflows().items():
            for job_id, job in workflow["jobs"].items():
                with self.subTest(workflow=name, job=job_id):
                    self.assertIn("permissions", job)
                    if (name, job_id) == ("pages.yml", "deploy"):
                        self.assertEqual(job["permissions"], {"pages": "write", "id-token": "write"})
                    elif (name, job_id) == ("pages.yml", "approval"):
                        self.assertEqual(job["permissions"], {})
                    else:
                        self.assertEqual(job["permissions"], {"contents": "read"})

    def test_write_scopes_appear_only_in_the_guarded_deploy_job(self):
        for name in EXPECTED_WORKFLOWS:
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                writes = re.findall(r"^\s*([\w-]+):\s*write\b", text, re.MULTILINE)
                self.assertEqual(sorted(writes), ["id-token", "pages"] if name == "pages.yml" else [])
                self.assertNotRegex(text, r"write-all|read-all")


class ReleaseHoldTests(unittest.TestCase):
    pages = load("pages.yml")

    def test_deploy_condition_is_exactly_the_guard(self):
        deploy = self.pages["jobs"]["deploy"]
        self.assertEqual(deploy["if"], GUARD)
        self.assertNotRegex(deploy["if"], r"always\(|failure\(|cancelled\(|success\(")
        self.assertEqual(deploy["needs"], "build")
        self.assertEqual(deploy["environment"]["name"], "github-pages")
        self.assertEqual([action_name(step["uses"]) for step in steps_of(deploy)], ["actions/deploy-pages"])

    def test_build_runs_the_reusable_tests_after_approval(self):
        jobs = self.pages["jobs"]
        self.assertEqual(list(jobs), ["approval", "tests", "build", "deploy"])
        self.assertEqual(jobs["tests"]["uses"], "./.github/workflows/tests.yml")
        self.assertEqual(jobs["tests"]["needs"], "approval")
        self.assertEqual(jobs["build"]["needs"], ["approval", "tests"])
        build_runs = "\n".join(step.get("run", "") for step in steps_of(jobs["build"]))
        self.assertIn("scripts/prepare_pages.py --output", build_runs)
        self.assertIn("git rev-parse HEAD", build_runs)
        self.assertIn("actions/upload-pages-artifact", [action_name(s.get("uses", "")) for s in steps_of(jobs["build"])])

    def test_approval_job_and_first_build_step_are_the_shell_guard(self):
        for job_id in ("approval", "build"):
            with self.subTest(job=job_id):
                first = steps_of(self.pages["jobs"][job_id])[0]
                self.assertNotIn("uses", first)
                self.assertEqual(first["env"], GUARD_ENV)
                self.assertIn('[ -n "$APPROVED_SHA" ]', first["run"])

    def test_deployment_actions_only_in_pages_build_and_deploy(self):
        for name, workflow in all_workflows().items():
            for job_id, uses in uses_in(workflow):
                action = action_name(uses)
                with self.subTest(workflow=name, job=job_id, action=action):
                    self.assertNotEqual(action, "actions/configure-pages")
                    if action == "actions/deploy-pages":
                        self.assertEqual((name, job_id), ("pages.yml", "deploy"))
                    if action == "actions/upload-pages-artifact":
                        self.assertEqual((name, job_id), ("pages.yml", "build"))

    def test_guard_expression_semantics(self):
        self.assertTrue(evaluate(GUARD, APPROVED))
        for scenario, context in DENIED.items():
            with self.subTest(scenario=scenario):
                self.assertFalse(evaluate(GUARD, context))

    @unittest.skipUnless(shutil.which("bash"), "needs bash to execute the workflow's guard step")
    def test_shell_guard_fails_closed(self):
        uppercase = {**APPROVED, "inputs.release_sha": SHA.upper()}  # equal for the expression, refused by the shell
        for job_id in ("approval", "build"):
            step = steps_of(self.pages["jobs"][job_id])[0]
            for scenario, context, allowed in ([("approved", APPROVED, True), ("upper-case SHA", uppercase, False)]
                                               + [(name, ctx, False) for name, ctx in DENIED.items()]):
                with self.subTest(job=job_id, scenario=scenario):
                    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **resolve_env(step["env"], context)}
                    result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)


class PinningAndCommandTests(unittest.TestCase):
    def test_actions_are_pinned_to_full_shas_with_version_comments(self):
        pins: dict[str, set[str]] = {}
        for name in EXPECTED_WORKFLOWS:
            lines = [USES_LINE.match(line) for line in (WORKFLOWS / name).read_text(encoding="utf-8").splitlines()]
            matches = [match for match in lines if match]
            self.assertEqual(len(matches), len(uses_in(load(name))), "every uses: line must be parsed")
            for match in matches:
                uses, comment = match.groups()
                with self.subTest(workflow=name, uses=uses):
                    if uses.startswith("./"):
                        self.assertEqual(uses, "./.github/workflows/tests.yml")
                        continue
                    pinned = PINNED.match(uses)
                    self.assertIsNotNone(pinned, "pin actions by a full 40-hex commit SHA")
                    self.assertIn(pinned.group(1), ALLOWED_ACTIONS)
                    self.assertRegex(comment, VERSION_COMMENT)
                    pins.setdefault(pinned.group(1), set()).add(pinned.group(2) + comment.strip())
        for action, revisions in pins.items():
            self.assertEqual(len(revisions), 1, f"{action} is pinned to different revisions: {revisions}")

    def test_checkouts_never_persist_credentials(self):
        for name, workflow in all_workflows().items():
            for job in workflow["jobs"].values():
                for step in steps_of(job):
                    if action_name(step.get("uses", "")) == "actions/checkout":
                        with self.subTest(workflow=name, step=step.get("name")):
                            self.assertEqual(step["with"]["persist-credentials"], "false")
                            self.assertNotIn("ref", step["with"])

    def test_no_write_or_release_commands_and_no_inline_expressions(self):
        for name, workflow in all_workflows().items():
            for script in run_scripts(workflow):
                for label, pattern in FORBIDDEN_COMMANDS.items():
                    with self.subTest(workflow=name, forbidden=label):
                        self.assertIsNone(re.search(pattern, script), script)
                with self.subTest(workflow=name, script=script[:40]):
                    self.assertNotIn("${{", script, "pass expressions through env:, never into the script text")
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"secrets\.|github\.token|GITHUB_TOKEN")

    def test_python_runs_isolated(self):
        for name, workflow in all_workflows().items():
            for script in run_scripts(workflow):
                for argument in re.findall(r'venv/bin/python"\s+(\S+(?:\s+\S+)?)', script):
                    with self.subTest(workflow=name, argument=argument):
                        self.assertTrue(argument.startswith("-I ") or argument == "-m pip", argument)


class TestsWorkflowContentTests(unittest.TestCase):
    workflow = load("tests.yml")

    def runs(self, job_id: str) -> str:
        return "\n".join(step.get("run", "") for step in steps_of(self.workflow["jobs"][job_id]))

    def test_offline_checks(self):
        runs = self.runs("checks")
        for command in ("tests/run_all.py", "scripts/build_catalog.py --verify --check",
                        "scripts/build_thumbnails.py --check", "scripts/build_coverage.py --check",
                        'scripts/fetch_$family.py" --verify', "for family in wikis artists official archives",
                        "git diff --check", "git status --porcelain"):
            with self.subTest(command=command):
                self.assertIn(command, runs)
        for step in steps_of(self.workflow["jobs"]["checks"])[3:]:
            with self.subTest(step=step["name"]):
                self.assertIn("!cancelled()", step["if"], "later checks must still run after a failure")

    def test_browser_smoke_on_loopback_with_installed_chrome(self):
        runs = self.runs("browser")
        self.assertIn("pip install --quiet -r requirements-dev.txt", runs)
        self.assertIn("--bind 127.0.0.1", runs)
        self.assertIn('tests/browser_smoke.py --base http://127.0.0.1:8765/', runs)
        self.assertIn('--browser "$(command -v google-chrome)"', runs)

    def test_site_artifact_is_a_ci_artifact_not_a_deployment(self):
        job = self.workflow["jobs"]["site"]
        runs = self.runs("site")
        self.assertIn('scripts/prepare_pages.py --output "$RUNNER_TEMP/site-build/dynamix"', runs)
        self.assertIn("sha256sum --check", runs)
        self.assertIn("tests/browser_smoke.py --base http://127.0.0.1:8766/dynamix/", runs)
        upload = [step for step in steps_of(job) if "uses" in step]
        self.assertEqual([action_name(step["uses"]) for step in upload], ["actions/checkout", "actions/setup-python",
                                                                          "actions/upload-artifact"])
        self.assertEqual(upload[-1]["with"]["include-hidden-files"], "true")

    def test_check_sources_workflow_only_reports(self):
        workflow = load("check-sources.yml")
        runs = "\n".join(run_scripts(workflow))
        self.assertIn("scripts/check_sources.py --report", runs)
        self.assertNotRegex(runs, r"fetch_\w+\.py|prepare_pages|build_\w+\.py")
        actions = [action_name(uses) for _, uses in uses_in(workflow)]
        self.assertEqual(actions, ["actions/checkout", "actions/setup-python", "actions/upload-artifact"])


if __name__ == "__main__":
    unittest.main()
