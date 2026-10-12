"""The release gate of .github/workflows/pages.yml: its guards, the Release lookup and the deploy condition.

The release gate (owner decisions of 2026-10-11, superseding the manual-dispatch design of 06-validation-and-release.md
Gate 5) must hold by construction: Pages deploys only a published, non-prerelease GitHub Release whose tag is vX.Y.Z
(without leading zeros), or a manual dispatch on such a tag, and only when the tag's GitHub Release is published and
not a prerelease (looked up by every run, a re-run of a release run included, with one read-only API request in the
approval job and one in the build job: the only GitHub API requests of the workflows' scripts and the only uses of the
job token), the tag's commit is on main, the full Tests workflow passed on it and the approval job recorded it. The
deploy job's condition is evaluated with GitHub's expression semantics (the evaluator of tests/test_workflows.py), and
every shell guard is executed (the Release lookup with a stand-in curl, the main guard in temporary Git
repositories with a local bare origin), for each scenario.
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

from test_workflows import (  # noqa: E402
    GIT_ENV,
    WORKFLOWS,
    action_name,
    all_workflows,
    evaluate,
    load,
    resolve_env,
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
LS_REMOTE = 'remote="$(git ls-remote origin "$REF" "$REF^{}")"'
# The Release lookup (owner decision D1): one read-only GET of the tag's Release with the job token, whose response the
# Release guard after it reads from RUNNER_TEMP. Only these two steps of each Pages job see the token. A release run
# looks the Release up as a dispatch does: a re-run keeps the event of the run it repeats, which no longer tells whether
# the Release is still published and not a prerelease.
LOOKUP_NAME = "Look up the GitHub Release of the tag"
LOOKUP_ENV = {"EVENT_NAME": "${{ github.event_name }}", "REF": "${{ github.ref }}", "TOKEN": "${{ github.token }}"}
RELEASE_GUARD_ENV = {"EVENT_NAME": "${{ github.event_name }}", "REF": "${{ github.ref }}"}
RELEASE_API = '"https://api.github.com/repos/$GITHUB_REPOSITORY/releases/tags/$tag"'
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
NETWORK_COMMAND = re.compile(
    r"\b(?:curl|wget|nc|ncat|socat|telnet|ssh|scp|sftp|rsync|ftp)\b|/dev/(?:tcp|udp)/|"
    r"\bgit\s+(?:ls-remote|fetch|pull|clone)\b"
)
REPOSITORY = "FridrichMethod/dynamix"
TOKEN = "stand-in-job-token"
# The first five steps of the approval and the build job: the guards before and after the checkout.
GUARD_ROLES = ("event", "lookup", "release", "checkout", "main")
# The event guard's tag check: v and three numbers without leading zeros (owner decision D2), under LC_ALL=C.
TAG_CHECK = r'[[ "$REF" =~ ^refs/tags/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]'
RETIRED_GATE = ("RELEASE_APPROVED_SHA", "release_sha", "vars.", "inputs.")  # the manual-dispatch gate before v1.1.0
# What each README says about a re-run of the deploy job alone (R1), and what it said while such a re-run could still
# deploy.
README_RERUN = {
    "README.md": (
        'A re-run of the deploy job alone, or of any jobs without the first job ("Release approval"), deploys '
        'nothing; re-run the whole run ("Re-run all jobs").',
        "does not look the Release up again",
    ),
    "README.zh-CN.md": (
        "只重新运行部署任务，或重新运行不含第一个任务（“Release approval”）的任何任务，都不会部署任何内容；"
        "应重新运行整次运行（“Re-run all jobs”）。",
        "不会再次查询 Release",
    ),
}
# The environment rule before the owner decision of 2026-10-12: the github-pages environment allowed main and every v*
# tag, so only the documentation kept tags before v1.1.1 from being run. The pages.yml header (English) and the READMEs
# (README.md English, README.zh-CN.md Chinese) must not say so again; matched in lower case, without code backticks.
RETIRED_ENVIRONMENT = {
    "en": (
        "only tags from v1.1.1 on should be run by hand",
        "must allow deployments from v* tags",
        "allows deployments from v* tags",
        "allows main",
        "allows deployments from main",
    ),
    "zh": ("只应手动运行 v1.1.1 起的标签", "允许来自 v* 标签的部署", "允许 main", "允许来自 main"),
}
# Wording that the pages.yml header (read when releasing) must not have (matched in lower case): the release hold, the
# retired dispatch gate and wording of earlier versions of this workflow.
HEADER_STALE = (
    "RELEASE_APPROVED_SHA",
    "release_sha",
    "hold",
    "approved sha",
    "on main, not",
    "does not look up",
    "dispatched tag",
    "--ref v1.1.0",  # v1.1.0's own pages.yml does no Release lookup
    "with one read-only API request and deploys",  # the approval and the build job each send one
    "repeats no lookup, so re-run the whole run",  # a re-run of the deploy job alone now deploys nothing
    "repeats no lookup and deploys nothing",  # a re-run that includes the build job repeats its lookup
    *RETIRED_ENVIRONMENT["en"],
)
# What the header must say: what deploys, what fails closed and the repository settings around the workflow.
HEADER_REQUIRED = (
    "published",
    "vX.Y.Z",
    "prerelease",
    "on main",
    "Tests workflow",
    "fails closed",
    "gh workflow run pages.yml --ref v1.1.1",
    # A run takes pages.yml from the tag's commit: tags before v1.1.1 run a workflow without the lookup.
    "a run on an older tag would use that tag's own workflow, which does not check the Release",
    "the github-pages environment refuses its deployment",
    "v*",
    "without leading zeros",
    "Every run, a release run, a re-run of one and a dispatch alike, first looks up the tag's GitHub Release",
    "with one read-only API request in the approval job (and again in the build job)",
    "only if that Release is published and not a prerelease",
    "A re-run keeps the event of the run it repeats",
    "A re-run of the deploy job alone, or of any jobs without the approval job, deploys nothing, since the deploy "
    "job requires the approval job's record of the current attempt: re-run the whole run.",
    "the deploy job deploys only the commit that the approval job of the same attempt recorded",
    "needs the tag to name the run's commit on origin too (read with git ls-remote, without credentials",
    "a tag that origin now has on another commit",
    'ruleset "Release tags v*" lets only repository admins create, move or delete v* tags',
    "The github-pages environment accepts deployments only from tags from v1.1.1 on",
)
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

# How origin's tags change after the checkout (SEC-2), which the checkout's own tags do not show: the tag, the run's
# commit, the git commands on origin and the commit that the main guard's refusal names (None: the guard passes). The
# names first and second stand for the commits of release_repository(), whose checkout keeps v1.1.0 (annotated) on
# second and v1.0.0 (lightweight) on first. Refs whose names end in the tag's name sort before and after it in the
# output of git ls-remote, so only the exact names count.
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
        (("update-ref", "refs/heads/refs/tags/v1.1.0", "first"), ("tag", "x/refs/tags/v1.1.0", "first")),
        None,
    ),
    "refs on origin whose names end in a lightweight tag's name": (
        "v1.0.0",
        "first",
        (("update-ref", "refs/heads/refs/tags/v1.0.0", "second"), ("tag", "x/refs/tags/v1.0.0", "second")),
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
    "a lightweight tag moved on origin": ("v1.0.0", "first", (("tag", "-f", "v1.0.0", "second"),), "second"),
    "a tag deleted on origin": ("v1.1.0", "second", (("tag", "-d", "v1.1.0"),), "no commit"),
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


def run_steps(workflows: dict[str, dict]) -> list[tuple[str, str, dict]]:
    """``(workflow, job id, step)`` for every step with a ``run`` script, in order."""
    return [
        (name, job_id, step)
        for name, workflow in workflows.items()
        for job_id, job in workflow["jobs"].items()
        for step in steps_of(job)
        if "run" in step
    ]


class ReleaseGateTests(unittest.TestCase):
    pages = load("pages.yml")

    def guards(self, job_id: str) -> dict[str, dict]:
        """The first five steps of the approval or the build job by role (GUARD_ROLES): the event guard, the Release
        lookup and the Release guard before any checkout, the checkout, and the main-ancestry guard after it."""
        return dict(zip(GUARD_ROLES, steps_of(self.pages["jobs"][job_id])[:5], strict=True))

    def guard_steps(self, job_id: str) -> tuple[dict, ...]:
        """The shell steps among the guards of one job: all but the checkout."""
        return tuple(step for role, step in self.guards(job_id).items() if role != "checkout")

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

    def test_jobs_run_the_reusable_tests_between_approval_and_build(self):
        jobs = self.pages["jobs"]
        self.assertEqual(list(jobs), ["approval", "tests", "build", "deploy"])
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
        self.assertEqual(main["run"].count(LS_REMOTE), 1, "the tag as origin has it, peeled to its commit")
        self.assertIn('git merge-base --is-ancestor "$SHA" refs/remotes/origin/main', main["run"])
        for step in (event, lookup, release, main):
            with self.subTest(step=step["name"]):
                self.assertTrue(step["run"].startswith('fail() { echo "::error::$1"; exit 1; }\n'))

    def test_the_retired_dispatch_gate_is_gone(self):
        text = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
        for retired in RETIRED_GATE:
            with self.subTest(retired=retired):
                self.assertNotIn(retired, text)

    def test_header_comment_describes_the_release_gate(self):
        """A maintainer reads this comment when releasing: it says what deploys and what fails closed."""
        header = []
        for line in (WORKFLOWS / "pages.yml").read_text(encoding="utf-8").splitlines():
            if line.startswith("on:"):
                break
            if line.startswith("#"):
                header.append(line.lstrip("#").strip())
        text = " ".join(header)
        for stale in HEADER_STALE:
            with self.subTest(stale=stale):
                self.assertNotIn(stale.lower(), text.lower())
        for required in HEADER_REQUIRED:
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_readmes_say_that_a_re_run_of_the_deploy_job_alone_deploys_nothing(self):
        """Both READMEs tell a maintainer that a re-run without the approval job deploys nothing (R1) and to re-run
        the whole run, no longer only that such a re-run repeats no lookup."""
        for relative, (required, stale) in README_RERUN.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(document=relative):
                self.assertIn(required, text)
                self.assertNotIn(stale, text)

    def test_readmes_drop_the_retired_environment_rule(self):
        """The READMEs, which describe the release, no longer say that the environment allows main or every v* tag
        or that only the documentation keeps older tags from being run (RETIRED_ENVIRONMENT)."""
        for relative, language in (("README.md", "en"), ("README.zh-CN.md", "zh")):
            text = (ROOT / relative).read_text(encoding="utf-8").replace("`", "").replace("**", "").lower()
            for stale in RETIRED_ENVIRONMENT[language]:
                with self.subTest(document=relative, stale=stale):
                    self.assertNotIn(stale.lower(), text)

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
                    self.assertIn(RELEASE_API if role == "lookup" else LS_REMOTE, network[0])
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
