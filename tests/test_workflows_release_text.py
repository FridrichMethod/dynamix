"""The release text around .github/workflows/pages.yml: what its comments and the READMEs say about the release gate.

A maintainer reads the pages.yml header when releasing, and the READMEs, which are staged with the website, describe the
release. The header (every comment before on:, and its lines that start with # alone: header_readings()) says what
deploys and what fails closed (HEADER_REQUIRED) and has no stale wording (HEADER_STALE, RERUN_STALE); no comment of
pages.yml (comment_blocks()) and no part of README.md says that a re-run without the approval job repeats no lookup
(RERUN_STALE); the READMEs say that such a re-run deploys nothing and fails at the Re-run check job (README_RERUN) and
no longer describe the retired environment rules (RETIRED_ENVIRONMENT); Gate 5 of
docs/implementation/06-validation-and-release.md records the review fixes and the environment from v1.1.2 on
(GATE5_REVIEW_FIXES, GATE5_ENVIRONMENT). Stale wording counts in either reading of a text (readings()).
tests/test_workflows_release_gate.py checks the workflow itself.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_workflows import WORKFLOWS  # noqa: E402

# What each README says about a re-run of the deploy job alone (R1) and the job that fails it (PROD-1), and what it
# said while such a re-run could still deploy (matched in either reading: has_stale()).
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
# check job (PROD-1), and that a run on v1.1.1 takes that tag's workflow, without the attempt record or the origin
# check.
GATE5 = ROOT / "docs/implementation/06-validation-and-release.md"
GATE5_REVIEW_FIXES = (
    'A last job, "Re-run check", then fails such a re-run with an error saying that it deployed nothing, so the run '
    "does not end green.",
    "Since a run takes `pages.yml` from the tag's commit, a run on `v1.1.1` uses that tag's own workflow, which has "
    "neither check.",
)
# The owner decision of 2026-10-13 in Gate 5 (ENV): the environment's tag rules from v1.1.2 on, and why.
GATE5_ENVIRONMENT = (
    "**Owner decision (2026-10-13, after release v1.1.2):** the `github-pages` environment accepts deployments only "
    "from tags from `v1.1.2` on (tag rules `v1.1.[2-9]*`, `v1.1.1[0-9]*`, `v1.[2-9]*`, `v1.[1-9][0-9]*`, `v[2-9]*` and "
    "`v[1-9][0-9]*`), so it refuses `v1.1.1` and older tags and accepts `v1.1.10` and later ones.",
    "`v1.1.2` is the first release whose `pages.yml` has every current check: the Release lookup (since `v1.1.1`), and "
    "the record of the attempt, the Re-run check job and the check of the tag on origin (since `v1.1.2`).",
    "a run on an older tag would use that tag's own workflow, which lacks some of these checks, and the environment "
    "refuses its deployment.",
)
# The environment rules before the owner decisions of 2026-10-12 and 2026-10-13, and what the documents said around
# them. Until 2026-10-12 the github-pages environment allowed main and every v* tag, so only the documentation kept tags
# before v1.1.1 from being run; until 2026-10-13 it accepted tags from v1.1.1 on, and the documents said that a run on
# v1.1.1 uses that tag's workflow, which has neither the attempt record nor the origin check, so re-run such a run
# whole. It now accepts tags from v1.1.2 on (GATE5_ENVIRONMENT). The pages.yml header (English) and the READMEs
# (README.md English, README.zh-CN.md Chinese) must not say any of this again; matched in either reading (has_stale()).
# "Neither" counts only after "own workflow" (m2): a sentence such as "a dispatch on a branch, which has neither a tag
# nor a Release, fails closed" is accurate (RETIRED_ENVIRONMENT_SAMPLES).
RETIRED_ENVIRONMENT = {
    "en": (
        "only tags from v1.1.1 on should be run by hand",
        "must allow deployments from v* tags",
        "allows deployments from v* tags",
        "allows main",
        "allows deployments from main",
        "from v1.1.1 on",
        "a run on v1.1.1 uses that tag's own workflow",
        "own workflow, which has neither",
        "re-run such a run whole",
    ),
    "zh": (
        "只应手动运行 v1.1.1 起的标签",
        "允许来自 v* 标签的部署",
        "允许 main",
        "允许来自 main",
        "v1.1.1 起",
        "在 v1.1.1 上的运行会使用该标签自己的工作流",
        "自己的工作流，两者都没有",
        "这样的运行也应整次重新运行",
    ),
}
# What no comment of pages.yml (the header or any other) and no part of README.md may say about a re-run without the
# approval job (R1): that it repeats no lookup. It deploys nothing, and one that includes the build job repeats that
# job's lookup. Matched in either reading of the text (rerun_stale()), the family is:
# - "repeat(s) no" with at most two words before "lookup", whatever the subject ("repeats no Release lookup", "repeats
#   no GitHub Release lookup", "repeats no further lookup", "repeats no Release-lookup");
# - "does not", "do not", "doesn't", "don't" or "never" and "repeat(s)" with at most two words before "lookup" ("does
#   not repeat the lookup"), when the subject is a re-run or a pronoun that can stand for one (m2): at the start of a
#   clause, after at most four words that are not prepositions ("A re-run", "Such re-runs", "If it failed, a re-run"),
#   either "re-run(s)", alone or followed by a phrase that starts with of, without, with, alone, that, which or "(", or
#   "re-running" and any phrase, where the phrase starts no new clause (_NEW_CLAUSE: no sentence break, and no comma,
#   alone or before "and", "but", "or", "so" or "while", before "the", "a", "an", "its", "this", "it" or "they"), as in
#   "A re-run of the deploy job alone, or of any jobs without the approval job, does not", "A re-run without the
#   approval job and the build job does not" or "Re-running the deploy job alone does not"; or "it" or "they" at the
#   start of a sentence. "The deploy job does not repeat the lookup", "The Re-run check job does not repeat the lookup"
#   and "In a re-run, the deploy job does not repeat the lookup" are not in it;
# - "lookup it does not repeat", as the approval job's outputs comment said after the first R1 fix.
# "look-up" and "look up" count as "lookup" (E5). The first and the last alternative need no word boundary at either
# end, so a text with one of 1bed861's three phrases ("repeats no lookup", "repeats no Release lookup", "lookup it does
# not repeat"), even inside a longer word, is in the family, as 1bed861 matched them. A sentence that says what a
# re-run repeats, or that names a lookup further on, is not in it (RERUN_STALE_SAMPLES).
_LOOKUP = r"look[ -]?ups?"
_PREPOSITION = r"(?:of|in|on|at|for|from|to|by|with|without|after|before|during|than|as|into|per|via) "
_NEW_CLAUSE = r"[.;:!?](?: |$)|, (?:(?:and|but|or|so|while) )?(?:the|a|an|its|this|it|they) "
_PHRASE = rf"(?:(?!{_NEW_CLAUSE}).)*?"
_RERUN_SUBJECT = (
    rf"(?:^|(?<=[.;:!?,]) |(?<=\())(?:(?!{_PREPOSITION})[a-z][a-z0-9-]* ){{0,4}}"
    rf"(?:re-?runs?(?: (?:(?:of|without|with|alone|that|which)\b|\(){_PHRASE})?|re-?running\b{_PHRASE}),?"
    r"|(?:^|(?<=[.;:!?]) )(?:it|they)"
)
RERUN_STALE = re.compile(
    rf"repeats? no (?:[a-z]+[ -]){{0,2}}{_LOOKUP}"
    rf"|(?:{_RERUN_SUBJECT}) (?:(?:does|do) not|doesnt|dont|never) repeats? (?:[a-z]+[ -]){{0,2}}{_LOOKUP}"
    rf"|{_LOOKUP} it does not repeat"
)
# What the comment of the Re-run check job says of the runs that skip it (E8, m3): a cancelled run ends cancelled, so
# the job does not run in it (!cancelled()).
RERUN_CHECK_COMMENT = (
    "GitHub re-runs a job together with every job that needs it, so every re-run reaches this job.",
    "The first attempt and a re-run of all jobs skip it, and so do a run that the approval job refused and a cancelled "
    "run, which ends cancelled, not green.",
)
# Wording that the pages.yml header (read when releasing) must not have (matched in either reading: has_stale()): the
# release hold, the retired dispatch gate and wording of earlier versions of this workflow.
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
    "gh workflow run pages.yml --ref v1.1.3",
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
    # A run takes pages.yml from the tag's commit, and v1.1.2 is the first release whose workflow has every check above
    # (the Release lookup, the attempt record, the Re-run check job and the origin check): the environment refuses the
    # deployment of a run on an older tag (ENV, owner decision of 2026-10-13).
    "A run takes this file from the tag's commit, and the github-pages environment accepts deployments only from tags "
    "from v1.1.2 on: a run on an older tag would use that tag's own workflow, which lacks some of these checks, and "
    "the environment refuses its deployment.",
)


# What unformatted() reads as markup, keeping only the text that it marks, as CommonMark reads it on one line: an open
# tag (a tag name, attributes after spaces or tabs, an optional / and >) or a closing tag, which goes (<code>main</code>
# reads main); an autolink such as <https://...>, which goes whole; and the brackets and target of an inline link
# ([main](...) reads main). Any other <, as in "a < b" or a version comparison such as "<v1.1.2 ... >=v1.1.2", starts
# no tag and stays, as GitHub shows it (M1).
_ATTRIBUTE = r"""[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t]*=[ \t]*(?:[^\s"'=<>`]+|'[^'\n]*'|"[^"\n]*"))?"""
MARKUP = re.compile(
    rf"<[A-Za-z][A-Za-z0-9-]*(?:{_ATTRIBUTE})*[ \t]*/?>|</[A-Za-z][A-Za-z0-9-]*[ \t]*>"
    r"|<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*>"
    r"|\[([^\[\]\n]*)\]\([^()\s]*\)"
)
# What unformatted() drops: code backticks, backslashes (Markdown escapes such as \*), the emphasis and strikethrough
# markers *, _ and ~, straight and curly quotes, and invisible format characters (zero-width space, non-joiner and
# joiner, word joiner, byte order mark and soft hyphen), which split a word without showing.
FORMATTING = re.compile("[`\\\\*_~\"'\u2018\u2019\u201c\u201d\u200b\u200c\u200d\u2060\ufeff\u00ad]")
# A Chinese character or CJK punctuation. Chinese typography may leave out the space between CJK and Latin text, so
# unformatted() drops a space next to one (CJK_SPACE): "允许 main" and "允许main" read alike.
CJK = "\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
CJK_SPACE = re.compile(f" (?=[{CJK}])|(?<=[{CJK}]) ")


def unformatted(text: str, markup: bool = True) -> str:
    """``text`` as the stale wording (HEADER_STALE, RERUN_STALE, RETIRED_ENVIRONMENT, README_RERUN) is matched in
    pages.yml and the READMEs: without MARKUP (the text it marks kept; with ``markup`` false, MARKUP stays as written)
    and FORMATTING, with each run of whitespace as one space and no space next to CJK text (CJK_SPACE), in lower case.
    "allows `main`", 'allows "main"', "allows **main**", "allows <code>main</code>", "allows [main](...)" and
    "allows  main" all read "allows main", "*v\\** tags" reads "v tags", as "v* tags" does, and "允许`main`" reads
    "允许main", as "允许 main" does (UNFORMATTED)."""
    marked = MARKUP.sub(lambda match: match.group(1) or "", text) if markup else text
    return CJK_SPACE.sub("", re.sub(r"\s+", " ", FORMATTING.sub("", marked))).lower()


def readings(text: str) -> tuple[str, str]:
    """The two readings of ``text`` in which stale wording counts: unformatted() and unformatted() with MARKUP kept as
    written (M1). What MARKUP drops (a tag with its attributes, an autolink, a link target) then hides no wording that
    the text as written has: v1.1.2 read the text without MARKUP, so nothing that it refused passes."""
    return unformatted(text), unformatted(text, markup=False)


def has_stale(text: str, stale: str) -> bool:
    """Whether either reading of ``text`` (readings()) contains the stale wording ``stale`` as unformatted() reads
    it."""
    return any(unformatted(stale) in reading for reading in readings(text))


def rerun_stale(text: str) -> list[str]:
    """What RERUN_STALE finds in either reading of ``text`` (readings())."""
    return [match.group(0) for reading in readings(text) for match in RERUN_STALE.finditer(reading)]


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


def header_readings(workflow: str) -> tuple[str, str]:
    """The two readings of the header of the workflow file ``workflow`` (N1): header_comment(), and its lines that
    start with # alone, each without its leading #s and outer whitespace, joined with one space, as v1.1.2 read the
    header. So an indented comment or a line of code with an end-of-line comment between two such lines splits no
    stale phrase and no required one that v1.1.2 read across them (HEADER_LAYOUT_SAMPLES)."""
    lines = workflow.splitlines()
    end = next(number for number, line in enumerate(lines) if line.startswith("on:"))
    column_zero = " ".join(line.lstrip("#").strip() for line in lines[:end] if line.startswith("#"))
    return header_comment(workflow), column_zero


def header_stale(workflow: str) -> list[str]:
    """The stale wording in either reading of the header of ``workflow`` (header_readings()): each HEADER_STALE phrase
    that has_stale() finds and what rerun_stale() finds."""
    return [
        found
        for header in header_readings(workflow)
        for found in (*(stale for stale in HEADER_STALE if has_stale(header, stale)), *rerun_stale(header))
    ]


def header_missing(workflow: str, required: tuple[str, ...] = HEADER_REQUIRED) -> list[str]:
    """Each phrase of ``required`` (HEADER_REQUIRED) that either reading of the header of ``workflow``
    (header_readings()) lacks as written."""
    return [phrase for header in header_readings(workflow) for phrase in required if phrase not in header]


class ReleaseTextTests(unittest.TestCase):
    # Texts and how unformatted() reads them (E2, E3, M1): code, quotes, emphasis, Markdown escapes, extra spaces, HTML
    # tags, links, strikethrough, invisible characters and case are dropped; a space next to CJK text too, while a
    # space between Latin words and a < that starts no tag (MARKUP), as in a version comparison, stay.
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
        "allows <a href=x title=\"a b\" class='c'>main</a>": "allows main",
        "allows [main](https://example.invalid/main)": "allows main",
        "see <https://example.invalid/> now": "see now",
        "allows ~~main~~": "allows main",
        "allows m\u200bain, m\u200cai\u200dn, ma\u2060in, \ufeffmain and mai\u00adn": (
            "allows main, main, main, main and main"
        ),
        "Allows MAIN": "allows main",
        "a < b and c > d": "a < b and c > d",
        "a tag <v1.1.2 runs its workflow (>=v1.1.2 deploys)": "a tag <v1.1.2 runs its workflow (>=v1.1.2 deploys)",
        "环境允许`main`": "环境允许main",
        "环境允许 main 部署": "环境允许main部署",
        "不会再次查询 **Release**。": "不会再次查询release。",
        "（ v1.1.1 ）起": "（v1.1.1）起",
        "早于 <v1.1.2 的标签（>=v1.1.2 不同）": "早于<v1.1.2的标签（>=v1.1.2不同）",
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

    # Text in which a < starts no tag, as a version comparison such as "<v1.1.2 ... >=v1.1.2" does (M1), in README.md,
    # README.zh-CN.md, a comment of pages.yml and its header, with the stale wording that v1.1.2 refused in each.
    ANGLE_COMMENT = "On a tag <v1.1.2 a deploy-only re-run repeats no lookup (>=v1.1.2 differs)."
    ANGLE_SAMPLES = (
        (
            "README.md",
            "A run on a tag <v1.1.2 uses its own workflow; the environment allows main and refuses it (>=v1.1.2 "
            "deploys).",
            "allows main",
        ),
        (
            "README.zh-CN.md",
            "早于 <v1.1.2 的标签：只重新运行部署任务不会再次查询 Release（>=v1.1.2 不同）。",
            "不会再次查询 Release",
        ),
        ("pages.yml", ANGLE_COMMENT, "repeats no lookup"),
        ("pages.yml header", ANGLE_COMMENT, "repeats no lookup"),
    )

    def test_a_less_than_sign_that_starts_no_tag_hides_no_wording(self):
        """unformatted() keeps the text between a < that starts no tag and the next >, so the stale wording in it is
        still found, as v1.1.2 found it (M1)."""
        for document, text, stale in self.ANGLE_SAMPLES:
            if document == "pages.yml header":
                text = header_comment(f"# {text}\non:\n")
            with self.subTest(document=document):
                self.assertIn(unformatted(stale), unformatted(text))
                if document.startswith("pages.yml"):
                    self.assertTrue(RERUN_STALE.search(unformatted(text)), text)

    # Stale wording that only the text as written has (M1): in an attribute of a tag, which GitHub may show as a
    # tooltip, and in a link target, both of which MARKUP drops. v1.1.2 refused each of them in the header, and the
    # attribute ones in every comment of pages.yml after it and in the READMEs too.
    WRITTEN_SAMPLES = (
        ('The environment <abbr title="allows main">env</abbr> deploys.', "allows main"),
        ("See [Gate 5](06-validation-and-release.md#gate-5--release-hold-design).", "hold"),
        ('A re-run <span title="repeats no lookup">alone</span> deploys nothing.', None),
    )

    def test_markup_hides_no_wording_that_the_text_has_as_written(self):
        """Stale wording counts in either reading of a text (readings()), so a span that MARKUP drops does not hide
        it (M1)."""
        for text, stale in self.WRITTEN_SAMPLES:
            with self.subTest(text=text):
                self.assertNotIn(stale or "repeats no lookup", unformatted(text))
                self.assertTrue(has_stale(text, stale) if stale else rerun_stale(text))

    # Stale wording that only the text without MARKUP has (N2): split by a tag or a link, which unformatted() drops
    # but the text as written (unformatted() with markup false) keeps. The first reading of readings() finds it.
    SHOWN_SAMPLES = (
        ("The environment allows <code>main</code>.", "allows main"),
        ("The environment allows [main](https://example.invalid/main).", "allows main"),
        ("A re-run repeats <em>no</em> lookup.", None),
        ("It does not repeat <code>the</code> lookup.", None),
    )

    def test_markup_hides_no_wording_that_the_text_shows(self):
        """Stale wording counts in the reading without MARKUP as well (readings()), so a tag or a link inside it does
        not hide it (N2)."""
        for text, stale in self.SHOWN_SAMPLES:
            with self.subTest(text=text):
                written = unformatted(text, markup=False)
                self.assertFalse(unformatted(stale) in written if stale else RERUN_STALE.search(written))
                self.assertTrue(has_stale(text, stale) if stale else rerun_stale(text))

    def test_header_comment_describes_the_release_gate(self):
        """A maintainer reads this comment when releasing: it says what deploys and what fails closed. Every comment
        before on: counts (header_comment()), an indented or end-of-line one too, and so do the lines that start with #
        alone, as v1.1.2 read them (header_readings()). Stale wording counts with code backticks, quotes, emphasis or
        extra spaces too, in either reading of the text (readings()), as in the READMEs."""
        workflow = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
        with self.subTest("stale"):
            self.assertEqual(header_stale(workflow), [])
        with self.subTest("required"):
            self.assertEqual(header_missing(workflow), [])

    # Headers in which an indented comment, or a line of code with an end-of-line comment, sits between lines that start
    # with #, and what is wrong in them: the stale wording found (header_stale()) and the required wording "fails
    # closed" missing (header_missing()), in the reading of the header that the comment before each group names.
    HEADER_LAYOUT_SAMPLES = (
        # The lines that start with # alone, as v1.1.2 read the header (N1).
        ("# It allows\n  # (a note)\n# main. It fails closed.\non:\n", {"allows main"}),
        ("# It allows\nrun-name: Pages  # a note\n# main. It fails closed.\non:\n", {"allows main"}),
        ("# It repeats no\n  # (a note)\n# lookup. It fails closed.\non:\n", {"repeats no lookup"}),
        ("  # It fails closed.\n# It deploys tags.\non:\n", {"fails closed"}),
        ("# It fails\n#\n# closed.\non:\n", {"fails closed"}),
        # Every comment before on: (header_comment(), E4).
        ("# It fails\n  # (a note)\n# closed.\non:\n", {"fails closed"}),
        ("# It fails\nrun-name: Pages  # a note\n# closed.\non:\n", {"fails closed"}),
        ("# It fails closed.\n  # It allows main.\non:\n", {"allows main"}),
        # Neither.
        ("# It fails closed.\n  # (a note)\n# It refuses older tags.\non:\n", set()),
    )

    def test_a_comment_between_header_lines_hides_no_wording(self):
        """Stale wording counts in either reading of the header, and required wording must be in both
        (header_readings()), so nothing that v1.1.2 refused in the header passes for its layout (N1)."""
        for workflow, wrong in self.HEADER_LAYOUT_SAMPLES:
            with self.subTest(workflow=workflow):
                self.assertEqual({*header_stale(workflow), *header_missing(workflow, ("fails closed",))}, wrong)

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
        scripts, and no # inside a word or right after a quote; header_comment() is every comment before on:, and
        header_readings() adds the lines before on: that start with # alone."""
        self.assertEqual(comment_blocks(self.SAMPLE_WORKFLOW), self.SAMPLE_COMMENTS)
        self.assertEqual(header_comment(self.SAMPLE_WORKFLOW), self.SAMPLE_COMMENTS[0][1])
        self.assertEqual(header_readings(self.SAMPLE_WORKFLOW), (self.SAMPLE_COMMENTS[0][1], "a header line"))

    def test_no_comment_of_pages_yml_has_the_retired_re_run_wording(self):
        """No comment of pages.yml (comment_blocks(), the header and end-of-line comments included), such as the
        approval job's outputs comment, says that a re-run without the approval job repeats no lookup (RERUN_STALE,
        in either reading: rerun_stale())."""
        blocks = comment_blocks((WORKFLOWS / "pages.yml").read_text(encoding="utf-8"))
        comments = [comment for _, comment in blocks]
        self.assertTrue(any("outputs of an earlier attempt" in comment for comment in comments), comments)
        self.assertIn("v7.0.1", comments, "the end-of-line comment of the checkout's pin is read")
        self.assertEqual([comment for comment in comments if rerun_stale(comment)], [])

    def test_the_rerun_check_comment_names_the_runs_that_skip_the_job(self):
        """The comment of the Re-run check job says which runs skip it, a cancelled run among them
        (RERUN_CHECK_COMMENT)."""
        blocks = comment_blocks((WORKFLOWS / "pages.yml").read_text(encoding="utf-8"))
        (comment,) = [comment for _, comment in blocks if RERUN_CHECK_COMMENT[0] in comment]
        for phrase in RERUN_CHECK_COMMENT:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, comment)

    # Sentences in and outside the RERUN_STALE family (E5), matched in either reading (rerun_stale()).
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
            # A re-run or a pronoun as the subject of does not, doesn't, don't or never (m2).
            "A re-run of the deploy job alone doesn't repeat the lookup.",
            "A re-run of the deploy job alone never repeats the lookup.",
            "A re-run of the deploy job alone, or of any jobs without the approval job, does not repeat the lookup.",
            "Such re-runs don't repeat the lookup.",
            "If the deployment failed, a re-run of the deploy job does not repeat the lookup.",
            "They never repeat the Release lookup.",
            "Re-running the deploy job alone does not repeat the lookup.",
            "A re-run (of the deploy job alone) does not repeat the lookup.",
            "A re-run without the approval job and the build job does not repeat the lookup.",
            # 1bed861's phrases as substrings, as it matched them, inside longer words too.
            "It_repeats no lookup_step.",
            "Its_lookup it does not repeatedly send.",
        ),
        "allowed": (
            "A re-run keeps the event of the run it repeats, so only the lookup sees a Release that is now a draft.",
            "The build job repeats the approval job's guards and the Release lookup.",
            "Every run repeats the lookup.",
            "It repeats no guard of the approval job, and the build job repeats its own lookup.",
            "It repeats no guard of the approval job but the build job and its lookup run.",
            "It does not repeat the guards of the approval job while the build job repeats the lookup.",
            "A lookup that it repeats sees the Release as it is now.",
            # Another subject of does not, doesn't or never (m2).
            "The deploy job does not repeat the lookup; it relies on the approval job's record.",
            "The deploy job doesn't repeat the lookup, and the Re-run check job never repeats it.",
            "The Re-run check job does not repeat the lookup.",
            "In a re-run of the whole run, the deploy job does not repeat the lookup.",
            "The deploy job of a re-run never repeats the lookup.",
            "The job of a re-run does not repeat the lookup.",
            "A re-run of the deploy job deploys nothing, and the deploy job does not repeat the lookup.",
            "Once a re-run of the deploy job starts, the deploy job does not repeat the lookup.",
            "Re-running the deploy job alone deploys nothing, and the deploy job does not repeat the lookup.",
            "A re-run of the build job runs it. The deploy job does not repeat the lookup.",
            "The deploy job reads the record, and it does not repeat the lookup.",
            # At most two words before "lookup" (N3): with three, the object is another one.
            "A re-run repeats no job without its lookup.",
            "A re-run does not repeat jobs without their lookup.",
        ),
    }

    def test_the_retired_re_run_wording_is_one_narrow_family(self):
        for kind, sentences in self.RERUN_STALE_SAMPLES.items():
            for sentence in sentences:
                with self.subTest(kind=kind, sentence=sentence):
                    self.assertEqual(bool(rerun_stale(sentence)), kind == "stale")

    def test_readmes_say_that_a_re_run_of_the_deploy_job_alone_deploys_nothing(self):
        """Both READMEs tell a maintainer that a re-run without the approval job deploys nothing (R1), that the Re-run
        check job then fails it (PROD-1), and to re-run the whole run, no longer only that such a re-run repeats no
        lookup."""
        for relative, (required, stale) in README_RERUN.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(document=relative):
                self.assertIn(required, text)
                self.assertFalse(has_stale(text, stale))
        with self.subTest(document="README.md", stale=RERUN_STALE.pattern):
            self.assertEqual(rerun_stale((ROOT / "README.md").read_text(encoding="utf-8")), [])

    def test_gate_5_describes_the_review_fixes(self):
        """Gate 5 of 06-validation-and-release.md names the Re-run check job and says that v1.1.1's own workflow has
        neither the attempt record nor the origin check (GATE5_REVIEW_FIXES)."""
        text = re.sub(r"\s+", " ", GATE5.read_text(encoding="utf-8"))
        for phrase in GATE5_REVIEW_FIXES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_gate_5_records_the_environment_from_v1_1_2(self):
        """Gate 5 of 06-validation-and-release.md records the owner decision of 2026-10-13: the environment's tag rules
        from v1.1.2 on, and that a run on an older tag would use a workflow without some of the checks
        (GATE5_ENVIRONMENT)."""
        text = re.sub(r"\s+", " ", GATE5.read_text(encoding="utf-8"))
        for phrase in GATE5_ENVIRONMENT:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    # Sentences in and outside RETIRED_ENVIRONMENT (m2), in English and Chinese.
    RETIRED_ENVIRONMENT_SAMPLES = {
        "stale": (
            ("en", "A run on an older tag uses that tag's own workflow, which has neither."),
            ("zh", "较早标签上的运行会使用该标签自己的工作流，两者都没有。"),
        ),
        "allowed": (
            ("en", "A dispatch on a branch, which has neither a vX.Y.Z tag nor a Release, fails closed."),
            ("zh", "如果两者都没有，该文件不会收录。"),
        ),
    }

    def test_the_retired_environment_wording_is_narrow(self):
        for kind, sentences in self.RETIRED_ENVIRONMENT_SAMPLES.items():
            for language, sentence in sentences:
                with self.subTest(kind=kind, sentence=sentence):
                    found = [stale for stale in RETIRED_ENVIRONMENT[language] if has_stale(sentence, stale)]
                    self.assertEqual(bool(found), kind == "stale", found)

    def test_readmes_drop_the_retired_environment_rule(self):
        """The READMEs, which describe the release, no longer say that the environment allows main or every v* tag,
        that only the documentation keeps older tags from being run, that it accepts tags from v1.1.1 on or what a run
        on v1.1.1 lacks (RETIRED_ENVIRONMENT)."""
        for relative, language in (("README.md", "en"), ("README.zh-CN.md", "zh")):
            text = (ROOT / relative).read_text(encoding="utf-8")
            for stale in RETIRED_ENVIRONMENT[language]:
                with self.subTest(document=relative, stale=stale):
                    self.assertFalse(has_stale(text, stale))


if __name__ == "__main__":
    unittest.main()
