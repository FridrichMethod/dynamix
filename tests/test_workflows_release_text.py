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
# What no comment of pages.yml (the header or any other) and no part of README.md may say about a re-run without the
# approval job (R1): that it repeats no lookup. It deploys nothing, and one that includes the build job repeats that
# job's lookup. Matched in the text as unformatted() reads it, the family is "repeat(s) no" with at most two words
# before "lookup" ("repeats no Release lookup", "repeats no GitHub Release lookup", "repeats no further lookup",
# "repeats no Release-lookup"), "do(es) not repeat" with at most two words before it ("does not repeat the lookup"),
# and "lookup it does not repeat", as the approval job's outputs comment said after the first R1 fix; "look-up" and
# "look up" count as "lookup" (E5). A sentence that says what a re-run repeats, or that names a lookup further on, is
# not in it (RERUN_STALE_SAMPLES).
_LOOKUP = r"look[ -]?ups?\b"
RERUN_STALE = re.compile(
    rf"\brepeats? no (?:[a-z]+[ -]){{0,2}}{_LOOKUP}"
    rf"|\b(?:does|do) not repeat (?:[a-z]+[ -]){{0,2}}{_LOOKUP}"
    rf"|\b{_LOOKUP} it does not repeat\b"
)
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


# What unformatted() reads as markup, keeping only the text that it marks: an HTML tag (<code>main</code> reads main; an
# autolink such as <https://...> goes whole) and the brackets and target of an inline Markdown link ([main](...) reads
# main). A < that a letter or a / does not follow, as in "a < b", starts no tag.
MARKUP = re.compile(r"</?[A-Za-z][^<>\n]*>|\[([^\[\]\n]*)\]\([^()\s]*\)")
# What unformatted() drops: code backticks, backslashes (Markdown escapes such as \*), the emphasis and strikethrough
# markers *, _ and ~, straight and curly quotes, and invisible format characters (zero-width space, non-joiner and
# joiner, word joiner, byte order mark and soft hyphen), which split a word without showing.
FORMATTING = re.compile("[`\\\\*_~\"'\u2018\u2019\u201c\u201d\u200b\u200c\u200d\u2060\ufeff\u00ad]")
# A Chinese character or CJK punctuation. Chinese typography may leave out the space between CJK and Latin text, so
# unformatted() drops a space next to one (CJK_SPACE): "允许 main" and "允许main" read alike.
CJK = "\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
CJK_SPACE = re.compile(f" (?=[{CJK}])|(?<=[{CJK}]) ")


def unformatted(text: str) -> str:
    """``text`` as the stale wording (HEADER_STALE, RERUN_STALE, RETIRED_ENVIRONMENT, README_RERUN) is matched in
    pages.yml and the READMEs: without MARKUP (the text it marks kept) and FORMATTING, with each run of whitespace as
    one space and no space next to CJK text (CJK_SPACE), in lower case. "allows `main`", 'allows "main"', "allows
    **main**", "allows <code>main</code>", "allows [main](...)" and "allows  main" all read "allows main", "*v\\** tags"
    reads "v tags", as "v* tags" does, and "允许`main`" reads "允许main", as "允许 main" does (UNFORMATTED)."""
    marked = MARKUP.sub(lambda match: match.group(1) or "", text)
    return CJK_SPACE.sub("", re.sub(r"\s+", " ", FORMATTING.sub("", marked))).lower()


# A comment of a workflow file: from a # at the start of a line or after whitespace to the end of the line (E4). That is
# a full-line comment at any indentation and an end-of-line comment, in the YAML and in a run script alike; a # inside a
# word or right after a quote, as in ${REF#refs/tags/} or "### Pages payload", starts none.
COMMENT = re.compile(r"(?:^|\s)#+(.*)$")


def comment_blocks(text: str) -> list[tuple[int, str]]:
    """Every comment of a workflow file (COMMENT), as blocks of consecutive lines that carry one: the number of the
    block's first line and its comments joined with one space."""
    blocks: list[tuple[int, str]] = []
    block: list[str] = []
    start = 0
    for number, line in enumerate([*text.splitlines(), ""], 1):
        match = COMMENT.search(line)
        if match:
            start = start if block else number
            block.append(match.group(1).strip())
        elif block:
            blocks.append((start, " ".join(part for part in block if part)))
            block = []
    return blocks


def header_comment(text: str) -> str:
    """The header of a workflow file: every comment before its line "on:", joined with one space."""
    lines = text.splitlines()
    end = next(number for number, line in enumerate(lines) if line.startswith("on:"))
    return " ".join(comment for _, comment in comment_blocks("\n".join(lines[:end])))


class ReleaseTextTests(unittest.TestCase):
    # Texts and how unformatted() reads them (E2, E3): code, quotes, emphasis, Markdown escapes, extra spaces, HTML
    # tags, links, strikethrough, invisible characters and case are dropped; a space next to CJK text too, while a
    # space between Latin words and a < that starts no tag stay.
    UNFORMATTED = {
        "allows `main`": "allows main",
        'allows "main"': "allows main",
        "allows \u201cmain\u201d and \u2018v*\u2019 tags": "allows main and v tags",
        "allows 'v*' tags": "allows v tags",
        "allows **main**": "allows main",
        "allows _main_": "allows main",
        "allows *v\\** tags": "allows v tags",
        "allows  main\n and\tmore": "allows main and more",
        "allows <code>main</code>": "allows main",
        'allows <span class="x">main</span><br/>': "allows main",
        "allows [main](https://example.invalid/main)": "allows main",
        "see <https://example.invalid/> now": "see now",
        "allows ~~main~~": "allows main",
        "allows m\u200bain, m\u200cai\u200dn, ma\u2060in, \ufeffmain and mai\u00adn": "allows main, main, main, main and main",
        "Allows MAIN": "allows main",
        "a < b and c > d": "a < b and c > d",
        "环境允许`main`": "环境允许main",
        "环境允许 main 部署": "环境允许main部署",
        "不会再次查询 **Release**。": "不会再次查询release。",
        "（ v1.1.1 ）起": "（v1.1.1）起",
    }

    def test_unformatted_reads_formatted_text_as_plain_text(self):
        for text, plain in self.UNFORMATTED.items():
            with self.subTest(text=text):
                self.assertEqual(unformatted(text), plain)
        # Both sides are unformatted, so a Chinese phrase without the space between CJK and Latin text contains the
        # stale phrase that has it.
        for text, stale in (
            ("环境允许`main`", "允许 main"),
            ("环境允许main", "允许 main"),
            ("不会再次查询Release", "不会再次查询 Release"),
        ):
            with self.subTest(text=text, stale=stale):
                self.assertIn(unformatted(stale), unformatted(text))

    def test_header_comment_describes_the_release_gate(self):
        """A maintainer reads this comment when releasing: it says what deploys and what fails closed. Every comment
        before on: counts (header_comment()), an indented or end-of-line one too, and stale wording counts with code
        backticks, quotes, emphasis or extra spaces too (unformatted()), as in the READMEs."""
        text = header_comment((WORKFLOWS / "pages.yml").read_text(encoding="utf-8"))
        for stale in HEADER_STALE:
            with self.subTest(stale=stale):
                self.assertNotIn(unformatted(stale), unformatted(text))
        with self.subTest(stale=RERUN_STALE.pattern):
            self.assertEqual(RERUN_STALE.findall(unformatted(text)), [])
        for required in HEADER_REQUIRED:
            with self.subTest(required=required):
                self.assertIn(required, text)

    # A workflow in the shapes that comments take in pages.yml, and its comment blocks and header (E4).
    SAMPLE_WORKFLOW = (
        "name: Sample  # an end-of-line comment\n"
        "  # an indented comment\n"
        "# a header line\n"
        "on:\n"
        "  workflow_dispatch:  # trailing\n"
        "jobs:\n"
        "  sample:\n"
        "    steps:\n"
        "      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567 # v1.2.3\n"
        "      - run: |\n"
        '          tag="${REF#refs/tags/}"\n'
        '          echo "### not a comment"\n'
        "          #   a shell comment\n"
        "          #\n"
        "          ## and more\n"
    )
    SAMPLE_COMMENTS = [
        (1, "an end-of-line comment an indented comment a header line"),
        (5, "trailing"),
        (9, "v1.2.3"),
        (13, "a shell comment and more"),
    ]

    def test_every_comment_of_a_workflow_is_read(self):
        """comment_blocks() reads full-line comments at any indentation and end-of-line comments, in the YAML and in run
        scripts, and no # inside a word or right after a quote; header_comment() is every comment before on:."""
        self.assertEqual(comment_blocks(self.SAMPLE_WORKFLOW), self.SAMPLE_COMMENTS)
        self.assertEqual(header_comment(self.SAMPLE_WORKFLOW), self.SAMPLE_COMMENTS[0][1])

    def test_no_comment_of_pages_yml_has_the_retired_re_run_wording(self):
        """No comment of pages.yml (comment_blocks(), the header and end-of-line comments included), such as the
        approval job's outputs comment, says that a re-run without the approval job repeats no lookup (RERUN_STALE,
        matched as unformatted() reads them)."""
        blocks = comment_blocks((WORKFLOWS / "pages.yml").read_text(encoding="utf-8"))
        comments = [comment for _, comment in blocks]
        self.assertTrue(any("outputs of an earlier attempt" in comment for comment in comments), comments)
        self.assertIn("v7.0.1", comments, "the end-of-line comment of the checkout's pin is read")
        self.assertEqual([comment for comment in comments if RERUN_STALE.search(unformatted(comment))], [])

    # Sentences in and outside the RERUN_STALE family (E5), matched as unformatted() reads them.
    RERUN_STALE_SAMPLES = {
        "stale": (
            "A re-run of the deploy job alone repeats no lookup.",
            "It repeats no Release lookup and deploys nothing.",
            "It repeats no `Release` lookup; it deploys nothing.",
            "It repeats no GitHub Release lookup.",
            "It repeats no Release-lookup.",
            "It repeats no look-up.",
            "It repeats no look up.",
            "It repeats no further lookup.",
            "Such re-runs repeat no lookups.",
            "It does not repeat the lookup.",
            "It does not repeat the Release lookup.",
            "It does not repeat lookups.",
            "Re-runs without the approval job do not repeat the lookup.",
            "It keeps the outputs of an earlier attempt, whose lookup it does not repeat.",
        ),
        "allowed": (
            "A re-run keeps the event of the run it repeats, so only the lookup sees a Release that is now a draft.",
            "The build job repeats the approval job's guards and the Release lookup.",
            "Every run repeats the lookup.",
            "It repeats no guard of the approval job, and the build job repeats its own lookup.",
            "It repeats no guard of the approval job but the build job and its lookup run.",
            "It does not repeat the guards of the approval job while the build job repeats the lookup.",
            "A lookup that it repeats sees the Release as it is now.",
        ),
    }

    def test_the_retired_re_run_wording_is_one_narrow_family(self):
        for kind, sentences in self.RERUN_STALE_SAMPLES.items():
            for sentence in sentences:
                with self.subTest(kind=kind, sentence=sentence):
                    self.assertEqual(bool(RERUN_STALE.search(unformatted(sentence))), kind == "stale")

    def test_readmes_say_that_a_re_run_of_the_deploy_job_alone_deploys_nothing(self):
        """Both READMEs tell a maintainer that a re-run without the approval job deploys nothing (R1), that the Re-run
        check job then fails it (PROD-1), and to re-run the whole run, no longer only that such a re-run repeats no
        lookup."""
        for relative, (required, stale) in README_RERUN.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(document=relative):
                self.assertIn(required, text)
                self.assertNotIn(unformatted(stale), unformatted(text))
        with self.subTest(document="README.md", stale=RERUN_STALE.pattern):
            self.assertEqual(RERUN_STALE.findall(unformatted((ROOT / "README.md").read_text(encoding="utf-8"))), [])

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
