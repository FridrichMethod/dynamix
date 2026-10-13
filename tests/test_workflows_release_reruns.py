"""The jobs of a run of .github/workflows/pages.yml and of its re-runs, as GitHub decides them.

tests/test_workflows_release_gate.py holds the scenarios and the checks of the workflow as written, and
tests/test_workflows_release_guards.py executes its shell guards. Here the jobs of a published release's run and of
its re-runs run as GitHub decides them (run_attempt()), each job's if: as written: which jobs a re-run repeats, which
of them run, and whether the attempt deploys and ends green (PROD-1, E8).
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_workflows import RUN_CANCELLED, evaluate  # noqa: E402
from test_workflows_release_gate import RERUN_CHECK, SHA, PagesJobs, released  # noqa: E402

# A job if: that names a status function is evaluated whatever the results of the jobs it needs; GitHub prefixes
# success() to any other, which then runs only when every job it needs succeeded (GitHub's "Status check functions").
STATUS_FUNCTION = re.compile(r"\b(?:always|success|failure|cancelled)\s*\(")


def needed(job: dict) -> list[str]:
    """The jobs that ``job`` needs, as a list (needs: may name one job without brackets)."""
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def rerun_jobs(jobs: dict[str, dict], selected: set[str]) -> set[str]:
    """The jobs that GitHub runs again when a re-run selects ``selected``: those and every job that needs one of
    them, directly or through other jobs ("Re-run failed jobs" selects the failed and the cancelled jobs, "Re-run this
    job" one job)."""
    found = set(selected)
    while extra := {job_id for job_id, job in jobs.items() if job_id not in found and found & set(needed(job))}:
        found |= extra
    return found


def run_attempt(
    jobs: dict[str, dict],
    context: dict,
    number: int,
    rerun: set[str],
    failing: set[str],
    previous: dict,
    cancelled_at: str | None = None,
) -> dict[str, tuple[str, dict]]:
    """Attempt ``number`` of a run as GitHub runs its jobs, in file order (each needs only jobs above it), as {job:
    (result, outputs)}. A job outside ``rerun`` keeps its result and outputs of ``previous``. A job in it runs when
    its if: holds (STATUS_FUNCTION), with the results and outputs of the jobs it needs, and then fails when it is in
    ``failing`` or is the Re-run check job (its one step fails: test_the_rerun_check_step_fails_with_its_reason);
    otherwise it succeeds, and the approval job records the commit and this attempt (its record step). A job that
    does not run is skipped. When someone cancels the run while ``cancelled_at`` runs, that job ends cancelled, and
    every job after it sees a cancelled run (RUN_CANCELLED): one whose if: names no status function does not run
    (GitHub's implied success() is false), and one whose if: names one runs when its if: holds (E8)."""
    state: dict[str, tuple[str, dict]] = {}
    cancelled = False
    for job_id, job in jobs.items():
        if job_id not in rerun:
            state[job_id] = previous[job_id]
            continue
        seen = {**context, "github.run_attempt": str(number), RUN_CANCELLED: cancelled}
        for need in needed(job):
            result, outputs = state[need]
            seen[f"needs.{need}.result"] = result
            seen.update({f"needs.{need}.outputs.{name}": value for name, value in outputs.items()})
        condition = job.get("if")
        if condition is not None and STATUS_FUNCTION.search(condition):
            runs = evaluate(condition, seen)
        else:
            runs = not cancelled and all(state[need][0] == "success" for need in needed(job))
            runs = runs and (condition is None or evaluate(condition, seen))
        if not runs:
            state[job_id] = ("skipped", {})
        elif job_id == cancelled_at:
            state[job_id] = ("cancelled", {})
        elif job_id in failing or job_id == RERUN_CHECK:
            state[job_id] = ("failure", {})
        else:
            state[job_id] = ("success", {"commit": SHA, "attempt": str(number)} if job_id == "approval" else {})
        cancelled = cancelled or job_id == cancelled_at
    return state


FIRST = "first attempt"
ALL = "Re-run all jobs"
FAILED = "Re-run failed jobs"
# Each attempt of a run of a published release: how it was started (FIRST, ALL, FAILED, or the id of the job whose
# "Re-run this job" started it), the jobs that fail when they run and, for an attempt that someone cancels, the job
# during which they do; then whether the last attempt deployed and whether its Re-run check job ran (PROD-1). A re-run
# that leaves out the approval job, after a failed or cancelled deploy, test or build job or of the deploy or build job
# alone, keeps the approval job's record of an earlier attempt: its deploy job is skipped and its Re-run check job runs
# and fails. A first attempt or a re-run of all jobs never runs it, nor does a run that the approval job refused or a
# cancelled run (E8), which ends cancelled.
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
    "a first attempt cancelled during the tests": (((FIRST, (), "tests"),), False, False),
    "failed jobs re-run after a first attempt cancelled during the tests": (
        ((FIRST, (), "tests"), (FAILED, ())),
        False,
        True,
    ),
    "all jobs re-run, cancelled during the tests": (((FIRST, ()), (ALL, (), "tests")), False, False),
    "failed jobs re-run after the tests failed, cancelled during the tests": (
        ((FIRST, ("tests",)), (FAILED, (), "tests")),
        False,
        False,
    ),
    "the build job re-run after a deployment, cancelled during the build": (
        ((FIRST, ()), ("build", (), "build")),
        False,
        False,
    ),
}


class ReleaseRerunTests(PagesJobs, unittest.TestCase):
    def test_every_attempt_that_deploys_nothing_fails(self):
        """The jobs of a published release's run and of its re-runs (RERUNS), each if: as pages.yml writes it: an
        attempt ends green (no job failed or was cancelled) exactly when it deployed (PROD-1). A re-run that left out
        the approval job deploys nothing and its Re-run check job fails it; a first attempt, a re-run of all jobs and a
        cancelled attempt never run that job (E8)."""
        jobs = self.pages["jobs"]
        for scenario, (attempts, deployed, checked) in RERUNS.items():
            with self.subTest(scenario=scenario):
                state: dict[str, tuple[str, dict]] = {}
                for number, (start, failing, *cancel) in enumerate(attempts, 1):
                    if start == FIRST or start == ALL:
                        selected = set(jobs)
                    elif start == FAILED:
                        selected = {
                            job_id for job_id, (result, _) in state.items() if result in ("failure", "cancelled")
                        }
                    else:
                        selected = {start}
                    rerun = rerun_jobs(jobs, selected)
                    cancelled_at = cancel[0] if cancel else None
                    self.assertTrue(cancelled_at is None or cancelled_at in rerun, "a job of this attempt is cancelled")
                    state = run_attempt(jobs, released("v1.1.2"), number, rerun, set(failing), state, cancelled_at)
                    self.assertTrue(cancelled_at is None or state[cancelled_at][0] == "cancelled", state)
                results = {job_id: result for job_id, (result, _) in state.items()}
                green = not {"failure", "cancelled"} & set(results.values())
                self.assertEqual(green, deployed, f"green only when this attempt deployed: {results}")
                self.assertEqual(results["deploy"] == "success" and "deploy" in rerun, deployed, results)
                ran_check = results.get(RERUN_CHECK, "skipped") != "skipped" and RERUN_CHECK in rerun
                self.assertEqual(ran_check, checked, results)

    def test_a_cancelled_run_runs_later_jobs_only_through_a_status_function(self):
        """run_attempt() in a run cancelled during one job: a later job whose if: names no status function does not
        run, even when it needs no job (GitHub's implied success() is false in a cancelled run), while one whose if:
        names a status function runs when its if: holds (E8). Without the cancellation each of them runs."""
        jobs = {
            "first": {},
            "second": {},
            "third": {"if": "github.run_attempt == '1'"},
            "always": {"if": "always()"},
            "not-cancelled": {"if": "!cancelled()"},
        }
        for cancelled_at, results in (
            ("first", ("cancelled", "skipped", "skipped", "success", "skipped")),
            (None, ("success",) * 5),
        ):
            with self.subTest(cancelled_at=cancelled_at):
                state = run_attempt(jobs, {}, 1, set(jobs), set(), {}, cancelled_at)
                expected = dict(zip(jobs, results, strict=True))
                self.assertEqual({job_id: result for job_id, (result, _) in state.items()}, expected)

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


if __name__ == "__main__":
    unittest.main()
