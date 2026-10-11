"""Static and behavioral checks of .github/workflows: triggers, release hold, permissions, pinning, no write paths.

The release hold (06-validation-and-release.md, Gate 5) must hold by construction: ordinary pushes and pull requests
only test and upload CI artifacts; Pages deploys only from a manual dispatch of main whose release_sha input and the
repository variable RELEASE_APPROVED_SHA both name the dispatched commit (unset variable = closed); no workflow has a
schedule, pushes, tags, releases or opens pull requests. Workflows are parsed with the stdlib YAML-subset reader in
tests/test_workflows_yaml.py; the deploy guard is also evaluated, and the shell guard executed, for each scenario.
The lint job runs every pre-commit hook read-only and never fixes; the checks job runs the unit tests under coverage
and enforces fail_under. Their shell steps are executed with stand-ins (a fake Node.js, a stand-in test suite).
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_workflows_yaml import parse_yaml  # noqa: E402

WORKFLOWS = ROOT / ".github/workflows"
EXPECTED_WORKFLOWS = {"tests.yml", "pages.yml", "check-sources.yml"}
GUARD = (
    "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && "
    "inputs.release_sha == github.sha && vars.RELEASE_APPROVED_SHA == github.sha"
)
GUARD_ENV = {
    "EVENT_NAME": "${{ github.event_name }}",
    "REF": "${{ github.ref }}",
    "SHA": "${{ github.sha }}",
    "RELEASE_SHA": "${{ inputs.release_sha }}",
    "APPROVED_SHA": "${{ vars.RELEASE_APPROVED_SHA }}",
}
ALLOWED_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "actions/upload-artifact",
    "actions/upload-pages-artifact",
    "actions/deploy-pages",
}
PAGES_ONLY_ACTIONS = {"actions/upload-pages-artifact", "actions/deploy-pages", "actions/configure-pages"}
USES_LINE = re.compile(r"^\s*(?:- )?uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"^([\w.-]+/[\w.-]+)@([0-9a-f]{40})$")
VERSION_COMMENT = re.compile(r"^\s+# v\d+(?:\.\d+){1,2}\s*$")
FORBIDDEN_COMMANDS = {
    "git push": r"\bgit\s+push\b",
    "git tag": r"\bgit\s+tag\b",
    "git commit": r"\bgit\s+commit\b",
    "git remote": r"\bgit\s+remote\b",
    "git config": r"\bgit\s+config\b",
    "gh (any GitHub CLI call)": r"\bgh\s+\w",
    "curl write method": r"\bcurl\b[^\n]*(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)",
    "swallowed failure": r"\|\|\s*(?:true\b|:(?![^\s;)])|echo\b|exit\s+0\b)|\bset\s+\+e\b",
    "token": r"GITHUB_TOKEN|GH_TOKEN|github\.token|secrets\.",
}
AFTER_INSTALL = "${{ !cancelled() && steps.install.outcome == 'success' }}"
COVERAGE_FILE = "${{ runner.temp }}/coverage/.coverage"  # outside the checkout
VENV_PYTHON = '"$RUNNER_TEMP/venv/bin/python"'
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
APPROVED = {
    "github.event_name": "workflow_dispatch",
    "github.ref": "refs/heads/main",
    "github.sha": SHA,
    "inputs.release_sha": SHA,
    "vars.RELEASE_APPROVED_SHA": SHA,
}
DENIED = {  # each must neither satisfy the deploy condition nor pass the shell guard
    "push to main": {**APPROVED, "github.event_name": "push", "inputs.release_sha": ""},
    "pull request": {
        **APPROVED,
        "github.event_name": "pull_request",
        "github.ref": "refs/pull/1/merge",
        "inputs.release_sha": "",
    },
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


def run_guard(step: dict, context: dict) -> subprocess.CompletedProcess:
    """Execute a shell guard step with bash -eo pipefail, its env resolved from ``context``."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **resolve_env(step["env"], context)}
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], env=env, capture_output=True, text=True
    )


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
        self.assertEqual(
            pages_on["workflow_dispatch"],
            {
                "inputs": {
                    "release_sha": {
                        "description": pages_on["workflow_dispatch"]["inputs"]["release_sha"]["description"],
                        "required": "true",
                        "type": "string",
                    }
                }
            },
        )

    def test_no_schedule_or_privileged_triggers_anywhere(self):
        for name, workflow in all_workflows().items():
            with self.subTest(workflow=name):
                self.assertFalse(
                    set(workflow["on"])
                    & {
                        "schedule",
                        "pull_request_target",
                        "workflow_run",
                        "repository_dispatch",
                        "release",
                        "issue_comment",
                    }
                )
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
        self.assertIn(
            "actions/upload-pages-artifact", [action_name(s.get("uses", "")) for s in steps_of(jobs["build"])]
        )

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

    def test_header_comment_describes_the_guard_not_a_hold(self):
        """A maintainer reads this comment at dispatch time: it describes the guard as it works after a release (the
        variable names the released commit), not a hold during which the variable stays unset."""
        header = []
        for line in (WORKFLOWS / "pages.yml").read_text(encoding="utf-8").splitlines():
            if line.startswith("on:"):
                break
            if line.startswith("#"):
                header.append(line.lstrip("#").strip())
        text = " ".join(header)
        for stale in ("stays unset", "during the hold", "release hold", "every dispatch fails closed"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, text.lower())
        for required in (
            "RELEASE_APPROVED_SHA",
            "release_sha",
            "main",
            "last released commit",
            "unset",
            "fails closed",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

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
            for scenario, context, allowed in [("approved", APPROVED, True), ("upper-case SHA", uppercase, False)] + [
                (name, ctx, False) for name, ctx in DENIED.items()
            ]:
                with self.subTest(job=job_id, scenario=scenario):
                    result = run_guard(step, context)
                    self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("bash"), "needs bash to execute the workflow's guard step")
    def test_guard_messages_describe_the_guard_not_a_hold(self):
        """The variable names the last released commit; when it is unset or empty at dispatch time, the message says
        that no commit is approved and the dispatch fails closed, not that a release hold is in force. Both guard steps
        are the same script."""
        steps = [steps_of(self.pages["jobs"][job_id])[0] for job_id in ("approval", "build")]
        self.assertEqual(steps[0]["run"], steps[1]["run"])
        messages = re.findall(r'\bfail "([^"]*)"', steps[0]["run"])
        self.assertEqual(len(messages), 6, messages)
        for message in messages:
            with self.subTest(message=message):
                self.assertNotRegex(message, re.compile(r"\bhold\b|\bheld\b", re.IGNORECASE))
        for scenario in ("variable unset (release hold)", "variable empty"):
            for job_id, step in zip(("approval", "build"), steps, strict=True):
                with self.subTest(job=job_id, scenario=scenario):
                    result = run_guard(step, DENIED[scenario])
                    self.assertNotEqual(result.returncode, 0)
                    error = [line for line in result.stdout.splitlines() if line.startswith("::error::")]
                    self.assertEqual(len(error), 1, result.stdout)
                    for phrase in ("RELEASE_APPROVED_SHA", "no approved sha", "fails closed"):
                        self.assertIn(phrase.casefold(), error[0].casefold())
                    self.assertNotRegex(error[0], re.compile(r"\bhold\b", re.IGNORECASE))


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

    def test_forbidden_patterns_are_not_vacuous(self):
        samples = {
            "git push": "git push origin HEAD:main",
            "git tag": "git tag v1.0.0",
            "git commit": "git commit -m x",
            "git remote": "git remote set-url origin x",
            "git config": "git config user.name bot",
            "gh (any GitHub CLI call)": "gh release create v1",
            "curl write method": "curl -X POST https://x",
            "token": 'echo "$GITHUB_TOKEN"',
        }
        for label, sample in samples.items():
            with self.subTest(forbidden=label):
                self.assertRegex(sample, FORBIDDEN_COMMANDS[label])
        self.assertRegex("gh pr create --fill", FORBIDDEN_COMMANDS["gh (any GitHub CLI call)"])
        for swallowed in (
            "check || true",
            "check || :",
            "check ||:;",
            "check || echo failed",
            "check || exit 0",
            "set +e",
        ):
            with self.subTest(swallowed=swallowed):
                self.assertRegex(swallowed, FORBIDDEN_COMMANDS["swallowed failure"])
        for fine in ('command -v x || { echo "::error::x"; exit 1; }', "[ -f a ] || { continue; }", "a::b"):
            with self.subTest(fine=fine):
                self.assertNotRegex(fine, FORBIDDEN_COMMANDS["swallowed failure"])

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
        for command in (
            "tests/run_all.py",
            "scripts/build_catalog.py --verify --check",
            "scripts/build_thumbnails.py --check",
            "scripts/build_coverage.py --check",
            "scripts/update_docs_stats.py --check",
            'scripts/fetch_$family.py" --verify',
            "for family in wikis artists official archives",
            "git diff --check",
            "git status --porcelain",
        ):
            with self.subTest(command=command):
                self.assertIn(command, runs)
        for step in steps_of(self.workflow["jobs"]["checks"])[3:]:
            with self.subTest(step=step["name"]):
                self.assertIn("!cancelled()", step["if"], "later checks must still run after a failure")

    def test_browser_smoke_on_loopback_with_installed_chrome(self):
        runs = self.runs("browser")
        self.assertIn("pip install --quiet -r requirements-dev.txt", runs)
        self.assertIn("--bind 127.0.0.1", runs)
        self.assertIn("tests/browser_smoke.py --base http://127.0.0.1:8765/", runs)
        self.assertIn('--browser "$(command -v google-chrome)"', runs)

    def test_site_artifact_is_a_ci_artifact_not_a_deployment(self):
        job = self.workflow["jobs"]["site"]
        runs = self.runs("site")
        self.assertIn('scripts/prepare_pages.py --output "$RUNNER_TEMP/site-build/dynamix"', runs)
        self.assertIn("sha256sum --check", runs)
        self.assertIn("tests/browser_smoke.py --base http://127.0.0.1:8766/dynamix/", runs)
        upload = [step for step in steps_of(job) if "uses" in step]
        self.assertEqual(
            [action_name(step["uses"]) for step in upload],
            ["actions/checkout", "actions/setup-python", "actions/upload-artifact"],
        )
        self.assertEqual(upload[-1]["with"]["include-hidden-files"], "true")

    def test_check_sources_workflow_only_reports(self):
        workflow = load("check-sources.yml")
        runs = "\n".join(run_scripts(workflow))
        self.assertIn("scripts/check_sources.py --report", runs)
        self.assertNotRegex(runs, r"fetch_\w+\.py|prepare_pages|build_\w+\.py")
        actions = [action_name(uses) for _, uses in uses_in(workflow)]
        self.assertEqual(actions, ["actions/checkout", "actions/setup-python", "actions/upload-artifact"])

    def step(self, job_id: str, prefix: str) -> dict:
        steps = [step for step in steps_of(self.workflow["jobs"][job_id]) if step.get("name", "").startswith(prefix)]
        self.assertEqual(len(steps), 1, f"{job_id}: {prefix}")
        return steps[0]

    def test_lint_job_runs_every_hook_read_only_and_never_fixes(self):
        job = self.workflow["jobs"]["lint"]
        self.assertEqual(job["name"], "Lint, format and type checks")
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertNotIn("if", job, "a job condition could skip the lint job, also in the pages.yml call")
        actions = [action_name(step["uses"]) for step in steps_of(job) if "uses" in step]
        self.assertEqual(actions, ["actions/checkout", "actions/setup-python"])
        install = steps_of(job)[2]
        self.assertEqual(install["id"], "install")
        self.assertIn(f"{VENV_PYTHON} -m pip install --quiet -r requirements-lint.txt", install["run"].splitlines())
        self.assertRegex(install["run"], r"(?m)^npm ci --ignore-scripts\b")
        engines = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["engines"]["node"]
        self.assertIn(engines, install["run"], "the Node.js check names the range of package.json")
        hooks = self.step("lint", "Every pre-commit hook on every file")
        self.assertEqual(hooks["if"], AFTER_INSTALL)
        self.assertEqual(hooks["env"], {"PRE_COMMIT_HOME": "${{ runner.temp }}/pre-commit"})
        self.assertEqual(hooks["run"], f"{VENV_PYTHON} -I -m pre_commit run --all-files --show-diff-on-failure")
        unchanged = steps_of(job)[-1]
        self.assertEqual(unchanged["name"], "Lint left the checkout unchanged")
        self.assertIn("git status --porcelain --untracked-files=all", unchanged["run"])
        for step in steps_of(job)[3:]:
            with self.subTest(step=step["name"]):
                self.assertIn("!cancelled()", step["if"])
        runs = self.runs("lint")
        self.assertNotRegex(runs, r"--(?:fix|write|unsafe-fixes)\b", "the lint job checks; it never rewrites files")
        self.assertNotRegex(runs, r"\bpre[-_]commit\s+(?:install|autoupdate)\b")
        self.assertNotRegex(runs, r"\bnpm\s+(?:install|i|update|exec)\b|\bnpx\b", "only `npm ci` from the lockfile")

    def test_unit_tests_run_under_coverage_with_data_outside_the_checkout(self):
        job = self.workflow["jobs"]["checks"]
        self.assertIn(
            f"{VENV_PYTHON} -m pip install --quiet -r requirements.txt -r requirements-lint.txt",
            steps_of(job)[2]["run"].splitlines(),
        )
        unit = [step for step in steps_of(job) if "tests/run_all.py" in step.get("run", "")]
        self.assertEqual(len(unit), 1)
        self.assertEqual(unit[0]["if"], AFTER_INSTALL)
        self.assertEqual(unit[0]["env"], {"COVERAGE_FILE": COVERAGE_FILE})
        self.assertEqual(
            unit[0]["run"].splitlines(),
            ['mkdir -p "$RUNNER_TEMP/coverage"', f"{VENV_PYTHON} -I -m coverage run tests/run_all.py"],
        )

    def test_coverage_step_publishes_the_table_and_enforces_fail_under(self):
        steps = steps_of(self.workflow["jobs"]["checks"])
        report = self.step("checks", "Coverage of scripts/ by the unit tests")
        unit = next(n for n, step in enumerate(steps) if "tests/run_all.py" in step.get("run", ""))
        self.assertIs(steps[unit + 1], report, "the report follows the unit tests")
        self.assertEqual(report["if"], AFTER_INSTALL)
        self.assertEqual(report["env"], {"COVERAGE_FILE": COVERAGE_FILE})
        commands = [line.strip() for line in report["run"].splitlines() if line.strip()]
        self.assertEqual(commands[0], f'{VENV_PYTHON} -I -m coverage combine --quiet "$RUNNER_TEMP/coverage"')
        self.assertEqual(commands[-1], f"{VENV_PYTHON} -I -m coverage report", "the last command enforces fail_under")
        self.assertIn(f"{VENV_PYTHON} -I -m coverage report --format=markdown --fail-under=0", commands)
        self.assertIn('} >> "$GITHUB_STEP_SUMMARY"', commands)
        self.assertEqual(
            re.findall(r"--fail-under\S*", report["run"]),
            ["--fail-under=0"],
            "only the summary table may ignore fail_under",
        )

    def test_pages_reaches_lint_and_coverage_through_the_reusable_workflow(self):
        pages = load("pages.yml")
        self.assertEqual(pages["jobs"]["tests"]["uses"], "./.github/workflows/tests.yml")
        self.assertIn("tests", pages["jobs"]["build"]["needs"])
        self.assertIn("workflow_call", self.workflow["on"])
        for job_id in ("lint", "checks"):
            with self.subTest(job=job_id):
                self.assertNotIn("if", self.workflow["jobs"][job_id], "a job condition could skip it in a release")
        self.assertIn("coverage report", self.runs("checks"))
        self.assertIn("pre_commit run --all-files", self.runs("lint"))


GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git to execute workflow steps")
class StepBehaviorTests(unittest.TestCase):
    """Execute the checks job's own shell steps in temporary trees: a failing check must fail its step."""

    workflow = load("tests.yml")

    def script(self, prefix: str, job_id: str = "checks") -> str:
        steps = [step for step in steps_of(self.workflow["jobs"][job_id]) if step["name"].startswith(prefix)]
        self.assertEqual(len(steps), 1, prefix)
        return steps[0]["run"]

    def bash(self, script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess:
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), **GIT_ENV, **env}
        # GitHub Actions runs `shell: bash` steps as `bash --noprofile --norc -eo pipefail {0}`.
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
        )

    def repository(self, base: Path, files: dict[str, bytes]) -> Path:
        root = base / "checkout"
        root.mkdir()
        self.bash("git init -q", root)
        for rel, data in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        self.assertEqual(self.bash("git add -A && git commit -q -m fixture", root).returncode, 0)
        return root

    def test_family_verify_loop_fails_when_any_present_manifest_fails(self):
        script = self.script("Every family manifest")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            venv = base / "runner/venv/bin"
            venv.mkdir(parents=True)
            (venv / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
            (venv / "python").chmod(0o755)
            root = base / "checkout"
            for family, body in (
                ("wikis", "raise SystemExit(1)\n"),
                ("artists", "print('verified')\n"),
                ("archives", "print('verified')\n"),
            ):
                (root / "scripts").mkdir(parents=True, exist_ok=True)
                (root / "data/sources").mkdir(parents=True, exist_ok=True)
                (root / f"scripts/fetch_{family}.py").write_text(
                    "import sys\nassert sys.argv[1:] == ['--verify'] and sys.flags.isolated\n" + body, encoding="utf-8"
                )
                (root / f"data/sources/{family}.json").write_text("{}\n", encoding="utf-8")
            failing = self.bash(script, root, RUNNER_TEMP=str(base / "runner"))
            self.assertNotEqual(failing.returncode, 0, failing.stdout + failing.stderr)
            self.assertIn("::error file=data/sources/wikis.json::fetch_wikis.py --verify failed", failing.stdout)
            self.assertIn("data/sources/official.json is absent", failing.stdout)
            self.assertIn("verified 2 family manifests", failing.stdout)
            (root / "scripts/fetch_wikis.py").write_text("print('verified')\n", encoding="utf-8")
            passing = self.bash(script, root, RUNNER_TEMP=str(base / "runner"))
            self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
            self.assertIn("verified 3 family manifests", passing.stdout)
            (root / "scripts/fetch_artists.py").unlink()  # a manifest without its verifier is a failure, not a skip
            missing = self.bash(script, root, RUNNER_TEMP=str(base / "runner"))
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("fetch_artists.py --verify failed", missing.stdout)

    def test_whitespace_step_fails_on_committed_whitespace_errors_except_pinned_bytes(self):
        script = self.script("No whitespace errors")
        with tempfile.TemporaryDirectory() as temporary:
            files = {
                ".gitattributes": b"/assets/** -text\n",
                "README.md": b"# ok\n",
                "assets/public/x/sidecar.txt": b"pinned bytes with trailing space \n",
            }
            root = self.repository(Path(temporary), files)
            clean = self.bash(script, root)
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            (root / "docs").mkdir()
            (root / "docs/notes.md").write_bytes(b"trailing space \n")
            self.assertEqual(self.bash("git add -A && git commit -q -m whitespace", root).returncode, 0)
            committed = self.bash(script, root)
            self.assertNotEqual(committed.returncode, 0)
            self.assertIn("docs/notes.md:1: trailing whitespace", committed.stdout)

    def test_source_check_step_publishes_the_report_and_fails_when_incomplete(self):
        steps = [
            step
            for step in steps_of(load("check-sources.yml")["jobs"]["report"])
            if step.get("name", "").startswith("Compare live metadata")
        ]
        self.assertEqual(len(steps), 1)
        fake = (
            '#!/bin/sh\n[ "$1 $2 $3" = "-I scripts/check_sources.py --report" ] || exit 99\n'
            'if [ -n "$WRITE_REPORT" ]; then printf "## Source metadata check\\n" > "$4"; fi\n'
            'exit "$FAKE_STATUS"\n'
        )
        for status, write, expected, message in (
            ("0", "1", 0, "listed completely"),
            ("3", "1", 3, "::error::Live source check incomplete"),
            ("1", "", 1, "::error::check_sources.py failed with exit status 1"),
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                venv = base / "runner/venv/bin"
                venv.mkdir(parents=True)
                (venv / "python").write_text(fake, encoding="utf-8")
                (venv / "python").chmod(0o755)
                summary = base / "step-summary.md"
                result = self.bash(
                    steps[0]["run"],
                    base,
                    RUNNER_TEMP=str(base / "runner"),
                    GITHUB_STEP_SUMMARY=str(summary),
                    FAKE_STATUS=status,
                    WRITE_REPORT=write,
                )
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertIn(message, result.stdout)
                published = summary.read_text(encoding="utf-8") if summary.exists() else ""
                self.assertEqual(
                    "## Source metadata check" in published,
                    bool(write),
                    "a written report is published even when the check is incomplete",
                )

    def test_unchanged_checkout_step_fails_on_any_modification(self):
        script = self.script("Checks left the checkout unchanged")
        with tempfile.TemporaryDirectory() as temporary:
            root = self.repository(Path(temporary), {".gitignore": b"__pycache__/\n", "data/catalog.json": b"{}\n"})
            (root / "scripts/__pycache__").mkdir(parents=True)
            (root / "scripts/__pycache__/x.pyc").write_bytes(b"\0")  # ignored byproduct of running scripts
            self.assertEqual(self.bash(script, root).returncode, 0)
            for change in ("data/catalog.json", "data/new.json"):
                with self.subTest(change=change):
                    (root / change).write_text('{"changed": true}\n', encoding="utf-8")
                    result = self.bash(script, root)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(change, result.stdout)
                    self.bash("git checkout -q -- . && git clean -q -f -- data", root)

    def test_lint_byproducts_do_not_trip_the_unchanged_checkout_step(self):
        script = self.script("Lint left the checkout unchanged", "lint")
        with tempfile.TemporaryDirectory() as temporary:
            files = {".gitignore": (ROOT / ".gitignore").read_bytes(), "src/archive.js": b"'use strict';\n"}
            root = self.repository(Path(temporary), files)
            for byproduct in (
                "node_modules/eslint/package.json",
                ".ruff_cache/0.17.0/12345",
                ".mypy_cache/3.14/x.json",
                ".coverage",
                ".coverage.runner.1234.XaBc",
            ):
                (root / byproduct).parent.mkdir(parents=True, exist_ok=True)
                (root / byproduct).write_bytes(b"{}\n")
            clean = self.bash(script, root)
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            (root / "src/archive.js").write_bytes(b'"use strict";\n')  # what a formatter or --fix would do
            changed = self.bash(script, root)
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn("::error::a lint hook modified the checkout", changed.stdout)
            self.assertIn("src/archive.js", changed.stdout)

    def test_lint_install_step_requires_a_supported_node(self):
        script = self.script("Install the pinned lint tools", "lint")
        versions = {
            "v20.19.0": True,
            "v20.20.2": True,
            "v22.13.0": True,
            "v22.21.1": True,
            "v24.0.0": True,
            "v25.1.0": True,
            "v18.20.8": False,
            "v20.18.3": False,
            "v21.7.3": False,
            "v22.12.0": False,
            "v23.11.1": False,
            "unknown": False,
        }
        stubs = {
            "python": '[ "$1 $2" = "-m venv" ] || exit 97\nmkdir -p "$3/bin"\n'
            'printf \'#!/bin/sh\\necho "venv-python $*" >> "$CALLS"\\n\' > "$3/bin/python"\n'
            'chmod +x "$3/bin/python"\n',
            "node": '[ "$1" = "--version" ] || exit 97\necho "$FAKE_NODE"\n',
            "npm": 'echo "npm $*" >> "$CALLS"\n',
        }
        for version, supported in versions.items():
            with self.subTest(node=version), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                (base / "bin").mkdir()
                for name, body in stubs.items():
                    (base / "bin" / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
                    (base / "bin" / name).chmod(0o755)
                calls = base / "calls.log"
                result = self.bash(
                    script,
                    base,
                    PATH=f"{base / 'bin'}:/usr/bin:/bin",
                    RUNNER_TEMP=str(base / "runner"),
                    CALLS=str(calls),
                    FAKE_NODE=version,
                )
                logged = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
                self.assertIn("venv-python -m pip install --quiet -r requirements-lint.txt", logged)
                self.assertIn(f"Node.js {version}", result.stdout)
                if supported:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(logged[-1], "npm ci --ignore-scripts --no-audit --no-fund")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"::error::Node.js {version} does not satisfy", result.stdout)
                    self.assertFalse([line for line in logged if line.startswith("npm")], "no npm ci after a refusal")

    @unittest.skipUnless(importlib.util.find_spec("coverage"), "needs coverage.py from requirements-lint.txt")
    def test_coverage_steps_measure_isolated_subprocesses_and_enforce_fail_under(self):
        """The unit-test and coverage steps as written, with the real pyproject.toml, on a stand-in suite whose
        tests/run_all.py starts ``python -I scripts/child.py``: the child must be measured (or the report would fall
        below fail_under), the data stays outside the checkout, and an unexecuted script makes the report fail."""
        unit = next(step for step in steps_of(self.workflow["jobs"]["checks"]) if "run_all.py" in step.get("run", ""))
        report = self.script("Coverage of scripts/ by the unit tests")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runner = base / "runner"
            (runner / "venv/bin").mkdir(parents=True)
            (runner / "venv/bin/python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
            (runner / "venv/bin/python").chmod(0o755)
            root = base / "checkout"
            (root / "scripts").mkdir(parents=True)
            (root / "tests").mkdir()
            shutil.copyfile(ROOT / "pyproject.toml", root / "pyproject.toml")
            (root / "scripts/child.py").write_text('print("measured in an isolated subprocess")\n', encoding="utf-8")
            (root / "tests/run_all.py").write_text(
                "import subprocess, sys\nsubprocess.run([sys.executable, '-I', 'scripts/child.py'], check=True)\n",
                encoding="utf-8",
            )
            summary = base / "step-summary.md"
            env = {
                "RUNNER_TEMP": str(runner),
                "COVERAGE_FILE": str(runner / "coverage/.coverage"),
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            tests = self.bash(unit["run"], root, **env)
            self.assertEqual(tests.returncode, 0, tests.stdout + tests.stderr)
            self.assertIn("measured in an isolated subprocess", tests.stdout)
            self.assertTrue(list((runner / "coverage").glob(".coverage.*")), "one data file per measured process")
            self.assertEqual(sorted(path.name for path in root.rglob(".coverage*")), [], "no data in the checkout")
            passing = self.bash(report, root, **env)
            self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
            self.assertRegex(passing.stdout, r"scripts/child\.py\s.*\b100(?:\.0)?%")
            self.assertIn("### Coverage of scripts/ by the unit tests", summary.read_text(encoding="utf-8"))
            self.assertIn("scripts/child.py", summary.read_text(encoding="utf-8"))
            # An unexecuted script lowers the total below fail_under: the step fails, the table is still published.
            (root / "scripts/unexecuted.py").write_text(
                "".join(f"value_{n} = {n}\n" for n in range(60)), encoding="utf-8"
            )
            shutil.rmtree(runner / "coverage")
            summary.unlink()
            self.assertEqual(self.bash(unit["run"], root, **env).returncode, 0)
            failing = self.bash(report, root, **env)
            self.assertEqual(failing.returncode, 2, failing.stdout + failing.stderr)
            self.assertIn("is less than fail-under", failing.stdout)
            self.assertIn("scripts/unexecuted.py", summary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
