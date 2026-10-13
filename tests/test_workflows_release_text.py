"""The release text around .github/workflows/pages.yml: what its comments and the READMEs say about the release gate.

A maintainer reads the pages.yml header when releasing, and the READMEs, which are staged with the website, describe the
release. The header says what deploys and what fails closed (HEADER_REQUIRED); no comment of pages.yml says that a
re-run without the approval job repeats no lookup (RERUN_STALE); the READMEs say that such a re-run deploys nothing and
fails at the Re-run check job (README_RERUN) and no longer describe the retired environment rule (RETIRED_ENVIRONMENT);
Gate 5 of docs/implementation/06-validation-and-release.md records the review fixes (GATE5_REVIEW_FIXES). Stale wording
is matched as unformatted() reads it. tests/test_workflows_release_gate.py checks the workflow itself.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_workflows import WORKFLOWS  # noqa: E402

# What each README says about a re-run of the deploy job alone (R1) and the job that fails it (PROD-1), and what it
# said while such a re-run could still deploy (matched as unformatted() reads it).
README_RERUN = {
    "README.md": (
        'A re-run of the deploy job alone, or of any jobs without the first job ("Release approval"), deploys '
        'nothing and fails at its last job ("Re-run check") with an error that says so; re-run the whole run '
        '("Re-run all jobs").',
        "does not look the Release up again",
    ),
    "README.zh-CN.md": (
        "只重新运行部署任务，或重新运行不含第一个任务（“Release approval”）的任何任务，都不会部署任何内容，"
        "并会在最后一个任务（“Re-run check”）以说明原因的错误失败；应重新运行整次运行（“Re-run all jobs”）。",
        "不会再次查询 Release",
    ),
}
# What Gate 5 of docs/implementation/06-validation-and-release.md says of the review fixes after v1.1.1: the Re-run
# check job (PROD-1), and that a run on v1.1.1 takes that tag's workflow, without the attempt record or the origin check.
GATE5 = ROOT / "docs/implementation/06-validation-and-release.md"
GATE5_REVIEW_FIXES = (
    'A last job, "Re-run check", then fails such a re-run with an error saying that it deployed nothing, so the run '
    "does not end green.",
    "Since a run takes `pages.yml` from the tag's commit, a run on `v1.1.1` uses that tag's own workflow, which has "
    "neither check.",
)
# The environment rule before the owner decision of 2026-10-12: the github-pages environment allowed main and every v*
# tag, so only the documentation kept tags before v1.1.1 from being run. The pages.yml header (English) and the READMEs
# (README.md English, README.zh-CN.md Chinese) must not say so again; matched as unformatted() reads them.
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
# What no comment of pages.yml (the header or a comment after it) may say about a re-run without the approval job (R1):
# that it repeats no lookup. It deploys nothing, and one that includes the build job repeats that job's lookup. Matched
# as unformatted() reads them, "repeats no lookup" and "repeats no Release lookup" cover every sentence with either, and
# "lookup it does not repeat" the approval job's outputs comment as the first R1 fix wrote it.
RERUN_STALE = ("repeats no lookup", "repeats no Release lookup", "lookup it does not repeat")
# Wording that the pages.yml header (read when releasing) must not have (matched as unformatted() reads it): the release
# hold, the retired dispatch gate and wording of earlier versions of this workflow.
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
    *RERUN_STALE,
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
    "gh workflow run pages.yml --ref v1.1.2",
    # A run takes pages.yml from the tag's commit: tags before v1.1.1 run a workflow without the lookup, and v1.1.1 one
    # without the attempt record (R1) and the origin check (SEC-2).
    "so the Release lookup applies to tags from v1.1.1 on",
    "a run on an older tag would use that tag's own workflow, which does not check the Release",
    "The attempt record and the origin check below apply to tags from v1.1.2 on: a run on v1.1.1 uses that tag's own "
    "workflow, which has neither.",
    "the github-pages environment refuses its deployment",
    "v*",
    "without leading zeros",
    "Every run, a release run, a re-run of one and a dispatch alike, first looks up the tag's GitHub Release",
    "with one read-only API request in the approval job (and again in the build job)",
    "only if that Release is published and not a prerelease",
    "A re-run keeps the event of the run it repeats",
    "A re-run of the deploy job alone, or of any jobs without the approval job, deploys nothing, since the deploy "
    "job requires the approval job's record of the current attempt, and the Re-run check job then fails it with an "
    "error that says so: re-run the whole run.",
    "the deploy job deploys only the commit that the approval job of the same attempt recorded",
    "needs the tag to name the run's commit on origin too (read with git ls-remote, without credentials",
    "a tag that origin now has on another commit",
    'ruleset "Release tags v*" lets only repository admins create, move or delete v* tags',
    "The github-pages environment accepts deployments only from tags from v1.1.1 on",
)


# What unformatted() drops: code backticks, backslashes (Markdown escapes such as \*), the emphasis markers * and _, and
# straight and curly quotes.
FORMATTING = re.compile("[`\\\\*_\"'\u2018\u2019\u201c\u201d]")


def unformatted(text: str) -> str:
    """``text`` as the stale wording (HEADER_STALE, RERUN_STALE, RETIRED_ENVIRONMENT, README_RERUN) is matched in
    pages.yml and the READMEs: in lower case, without FORMATTING, and with each run of whitespace as one space.
    "allows `main`", 'allows "main"', "allows **main**" and "allows  main" all read "allows main", and "*v\\** tags"
    reads "v tags", as "v* tags" does."""
    return re.sub(r"\s+", " ", FORMATTING.sub("", text)).lower()


class ReleaseTextTests(unittest.TestCase):
    def test_header_comment_describes_the_release_gate(self):
        """A maintainer reads this comment when releasing: it says what deploys and what fails closed. Stale wording
        counts with code backticks, quotes, emphasis or extra spaces too (unformatted()), as in the READMEs."""
        header = []
        for line in (WORKFLOWS / "pages.yml").read_text(encoding="utf-8").splitlines():
            if line.startswith("on:"):
                break
            if line.startswith("#"):
                header.append(line.lstrip("#").strip())
        text = " ".join(header)
        for stale in HEADER_STALE:
            with self.subTest(stale=stale):
                self.assertNotIn(unformatted(stale), unformatted(text))
        for required in HEADER_REQUIRED:
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_comments_after_the_header_drop_the_retired_re_run_wording(self):
        """The comments after the header, such as the approval job's outputs comment, do not say that a re-run without
        the approval job repeats no lookup either (RERUN_STALE, matched as unformatted() reads them)."""
        comments, block, after_header = [], [], False
        for line in [*(WORKFLOWS / "pages.yml").read_text(encoding="utf-8").splitlines(), ""]:
            after_header = after_header or line.startswith("on:")
            if after_header and line.strip().startswith("#"):
                block.append(line.strip().lstrip("#").strip())
            elif block:
                comments.append(" ".join(block))
                block = []
        self.assertTrue(any("outputs of an earlier attempt" in comment for comment in comments), comments)
        for stale in RERUN_STALE:
            with self.subTest(stale=stale):
                self.assertEqual([comment for comment in comments if unformatted(stale) in unformatted(comment)], [])

    def test_readmes_say_that_a_re_run_of_the_deploy_job_alone_deploys_nothing(self):
        """Both READMEs tell a maintainer that a re-run without the approval job deploys nothing (R1), that the Re-run
        check job then fails it (PROD-1), and to re-run the whole run, no longer only that such a re-run repeats no
        lookup."""
        for relative, (required, stale) in README_RERUN.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(document=relative):
                self.assertIn(required, text)
                self.assertNotIn(unformatted(stale), unformatted(text))

    def test_gate_5_describes_the_review_fixes(self):
        """Gate 5 of 06-validation-and-release.md names the Re-run check job and says that v1.1.1's own workflow has
        neither the attempt record nor the origin check (GATE5_REVIEW_FIXES)."""
        text = re.sub(r"\s+", " ", GATE5.read_text(encoding="utf-8"))
        for phrase in GATE5_REVIEW_FIXES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_readmes_drop_the_retired_environment_rule(self):
        """The READMEs, which describe the release, no longer say that the environment allows main or every v* tag
        or that only the documentation keeps older tags from being run (RETIRED_ENVIRONMENT)."""
        for relative, language in (("README.md", "en"), ("README.zh-CN.md", "zh")):
            text = unformatted((ROOT / relative).read_text(encoding="utf-8"))
            for stale in RETIRED_ENVIRONMENT[language]:
                with self.subTest(document=relative, stale=stale):
                    self.assertNotIn(unformatted(stale), text)


if __name__ == "__main__":
    unittest.main()
