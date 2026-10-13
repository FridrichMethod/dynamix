"""Release state of the public documentation. The READMEs, which are staged with the website, describe the current
release and the release-triggered Pages workflow; every other document leaves the release state to them, and no
document describes the retired manual-dispatch gate.

The documents and the rendering helper plain() come from tests/test_docs.py, and the release wording rules
(hold_wording(), release_wording_problems()) from tests/test_docs_wording.py, which also holds their samples. Offline:
it reads files of this checkout and runs git only to list the tags of HEAD, in this checkout and in a temporary
repository.
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_docs import DOCUMENTS, ROOT, plain, read  # noqa: E402 - the documents and a rendering helper (no TestCase)
from test_docs_wording import hold_wording, release_wording_problems  # noqa: E402 - the wording rules (no TestCase)

GITHUB = "https://github.com/"
REPOSITORY = GITHUB + "FridrichMethod/dynamix"
RELEASE_LINK = re.compile(r"https://github\.com/([^/\s()<>\[\]]+/[^/\s()<>\[\]]+)/releases/tag/([^\s()<>\[\]]+)")
VERSION = re.compile(r"v\d+\.\d+\.\d+")
# Release and website URLs, which both READMEs link alike.
PUBLISHED_URL = re.compile(
    r"https://(?:fridrichmethod\.github\.io/|github\.com/FridrichMethod/dynamix/releases)[^\s()<>\[\]]*"
)
# The repository's files at one ref (raw/<ref>/): main, which archive_url follows, the placeholder <tag> of the
# storage-tier paragraph (RAW_REFS), or a release, which can only be the current one: the Releases section gives the
# raw link of the tag that the website is deployed from (TEST-1).
RAW_LINK = re.compile(r"https://github\.com/FridrichMethod/dynamix/raw/([^/\s()\[\]`]+)/")
RAW_REFS = ("main", "<tag>")
# A release named with its date in either language, "`v1.1.2` (2026-10-12)" or "`v1.1.2`（2026-10-12）": the Releases
# section dates the current release first, then older ones, and both languages give the same releases and dates in
# the same order (TEST-1).
DATED_RELEASE = re.compile(r"`(v\d+\.\d+\.\d+)` ?[(（](\d{4}-\d{2}-\d{2})[)）]")
# The READMEs, which alone describe the release state (tests/test_docs_stats.py reads them from here too), each
# README's Status line prefix and the title of its Releases section.
READMES = ("README.md", "README.zh-CN.md")
README_RELEASE_PARTS = {"README.md": ("**Status:**", "Releases"), "README.zh-CN.md": ("**状态**：", "发布")}
# The release gate that v1.1.0 retired: a manual dispatch of main approved by the RELEASE_APPROVED_SHA variable and a
# release_sha input. Pages now deploys a published vX.Y.Z release (.github/workflows/pages.yml), so no document names
# the variable or the input, or tells a release to dispatch the workflow on main. Matched in the rendered text too
# (retired_gate()), so `main` needs no backticks here.
RETIRED_GATE = re.compile(
    r"RELEASE_APPROVED_SHA|\brelease_sha\b|"
    r"\bdispatch(?:ed|es|ing)? (?:it |the [^.;:]{0,30}? )?on main\b|在 main 上手动触发",
    re.IGNORECASE,
)


def readme_release_text(relative: str, text: str) -> tuple[str, str] | None:
    """The Status line and the Releases section of one README, or None when either is missing or not unique."""
    prefix, title = README_RELEASE_PARTS[relative]
    lines = text.splitlines()
    status = [line for line in lines if line.startswith(prefix)]
    if len(status) != 1 or lines.count(f"## {title}") != 1:
        return None
    start = lines.index(f"## {title}")
    end = next((n for n in range(start + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
    return status[0], "\n".join(lines[start + 1 : end])


def version_key(tag: str) -> tuple[int, ...]:
    """vX.Y.Z in numeric order (v1.10.0 after v1.2.3)."""
    return tuple(int(part) for part in tag[1:].split("."))


def dated_releases(relative: str, text: str) -> list[tuple[str, str]]:
    """The releases that the Releases section of one README names with a date (DATED_RELEASE), in order, as (version,
    date) pairs; none when the README has no such section."""
    parts = readme_release_text(relative, text)
    return DATED_RELEASE.findall(parts[1]) if parts else []


def dated_release_problems(relative: str, version: str, dated: list[tuple[str, str]]) -> list[str]:
    """What is wrong with the dated releases of one Releases section: the current release ``version`` must be dated
    first, each release once, newest first, and each date a calendar date no later than the date before it."""
    problems = []
    if [found for found, _ in dated[:1]] != [version]:
        problems.append(f"{relative}: the Releases section dates {dated[:1]} first, not the current release {version}")
    versions = [found for found, _ in dated]
    if versions != sorted(set(versions), key=version_key, reverse=True):
        problems.append(f"{relative}: the Releases section dates {versions}, not each release once, newest first")
    dates = [day for _, day in dated]
    for day in dates:
        try:
            datetime.date.fromisoformat(day)
        except ValueError:
            problems.append(f"{relative}: the Releases section gives {day}, which is not a date")
    if dates != sorted(dates, reverse=True):
        problems.append(f"{relative}: the Releases section dates a newer release before an older one: {dated}")
    return problems


def release_problems(relative: str, text: str) -> tuple[str | None, list[str]]:
    """The current release one README names and what is wrong with its description. Every release link is a vX.Y.Z
    release of this repository; the Status line links exactly one, the current release, which is the newest release
    linked anywhere (older ones, such as the first release, may be linked too) and the first version the Releases
    section names, links and dates (dated_release_problems()). Every raw link names main, the placeholder <tag> or the
    current release, and the Releases section gives the current release's. Neither part keeps wording of the former
    release hold, and the README does not describe the retired manual-dispatch gate."""
    parts = readme_release_text(relative, text)
    if parts is None:
        return None, [f"{relative}: needs one {README_RELEASE_PARTS[relative][0]!r} line and one Releases section"]
    status, section = parts
    links = RELEASE_LINK.findall(text)
    foreign = sorted({repository for repository, _ in links if GITHUB + repository != REPOSITORY})
    problems = [f"{relative}: release links of other repositories: {foreign}"] if foreign else []
    tags = sorted({tag for _, tag in links if VERSION.fullmatch(tag)}, key=version_key)
    malformed = sorted({tag for _, tag in links} - set(tags))
    if malformed:
        problems.append(f"{relative}: release links name {malformed}, which are not vX.Y.Z releases")
    current = sorted({tag for _, tag in RELEASE_LINK.findall(status)})
    if len(current) != 1 or not VERSION.fullmatch(current[0]):
        return None, [*problems, f"{relative}: the Status line links {current}, not one vX.Y.Z release"]
    version = current[0]
    if f"{REPOSITORY}/releases/tag/{version}" not in status:
        problems.append(f"{relative}: the Status line does not link release {version} of {REPOSITORY}")
    newer = [tag for tag in tags if version_key(tag) > version_key(version)]
    if newer:
        problems.append(f"{relative}: links releases {newer}, newer than the current release {version}")
    named = re.findall(r"`(v\d+\.\d+\.\d+)`", section)
    if named[:1] != [version]:
        problems.append(f"{relative}: the Releases section names {named[:1]} first, not {version}")
    if f"{REPOSITORY}/releases/tag/{version}" not in section:
        problems.append(f"{relative}: the Releases section does not link release {version}")
    problems.extend(dated_release_problems(relative, version, DATED_RELEASE.findall(section)))
    stale = sorted({ref for ref in RAW_LINK.findall(text) if ref not in (*RAW_REFS, version)})
    if stale:
        problems.append(f"{relative}: raw links name {stale}, not main, <tag> or the current release {version}")
    if version not in RAW_LINK.findall(section):
        problems.append(f"{relative}: the Releases section gives no raw link of release {version}")
    for place, part in (("Status line", status), ("Releases section", section)):
        problems.extend(f"{relative}: hold wording in the {place}: {found!r}" for found in hold_wording(plain(part)))
    problems.extend(f"{relative}: describes the retired gate: {found!r}" for found in retired_gate(text))
    return version, problems


def retired_gate(text: str) -> list[str]:
    """Mentions of the retired manual-dispatch gate, in the raw and the rendered text."""
    return sorted({match.group() for source in (text, plain(text)) for match in RETIRED_GATE.finditer(source)})


def head_release_tags(root: Path = ROOT) -> list[str]:
    """The vX.Y.Z tags that point at HEAD, oldest first: the release being built when the checkout is a release tag,
    as in the Pages workflow's test run. Empty when HEAD has no such tag or git cannot tell."""
    try:
        listing = subprocess.run(
            ["git", "tag", "--points-at", "HEAD"], cwd=root, capture_output=True, text=True, check=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted((tag for tag in listing.stdout.split() if VERSION.fullmatch(tag)), key=version_key)


def tag_problems(readmes: dict[str, str], tags: list[str]) -> list[str]:
    """A checkout tagged with vX.Y.Z releases is a release build: each README names the newest of them as its current
    release, so the website a release deploys describes that release."""
    if not tags:
        return []
    newest = max(tags, key=version_key)
    problems = []
    for relative, text in readmes.items():
        version = release_problems(relative, text)[0]
        if version != newest:
            problems.append(f"{relative}: names {version} as the current release, but HEAD is the release {newest}")
    return problems


def release_state_problems(readmes: dict[str, str]) -> list[str]:
    """Every problem of each README's release description (release_problems), plus disagreement between the languages:
    both name the same release, link the same release and Pages URLs, and date the same releases with the same dates in
    the same order (dated_releases())."""
    problems, versions, urls, dated = [], {}, {}, {}
    for relative, text in readmes.items():
        version, found = release_problems(relative, text)
        problems.extend(found)
        versions[relative] = version
        urls[relative] = sorted(set(PUBLISHED_URL.findall(text)))
        dated[relative] = dated_releases(relative, text)
    if len(set(versions.values())) > 1:
        problems.append(f"the READMEs name different releases: {versions}")
    if len({tuple(found) for found in urls.values()}) > 1:
        problems.append(f"the READMEs link different release or Pages URLs: {urls}")
    if len({tuple(found) for found in dated.values()}) > 1:
        problems.append(f"the READMEs date different releases or dates: {dated}")
    return problems


class ReleaseStateTests(unittest.TestCase):
    """The READMEs, which are staged with the website, describe the current release and the release-triggered Pages
    workflow. Every other document leaves the release state to them: no deployment or publication claim and no hold
    wording; a sentence about deployment or release is negated, conditional, future or restricted (how the guard
    works, not what happened). No document describes the retired manual-dispatch gate."""

    PAGES_URL = "https://fridrichmethod.github.io/dynamix/"
    # What each README's Releases section says about the release-triggered workflow, with the owner decisions of
    # 2026-10-11: tags without leading zeros (D2), every run, a re-run of a release run included, looking up the tag's
    # Release (D1), and the tag ruleset beside the environment rule (D5). A run takes pages.yml from the tag's commit,
    # and v1.1.1 is the first release whose workflow looks the tag's Release up, so older tags are said to run their own
    # workflow, which the environment refuses to deploy. A re-run without the approval job deploys nothing (R1), and a
    # tag moved on GitHub after the run started fails closed when the approval or the build job checks it, the only two
    # reads of the tag on GitHub (SEC-2, DOC-1); v1.1.2 is the first release whose workflow does both, so v1.1.1 is said
    # to lack them and the dispatch example names the current release. The current release is dated by the US Pacific
    # date of its release, as the earlier ones are (v1.1.0, tagged at 2026-10-11 20:18 PDT, is dated 2026-10-11): v1.1.2
    # is released on 2026-10-12 (DATE-1). The two releases before it are named in order, v1.1.1 the previous one and
    # v1.1.0 the one before it (TEST-1).
    RELEASE_MODEL = {
        "README.md": (
            "Version `v1.1.2` (2026-10-12) is the current release",
            "Version `v1.1.1` (2026-10-12) was the previous release, and version `v1.1.0` (2026-10-11) the one before "
            "it.",
            "published",
            "prerelease",
            "`vX.Y.Z`",
            "`v1.1.0-rc1`",
            "on `main`",
            "fails closed",
            "gh workflow run pages.yml --ref v1.1.2",
            "so the Release lookup applies to tags from `v1.1.1` on",
            "a run on an older tag would use that tag's own workflow, which does not check the Release",
            "The record of the current attempt and the check of the tag on GitHub apply to tags from `v1.1.2` on: a run on "
            "`v1.1.1` uses that tag's own workflow, which has neither, so re-run such a run whole too.",
            "the `github-pages` environment refuses its deployment",
            "`v*`",
            "three numbers without leading zeros",
            "`v01.1.0`",
            "Every run, a release run, a re-run of one and a dispatch alike, first looks up the tag's GitHub Release",
            "only if that Release is published and not a prerelease",
            "A re-run keeps the event of the run it repeats",
            'A re-run of the deploy job alone, or of any jobs without the first job ("Release approval"), deploys nothing '
            'and fails at its last job ("Re-run check") with an error that says so; re-run the whole run ("Re-run all '
            'jobs")',
            "a tag that names another commit on GitHub when the approval or the build job checks it (one moved after "
            "the run started)",
            "`github-pages` environment accepts deployments only from tags from `v1.1.1` on",
            "a repository ruleset named `Release tags v*` lets only repository admins create, move or delete `v*` tags",
        ),
        "README.zh-CN.md": (
            "`v1.1.2`（2026-10-12）是当前版本",
            "`v1.1.1`（2026-10-12）是上一个版本，`v1.1.0`（2026-10-11）是再上一个版本。",
            "发布",
            "预发布",
            "`vX.Y.Z`",
            "`v1.1.0-rc1`",
            "`main` 上",
            "失败关闭",
            "gh workflow run pages.yml --ref v1.1.2",
            "因此 Release 查询适用于 `v1.1.1` 起的标签",
            "较早标签上的运行会使用该标签自己的工作流，它不检查 Release",
            "对当前这次尝试的记录和在 GitHub 上对标签的检查适用于 `v1.1.2` 起的标签：在 `v1.1.1` 上的运行会使用该标签自己的工作流，"
            "两者都没有，因此这样的运行也应整次重新运行。",
            "`github-pages` 环境会拒绝它的部署",
            "`v*`",
            "三个不带前导零的数字",
            "`v01.1.0`",
            "每次运行，无论是发布的运行、它的重新运行还是手动触发，都会先查询该标签的 GitHub Release",
            "只有该 Release 已发布且不是预发布版本时才部署",
            "重新运行沿用它所重复的那次运行的事件",
            "只重新运行部署任务，或重新运行不含第一个任务（“Release approval”）的任何任务，都不会部署任何内容，"
            "并会在最后一个任务（“Re-run check”）以说明原因的错误失败；应重新运行整次运行（“Re-run all jobs”）",
            "在批准任务或构建任务检查时已在 GitHub 上指向另一个提交的标签（运行开始后被移动过）",
            "`github-pages` 环境只接受来自 `v1.1.1` 起的标签的部署",
            "名为 `Release tags v*` 的仓库规则集只允许仓库管理员创建、移动或删除 `v*` 标签",
        ),
    }

    def test_no_document_names_the_retired_gate(self):
        for relative in (*DOCUMENTS, "NOTICE"):
            with self.subTest(document=relative):
                self.assertEqual(retired_gate(read(relative)), [])

    def test_no_deployment_claims_outside_the_readmes(self):
        for relative in (*DOCUMENTS, "NOTICE"):
            if relative in READMES:
                continue
            with self.subTest(document=relative):
                self.assertEqual(release_wording_problems(read(relative)), [])

    def test_readmes_describe_the_release_and_its_guard(self):
        for relative in READMES:
            text = read(relative).replace("**", "")
            for phrase in (
                self.PAGES_URL,
                "/releases/tag/v",
                "(.github/workflows/pages.yml)",
                "(.github/workflows/check-sources.yml)",
            ):
                with self.subTest(document=relative, phrase=phrase):
                    self.assertIn(phrase, text, f"{relative} must say {phrase!r}")
            section = readme_release_text(relative, read(relative))
            self.assertIsNotNone(section)
            for phrase in self.RELEASE_MODEL[relative]:
                with self.subTest(document=relative, release_model=phrase):
                    self.assertIn(phrase, section[1], f"the Releases section of {relative} must say {phrase!r}")
            for stale in (
                "the website is not deployed",
                "nothing has been released",
                "网站尚未部署",
                "目前没有任何发布",
                "release_approved_sha",
                "release_sha",
                "does not look up the tag",
                "不会查询该标签",
                "--ref v1.1.0",  # v1.1.0's own pages.yml does no Release lookup, so it is no example of one
            ):
                with self.subTest(document=relative, stale=stale):
                    self.assertNotIn(stale, text.lower(), f"{relative} still describes the release hold or old gate")
        self.assertEqual(release_state_problems({relative: read(relative) for relative in READMES}), [])

    def test_a_release_build_names_its_tag(self):
        """Where the checkout is a release tag (the Pages workflow's test run), both READMEs name that release."""
        readmes = {relative: read(relative) for relative in READMES}
        self.assertEqual(tag_problems(readmes, head_release_tags()), [])

    # A README pair in the shape of the real ones, which the release check accepts, and edits it must refuse.
    SAMPLE = {
        "README.md": "# Sample\n\n**English** · [简体中文](README.zh-CN.md)\n\n"
        "**Status:** released as [v1.2.3](https://github.com/FridrichMethod/dynamix/releases/tag/v1.2.3). The "
        "website is published with GitHub Pages at <https://fridrichmethod.github.io/dynamix/>; see "
        "[Releases](#releases).\n\n"
        "Each tag keeps its originals under `https://github.com/FridrichMethod/dynamix/raw/<tag>/`.\n\n"
        "## Releases\n\n"
        "Version `v1.2.3` (2026-01-02) is the latest release. Its "
        "[GitHub Release](https://github.com/FridrichMethod/dynamix/releases/tag/v1.2.3) carries the archive. "
        "Version `v1.0.0` (2025-12-01) was the "
        "[first release](https://github.com/FridrichMethod/dynamix/releases/tag/v1.0.0).\n\n"
        "- The [Pages workflow](.github/workflows/pages.yml) deploys only a published release of a `vX.Y.Z` tag whose "
        "commit is on `main`.\n"
        "- Repository links (`https://github.com/FridrichMethod/dynamix/raw/main/...`) follow `main`; the tag keeps "
        "every file at `https://github.com/FridrichMethod/dynamix/raw/v1.2.3/` followed by its path.\n"
        "- Later pushes to `main` are tested but not deployed.\n\n"
        "## Sources\n",
        "README.zh-CN.md": "# 样例\n\n[English](README.md) · **简体中文**\n\n"
        "**状态**：已发布 [v1.2.3](https://github.com/FridrichMethod/dynamix/releases/tag/v1.2.3)。网站通过 GitHub Pages "
        "发布在 <https://fridrichmethod.github.io/dynamix/>；见[发布](#发布)。\n\n"
        "每个标签都在 `https://github.com/FridrichMethod/dynamix/raw/<tag>/` 下保留其原文件。\n\n"
        "## 发布\n\n"
        "`v1.2.3`（2026-01-02）是最新版本。其 "
        "[GitHub Release](https://github.com/FridrichMethod/dynamix/releases/tag/v1.2.3) 提供完整存档。"
        "`v1.0.0`（2025-12-01）是[首个版本](https://github.com/FridrichMethod/dynamix/releases/tag/v1.0.0)。\n\n"
        "- [Pages 工作流](.github/workflows/pages.yml)只部署 `vX.Y.Z` 标签的已发布版本，且其提交须在 `main` 上。\n"
        "- 仓库链接（`https://github.com/FridrichMethod/dynamix/raw/main/...`）跟随 `main`；该版本的标签保留其每个文件，"
        "地址为 `https://github.com/FridrichMethod/dynamix/raw/v1.2.3/` 加上文件路径。\n"
        "- 之后推送到 `main` 的提交会被测试但不会部署。\n\n"
        "## 来源\n",
    }
    TAG_LINK = "https://github.com/FridrichMethod/dynamix/releases/tag/v1.2.3"
    # Sentences of the former release hold (verbatim from the READMEs before v1.0.0) and variants that dodge exact text.
    HOLD_WORDING = {
        "README.md": (
            "Nothing has been released.",
            "The website is not deployed, no version tag exists and no GitHub Release has been published.",
            "`RELEASE_APPROVED_SHA` is unset, so every dispatch stops at the approval job.",
            "Tags and GitHub Releases are separate decisions that have not been made.",
            "The website is *not* deployed.",
            "The website isn't deployed yet; deployment is held.",
            "[The website](archive.html) is not deployed.",
            "GitHub Pages deployment is held until a later, explicit release decision.",
            "Pages deployment is paused.",
            "The website is currently not deployed.",
            "Because `RELEASE_APPROVED_SHA` is unset, every dispatch fails closed.",
            "`RELEASE_APPROVED_SHA` is not set, so every dispatch fails closed.",
            "The variable is unset if you look at the settings today.",
            "Nothing is deployed.",
            "Pages deployment is temporarily paused.",
            "If a tag is not `vX.Y.Z`, the run fails closed. Nothing is deployed.",
            # A condition in an earlier sentence covers neither the unset variable nor "nothing is deployed".
            "The run fails closed if a tag is not `vX.Y.Z`. The variable is unset.",
            "The run fails closed if a tag is not `vX.Y.Z`; the variable is unset.",
            "Nothing deploys unless the Release is published! The variable is unset.",
            "What happens if a tag is not `vX.Y.Z`? The variable is unset.",
            "If a tag is not `vX.Y.Z`, the run fails closed; nothing is deployed.",
            "If a tag is not `vX.Y.Z`, the run fails closed! Nothing is deployed.",
            "What if a tag is not `vX.Y.Z`? Nothing is deployed.",
        ),
        "README.zh-CN.md": (
            "目前没有任何发布。",
            "网站尚未部署，没有版本标签，也没有发布 GitHub Release。",
            "`RELEASE_APPROVED_SHA` 未设置，因此每次触发都会停在批准任务。",
            "标签和 GitHub Release 是另外的决定，目前尚未做出。",
            "网站还没有部署。",
            "GitHub Pages 部署处于冻结状态。",
            "[网站](archive.html)还未部署。",
            "网站目前未部署。",
            "GitHub Pages 部署已暂停。",
            "当前 `RELEASE_APPROVED_SHA` 仍未设置。",
            "`RELEASE_APPROVED_SHA` 目前未设置。",
            "网站暂不部署。",
            "变量还 未设置。",
            # A condition in an earlier sentence does not cover the unset variable, nor one before a 、 that stands for
            # a sentence mark.
            "只要标签正确就会部署。变量目前未设置。",
            "只要标签正确就会部署；变量目前未设置。",
            "只要标签正确就会部署！变量目前未设置。",
            "要是标签正确会部署吗？变量目前未设置。",
            "只要标签正确就会部署、变量目前未设置。",
        ),
    }
    # Sentences that describe the release gate: what fails closed is a condition of a run, not the site's state.
    GUARD_WORDING = {
        "README.md": (
            "If the release is a prerelease or its tag's commit is not on `main`, the run fails closed.",
            "A dispatch on a branch fails closed, and a push to `main` is tested but not deployed.",
            "A tag that is not `vX.Y.Z` is never deployed.",
            "When a dispatch is on a branch, nothing is deployed.",
            # A full stop also ends the phrase of a held deployment ("deployment of `v01.1.0`").
            "A deployment of `v01.1.0` fails closed. Its run is halted.",
        ),
        "README.zh-CN.md": (
            "如果发布是预发布版本，或其标签的提交不在 `main` 上，运行会失败关闭。",
            "在分支上手动触发会失败关闭；推送到 `main` 的提交会被测试但不会部署。",
            "不是 `vX.Y.Z` 的标签从不部署。",
        ),
    }
    # Sentences of the retired manual-dispatch gate (verbatim from the READMEs at v1.0.0) and variants.
    RETIRED_WORDING = {
        "README.md": (
            "`RELEASE_APPROVED_SHA` names the released commit.",
            "Its `release_sha` input is the full SHA of the dispatched commit.",
            "The Pages workflow dispatched on `main` with the same SHA deploys it.",
            "A later release dispatches the Pages workflow on `main`.",
            "Dispatch it on main.",
            "Dispatching it on `main` deploys the release.",
        ),
        "README.zh-CN.md": (
            "`RELEASE_APPROVED_SHA` 指向已发布的提交。",
            "输入的 `release_sha` 是被触发提交的完整 SHA。",
            "在 `main` 上手动触发 Pages 工作流，填写同一个 SHA。",
            "之后的发布需要在 main 上手动触发 Pages 工作流。",
        ),
    }

    def edited(self, relative: str, old: str, new: str, count: int = -1) -> dict[str, str]:
        self.assertIn(old, self.SAMPLE[relative], "the sample edit must apply")
        return {**self.SAMPLE, relative: self.SAMPLE[relative].replace(old, new, count)}

    # What each README says about repository links in its storage-tier paragraph and in its Releases section.
    ARCHIVE_LINKS = {
        "README.md": ("`archive_url`", "follow `main`", "404", "until the next release"),
        "README.zh-CN.md": ("`archive_url`", "跟随 `main`", "404", "在下一次发布之前"),
    }

    def test_readmes_say_that_repository_links_follow_main(self):
        """archive_url points at raw/main by contract (09-frozen-contracts.md), while the website shows the last
        release: a file withdrawn on main after that release has no repository link there until the next release. The
        storage-tier paragraph and the Releases section of both READMEs say so."""
        for relative, phrases in self.ARCHIVE_LINKS.items():
            text = read(relative)
            tier = next(part for part in text.split("\n\n") if "`archive_url`" in part and "/raw/main/" in part)
            parts = readme_release_text(relative, text)
            self.assertIsNotNone(parts)
            for place, part in (("storage tiers", tier), ("Releases", parts[1])):
                for phrase in phrases:
                    with self.subTest(document=relative, place=place, phrase=phrase):
                        self.assertIn(phrase, part)

    def test_release_check_accepts_the_sample(self):
        self.assertEqual(release_state_problems(self.SAMPLE), [])

    def test_release_check_refuses_mismatched_releases(self):
        en, zh = "README.md", "README.zh-CN.md"
        cases = {
            "zh-only version bump": self.edited(zh, "v1.2.3", "v1.2.4"),
            "en-only version bump": self.edited(en, "v1.2.3", "v1.3.0"),
            "Status link bumped, Releases section not": self.edited(
                en, "[v1.2.3](" + self.TAG_LINK, "[v1.3.0](" + self.TAG_LINK.replace("v1.2.3", "v1.3.0")
            ),
            "links bumped, the section's version not": self.edited(
                zh, self.TAG_LINK, self.TAG_LINK.replace("v1.2.3", "v1.3.0")
            ),
            "release of another repository": self.edited(
                en, self.TAG_LINK, "https://github.com/someone/else/releases/tag/v1.2.3", 1
            ),
            "Pages URL in one language only": self.edited(
                zh, "https://fridrichmethod.github.io/dynamix/", "https://fridrichmethod.github.io/dynamix/archive.html"
            ),
            "no release link": self.edited(en, self.TAG_LINK, "https://github.com/FridrichMethod/dynamix/releases"),
        }
        for case, readmes in cases.items():
            with self.subTest(case=case):
                self.assertNotEqual(release_state_problems(readmes), [])

    def test_release_check_compares_the_dated_releases_of_both_languages(self):
        """Each edit leaves both READMEs acceptable on their own (release_problems), but the two languages no longer
        give the same releases with the same dates in the same order (TEST-1): a date changed in one language, an
        older release left undated in one, or one more release dated in one."""
        en, zh = "README.md", "README.zh-CN.md"
        cases = {
            "the current release's date changed in Chinese only": (zh, "（2026-01-02）", "（2026-01-03）"),
            "an older release's date changed in English only": (en, "(2025-12-01)", "(2025-11-30)"),
            "an older release undated in English only": (en, "`v1.0.0` (2025-12-01)", "`v1.0.0`"),
            "one more release dated in Chinese only": (
                zh,
                "/releases/tag/v1.0.0)。\n",
                "/releases/tag/v1.0.0)。此前的 `v0.9.0`（2025-11-01）只是预览。\n",
            ),
        }
        for case, (relative, old, new) in cases.items():
            with self.subTest(case=case):
                readmes = self.edited(relative, old, new, 1)
                for language, text in readmes.items():
                    self.assertEqual(release_problems(language, text)[1], [], f"{language} alone is acceptable")
                problems = release_state_problems(readmes)
                self.assertEqual([p for p in problems if "date different" in p], problems)
                self.assertEqual(len(problems), 1)

    ANCHORS = {
        "README.md": ("see [Releases](#releases).", "is the latest release."),
        "README.zh-CN.md": ("见[发布](#发布)。", "是最新版本。"),
    }

    def test_release_check_refuses_hold_wording(self):
        for relative, wording in self.HOLD_WORDING.items():
            for sentence in wording:
                for place, anchor in zip(("Status line", "Releases section"), self.ANCHORS[relative], strict=True):
                    with self.subTest(document=relative, place=place, sentence=sentence):
                        readmes = self.edited(relative, anchor, f"{anchor} {sentence}", 1)
                        self.assertNotEqual(release_state_problems(readmes), [])
        # A line break ends a sentence too: a list item without a full stop does not cover the next item.
        item = "- Later pushes to `main` are tested but not deployed."
        lines = "- If a tag is not `vX.Y.Z`, the run fails closed\n- Nothing is deployed."
        self.assertNotEqual(release_state_problems(self.edited("README.md", item, lines)), [])

    def test_release_check_accepts_a_description_of_the_guard(self):
        for relative, wording in self.GUARD_WORDING.items():
            for sentence in wording:
                for place, anchor in zip(("Status line", "Releases section"), self.ANCHORS[relative], strict=True):
                    with self.subTest(document=relative, place=place, sentence=sentence):
                        readmes = self.edited(relative, anchor, f"{anchor} {sentence}", 1)
                        self.assertEqual(release_state_problems(readmes), [])

    def test_release_check_refuses_the_retired_gate(self):
        for relative, wording in self.RETIRED_WORDING.items():
            for sentence in wording:
                for place, anchor in zip(("Status line", "Releases section"), self.ANCHORS[relative], strict=True):
                    with self.subTest(document=relative, place=place, sentence=sentence):
                        readmes = self.edited(relative, anchor, f"{anchor} {sentence}", 1)
                        _, problems = release_problems(relative, readmes[relative])
                        self.assertTrue([p for p in problems if "retired gate" in p], problems)

    def test_release_check_refuses_stale_or_malformed_release_links(self):
        """Each edit breaks one README on its own (release_problems), whatever the other language says."""
        en, zh = "README.md", "README.zh-CN.md"
        link = "https://github.com/FridrichMethod/dynamix/releases/tag/"
        cases = {
            "a newer release linked than the Status line's": (
                en,
                "is the latest release.",
                f"is the latest release. Version `v1.10.0` is [next]({link}v1.10.0).",
            ),
            "the Status line links two releases": (
                zh,
                "；见[发布](#发布)。",
                f"（[v1.0.0]({link}v1.0.0) 之后）；见[发布](#发布)。",
            ),
            "the Status line links no release": (en, f"[v1.2.3]({link}v1.2.3). The", "v1.2.3. The"),
            "the Releases section does not link the current release": (
                zh,
                f"[GitHub Release]({link}v1.2.3) 提供完整存档。",
                "GitHub Release 提供完整存档。",
            ),
            "a release link that is not vX.Y.Z": (en, f"{link}v1.0.0", f"{link}v1.0"),
            "the first release named first": (zh, "`v1.2.3`（2026-01-02）是最新版本。", "最新版本（2026-01-02）。"),
            # The raw links (TEST-1): one of an older release kept after a version bump and one of a ref that is not
            # main, <tag> or a release, each beside the current release's, and a Releases section without the latter.
            "a raw link of an older release": (en, "dynamix/raw/<tag>/", "dynamix/raw/v1.0.0/"),
            "a raw link of a ref that is not main, <tag> or a release": (zh, "dynamix/raw/<tag>/", "dynamix/raw/v1.2/"),
            "no raw link of the current release": (
                en,
                "; the tag keeps every file at `https://github.com/FridrichMethod/dynamix/raw/v1.2.3/` followed by its "
                "path.",
                ".",
            ),
            # The dated releases (TEST-1): the current release undated, a release dated twice, an older release before a
            # newer one, a newer release with an earlier date, and a date that is not on the calendar.
            "the current release undated": (zh, "`v1.2.3`（2026-01-02）是最新版本。", "`v1.2.3` 是最新版本。"),
            "a release dated twice": (zh, "是最新版本。", "是最新版本，`v1.2.3`（2026-01-02）也是。"),
            "an older release named before a newer one": (
                en,
                "Version `v1.0.0` (2025-12-01) was the ",
                "Version `v1.0.0` (2025-12-01) came before `v1.1.0` (2025-11-15) and was the ",
            ),
            "a newer release dated before an older one": (en, "`v1.0.0` (2025-12-01)", "`v1.0.0` (2026-02-01)"),
            "a date that is not on the calendar": (zh, "`v1.0.0`（2025-12-01）", "`v1.0.0`（2025-02-30）"),
        }
        for case, (relative, old, new) in cases.items():
            with self.subTest(case=case):
                text = self.edited(relative, old, new, 1)[relative]
                self.assertNotEqual(release_problems(relative, text)[1], [], text)
                self.assertNotEqual(release_state_problems({**self.SAMPLE, relative: text}), [])

    def test_a_release_build_must_name_the_newest_tag_of_its_commit(self):
        self.assertEqual(tag_problems(self.SAMPLE, []), [], "an untagged checkout names any release")
        self.assertEqual(tag_problems(self.SAMPLE, ["v1.2.3"]), [])
        self.assertEqual(tag_problems(self.SAMPLE, ["v1.0.0", "v1.2.3"]), [])
        self.assertNotEqual(tag_problems(self.SAMPLE, ["v1.2.4"]), [])
        self.assertNotEqual(tag_problems(self.SAMPLE, ["v1.2.3", "v1.10.0"]), [], "numeric, not lexical, order")

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_head_release_tags_reads_the_checkout(self):
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()

            def git(*arguments: str) -> None:
                subprocess.run(["git", *arguments], cwd=root, env=environment, check=True, capture_output=True)

            git("init", "-q")
            git("commit", "-q", "--allow-empty", "-m", "first")
            git("tag", "v1.0.0")
            git("commit", "-q", "--allow-empty", "-m", "second")
            self.assertEqual(head_release_tags(root), [])
            for tag in ("v1.10.0", "v1.2.3", "v1.2.3-rc1", "latest"):
                git("tag", "-a", "-m", tag, tag)
            self.assertEqual(head_release_tags(root), ["v1.2.3", "v1.10.0"])
            empty = Path(temporary) / "empty"
            empty.mkdir()
            with unittest.mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": temporary}):
                self.assertEqual(head_release_tags(empty), [])


if __name__ == "__main__":
    unittest.main()
