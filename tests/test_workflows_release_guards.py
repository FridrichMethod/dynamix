"""The shell guards of the release gate of .github/workflows/pages.yml, executed for each scenario.

tests/test_workflows_release_gate.py holds the scenarios, the deploy condition and the checks of the workflow as
written. Here the guard steps of the approval and the build job run in bash as GitHub runs them: the event guard, the
Release lookup with a stand-in curl (it records each request and answers with the scenario's response, so no request
leaves the machine) and the Release guard; the main guard in temporary Git repositories with a local bare origin, also
with a stand-in timeout that ends its read of origin; the approval job's record step; and the Re-run check job's step.
The jobs of a run and of its re-runs also run here as GitHub decides them (run_attempt()), each job's if: as written.
"""

import functools
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

from test_workflows import GIT_ENV, evaluate, resolve_env, steps_of  # noqa: E402
from test_workflows_release_gate import (  # noqa: E402
    ACCEPTED,
    DENIED,
    LOOKUP,
    LS_REMOTE_SECONDS,
    RELEASE_LOOKUP_DENIED,
    REPOSITORY,
    RERUN_CHECK,
    RERUN_CHECK_ERROR,
    SHA,
    TAGGED,
    PagesJobs,
    approved,
    dispatched,
    released,
    triggered,
)

API_HEADERS = ["Accept: application/vnd.github+json", "X-GitHub-Api-Version: 2022-11-28"]
# The options the lookup may give curl, and whether each takes a value: no method, data, form, upload, config, redirect
# or credential option, so the request is a GET of the one URL.
CURL_OPTIONS = {
    "--silent": False,
    "--show-error": False,
    "--proto": True,
    "--max-time": True,
    "--header": True,
    "--output": True,
    "--write-out": True,
}
# The longest --max-time (whole seconds) the lookup may give curl: a hung request then fails the lookup, and with it the
# run, within two minutes instead of at the job's timeout.
LOOKUP_MAX_SECONDS = 120
TOKEN = "stand-in-job-token"

# How origin's tags change after the checkout (SEC-2), which the checkout's own tags do not show: the tag, the run's
# commit, the git commands on origin and the commit that the main guard's refusal names (None: the guard passes). The
# names first and second stand for the commits of release_repository(), whose checkout keeps v1.1.0 (annotated) on
# second and v1.0.0 (lightweight) on first. Refs whose names end in the tag's name sort before and after it in the
# output of git ls-remote (refs/heads/... and refs/tags/a/... before it, refs/tags/x/... after it), and an annotated
# one adds its own peeled ^{} line right after its own line, so the decoys' ^{} lines come both before and after the
# tag's (and a lightweight tag has none): only the exact names count, the peeled line's included, not the first or the
# last ^{} line.
ORIGIN_CHANGES = {
    "an annotated tag as origin has it": ("v1.1.0", "second", (), None),
    "a lightweight tag as origin has it": ("v1.0.0", "first", (), None),
    "an annotated tag re-created on origin on the same commit": (
        "v1.1.0",
        "second",
        (("tag", "-f", "-a", "-m", "again", "v1.1.0", "second"),),
        None,
    ),
    "an annotated tag re-created on origin as a lightweight one": (
        "v1.1.0",
        "second",
        (("tag", "-f", "v1.1.0", "second"),),
        None,
    ),
    "refs on origin whose names end in an annotated tag's name": (
        "v1.1.0",
        "second",
        (
            ("update-ref", "refs/heads/refs/tags/v1.1.0", "first"),
            ("tag", "-a", "-m", "decoy", "a/refs/tags/v1.1.0", "first"),
            ("tag", "-a", "-m", "decoy", "x/refs/tags/v1.1.0", "first"),
        ),
        None,
    ),
    "refs on origin whose names end in a lightweight tag's name": (
        "v1.0.0",
        "first",
        (
            ("update-ref", "refs/heads/refs/tags/v1.0.0", "second"),
            ("tag", "-a", "-m", "decoy", "a/refs/tags/v1.0.0", "second"),
            ("tag", "-a", "-m", "decoy", "x/refs/tags/v1.0.0", "second"),
        ),
        None,
    ),
    "an annotated tag moved on origin": (
        "v1.1.0",
        "second",
        (("tag", "-f", "-a", "-m", "moved", "v1.1.0", "first"),),
        "first",
    ),
    "an annotated tag moved on origin as a lightweight one": (
        "v1.1.0",
        "second",
        (("tag", "-f", "v1.1.0", "first"),),
        "first",
    ),
    "an annotated tag moved on origin, with an annotated tag whose name ends in its name on the run's commit": (
        "v1.1.0",
        "second",
        (
            ("tag", "-f", "-a", "-m", "moved", "v1.1.0", "first"),
            ("tag", "-a", "-m", "decoy", "a/refs/tags/v1.1.0", "second"),
            ("tag", "-a", "-m", "decoy", "x/refs/tags/v1.1.0", "second"),
        ),
        "first",
    ),
    "a lightweight tag moved on origin": ("v1.0.0", "first", (("tag", "-f", "v1.0.0", "second"),), "second"),
    "a tag deleted on origin": ("v1.1.0", "second", (("tag", "-d", "v1.1.0"),), "no commit"),
}


# A stand-in for GNU timeout, put first on PATH: it writes its arguments, one per line, to $STAND_IN_TIMEOUT_CALL and
# exits at once with 124, the status of timeout when the command ran over the limit, without starting the command (F2).
STAND_IN_TIMEOUT = '#!/bin/sh\nprintf "%s\\n" "$@" > "$STAND_IN_TIMEOUT_CALL"\nexit 124\n'


# A job if: that names a status function is evaluated whatever the results of the jobs it needs; GitHub prefixes
# success() to any other, which then runs only when every job it needs succeeded (GitHub's "Status check functions").
STATUS_FUNCTION = re.compile(r"\b(?:always|success|failure|cancelled)\s*\(")


def needed(job: dict) -> list[str]:
    """The jobs that ``job`` needs, as a list (needs: may name one job without brackets)."""
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def rerun_jobs(jobs: dict[str, dict], selected: set[str]) -> set[str]:
    """The jobs that GitHub runs again when a re-run selects ``selected``: those and every job that needs one of
    them, directly or through other jobs ("Re-run failed jobs" selects the failed jobs, "Re-run this job" one job)."""
    found = set(selected)
    while extra := {job_id for job_id, job in jobs.items() if job_id not in found and found & set(needed(job))}:
        found |= extra
    return found


def run_attempt(
    jobs: dict[str, dict], context: dict, number: int, rerun: set[str], failing: set[str], previous: dict
) -> dict[str, tuple[str, dict]]:
    """Attempt ``number`` of a run as GitHub runs its jobs, in file order (each needs only jobs above it), as {job:
    (result, outputs)}. A job outside ``rerun`` keeps its result and outputs of ``previous``. A job in it runs when
    its if: holds (STATUS_FUNCTION), with the results and outputs of the jobs it needs, and then fails when it is in
    ``failing`` or is the Re-run check job (its one step fails: test_the_rerun_check_step_fails_with_its_reason);
    otherwise it succeeds, and the approval job records the commit and this attempt (its record step). A job that
    does not run is skipped."""
    state: dict[str, tuple[str, dict]] = {}
    for job_id, job in jobs.items():
        if job_id not in rerun:
            state[job_id] = previous[job_id]
            continue
        seen = {**context, "github.run_attempt": str(number)}
        for need in needed(job):
            result, outputs = state[need]
            seen[f"needs.{need}.result"] = result
            seen.update({f"needs.{need}.outputs.{name}": value for name, value in outputs.items()})
        condition = job.get("if")
        if condition is not None and STATUS_FUNCTION.search(condition):
            runs = evaluate(condition, seen)
        else:
            runs = all(state[need][0] == "success" for need in needed(job))
            runs = runs and (condition is None or evaluate(condition, seen))
        if not runs:
            state[job_id] = ("skipped", {})
        elif job_id in failing or job_id == RERUN_CHECK:
            state[job_id] = ("failure", {})
        else:
            state[job_id] = ("success", {"commit": SHA, "attempt": str(number)} if job_id == "approval" else {})
    return state


FIRST = "first attempt"
ALL = "Re-run all jobs"
FAILED = "Re-run failed jobs"
# Each attempt of a run of a published release: how it was started (FIRST, ALL, FAILED, or the id of the job whose
# "Re-run this job" started it) and the jobs that fail when they run; then whether the last attempt deployed and whether
# its Re-run check job ran (PROD-1). A re-run that leaves out the approval job, after a failed deploy, test or build job
# or of the deploy or build job alone, keeps the approval job's record of an earlier attempt: its deploy job is skipped
# and its Re-run check job runs and fails. A first attempt or a re-run of all jobs never runs it, nor does a run that the
# approval job refused.
RERUNS = {
    "a first attempt": (((FIRST, ()),), True, False),
    "a first attempt that the approval job refuses": (((FIRST, ("approval",)),), False, False),
    "a first attempt whose tests fail": (((FIRST, ("tests",)),), False, False),
    "a first attempt whose deployment fails": (((FIRST, ("deploy",)),), False, False),
    "all jobs re-run after a deployment": (((FIRST, ()), (ALL, ())), True, False),
    "all jobs re-run after the deployment failed": (((FIRST, ("deploy",)), (ALL, ())), True, False),
    "all jobs re-run, refused by the approval job this time": (((FIRST, ()), (ALL, ("approval",))), False, False),
    "failed jobs re-run after the deployment failed": (((FIRST, ("deploy",)), (FAILED, ())), False, True),
    "failed jobs re-run after the tests failed": (((FIRST, ("tests",)), (FAILED, ())), False, True),
    "failed jobs re-run after the build failed": (((FIRST, ("build",)), (FAILED, ())), False, True),
    "failed jobs re-run after the approval job refused": (((FIRST, ("approval",)), (FAILED, ())), True, False),
    "the deploy job re-run after a deployment": (((FIRST, ()), ("deploy", ())), False, True),
    "the build job re-run after a deployment": (((FIRST, ()), ("build", ())), False, True),
    "the approval job re-run after a deployment": (((FIRST, ()), ("approval", ())), True, False),
    "the Re-run check job re-run after a re-run of the deploy job": (
        ((FIRST, ()), ("deploy", ()), (RERUN_CHECK, ())),
        False,
        True,
    ),
    "all jobs re-run after a re-run of the deploy job": (((FIRST, ()), ("deploy", ()), (ALL, ())), True, False),
    "failed jobs re-run after a re-run of all jobs failed to deploy": (
        ((FIRST, ()), (ALL, ("deploy",)), (FAILED, ())),
        False,
        True,
    ),
}


WIDE_DIGIT = "\u0663"  # ARABIC-INDIC DIGIT THREE


@functools.cache
def wide_digit_locale() -> str | None:
    """An installed locale in which bash's ``[0-9]`` also matches digits of other scripts, as glibc's en_US.UTF-8
    does: the guards run in it (run_guard), so a guard that relies on the locale for its tag check is caught. None
    when bash or such a locale is missing; the guards then run in the C locale."""
    if not shutil.which("bash"):
        return None
    for name in ("en_US.UTF-8", "en_GB.UTF-8", "de_DE.UTF-8", "fr_FR.UTF-8"):
        probe = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", '[[ "$PROBE" =~ ^[0-9]$ ]]'],
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": name, "PROBE": WIDE_DIGIT},
            capture_output=True,
        )
        if probe.returncode == 0:
            return name
    return None


def run_guard(step: dict, context: dict, cwd: Path | None = None, **extra: str) -> subprocess.CompletedProcess:
    """Execute a shell guard step with bash -eo pipefail, its env resolved from ``context`` (plus ``extra``), in the
    locale of :func:`wide_digit_locale` when there is one (the step's own env, LC_ALL included, takes precedence)."""
    locale = {"LANG": name} if (name := wide_digit_locale()) else {}
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        **GIT_ENV,
        **locale,
        **extra,
        **resolve_env(step["env"], context),
    }
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def error_lines(result: subprocess.CompletedProcess) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("::error::")]


# A stand-in for curl, put first on PATH: it records each call (arguments and standard input) and answers with the
# scenario's response, which a test writes to response.json in $STAND_IN_CURL; without one it fails (status 99).
STAND_IN_CURL = """\
import json, os, sys
from pathlib import Path

state = Path(os.environ["STAND_IN_CURL"])
arguments = sys.argv[1:]
with open(state / "calls.jsonl", "a", encoding="utf-8") as calls:
    calls.write(json.dumps({"arguments": arguments, "stdin": sys.stdin.read()}) + "\\n")
if not (state / "response.json").exists():
    sys.exit(99)
status, body, exit_status = json.loads((state / "response.json").read_text(encoding="utf-8"))
if exit_status == 0 and "--output" in arguments:
    Path(arguments[arguments.index("--output") + 1]).write_text(body, encoding="utf-8")
sys.stdout.write(status)
sys.exit(exit_status)
"""


def stand_in_curl(base: Path) -> tuple[Path, Path]:
    """A directory holding the stand-in ``curl`` (to prepend to PATH) and its state directory, under ``base``."""
    tools, state = base / "bin", base / "curl-state"
    tools.mkdir()
    state.mkdir()
    (tools / "curl.py").write_text(STAND_IN_CURL, encoding="utf-8")
    (tools / "curl").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{tools / "curl.py"}" "$@"\n', encoding="utf-8")
    (tools / "curl").chmod(0o755)
    return tools, state


def curl_calls(state: Path) -> list[dict]:
    """The stand-in curl's calls so far, oldest first: each one's ``arguments`` and ``stdin``."""
    calls = state / "calls.jsonl"
    return [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []


def lookup_environment(base: Path, context: dict) -> tuple[dict[str, str], Path, Path]:
    """The environment in which the Release lookup and the Release guard run under ``base``: the stand-in curl first on
    PATH, answering with ``context[LOOKUP]`` when the scenario has one, RUNNER_TEMP at ``base / "runner"`` and the
    repository's name. Returns it with the stand-in's state directory and RUNNER_TEMP."""
    tools, state = stand_in_curl(base)
    if LOOKUP in context:
        (state / "response.json").write_text(json.dumps(context[LOOKUP]), encoding="utf-8")
    runner = base / "runner"
    runner.mkdir()
    extra = {
        "PATH": f"{tools}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
        "RUNNER_TEMP": str(runner),
        "GITHUB_REPOSITORY": REPOSITORY,
        "STAND_IN_CURL": str(state),
    }
    return extra, state, runner


def curl_options(arguments: list[str]) -> tuple[list[tuple[str, str | None]], list[str]]:
    """curl's arguments as (option, value) pairs by CURL_OPTIONS, and the other arguments (URLs). An option that
    CURL_OPTIONS does not list is returned with the value None."""
    options: list[tuple[str, str | None]] = []
    urls = []
    remaining = iter(arguments)
    for argument in remaining:
        if not argument.startswith("-"):
            urls.append(argument)
        elif CURL_OPTIONS.get(argument):
            options.append((argument, next(remaining, None)))
        else:
            options.append((argument, None))
    return options, urls


class ReleaseGuardTests(PagesJobs, unittest.TestCase):
    def gate(self, job_id: str, context: dict, base: Path) -> tuple[list[subprocess.CompletedProcess], list[dict]]:
        """Run the event guard, the Release lookup and the Release guard of one job in ``base``, in order and each only
        after the one before passed, as GitHub runs steps. The stand-in curl answers the lookup with
        ``context[LOOKUP]``. Returns the results of the steps that ran and the stand-in's calls; RUNNER_TEMP is
        ``base / "runner"``."""
        extra, state, _ = lookup_environment(base, context)
        guards = self.guards(job_id)
        results: list[subprocess.CompletedProcess] = []
        for role in ("event", "lookup", "release"):
            results.append(run_guard(guards[role], {**context, "github.token": TOKEN}, **extra))
            if results[-1].returncode != 0:
                break
        return results, curl_calls(state)

    def run_gate(self, job_id: str, context: dict) -> tuple[list[subprocess.CompletedProcess], list[dict]]:
        with tempfile.TemporaryDirectory() as temporary:
            return self.gate(job_id, context, Path(temporary))

    @unittest.skipUnless(shutil.which("bash"), "needs bash to execute the workflow's step")
    def test_the_record_step_writes_exactly_the_commit_and_the_attempt(self):
        """The record step writes github.sha and the runner's GITHUB_RUN_ATTEMPT. As the approval job's outputs, they
        let the deploy job of that attempt deploy, and not the deploy job of a later attempt that reuses them."""
        record = steps_of(self.pages["jobs"]["approval"])[-1]
        condition = self.pages["jobs"]["deploy"]["if"]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "github-output"
            result = run_guard(record, {"github.sha": SHA}, GITHUB_OUTPUT=str(output), GITHUB_RUN_ATTEMPT="3")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), f"commit={SHA}\nattempt=3\n")
            written = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
        outputs = {f"needs.approval.outputs.{name}": value for name, value in written.items()}
        for run_attempt, allowed in (("3", True), ("4", False)):
            with self.subTest(run_attempt=run_attempt):
                context = {**dispatched(TAGGED), **outputs, "github.run_attempt": run_attempt}
                self.assertEqual(evaluate(condition, context), allowed)

    def test_every_attempt_that_deploys_nothing_fails(self):
        """The jobs of a published release's run and of its re-runs (RERUNS), each if: as pages.yml writes it: an
        attempt ends green exactly when it deployed (PROD-1). A re-run that left out the approval job deploys nothing
        and its Re-run check job fails it; a first attempt and a re-run of all jobs never run that job."""
        jobs = self.pages["jobs"]
        for scenario, (attempts, deployed, checked) in RERUNS.items():
            with self.subTest(scenario=scenario):
                state: dict[str, tuple[str, dict]] = {}
                for number, (start, failing) in enumerate(attempts, 1):
                    if start == FIRST or start == ALL:
                        selected = set(jobs)
                    elif start == FAILED:
                        selected = {job_id for job_id, (result, _) in state.items() if result == "failure"}
                    else:
                        selected = {start}
                    rerun = rerun_jobs(jobs, selected)
                    state = run_attempt(jobs, released("v1.1.2"), number, rerun, set(failing), state)
                results = {job_id: result for job_id, (result, _) in state.items()}
                green = "failure" not in results.values()
                self.assertEqual(green, deployed, f"green only when this attempt deployed: {results}")
                self.assertEqual(results["deploy"] == "success" and "deploy" in rerun, deployed, results)
                ran_check = results.get(RERUN_CHECK, "skipped") != "skipped" and RERUN_CHECK in rerun
                self.assertEqual(ran_check, checked, results)

    def test_re_runs_reach_the_jobs_that_need_the_re_run_job(self):
        """rerun_jobs() follows needs: through other jobs, as GitHub re-runs dependents: every re-run reaches the
        Re-run check job, and only a re-run of the approval job (or of all jobs) repeats it."""
        jobs = self.pages["jobs"]
        self.assertEqual(rerun_jobs(jobs, {"tests"}), {"tests", "build", "deploy", RERUN_CHECK})
        self.assertEqual(rerun_jobs(jobs, {"approval"}), set(jobs))
        self.assertEqual(rerun_jobs(jobs, {RERUN_CHECK}), {RERUN_CHECK})
        for job_id in jobs:
            with self.subTest(job=job_id):
                self.assertIn(RERUN_CHECK, rerun_jobs(jobs, {job_id}))
                self.assertEqual("approval" in rerun_jobs(jobs, {job_id}), job_id == "approval")

    @unittest.skipUnless(shutil.which("bash"), "needs bash to execute the workflow's step")
    def test_the_rerun_check_step_fails_with_its_reason(self):
        """The Re-run check job's one step, run as GitHub runs it, prints exactly one error annotation, which says
        that this attempt did not run the Release approval job, deployed nothing and that all jobs must be re-run, and
        fails."""
        (step,) = steps_of(self.pages["jobs"][RERUN_CHECK])
        result = run_guard({**step, "env": {}}, {})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(error_lines(result), [f"::error::{RERUN_CHECK_ERROR}"])
        for phrase in ("did not run the Release approval job", "deployed nothing", "re-run all jobs"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, RERUN_CHECK_ERROR)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq to execute the guard steps")
    def test_event_and_release_guards_fail_closed(self):
        """Each scenario through the event guard, the Release lookup and the Release guard of both jobs. An accepted
        one passes all three without an error; a refused one stops at one of them with exactly one ::error:: line:
        RELEASE_LOOKUP_DENIED at the lookup or the Release guard, every other one at the event guard. Every run that
        passed the event guard sends the lookup, once, a release run as well as a dispatch; no other run sends it."""
        for job_id in ("approval", "build"):
            for scenario, context, allowed in [(name, ctx, True) for name, ctx in ACCEPTED.items()] + [
                (name, ctx, False) for name, ctx in DENIED.items()
            ]:
                with self.subTest(job=job_id, scenario=scenario):
                    results, calls = self.run_gate(job_id, context)
                    output = "".join(result.stdout + result.stderr for result in results)
                    passed = len(results) == 3 and results[-1].returncode == 0
                    self.assertEqual(passed, allowed, output)
                    self.assertEqual(sum(len(error_lines(result)) for result in results), 0 if allowed else 1, output)
                    if not allowed:
                        self.assertEqual(len(results) > 1, scenario in RELEASE_LOOKUP_DENIED, output)
                    self.assertEqual(len(calls), 1 if results[0].returncode == 0 else 0, output)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq to execute the guard steps")
    def test_a_release_run_checks_the_current_release(self):
        """GitHub's re-run of a release run keeps the event it started with, so the event guard still passes after the
        owner turned the Release into a prerelease or a draft or deleted it, and when the lookup answers for another
        tag or cannot be read. Each copy of the guards then makes its one lookup and refuses with the same reason as a
        dispatch on the same tag; the deploy condition alone would accept the run (EXPRESSION_BLIND). A re-run of the
        deploy job alone, which repeats no lookup, deploys nothing (R1)."""
        condition = self.pages["jobs"]["deploy"]["if"]
        releases = {
            name: context
            for name, context in RELEASE_LOOKUP_DENIED.items()
            if context["github.event_name"] == "release"
        }
        self.assertGreaterEqual(len(releases), 10, "withdrawn, mismatched and unreadable Releases of a release run")
        for job_id in ("approval", "build"):
            for scenario, context in releases.items():
                with self.subTest(job=job_id, scenario=scenario):
                    self.assertTrue(evaluate(condition, approved(context)), "the payload alone looks deployable")
                    later = {**context, "github.run_attempt": str(int(context["github.run_attempt"]) + 1)}
                    deploy_job_alone = approved(later, attempt=context["github.run_attempt"])
                    self.assertFalse(evaluate(condition, deploy_job_alone), "the deploy job alone deploys nothing")
                    results, calls = self.run_gate(job_id, context)
                    output = "".join(result.stdout + result.stderr for result in results)
                    self.assertEqual(results[0].returncode, 0, output)
                    self.assertNotEqual(results[-1].returncode, 0, output)
                    self.assertEqual(len(calls), 1, output)
                    dispatch = {**dispatched(TAGGED), LOOKUP: context[LOOKUP]}
                    refused, _ = self.run_gate(job_id, dispatch)
                    self.assertEqual(
                        [line for result in results for line in error_lines(result)],
                        [line for result in refused for line in error_lines(result)],
                        "a release run and a dispatch on the same tag are refused alike",
                    )

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git to execute the guard step")
    def test_main_guard_deploys_only_a_commit_on_main(self):
        """The main guard as it runs after actions/checkout with fetch-depth 0: the tag ref, the remote-tracking
        origin/main and a detached HEAD. The local branch main, which that checkout does not create, holds a commit off
        origin/main here: only origin/main counts."""
        with tempfile.TemporaryDirectory() as temporary:
            root, _, commits = release_repository(Path(temporary))
            first, second, side = commits["first"], commits["second"], commits["side"]
            scenarios = {
                "the tip of main": ("refs/tags/v1.1.0", second, second, True),
                "an older commit of main": ("refs/tags/v1.0.0", first, first, True),
                "a commit off main (on the local branch main only)": ("refs/tags/v1.2.0", side, side, False),
                "a checkout of another commit": ("refs/tags/v1.1.0", second, first, False),
                "a tag that names another commit": ("refs/tags/v1.0.0", second, second, False),
                "a tag missing from the checkout": ("refs/tags/v2.0.0", second, second, False),
                "a short SHA": ("refs/tags/v1.1.0", second[:12], second, False),
                "an upper-case SHA": ("refs/tags/v1.1.0", second.upper(), second, False),
            }
            for job_id in ("approval", "build"):
                step = self.guards(job_id)["main"]
                for scenario, (ref, sha, head, allowed) in scenarios.items():
                    with self.subTest(job=job_id, scenario=scenario):
                        git(root, "checkout", "-q", "--detach", head)
                        result = run_guard(step, {"github.ref": ref, "github.sha": sha}, cwd=root)
                        self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
                        self.assertEqual(len(error_lines(result)), 0 if allowed else 1, result.stdout)
                with self.subTest(job=job_id, scenario="no origin/main in the checkout (a shallow or branch fetch)"):
                    git(root, "checkout", "-q", "--detach", second)
                    git(root, "update-ref", "-d", "refs/remotes/origin/main")
                    result = run_guard(step, {"github.ref": "refs/tags/v1.1.0", "github.sha": second}, cwd=root)
                    git(root, "update-ref", "refs/remotes/origin/main", second)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(error_lines(result)), 1, result.stdout)
                    self.assertIn("origin/main", error_lines(result)[0])
                with self.subTest(job=job_id, scenario="outside a Git checkout"):
                    outside = Path(temporary) / "empty"
                    outside.mkdir(exist_ok=True)
                    context = {"github.ref": "refs/tags/v1.1.0", "github.sha": second}
                    # GIT_CEILING_DIRECTORIES keeps git from finding a repository above the temporary directory.
                    result = run_guard(step, context, cwd=outside, GIT_CEILING_DIRECTORIES=temporary)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(error_lines(result)), 1, result.stdout)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git to execute the guard step")
    def test_main_guard_reads_the_tag_from_origin(self):
        """actions/checkout refetches +<github.sha>:<tag> when origin's tag no longer names the run's commit, so the
        checkout's own tag always passes. The main guard reads the tag from origin (the tag and its peeled ^{} line of
        git ls-remote, by exact name) and refuses a tag moved or deleted there since the run started, and an origin it
        cannot read (SEC-2). Each scenario changes origin only, as a move after the checkout does; the checkout keeps
        v1.1.0 (annotated) on second and v1.0.0 (lightweight) on first."""
        with tempfile.TemporaryDirectory() as temporary:
            root, origin, commits = release_repository(Path(temporary))
            cases = [(job, *change) for job in ("approval", "build") for change in ORIGIN_CHANGES.items()]
            for job_id, scenario, (tag, run, changes, named) in cases:
                commands = [[commits.get(word, word) for word in command] for command in changes]
                sha, refused = commits[run], commits.get(named, named) if named else None
                with self.subTest(job=job_id, scenario=scenario):
                    result = self.main_guard_after(job_id, root, origin, commands, f"refs/tags/{tag}", sha)
                    self.assertEqual(result.returncode == 0, refused is None, result.stdout + result.stderr)
                    errors = error_lines(result)
                    self.assertEqual(len(errors), 0 if refused is None else 1, result.stdout)
                    message = f"On origin the tag refs/tags/{tag} names {refused}, not the release commit {sha}."
                    self.assertEqual(errors, [] if refused is None else [f"::error::{message}"])
            second = commits["second"]
            for job_id in ("approval", "build"):
                with self.subTest(job=job_id, scenario="an origin that cannot be read"):
                    git(root, "config", "remote.origin.url", str(Path(temporary) / "missing.git"))
                    try:
                        result = self.main_guard_after(job_id, root, origin, [], "refs/tags/v1.1.0", second)
                    finally:
                        git(root, "config", "remote.origin.url", str(origin))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(error_lines(result)), 1, result.stdout)
                    self.assertIn("could not be read from origin", error_lines(result)[0])

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git to execute the guard step")
    def test_main_guard_fails_when_origin_does_not_answer_in_time(self):
        """The main guard reads origin under timeout (F2): when timeout ends the read (status 124, here from a
        stand-in that exits so at once), the guard fails with the one error of an origin it cannot read. The stand-in
        sees the limit and the read it bounds: git ls-remote of origin's tag and its peeled line. The limit itself is
        pinned in tests/test_workflows_release_gate.py (LS_REMOTE_SECONDS); a real origin that accepts the
        connection and never answers fails the guard only after it, which is too slow for this suite."""
        with tempfile.TemporaryDirectory() as temporary:
            root, _, commits = release_repository(Path(temporary))
            tools = Path(temporary) / "timeout-bin"
            tools.mkdir()
            (tools / "timeout").write_text(STAND_IN_TIMEOUT, encoding="utf-8")
            (tools / "timeout").chmod(0o755)
            path = f"{tools}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"
            second, ref = commits["second"], "refs/tags/v1.1.0"
            git(root, "checkout", "-q", "--detach", second)
            for job_id in ("approval", "build"):
                with self.subTest(job=job_id):
                    call = Path(temporary) / f"timeout-{job_id}.txt"
                    context = {"github.ref": ref, "github.sha": second}
                    step = self.guards(job_id)["main"]
                    result = run_guard(step, context, cwd=root, PATH=path, STAND_IN_TIMEOUT_CALL=str(call))
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(error_lines(result), [f"::error::The tag {ref} could not be read from origin."])
                    self.assertTrue(call.exists(), "the read of origin goes through timeout")
                    limit, *command = call.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(command, ["git", "ls-remote", "origin", ref, f"{ref}^{{}}"])
                    self.assertEqual(limit, str(LS_REMOTE_SECONDS))
                    passed = run_guard(step, context, cwd=root)  # the real timeout and git: origin has the tag
                    self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

    def main_guard_after(
        self, job_id: str, root: Path, origin: Path, commands: list[list[str]], ref: str, sha: str
    ) -> subprocess.CompletedProcess:
        """Run the main guard of ``job_id`` in the checkout ``root`` at ``sha`` after ``commands`` changed ``origin``,
        and set origin's refs back afterwards."""
        refs = git(origin, "for-each-ref", "--format=%(refname) %(objectname)")
        try:
            for command in commands:
                git(origin, *command)
            git(root, "checkout", "-q", "--detach", sha)
            return run_guard(self.guards(job_id)["main"], {"github.ref": ref, "github.sha": sha}, cwd=root)
        finally:
            restore_refs(origin, refs)

    @unittest.skipUnless(shutil.which("bash"), "needs bash to execute the workflow's guard step")
    def test_the_tag_check_takes_ascii_digits_only_in_any_locale(self):
        """The event guard runs with LC_ALL=C, so the [0-9] of its vX.Y.Z check is the ASCII digits only. In a locale
        such as en_US.UTF-8, bash's [0-9] also matches Arabic-Indic, fullwidth or superscript digits, which a Git tag
        name may contain. The deploy condition's startsWith() would let such a tag through (EXPRESSION_BLIND), and so
        would the github-pages environment's tag rules (globs such as v1.[2-9]*) wherever the digit comes after the part
        that they match. The same step without LC_ALL accepts it in that locale, so the check is not vacuous."""
        locale = wide_digit_locale()
        if locale is None:
            self.skipTest("no installed locale in which bash's [0-9] matches the digits of other scripts")
        scenarios = (
            "dispatch on a tag with an Arabic-Indic digit",
            "dispatch on a tag with a fullwidth digit",
            "release of a tag with a superscript digit",
        )
        for job_id in ("approval", "build"):
            step = self.guards(job_id)["event"]
            self.assertEqual(step["env"].get("LC_ALL"), "C")
            loose = {**step, "env": {name: value for name, value in step["env"].items() if name != "LC_ALL"}}
            for scenario in scenarios:
                with self.subTest(job=job_id, scenario=scenario, locale=locale):
                    errors = error_lines(run_guard(step, DENIED[scenario]))
                    self.assertEqual(len(errors), 1)
                    self.assertIn("vX.Y.Z", errors[0])
                    accepted = run_guard(loose, DENIED[scenario])
                    self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq to execute the guard steps")
    def test_guards_name_the_reason(self):
        expected = {
            "push to main": "not push",
            "dispatch on main": "not refs/heads/main",
            "dispatch on the tag v1.1.0-rc1": "vX.Y.Z",
            "release v01.1.0": "without leading zeros",
            "prerelease v1.1.0": "prerelease",
            "release without a prerelease flag": "prerelease",
            "draft release": "draft",
            "release edited, not published": "edited",
            "release whose tag is not the ref": "v1.2.0",
            "dispatch on a tag without a published Release (HTTP 404)": "has no published GitHub Release (HTTP 404)",
            "dispatch whose Release lookup is forbidden (HTTP 403)": "returned HTTP 403, not 200",
            "dispatch whose Release lookup is redirected (HTTP 301) with the Release's body": "returned HTTP 301, not 200",
            "dispatch whose Release lookup gets no response (a network error)": "failed without a response",
            "dispatch on a tag whose Release is a prerelease": "prerelease=true",
            "dispatch on a tag whose Release is a draft": "draft=true",
            "dispatch on a tag whose Release names another tag": 'names the tag "v1.2.0", not v1.1.0',
            "dispatch on a tag whose Release has no prerelease flag": "prerelease=null",
            "dispatch on a tag whose Release has a string prerelease flag": 'prerelease="false"',
            "dispatch with a truncated Release response": "did not return valid JSON",
            "dispatch with a JSON array as the Release response": "is a JSON array, not one object",
            "dispatch with a JSON string as the Release response": "is a JSON string, not one object",
            "dispatch with a JSON number as the Release response": "is a JSON number, not one object",
            "dispatch with JSON null as the Release response": "is a JSON null, not one object",
            "dispatch with two JSON documents as the Release response": "is not one JSON object",
            "re-run of release v1.1.0, whose Release is now a prerelease": "prerelease=true",
            "re-run of release v1.1.0, whose Release is now a draft": "draft=true",
            "re-run of release v1.1.0, whose Release was deleted (HTTP 404)": "has no published GitHub Release",
            "re-run of release v1.1.0, whose Release lookup names another tag": 'names the tag "v1.2.0", not v1.1.0',
            "re-run of release v1.1.0, whose Release has no prerelease flag": "prerelease=null",
            "release v1.1.0 whose Release lookup is forbidden (HTTP 403)": "returned HTTP 403, not 200",
            "release v1.1.0 whose Release lookup gets no response (a network error)": "failed without a response",
            "release v1.1.0 with a truncated Release response": "did not return valid JSON",
        }
        for scenario, phrase in expected.items():
            with self.subTest(scenario=scenario):
                results, _ = self.run_gate("approval", DENIED[scenario])
                errors = [line for result in results for line in error_lines(result)]
                self.assertEqual(len(errors), 1)
                self.assertIn(phrase, errors[0])

    @unittest.skipUnless(shutil.which("bash"), "needs bash to execute the workflow's guard step")
    def test_the_release_lookup_is_one_read_only_get(self):
        """The lookup of a dispatch or a release run sends one request: a GET (no method, data, form, upload, config or
        redirect option) of the Releases endpoint for the run's tag, over HTTPS only, within LOOKUP_MAX_SECONDS, with
        the API headers of owner decision D1 and the job token as an Authorization header read from standard input, so
        the token is never an argument. It saves the status and the body under RUNNER_TEMP, where the Release guard
        reads them. Any other event sends nothing."""
        # Two tags per event, so the request names the run's tag, not a fixed one.
        events = (dispatched(TAGGED), released("v1.1.0"), dispatched("refs/tags/v10.20.30"), released("v2.0.0"))
        for job_id, event in [(job_id, event) for job_id in ("approval", "build") for event in events]:
            lookup = self.guards(job_id)["lookup"]
            context = {**event, "github.token": TOKEN}
            tag = event["github.ref"].removeprefix("refs/tags/")
            expected_url = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}"
            subtest = self.subTest(job=job_id, event=event["github.event_name"], tag=tag)
            with subtest, tempfile.TemporaryDirectory() as temporary:
                extra, state, runner = lookup_environment(Path(temporary), context)
                result = run_guard(lookup, context, **extra)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = curl_calls(state)
                self.assertEqual(len(calls), 1)
                self.assert_one_read_only_get(calls[0], expected_url, runner)
                self.assertEqual((runner / "release-lookup/status").read_text(encoding="utf-8"), "200\n")
                self.assertEqual(
                    (runner / "release-lookup/release.json").read_text(encoding="utf-8"), context[LOOKUP][1]
                )
                for other in (triggered("push", TAGGED), triggered("pull_request", TAGGED)):
                    outcome = run_guard(lookup, {**other, "github.token": TOKEN}, **{**extra, "RUNNER_TEMP": temporary})
                    self.assertNotEqual(outcome.returncode, 0, outcome.stdout)
                    self.assertEqual(len(error_lines(outcome)), 1, outcome.stdout)
                self.assertEqual(len(curl_calls(state)), 1, "no request for another event")

    def assert_one_read_only_get(self, call: dict, expected_url: str, runner: Path) -> None:
        """One call of the stand-in curl by the lookup: the token read from standard input, never an argument; a GET
        of ``expected_url`` with only CURL_OPTIONS, the API headers, HTTPS only and a bounded --max-time; the status
        written out and the body saved where the Release guard reads it (``runner`` is RUNNER_TEMP)."""
        arguments, stdin = call["arguments"], call["stdin"]
        self.assertEqual(stdin, f"Authorization: Bearer {TOKEN}\n")
        self.assertFalse(
            [argument for argument in arguments if TOKEN in argument], "the token must never be an argument"
        )
        options, urls = curl_options(arguments)
        self.assertEqual(urls, [expected_url])
        self.assertLessEqual({option for option, _ in options}, set(CURL_OPTIONS), "a GET and nothing else")
        values = dict(options)
        self.assertEqual(sorted(value for option, value in options if option == "--header"), ["@-", *API_HEADERS])
        self.assertEqual(values["--proto"], "=https")
        self.assertIn("--max-time", values, "a hung request must not run until the job's timeout")
        self.assertRegex(values["--max-time"] or "", r"^[1-9][0-9]*$", "a positive number of whole seconds")
        self.assertLessEqual(int(values["--max-time"] or 0), LOOKUP_MAX_SECONDS)
        self.assertEqual(values["--write-out"], "%{http_code}")
        self.assertEqual(values["--output"], str(runner / "release-lookup/release.json"))

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq to execute the guard step")
    def test_the_release_guard_needs_the_saved_response(self):
        """The Release guard decides only from the files the lookup saved: a dispatch or a release run without them
        fails closed (a release run's event does not stand in for the Release), and another event fails."""
        for job_id in ("approval", "build"):
            release = self.guards(job_id)["release"]
            for context, phrase in (
                (dispatched(TAGGED), "No response of the lookup"),
                (released("v1.1.0"), "No response of the lookup"),
                (triggered("push", TAGGED), "not push"),
            ):
                with self.subTest(job=job_id, event=context["github.event_name"]), tempfile.TemporaryDirectory() as tmp:
                    result = run_guard(release, context, RUNNER_TEMP=tmp)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    errors = error_lines(result)
                    self.assertEqual(len(errors), 1, result.stdout)
                    self.assertIn(phrase, errors[0])


def git(cwd: Path, *arguments: str) -> str:
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), **GIT_ENV}
    result = subprocess.run(["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def release_repository(base: Path) -> tuple[Path, Path, dict[str, str]]:
    """A checkout as actions/checkout leaves it with fetch-depth 0, and its origin: commits first <- second on
    origin/main, side off it; tags v1.0.0 (lightweight) on first, v1.1.0 (annotated) on second and v1.2.0 on side, in
    the checkout and on origin alike; the local branch main at side, unlike origin/main. origin is a bare repository
    under ``base`` whose main is second, and the checkout's remote origin points at it."""
    root = base / "checkout"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commits = {}
    for name, parent in (("first", None), ("second", "first"), ("side", "first")):
        if parent:
            git(root, "checkout", "-q", "--detach", commits[parent])
        (root / "file.txt").write_text(f"{name}\n", encoding="utf-8")
        git(root, "add", "file.txt")
        git(root, "commit", "-q", "-m", name)
        commits[name] = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", commits["second"])
    git(root, "update-ref", "refs/heads/main", commits["side"])
    git(root, "tag", "v1.0.0", commits["first"])
    git(root, "tag", "-a", "-m", "v1.1.0", "v1.1.0", commits["second"])
    git(root, "tag", "v1.2.0", commits["side"])
    origin = base / "origin.git"
    git(base, "clone", "-q", "--bare", str(root), str(origin))
    git(origin, "update-ref", "refs/heads/main", commits["second"])
    git(root, "config", "remote.origin.url", str(origin))
    return root, origin, commits


def restore_refs(repository: Path, listing: str) -> None:
    """Set the refs of ``repository`` back to ``listing``, as ``git for-each-ref --format='%(refname) %(objectname)'``
    printed them: refs created since are deleted, and moved or deleted ones are set again."""
    wanted = dict(line.split(" ") for line in listing.splitlines())
    current = git(repository, "for-each-ref", "--format=%(refname)").splitlines()
    for name in set(current) - set(wanted):
        git(repository, "update-ref", "-d", name)
    for name, target in wanted.items():
        git(repository, "update-ref", name, target)


if __name__ == "__main__":
    unittest.main()
