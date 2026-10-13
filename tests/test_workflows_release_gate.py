"""The release gate of .github/workflows/pages.yml: its guards, the Release lookup and the deploy condition.

The release gate (owner decisions of 2026-10-11, superseding the manual-dispatch design of 06-validation-and-release.md
Gate 5) must hold by construction: Pages deploys only a published, non-prerelease GitHub Release whose tag is vX.Y.Z
(without leading zeros), or a manual dispatch on such a tag, and only when the tag's GitHub Release is published and
not a prerelease (looked up by every run, a re-run of a release run included, with one read-only API request in the
approval job and one in the build job: the only GitHub API requests of the workflows' scripts and the only uses of the
job token), the tag's commit is on main, the full Tests workflow passed on it and the approval job recorded it. The
deploy job's condition is evaluated with GitHub's expression semantics (the evaluator of tests/test_workflows.py) for
each scenario, and tests/test_workflows_release_guards.py executes every shell guard for them (the Release lookup with
a stand-in curl, the main guard in temporary Git repositories with a local bare origin). The last job, Re-run check,
fails a re-run that left out the approval job and so deployed nothing (PROD-1); tests/test_workflows_release_guards.py
also runs the jobs of a run and of its re-runs as GitHub decides them. What the comments of pages.yml and the READMEs
say about the gate is checked in tests/test_workflows_release_text.py.
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_workflows import (  # noqa: E402
    WORKFLOWS,
    action_name,
    all_workflows,
    evaluate,
    load,
    steps_of,
    uses_in,
)

# The deploy job's condition: a tag ref, the commit that the approval job recorded after its guards passed (owner
# decision D4) in the deploy job's own attempt (R1, ATTEMPTS), and a dispatch or a published release that is not a
# prerelease. The prerelease flag is compared as JSON (owner decision D3): GitHub's == coerces null to false, so a
# release without the flag would pass "prerelease == false". It cannot tell a malformed tag or a commit off main
# (EXPRESSION_BLIND); the shell guards before it refuse those.
DEPLOY_IF = (
    "startsWith(github.ref, 'refs/tags/v') && needs.approval.outputs.commit == github.sha && "
    "needs.approval.outputs.attempt == github.run_attempt && "
    "(github.event_name == 'workflow_dispatch' || "
    "(github.event_name == 'release' && github.event.action == 'published' && "
    "toJSON(github.event.release.prerelease) == 'false'))"
)
# The last job (PROD-1). GitHub re-runs a job together with every job that needs it, so every re-run reaches this one,
# and a job if: with a status function (always()) is evaluated whatever the results of the jobs it needs. It runs only
# when the approval job succeeded in an earlier attempt and was not re-run: its record names another attempt, so the
# deploy job of this attempt was skipped (ATTEMPTS), and this job fails the run with the reason instead of letting it
# end green. It needs no permission, checkout, action or expression in its script, and stops within minutes.
RERUN_CHECK = "rerun-check"
RERUN_CHECK_IF = (
    "always() && needs.approval.result == 'success' && needs.approval.outputs.attempt != github.run_attempt"
)
RERUN_CHECK_ERROR = "This attempt did not run the Release approval job, so it deployed nothing; re-run all jobs."
RERUN_CHECK_JOB = {
    "name": "Re-run check",
    "needs": ["approval", "deploy"],
    "if": RERUN_CHECK_IF,
    "runs-on": "ubuntu-latest",
    "timeout-minutes": "5",
    "permissions": {},
    "steps": [
        {
            "name": "Fail a re-run that left out the Release approval job",
            "run": f'echo "::error::{RERUN_CHECK_ERROR}"\nexit 1\n',
        }
    ],
}
# The approval job's outputs: the commit it approved and the attempt of the run it ran in (the runner's
# GITHUB_RUN_ATTEMPT), written by its last step after every guard passed.
APPROVAL_OUTPUT = "needs.approval.outputs.commit"
APPROVAL_ATTEMPT = "needs.approval.outputs.attempt"
APPROVAL_OUTPUTS = {
    "commit": "${{ steps.approved.outputs.commit }}",
    "attempt": "${{ steps.approved.outputs.attempt }}",
}
RECORD_STEP = {
    "name": "Record the commit and the attempt that passed every guard",
    "id": "approved",
    "env": {"SHA": "${{ github.sha }}"},
    "run": 'echo "commit=$SHA" >> "$GITHUB_OUTPUT"\necho "attempt=$GITHUB_RUN_ATTEMPT" >> "$GITHUB_OUTPUT"\n',
}
EVENT_GUARD_ENV = {
    "EVENT_NAME": "${{ github.event_name }}",
    "ACTION": "${{ github.event.action }}",
    "REF": "${{ github.ref }}",
    "TAG": "${{ github.event.release.tag_name }}",
    "DRAFT": "${{ github.event.release.draft }}",
    "PRERELEASE": "${{ github.event.release.prerelease }}",
    "LC_ALL": "C",  # [0-9] in the tag check is then the ASCII digits only (WIDE_DIGIT_LOCALE)
}
MAIN_GUARD_ENV = {"REF": "${{ github.ref }}", "SHA": "${{ github.sha }}", "GIT_TERMINAL_PROMPT": "0"}
# The main guard also reads the tag from origin (SEC-2): actions/checkout refetches +<github.sha>:<tag> when the tag has
# moved, so the checkout's tag always names the run's commit. A read of the public repository, without credentials.
MAIN_GUARD_NAME = "Refuse unless the tag names the checked-out commit and it is on main"
# The read is bounded as the Release lookup's curl is (F2): GNU timeout ends a git ls-remote that a stalled origin
# holds, with exit status 124, and the guard fails through its "|| fail" (LS_REMOTE_FAILS) after LS_REMOTE_SECONDS
# instead of at the job's timeout (10 minutes in the approval job, 30 in the build job). The limit is exactly 60 whole
# seconds, the minute within which the main guard's comment says the guard fails (timeout 0 would set none), and
# timeout takes no option: with --foreground it would stop git alone and leave git's transport helper
# (git-remote-http) running past the limit.
LS_REMOTE = re.compile(r'remote="\$\(timeout (?P<seconds>[0-9]+) git ls-remote origin "\$REF" "\$REF\^\{\}"\)"')
LS_REMOTE_FAILS = ' \\\n  || fail "The tag $REF could not be read from origin."\n'  # in the run script as parsed
LS_REMOTE_SECONDS = 60
# The Release lookup (owner decision D1): one read-only GET of the tag's Release with the job token, whose response the
# Release guard after it reads from RUNNER_TEMP. Only these two steps of each Pages job see the token. A release run
# looks the Release up as a dispatch does: a re-run keeps the event of the run it repeats, which no longer tells whether
# the Release is still published and not a prerelease.
LOOKUP_NAME = "Look up the GitHub Release of the tag"
LOOKUP_ENV = {"EVENT_NAME": "${{ github.event_name }}", "REF": "${{ github.ref }}", "TOKEN": "${{ github.token }}"}
RELEASE_GUARD_ENV = {"EVENT_NAME": "${{ github.event_name }}", "REF": "${{ github.ref }}"}
RELEASE_API = '"https://api.github.com/repos/$GITHUB_REPOSITORY/releases/tags/$tag"'
NETWORK_COMMAND = re.compile(
    r"\b(?:curl|wget|nc|ncat|socat|telnet|ssh|scp|sftp|rsync|ftp)\b|/dev/(?:tcp|udp)/|"
    r"\bgit\s+(?:ls-remote|fetch|pull|clone)\b"
)
REPOSITORY = "FridrichMethod/dynamix"
# The first five steps of the approval and the build job: the guards before and after the checkout.
GUARD_ROLES = ("event", "lookup", "release", "checkout", "main")
# The event guard's tag check: v and three numbers without leading zeros (owner decision D2), under LC_ALL=C.
TAG_CHECK = r'[[ "$REF" =~ ^refs/tags/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]'
RETIRED_GATE = ("RELEASE_APPROVED_SHA", "release_sha", "vars.", "inputs.")  # the manual-dispatch gate before v1.1.0
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "89abcdef0123456789abcdef0123456789abcdef"


SAME_ATTEMPT = object()  # approved(): the approval job ran in the deploy job's own attempt


def approved(context: dict, commit: str | None = SHA, attempt: object = SAME_ATTEMPT) -> dict:
    """``context`` as the deploy job sees it once the approval job has recorded ``commit`` and ``attempt`` (None: no
    such output at all). By default the approval job ran in the deploy job's own attempt (``github.run_attempt``), as
    in a first run or a re-run of the whole run."""
    recorded = context.get("github.run_attempt") if attempt is SAME_ATTEMPT else attempt
    outputs = {APPROVAL_OUTPUT: commit, APPROVAL_ATTEMPT: recorded}
    return {**context, **{name: value for name, value in outputs.items() if value is not None}}


LOOKUP = "release lookup"  # a scenario's answer to the Release lookup: HTTP status, body and curl's exit status


def release_body(tag: str, **fields: object) -> str:
    """The body of GET /repos/{owner}/{repo}/releases/tags/{tag} for a published Release that is not a prerelease
    (``fields`` override its fields; None drops one)."""
    release = {
        "url": f"https://api.github.com/repos/{REPOSITORY}/releases/1",
        "tag_name": tag,
        "target_commitish": "main",
        "name": tag,
        "draft": False,
        "prerelease": False,
        **fields,
    }
    return json.dumps({name: value for name, value in release.items() if value is not None})


def released(tag: str, **release: object) -> dict:
    """The context of a published GitHub Release of ``tag`` (``release`` overrides fields of the event's release). Its
    Release lookup returns that Release as the event describes it; only a run that passes the event guard sends the
    lookup."""
    fields = {"tag_name": tag, "prerelease": False, "draft": False, **release}
    return {
        "github.event_name": "release",
        "github.event.action": "published",
        "github.ref": f"refs/tags/{tag}",
        "github.sha": SHA,
        "github.run_attempt": "1",
        **{f"github.event.release.{name}": value for name, value in fields.items() if value is not None},
        LOOKUP: ("200", release_body(tag, **release), 0),
    }


def rerun(tag: str, **current: object) -> dict:
    """A re-run of the whole run of ``released(tag)``, its second attempt: GitHub keeps the event of that run, while the
    Release lookup returns the Release of ``tag`` as it is now (``current`` overrides its fields)."""
    return {**looked_up(released(tag), "200", release_body(tag, **current)), "github.run_attempt": "2"}


def dispatched(ref: str, **release: object) -> dict:
    """A manual dispatch on ``ref``. Its Release lookup returns the published Release of the tag (``release`` overrides
    fields of that Release); only a dispatch that passes the event guard sends the lookup."""
    tag = ref.removeprefix("refs/tags/")
    return {
        "github.event_name": "workflow_dispatch",
        "github.ref": ref,
        "github.sha": SHA,
        "github.run_attempt": "1",
        LOOKUP: ("200", release_body(tag, **release), 0),
    }


def looked_up(context: dict, status: str, body: str, exit_status: int = 0) -> dict:
    """``context`` whose Release lookup gets another answer."""
    return {**context, LOOKUP: (status, body, exit_status)}


def triggered(event: str, ref: str) -> dict:
    return {"github.event_name": event, "github.ref": ref, "github.sha": SHA, "github.run_attempt": "1"}


# Each must pass the event guard, the Release lookup and the Release guard and, with the approval job's output, satisfy
# the deploy condition.
ACCEPTED = {
    "release v1.1.0 published": released("v1.1.0"),
    "re-run of release v1.1.0, whose Release is unchanged": rerun("v1.1.0"),
    "dispatch on the tag v1.1.0": dispatched("refs/tags/v1.1.0"),
    "release v10.20.30 published": released("v10.20.30"),
    "release v1.10.0 published": released("v1.10.0"),
    "dispatch on the tag v0.0.0": dispatched("refs/tags/v0.0.0"),
}
TAGGED = "refs/tags/v1.1.0"
NOT_FOUND = json.dumps({"message": "Not Found", "status": "404"})
# Runs on a vX.Y.Z tag that pass the event guard: the Release lookup or the Release guard refuses them (owner decision
# D1). A run deploys only a tag with a published Release that is not a prerelease; any other answer, a missing response
# included, fails closed. A release run is looked up too: a re-run keeps the event of the run it repeats, so its
# payload still passes the event guard after the Release became a prerelease or a draft or was deleted.
RELEASE_LOOKUP_DENIED = {
    "re-run of release v1.1.0, whose Release is now a prerelease": rerun("v1.1.0", prerelease=True),
    "re-run of release v1.1.0, whose Release is now a draft": rerun("v1.1.0", draft=True),
    "re-run of release v1.1.0, whose Release was deleted (HTTP 404)": looked_up(released("v1.1.0"), "404", NOT_FOUND),
    "re-run of release v1.1.0, whose Release lookup names another tag": rerun("v1.1.0", tag_name="v1.2.0"),
    "re-run of release v1.1.0, whose Release has no prerelease flag": rerun("v1.1.0", prerelease=None),
    "release v1.1.0 whose Release lookup is forbidden (HTTP 403)": looked_up(
        released("v1.1.0"), "403", json.dumps({"message": "Resource not accessible by integration"})
    ),
    "release v1.1.0 whose Release lookup fails at the server (HTTP 502)": looked_up(
        released("v1.1.0"), "502", "<html>Bad gateway</html>"
    ),
    "release v1.1.0 whose Release lookup gets no response (a network error)": looked_up(
        released("v1.1.0"), "000", "", 6
    ),
    "release v1.1.0 with a truncated Release response": looked_up(released("v1.1.0"), "200", '{"tag_name": "v1.1.0",'),
    "release v1.1.0 with an empty Release response": looked_up(released("v1.1.0"), "200", ""),
    "dispatch on a tag without a published Release (HTTP 404)": looked_up(dispatched(TAGGED), "404", NOT_FOUND),
    "dispatch whose Release lookup is forbidden (HTTP 403)": looked_up(
        dispatched(TAGGED), "403", json.dumps({"message": "Resource not accessible by integration"})
    ),
    "dispatch whose Release lookup is redirected (HTTP 301)": looked_up(
        dispatched(TAGGED), "301", json.dumps({"message": "Moved Permanently"})
    ),
    # Only the status refuses this one: the body is the tag's published Release, so a guard that also accepted a
    # redirect would deploy (curl follows no redirect, so a 301 is never the Release itself).
    "dispatch whose Release lookup is redirected (HTTP 301) with the Release's body": looked_up(
        dispatched(TAGGED), "301", release_body("v1.1.0")
    ),
    "dispatch whose Release lookup fails at the server (HTTP 502)": looked_up(
        dispatched(TAGGED), "502", "<html>Bad gateway</html>"
    ),
    "dispatch whose Release lookup gets no response (a network error)": looked_up(dispatched(TAGGED), "000", "", 6),
    "dispatch on a tag whose Release is a prerelease": dispatched(TAGGED, prerelease=True),
    "dispatch on a tag whose Release is a draft": dispatched(TAGGED, draft=True),
    "dispatch on a tag whose Release names another tag": dispatched(TAGGED, tag_name="v1.2.0"),
    "dispatch on a tag whose Release names the tag in upper case": dispatched(TAGGED, tag_name="V1.1.0"),
    "dispatch on a tag whose Release has no prerelease flag": dispatched(TAGGED, prerelease=None),
    "dispatch on a tag whose Release has no draft flag": dispatched(TAGGED, draft=None),
    "dispatch on a tag whose Release has a string prerelease flag": dispatched(TAGGED, prerelease="false"),
    "dispatch with a truncated Release response": looked_up(dispatched(TAGGED), "200", '{"tag_name": "v1.1.0",'),
    "dispatch with an empty Release response": looked_up(dispatched(TAGGED), "200", ""),
    "dispatch with a JSON array as the Release response": looked_up(
        dispatched(TAGGED), "200", f"[{release_body('v1.1.0')}]"
    ),
    "dispatch with two JSON documents as the Release response": looked_up(
        dispatched(TAGGED), "200", release_body("v9.9.9") + release_body("v1.1.0")
    ),
    "dispatch with a JSON string as the Release response": looked_up(dispatched(TAGGED), "200", '"v1.1.0"'),
    "dispatch with a JSON number as the Release response": looked_up(dispatched(TAGGED), "200", "1"),
    "dispatch with JSON null as the Release response": looked_up(dispatched(TAGGED), "200", "null"),
}
# Each must fail the gate: the event guard, or for RELEASE_LOOKUP_DENIED the Release lookup or the Release guard. The
# deploy condition refuses all but EXPRESSION_BLIND even with the approval job's output (as if a guard had been
# bypassed), and all of them without it (what a refusing approval job leaves).
DENIED = {
    "push to main": triggered("push", "refs/heads/main"),
    "push of the tag v1.1.0": triggered("push", "refs/tags/v1.1.0"),
    "pull request": triggered("pull_request", "refs/pull/1/merge"),
    "pull request from a tag-named branch": triggered("pull_request", "refs/tags/v1.1.0"),
    "schedule": triggered("schedule", "refs/heads/main"),
    "dispatch on main": dispatched("refs/heads/main"),
    "dispatch on the branch v1.1.0": dispatched("refs/heads/v1.1.0"),
    "dispatch on the tag v1.1": dispatched("refs/tags/v1.1"),
    "dispatch on the tag 1.1.0": dispatched("refs/tags/1.1.0"),
    "dispatch on the tag v1.1.0-rc1": dispatched("refs/tags/v1.1.0-rc1"),
    "dispatch on the tag v1.1.0.1": dispatched("refs/tags/v1.1.0.1"),
    "dispatch on the tag V1.1.0": dispatched("refs/tags/V1.1.0"),
    # Three numbers without leading zeros (owner decision D2, 2026-10-11).
    "release v01.1.0": released("v01.1.0"),
    "dispatch on the tag v1.01.0": dispatched("refs/tags/v1.01.0"),
    "release v1.1.00": released("v1.1.00"),
    "dispatch on a ref with a second line": dispatched("refs/tags/v1.1.0\nrefs/heads/main"),
    "dispatch on a tag with an Arabic-Indic digit": dispatched("refs/tags/v1.1.\u0663"),
    "dispatch on a tag with a fullwidth digit": dispatched("refs/tags/v\uff11.1.0"),
    "release of a tag with a superscript digit": released("v1.1.\u00b3"),
    "release v1.1": released("v1.1"),
    "release 1.1.0": released("1.1.0"),
    "release v1.1.0-rc1 marked as a prerelease": released("v1.1.0-rc1", prerelease=True),
    "release v1.1.0-rc1 not marked as a prerelease": released("v1.1.0-rc1"),
    "prerelease v1.1.0": released("v1.1.0", prerelease=True),
    "release without a prerelease flag": released("v1.1.0", prerelease=None),
    "draft release": released("v1.1.0", draft=True),
    "release edited, not published": {**released("v1.1.0"), "github.event.action": "edited"},
    "release whose tag is not the ref": released("v1.1.0", tag_name="v1.2.0"),
    **RELEASE_LOOKUP_DENIED,
}
# Scenarios that the deploy condition alone accepts when the approval job's output is github.sha (GitHub expressions
# have no regular expressions, startsWith() ignores case, the condition does not compare the release's draft flag or
# tag name, and it cannot see the Release lookup): the guards refuse them in the approval and the build job, so the
# approval job records no commit, and the deploy job needs both jobs. A stricter condition shrinks this set. In a
# re-run of the deploy job alone, which repeats no guard, none of them deploys (ATTEMPTS).
EXPRESSION_BLIND = {
    "dispatch on the tag v1.1",
    "dispatch on the tag v1.1.0-rc1",
    "dispatch on the tag v1.1.0.1",
    "dispatch on the tag V1.1.0",
    "release v01.1.0",
    "dispatch on the tag v1.01.0",
    "release v1.1.00",
    "dispatch on a ref with a second line",
    "dispatch on a tag with an Arabic-Indic digit",
    "dispatch on a tag with a fullwidth digit",
    "release of a tag with a superscript digit",
    "release v1.1",
    "release v1.1.0-rc1 not marked as a prerelease",
    "draft release",
    "release whose tag is not the ref",
    *RELEASE_LOOKUP_DENIED,
}
# The deploy job's attempt (github.run_attempt, numbered from 1) and the attempt that the approval job recorded (None:
# no such output), with whether the deploy condition holds (R1). A re-run of the whole run repeats every guard and the
# Release lookup and records its own attempt. A re-run of the deploy job alone ("Re-run this job" on Deploy, or "Re-run
# failed jobs" after only Deploy failed), like any re-run without the approval job, keeps the approval job's outputs of
# an earlier attempt, so it deploys nothing, also when it repeats the build job's guards and lookup.
ATTEMPTS = {
    "the first attempt": ("1", "1", True),
    "a re-run of the whole run (attempt 2 records 2)": ("2", "2", True),
    "a re-run of the deploy job alone (attempt 2 keeps the record of attempt 1)": ("2", "1", False),
    "a second re-run of the deploy job alone (attempt 3 keeps the record of attempt 1)": ("3", "1", False),
    "a re-run of the failed build and deploy jobs after a whole re-run (attempt 3 keeps 2)": ("3", "2", False),
    "a record of attempt 10 in attempt 1": ("1", "10", False),
    "no attempt output (as a refusing approval job leaves it)": ("1", None, False),
    "an empty attempt output": ("1", "", False),
}


def run_steps(workflows: dict[str, dict]) -> list[tuple[str, str, dict]]:
    """``(workflow, job id, step)`` for every step with a ``run`` script, in order."""
    return [
        (name, job_id, step)
        for name, workflow in workflows.items()
        for job_id, job in workflow["jobs"].items()
        for step in steps_of(job)
        if "run" in step
    ]


class PagesJobs:
    """pages.yml as parsed and the guard steps of its approval and build jobs, for ReleaseGateTests and for the
    ReleaseGuardTests of tests/test_workflows_release_guards.py, which executes them."""

    pages = load("pages.yml")

    def guards(self, job_id: str) -> dict[str, dict]:
        """The first five steps of the approval or the build job by role (GUARD_ROLES): the event guard, the Release
        lookup and the Release guard before any checkout, the checkout, and the main-ancestry guard after it."""
        return dict(zip(GUARD_ROLES, steps_of(self.pages["jobs"][job_id])[:5], strict=True))

    def guard_steps(self, job_id: str) -> tuple[dict, ...]:
        """The shell steps among the guards of one job: all but the checkout."""
        return tuple(step for role, step in self.guards(job_id).items() if role != "checkout")


class ReleaseGateTests(PagesJobs, unittest.TestCase):
    def test_jobs_run_the_reusable_tests_between_approval_and_build(self):
        jobs = self.pages["jobs"]
        self.assertEqual(list(jobs), ["approval", "tests", "build", "deploy", RERUN_CHECK])
        self.assertEqual(jobs["tests"]["uses"], "./.github/workflows/tests.yml")
        self.assertEqual(jobs["tests"]["needs"], "approval")
        self.assertEqual(jobs["build"]["needs"], ["approval", "tests"])
        self.assertEqual(jobs["deploy"]["needs"], ["approval", "build"])
        for job_id in ("approval", "tests", "build"):
            with self.subTest(job=job_id):
                self.assertNotIn("if", jobs[job_id], "a job condition could run it after a failed guard or skip it")
        build_runs = "\n".join(step.get("run", "") for step in steps_of(jobs["build"]))
        self.assertIn("scripts/prepare_pages.py --output", build_runs)
        self.assertIn("git rev-parse HEAD", build_runs)
        self.assertIn(
            "actions/upload-pages-artifact", [action_name(s.get("uses", "")) for s in steps_of(jobs["build"])]
        )

    def test_deploy_condition_is_exactly_the_gate(self):
        deploy = self.pages["jobs"]["deploy"]
        self.assertEqual(deploy["if"], DEPLOY_IF)
        self.assertNotRegex(deploy["if"], r"always\(|failure\(|cancelled\(|success\(")
        self.assertEqual(deploy["needs"], ["approval", "build"])
        self.assertEqual(deploy["environment"]["name"], "github-pages")
        self.assertEqual([action_name(step["uses"]) for step in steps_of(deploy)], ["actions/deploy-pages"])

    def test_the_rerun_check_job_is_the_reviewed_one(self):
        """The Re-run check job as written (PROD-1): last, needing the approval and the deploy job, with RERUN_CHECK_IF,
        no permission, a five-minute timeout and one step that only prints the error and fails; no checkout, action
        or expression in its script."""
        job = self.pages["jobs"][RERUN_CHECK]
        self.assertEqual(job, RERUN_CHECK_JOB)
        self.assertEqual(list(self.pages["jobs"])[-1], RERUN_CHECK, "it needs the deploy job, so it runs last")
        self.assertNotIn("${{", job["steps"][0]["run"])

    def test_the_rerun_check_runs_exactly_when_the_deploy_job_refuses_an_earlier_record(self):
        """For every scenario, attempt pair of ATTEMPTS (github.run_attempt as a string, as GitHub documents it, and
        as a number) and result of the approval job: the Re-run check job runs exactly when the approval job succeeded
        and the deploy condition refuses its record as one of another attempt. So the deploy job and this job never
        both run in one attempt, and a deploy job skipped for the attempt alone is always followed by it."""
        check, deploy = self.pages["jobs"][RERUN_CHECK]["if"], self.pages["jobs"]["deploy"]["if"]
        cases = [
            (scenario, context, label, attempts, result)
            for scenario, context in {**ACCEPTED, **DENIED}.items()
            for label, attempts in ATTEMPTS.items()
            for result in ("success", "failure", "cancelled", "skipped")
        ]
        for scenario, context, label, (current, recorded, allowed), result in cases:
            for run_attempt in (current, int(current)):
                seen = approved({**context, "github.run_attempt": run_attempt}, attempt=recorded)
                seen["needs.approval.result"] = result
                with self.subTest(scenario=scenario, attempt=label, run_attempt=run_attempt, approval=result):
                    self.assertEqual(evaluate(check, seen), result == "success" and not allowed)
                    self.assertFalse(evaluate(check, seen) and evaluate(deploy, seen), "never both in one attempt")
                    if scenario in ACCEPTED and result == "success":
                        self.assertNotEqual(evaluate(check, seen), evaluate(deploy, seen), "one of them runs")

    def test_approval_and_build_start_with_the_same_guards(self):
        jobs = self.pages["jobs"]
        approval = steps_of(jobs["approval"])
        self.assertEqual(len(approval), 6, "the five guard steps of GUARD_ROLES and the record of the commit")
        self.assertEqual(steps_of(jobs["build"])[:5], approval[:5], "the build job repeats the approval job's guards")
        event, lookup, release, checkout, main = approval[:5]
        self.assertNotIn("uses", event)
        self.assertEqual(event["env"], EVENT_GUARD_ENV)
        self.assertEqual(event["run"].count(TAG_CHECK), 1, "the tag check is exactly the vX.Y.Z pattern")
        self.assertEqual(lookup["name"], LOOKUP_NAME)
        self.assertEqual(lookup["env"], LOOKUP_ENV)
        self.assertEqual(release["env"], RELEASE_GUARD_ENV)
        self.assertIn('lookup="$RUNNER_TEMP/release-lookup"', release["run"], "the guard reads the saved response")
        self.assertNotRegex(release["run"], NETWORK_COMMAND, "the Release guard decides offline")
        self.assertEqual(action_name(checkout["uses"]), "actions/checkout")
        # The full history, so that origin/main is there to compare with; credentials are never kept.
        self.assertEqual(checkout["with"], {"fetch-depth": "0", "persist-credentials": "false"})
        self.assertNotIn("uses", main)
        self.assertEqual(main["name"], MAIN_GUARD_NAME)
        self.assertEqual(main["env"], MAIN_GUARD_ENV)
        self.assertEqual(len(LS_REMOTE.findall(main["run"])), 1, "the tag as origin has it, peeled to its commit")
        self.assertIn('git merge-base --is-ancestor "$SHA" refs/remotes/origin/main', main["run"])
        for step in (event, lookup, release, main):
            with self.subTest(step=step["name"]):
                self.assertTrue(step["run"].startswith('fail() { echo "::error::$1"; exit 1; }\n'))

    def test_the_main_guard_bounds_its_read_of_origin(self):
        """Each main guard reads origin with exactly one git ls-remote under timeout, whose limit is LS_REMOTE_SECONDS
        (60 whole seconds, as its comment says), and fails through "|| fail" when the read fails or runs over it
        (F2). tests/test_workflows_release_guards.py runs the guard when timeout ends the read."""
        for job_id in ("approval", "build"):
            script = self.guards(job_id)["main"]["run"]
            reads = list(LS_REMOTE.finditer(script))
            with self.subTest(job=job_id):
                self.assertEqual(len(reads), 1, "one bounded read of origin")
                self.assertEqual(reads[0].group("seconds"), str(LS_REMOTE_SECONDS), "60 whole seconds")
                self.assertEqual(script[reads[0].end() : reads[0].end() + len(LS_REMOTE_FAILS)], LS_REMOTE_FAILS)
                self.assertEqual(script.count("git ls-remote"), 1)

    def test_the_retired_dispatch_gate_is_gone(self):
        text = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
        for retired in RETIRED_GATE:
            with self.subTest(retired=retired):
                self.assertNotIn(retired, text)

    def test_deploy_condition_semantics(self):
        """The deploy job's own if: (as the workflow writes it, not only DEPLOY_IF) on every scenario. Without the
        approval job's record of github.sha, nothing deploys (D4): not when the output is missing or empty, as a
        refusing approval job leaves it, and not when it names another commit. Nor does a record of an earlier attempt
        deploy (R1): a re-run of the deploy job alone repeats no guard, so it refuses even the EXPRESSION_BLIND ones."""
        condition = self.pages["jobs"]["deploy"]["if"]
        self.assertLessEqual(EXPRESSION_BLIND, set(DENIED))
        for scenario, context in ACCEPTED.items():
            with self.subTest(accepted=scenario):
                self.assertTrue(evaluate(condition, approved(context)))
            for label, commit in (
                ("missing", None),
                ("empty", ""),
                ("another commit", OTHER_SHA),
                ("a short SHA", SHA[:12]),
            ):
                with self.subTest(accepted=scenario, approval_output=label):
                    self.assertFalse(evaluate(condition, approved(context, commit)))
        for scenario, context in DENIED.items():
            with self.subTest(denied=scenario):
                self.assertEqual(evaluate(condition, approved(context)), scenario in EXPRESSION_BLIND)
                self.assertFalse(evaluate(condition, approved(context, "")))
        for scenario, context in {**ACCEPTED, **DENIED}.items():
            later = {**context, "github.run_attempt": str(int(context["github.run_attempt"]) + 1)}
            with self.subTest(deploy_job_alone=scenario):
                self.assertFalse(evaluate(condition, approved(later, attempt=context["github.run_attempt"])))

    def test_only_the_attempt_that_passed_the_guards_deploys(self):
        """The approval job records the attempt it ran in, and the deploy condition requires it to be the deploy job's
        own (R1, ATTEMPTS): a first run and a re-run of the whole run deploy; a re-run of the deploy job alone, which
        repeats neither the guards nor the lookup, and a missing or empty record do not. GitHub documents
        github.run_attempt as a string; as a number it compares alike. A scenario that the guards refuse never deploys,
        and the deploy condition alone accepts an EXPRESSION_BLIND one only in the approval job's own attempt."""
        condition = self.pages["jobs"]["deploy"]["if"]
        cases = [
            (scenario, context, label, attempts)
            for scenario, context in {**ACCEPTED, **DENIED}.items()
            for label, attempts in ATTEMPTS.items()
        ]
        for scenario, context, label, (current, recorded, allowed) in cases:
            deployable = scenario in ACCEPTED or scenario in EXPRESSION_BLIND
            for run_attempt in (current, int(current)):
                with self.subTest(scenario=scenario, attempt=label, run_attempt=run_attempt):
                    seen = approved({**context, "github.run_attempt": run_attempt}, attempt=recorded)
                    self.assertEqual(evaluate(condition, seen), allowed and deployable)

    def test_approval_records_the_commit_after_every_guard(self):
        """The approval job's output is written by its last step, which has no if: or continue-on-error and so runs
        only when every guard before it passed; no other step of the workflow writes a step output."""
        approval = self.pages["jobs"]["approval"]
        self.assertEqual(approval["outputs"], APPROVAL_OUTPUTS)
        self.assertEqual(steps_of(approval)[-1], RECORD_STEP)
        self.assertIn(f"{APPROVAL_OUTPUT} == github.sha", self.pages["jobs"]["deploy"]["if"])
        self.assertIn(f"{APPROVAL_ATTEMPT} == github.run_attempt", self.pages["jobs"]["deploy"]["if"])
        for job_id, job in self.pages["jobs"].items():
            for step in steps_of(job):
                if step is steps_of(approval)[-1]:
                    continue
                with self.subTest(job=job_id, step=step.get("name")):
                    self.assertNotIn("GITHUB_OUTPUT", step.get("run", ""))
                    self.assertNotEqual(step.get("id"), "approved")

    def test_no_guard_step_or_gate_job_can_be_neutralised(self):
        """GitHub skips a step whose if: is false, lets a job succeed despite a failing step under continue-on-error,
        and runs a step with the shell it names. Any of these on a guard step, or continue-on-error or a defaults
        shell on the approval or the build job, would let a refused run reach the tests, the artifact or the
        deployment. The guard steps have exactly a name, an env and a run script, and run with the workflow's bash
        (bash --noprofile --norc -eo pipefail). An env that set GITHUB_RUN_ATTEMPT would let the record step write an
        attempt other than its own."""
        self.assertEqual(self.pages["defaults"], {"run": {"shell": "bash"}})
        for job_id in ("approval", "build"):
            job = self.pages["jobs"][job_id]
            for key in ("continue-on-error", "defaults", "if"):
                with self.subTest(job=job_id, key=key):
                    self.assertNotIn(key, job)
            for step in self.guard_steps(job_id):
                with self.subTest(job=job_id, step=step["name"]):
                    self.assertEqual(sorted(step), ["env", "name", "run"], "no if:, continue-on-error or shell:")
        record = steps_of(self.pages["jobs"]["approval"])[-1]
        self.assertEqual(sorted(record), ["env", "id", "name", "run"], "the record step runs only after the guards")
        # The recorded attempt is the runner's own: no env of the workflow, the approval job or the step sets it.
        for env in (self.pages.get("env") or {}, self.pages["jobs"]["approval"].get("env") or {}, record["env"]):
            self.assertNotIn("GITHUB_RUN_ATTEMPT", env)

    def test_guard_messages_describe_the_gate_not_a_hold(self):
        for job_id in ("approval", "build"):
            for step in self.guard_steps(job_id):
                messages = re.findall(r'\bfail "([^"]*)"', step["run"])
                self.assertTrue(messages)
                for message in messages:
                    with self.subTest(job=job_id, message=message):
                        self.assertNotRegex(message, re.compile(r"\bhold\b|\bheld\b|approved sha", re.IGNORECASE))
                        self.assertNotIn("RELEASE_APPROVED_SHA", message)

    def test_only_the_release_lookups_reach_the_github_api(self):
        """Every shell network tool or Git network command (NETWORK_COMMAND) in the workflows' scripts is a curl of a
        loopback smoke-test server (tests.yml), except one curl in the Release lookup of each Pages job, which alone
        names the GitHub API and receives the token, and one git ls-remote of origin in the main guard of each Pages
        job, without the token (SEC-2). Requests that Python, pip or npm make are not matched."""
        found = {"lookup": 0, "ls-remote": 0}
        roles = {LOOKUP_NAME: "lookup", MAIN_GUARD_NAME: "ls-remote"}
        for name, job_id, step in run_steps(all_workflows()):
            lines = step["run"].replace("\\\n", " ").splitlines()
            network = [line for line in lines if NETWORK_COMMAND.search(line)]
            role = roles.get(step.get("name")) if name == "pages.yml" else None
            command = "git ls-remote" if role == "ls-remote" else "curl"
            with self.subTest(workflow=name, job=job_id, step=step.get("name")):
                for line in network:
                    self.assertEqual(NETWORK_COMMAND.findall(line), [command], line)
                if role is not None:
                    self.assertIn(job_id, ("approval", "build"))
                    self.assertEqual(len(network), 1)
                    self.assertRegex(network[0], re.escape(RELEASE_API) if role == "lookup" else LS_REMOTE)
                    found[role] += 1
                if role == "lookup":
                    self.assertEqual(step["env"]["TOKEN"], "${{ github.token }}")
                    continue
                for line in network if role is None else ():
                    self.assertRegex(line, r"\bcurl (?:--[a-z-]+ )*(?:/dev/null )?http://127\.0\.0\.1:\d+/")
                self.assertNotIn("api.github.com", step["run"])
                self.assertNotIn("TOKEN", step.get("env") or {})
        self.assertEqual(found, {"lookup": 2, "ls-remote": 2})

    def test_the_network_rule_is_not_vacuous(self):
        """NETWORK_COMMAND matches the shell network tools and Git's network commands, and none of the local Git
        commands of the main guard."""
        for command in (
            "curl https://example.invalid/",
            "wget https://example.invalid/",
            "exec 3<>/dev/tcp/example.invalid/80",
            "git ls-remote origin refs/tags/v1.1.0",
            "git fetch origin main",
            "git pull",
            "git clone https://example.invalid/x.git",
        ):
            with self.subTest(network=command):
                self.assertRegex(command, NETWORK_COMMAND)
        for command in ("git rev-parse HEAD", 'git merge-base --is-ancestor "$SHA" refs/remotes/origin/main'):
            with self.subTest(local=command):
                self.assertNotRegex(command, NETWORK_COMMAND)

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


if __name__ == "__main__":
    unittest.main()
