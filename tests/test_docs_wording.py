"""Release wording of the public documentation: the rules that tell a statement about the site's release state from a
description of how the release guard works, and their samples. A document other than the READMEs makes no deployment
or publication claim, has no hold wording and tells no deployment or release as an event, and its other sentences
about deployment or release are negated, conditional, future or restricted (release_wording_problems()); the release
text of the READMEs has no hold wording either (hold_wording()). tests/test_docs_release.py applies both to the
documents.

The rendering helpers (prose_lines(), sentences(), plain()) and the release rules that tests/test_docs_stats.py also
applies to the generated blocks (FLAT_CLAIMS, RELEASE_WORDS, GUARDS) come from tests/test_docs.py. Offline: it reads no
file.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_docs import (  # noqa: E402 - the rendering helpers and the shared rules (no TestCase)
    _DONE,
    FLAT_CLAIMS,
    GUARDS,
    RELEASE_WORDS,
    plain,
    prose_lines,
    sentences,
)

# Wording of the release hold that ended with v1.0.0, in either language, matched in rendered text (see plain()).
# A website or Pages that is not deployed counts, also with words between ("网站目前未部署") or for now ("temporarily",
# 暂不), and so does a release or deployment that is held, paused or suspended; "later pushes are tested but not
# deployed" (true after a release) does not.
HOLD_MARKERS = re.compile(
    r"\b(?:website|site|pages)\b[^.;:]{0,60}?\b(?:is|was|are|were|has|have)(?: still| currently| now| temporarily)?"
    r"(?: not|n['’]t)(?: yet| currently)?(?: been)? (?:deployed|published|released|live)\b|"
    # A release that is held: with a to/of/on phrase only through a copula or "on hold" ("each release of the frozen
    # catalog" is not held), and the phrase may name a version ("deployment of v1.1.0 is paused").
    r"\b(?:release|deployment|publication)s?\b(?: (?:to|of|on) (?:[^.;:,]|\.(?=\S)){0,30}?)?"
    r"(?: (?:is|are|stays?|remains?|was|were|has been|have been)(?: still| currently| now| temporarily)?"
    r" (?:on )?(?:hold|held|paused|suspended|frozen|halted)| on hold)\b|"
    r"\b(?:release|deployment|publication)s? (?:hold|held|paused|suspended|frozen|halted)\b|"
    r"\bno version tag\b|\bno GitHub Release\b|\bnothing has been released\b|"
    r"\bha(?:s|ve) not been (?:made|published|released|created)\b|"
    r"(?:尚未|还没有|还未)(?:被)?(?:部署|发布|上线|做出|设置)|"
    r"(?:网站|站点)[^。；，：、,;:]{0,8}?(?:未|没有)(?:被)?(?:部署|发布|上线)|"
    r"(?:部署|发布|上线)[^。；，：、,;:]{0,6}?暂停|暂停[^。；，：、,;:]{0,4}?(?:部署|发布|上线)|"
    r"暂(?:时)?(?:不|未|没有)(?:被)?(?:部署|发布|上线)|"
    r"没有版本标签|没有任何发布|冻结",
    re.IGNORECASE,
)
# The release variable stated as unset, also with an adverb or as "not set" ("is still unset", "isn't set yet", 目前未设置).
# It is hold wording as a statement ("`RELEASE_APPROVED_SHA` is unset, so ..."), not as the condition of the guard ("If
# the variable is unset, the dispatch fails closed", "变量未设置时……"). 还未设置 and 还没有设置 are hold wording in any
# clause (HOLD_MARKERS); written with a space ("变量还 未设置") only this rule sees them.
UNSET_WORDING = re.compile(
    r"\b(?:RELEASE_APPROVED_SHA|variable)\b[^.;]{0,30}?\b(?:is|stays|remains|was)(?: still| currently| now)?"
    r"(?: unset|(?: not|n['’]t)(?: yet| currently)? set)\b|"
    r"(?:RELEASE_APPROVED_SHA|变量)\s*(?:目前|当前|现在|仍然|依然|仍|还|尚)?\s*(?:未|没有)(?:被)?设置",
    re.IGNORECASE,
)
# A condition opening the clause (当 but not 当前 "currently"; 若 but not 若干; "whether" of a check), or, in Chinese, one
# right after the words (时, 的话, 的情况下). An English "if" or "when" after a statement opens a clause of its own and
# leaves the statement as it is ("The variable is unset if you look at the settings today.").
CONDITION_BEFORE = re.compile(
    r"\b(?:if|when|whenever|while|unless|whether|as long as|in case)\b|"
    r"如果|假如|倘若|要是|一旦|只要|若(?!干)|当(?![前然中天日时])",
    re.IGNORECASE,
)
# A condition after a list joined with 、 covers the wording when the wording ends its item and a ， follows the
# condition ("变量未设置、标签错误时，……", not "变量未设置、查看设置时会发现" or "变量未设置会被发现、查看设置时").
_CONDITION_WORD = r"(?:(?<![暂及同当])时(?![间刻])|的话|情况下)"
CONDITION_AFTER = re.compile(rf"[^。；，：、,;:.!?]*?{_CONDITION_WORD}|、[^。；，：,;:.!?]+?{_CONDITION_WORD}(?=，)")
# ASCII punctuation ends a clause before whitespace only, so a version (v1.1.0) or a file name stays in its clause. 、
# joins the items of a list, not clauses ("若标签错误、变量未设置，……" is one condition): a condition before the list
# covers the wording only when the wording opens its item and a ， ends the list (clause_before()), and a date clause
# may list dates (release_events()). A 、 written for a ， between a condition and a statement reads as such a list too
# ("如果查看设置、变量未设置，这是事实。", "变量未设置、查看设置时，会发现。"): it has the punctuation and the condition
# words of a list of conditions, and only what its items mean tells the two apart, as in the English "If you look at
# the settings and the variable is unset, this is a fact.", which this check has always allowed.
CLAUSE_BREAK = re.compile(r"[,;:.!?](?=\s|$)|[，；：。！？]")
# A clause that continues the one before it ("If X is wrong, or the variable is unset, ..."): a condition that opens
# the first clause covers it (CONDITION_OPENS). A condition inside an earlier clause covers that clause alone ("The tag
# v1.1.0 exists if you check, and the variable is unset."). After a 、 the word may stand alone before the wording
# (clause_before(): "倘若标签不对、或者变量未设置，……"), so 或者 is matched whole.
COORDINATED = re.compile(r"\s*(?:(?:or|and)\b|或者|或|并且|且)", re.IGNORECASE)
CONDITION_OPENS = re.compile(rf"\s*(?:{CONDITION_BEFORE.pattern})", re.IGNORECASE)
# "Nothing is deployed" states the site's state unless a condition of the guard comes before it in its sentence
# ("When ..., nothing is deployed."): one whose own clause names what the guard checks, a tag, branch, release,
# prerelease, draft, version, commit, main, an event (push, pull request, schedule), the approval, a refusal
# or a failure (GUARD_CONDITION), not "When you look today, ...". A trailing condition does not count, as for the unset
# wording.
NOTHING_DEPLOYED = re.compile(rf"\b(?:nothing|none) (?:is|are) {_DONE}", re.IGNORECASE)
GUARD_CONDITION = re.compile(
    r"\b(?:(?:tag|branch|release|prerelease|draft|version|commit|push|pull|schedul|approv|refus|fail)\w*|main)\b",
    re.IGNORECASE,
)
# The sentence of "nothing is deployed" (English only) ends at . ! ? ; before whitespace or at a line break.
SENTENCE_BREAK = re.compile(r"[.!?;](?=\s)|\n")
# A deployment or release told as an event that happened, which is what happened, not how the guard works, whatever
# guard word the sentence also has ("... happened on 2026-10-11 when the owner approved it"); see release_events().
# A month name counts in any case ("october 11"): no sentence puts a day number right after "may" or "march". Digits,
# not word characters, bound an ISO date, so one glued to Chinese text ("于2026-10-11部署") or to a time
# (2026-10-11T12:00Z) counts too.
RELEASE_DATES = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)|"
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}\b|"
    r"\d{4} ?年|\d{1,2} ?月 ?\d{1,2} ?日",
    re.IGNORECASE,
)
# A clause that is only a date ("On 2026-10-11, ...", "2026-10-11，……"): it dates the rest of the sentence. Whitespace
# may end it: sentences() blanks a code span after the date to a space ("On 2026-10-11 `UTC`, ..."), and a space may
# stand before a full-width comma. "In" is not English before a day, but a translation of 在 may put it there.
BARE_DATE = re.compile(rf"\s*(?:(?:on|in|at|by|since|于|在)\s*)?(?:{RELEASE_DATES.pattern})\s*", re.IGNORECASE)
RELEASE_EVENT_MARKERS = re.compile(r"(?:部署|上线|发布)(?:了|过)|已于")
RELEASE_EVENT_VERBS = re.compile(
    r"\b(?:happened|took place|occurred|ran|succeeded|completed|finished|went (?:out|ahead|through)|"
    r"(?:was|were) (?:approved|triggered|started|performed|carried out|rolled out))\b",
    re.IGNORECASE,
)
# Words that open a subordinate clause: an event verb after one of them in its clause is a condition or a description
# ("runs only after the tests succeeded", "a release that was approved"), not the event the sentence tells.
SUBORDINATORS = re.compile(
    r"\b(?:after|before|once|until|till|when|whenever|while|if|unless|since|whether|that|which|who)\b", re.IGNORECASE
)


def clause_before(text: str, end: int) -> str:
    """The clause of ``text`` that ends at ``end``, joined with the clauses before it that it continues with "or" or
    "and" (COORDINATED) when a condition opens the first of them (CONDITION_OPENS), so that the condition counts for
    it; otherwise the clause alone. Where the clause that ends at ``end`` lists items with 、, the items before count
    only when ``end`` opens an item (or follows its 或, 且, ...: COORDINATED) of a list that a ， ends, its consequence
    following ("若标签错误、变量未设置，……"); otherwise the item alone does, as where 、 stands for a comma
    ("如果查看设置、会发现变量未设置。", "只要标签正确就会部署、变量目前未设置。"). A 、 in an earlier clause lists items of
    that clause and leaves the joined clauses as they are ("若标签错误、分支错误，或变量未设置，……")."""
    parts = CLAUSE_BREAK.split(text[:end])
    clause = continued = parts.pop()
    while parts and COORDINATED.match(continued):
        continued = parts.pop() + continued
    found = continued if CONDITION_OPENS.match(continued) else clause
    if "、" not in clause:
        return found
    item = clause.rpartition("、")[2]
    opens = not item.strip() or COORDINATED.fullmatch(item)
    after = CLAUSE_BREAK.search(text, end)
    return found if opens and after is not None and after.group() == "，" else item


def hold_wording(text: str) -> list[str]:
    """Hold wording in rendered text: every HOLD_MARKERS match; the release variable stated as unset unless a condition
    opens its clause (or the clause it continues) or, in Chinese, follows it; and "nothing is deployed" unless a
    condition of the guard comes before it in its sentence. With the condition, the text describes the guard, not the
    site's state."""
    found = [match.group() for match in HOLD_MARKERS.finditer(text)]
    for match in UNSET_WORDING.finditer(text):
        clause = clause_before(text, match.start()) + match.group()
        if not (CONDITION_BEFORE.search(clause) or CONDITION_AFTER.match(text, match.end())):
            found.append(match.group())
    for match in NOTHING_DEPLOYED.finditer(text):
        before = SENTENCE_BREAK.split(text[: match.start()])[-1]
        if not any(
            GUARD_CONDITION.search(clause_at(before, c.start(), c.end())) for c in CONDITION_BEFORE.finditer(before)
        ):
            found.append(match.group())
    return found


def clause_at(text: str, start: int, end: int) -> str:
    """The clause of ``text`` that holds ``text[start:end]``."""
    before = [match.end() for match in CLAUSE_BREAK.finditer(text) if match.end() <= start]
    after = CLAUSE_BREAK.search(text, end)
    return text[before[-1] if before else 0 : after.start() if after else len(text)]


def release_events(sentence: str) -> list[str]:
    """What tells a deployment or release in ``sentence`` as an event that happened: a date in a clause that names it
    (RELEASE_WORDS) or in a clause of its own (BARE_DATE, also an item listed with 、), so that a date of something else
    ("Sources were checked on 2026-10-10, and nothing here deploys") does not count; a Chinese completed-event marker
    (部署了, 已于); or a past-tense event verb that no subordinator introduces in its clause."""
    found = []
    for match in RELEASE_DATES.finditer(sentence):
        clause = clause_at(sentence, match.start(), match.end())
        if RELEASE_WORDS.search(clause) or any(BARE_DATE.fullmatch(item) for item in clause.split("、")):
            found.append(match.group())
    found += [match.group() for match in RELEASE_EVENT_MARKERS.finditer(sentence)]
    for match in RELEASE_EVENT_VERBS.finditer(sentence):
        if not SUBORDINATORS.search(CLAUSE_BREAK.split(sentence[: match.start()])[-1]):
            found.append(match.group())
    return found


def release_wording_problems(text: str) -> list[str]:
    """Statements about the site's release state in a document other than the READMEs, which alone describe it: a flat
    deployment or publication claim anywhere, hold wording in any sentence, a sentence about deployment or release that
    tells it as an event (release_events(): a date, a past-tense event verb) and one that is not negated, conditional,
    future or restricted (how the guard works, not what happened). Claims, hold wording and events are matched in the
    raw and the rendered text (see plain()), so that emphasis or a link around a date hides no event."""
    claims = {match.group() for source in (text, plain(text)) for match in FLAT_CLAIMS.finditer(source)}
    problems = [f"flat claim: {claim!r}" for claim in sorted(claims)]
    for sentence in sentences(prose_lines(text), keep_code=True):
        problems.extend(f"hold wording {found!r}: {sentence[:80]!r}" for found in hold_wording(plain(sentence)))
    for sentence in sentences(prose_lines(text)):
        if not RELEASE_WORDS.search(sentence):
            continue
        if release_events(sentence) or release_events(plain(sentence)):
            problems.append(f"release told as an event: {sentence[:80]!r}")
        elif not GUARDS.search(sentence):
            problems.append(f"unguarded release statement: {sentence[:80]!r}")
    return problems


class ReleaseWordingTests(unittest.TestCase):
    """The release wording rule for documents other than the READMEs (release_wording_problems()) on samples of what it
    refuses and what it allows."""

    # Statements about the site's release state that a document other than the READMEs must not make, and statements
    # about deployment or release that it may make (how the guard works, or unrelated uses of the words). Samples that
    # name RELEASE_APPROVED_SHA test this wording rule alone; test_no_document_names_the_retired_gate
    # (tests/test_docs_release.py) refuses them.
    OUTSIDE_REFUSED = (
        "GitHub Pages deployment is held until a later release decision, so the website is not deployed.",
        "GitHub Pages 部署处于冻结状态，网站尚未部署。",
        "The website is published on GitHub Pages and the v1.0.0 release is out.",
        "网站已发布在 GitHub Pages 上，v1.0.0 已发布。",
        "网站在 2026-10-11 部署到 GitHub Pages。原文件不会被修改。",
        "The website was put on GitHub Pages by a deployment on 2026-10-11. Originals are not modified.",
        "`RELEASE_APPROVED_SHA` is unset, so nothing is released.",
        "The website is *not* deployed yet.",
        # The variable stated as unset, not as a condition of the guard.
        "Because `RELEASE_APPROVED_SHA` is unset, every dispatch fails closed.",
        "The variable remains unset; if it is set, the dispatch deploys.",
        "当前 `RELEASE_APPROVED_SHA` 仍未设置，如果设置了才会部署。",
        "`RELEASE_APPROVED_SHA` 未设置，因此每次触发都会停在批准任务。",
        # A website that is not deployed with other words between, and deployment that is paused or suspended.
        "网站目前未部署。",
        "站点目前仍未部署到 GitHub Pages。",
        "Pages deployment is paused.",
        "Deployment to GitHub Pages is currently suspended.",
        "The website is currently not deployed.",
        "GitHub Pages 部署已暂停。",
        # A deployment told as something that happened, which a guard word ("when", 才) does not turn into a rule.
        "The deployment to GitHub Pages happened on 2026-10-11 when the owner approved it.",
        "The deployment ran when the owner approved the release.",
        "When the owner approved the release, the deployment ran.",
        "网站在 2026-10-11 所有者批准后才部署到 GitHub Pages。",
        # The variable stated as unset with an adverb or as not set, and a website withheld for now.
        "`RELEASE_APPROVED_SHA` is currently unset.",
        "`RELEASE_APPROVED_SHA` is still unset.",
        "`RELEASE_APPROVED_SHA` is not set, so every dispatch fails closed.",
        "The variable isn't set yet.",
        "`RELEASE_APPROVED_SHA` 目前未设置。",
        "网站暂不部署。",
        "The website is temporarily not deployed.",
        # Each other adverb, tense and negation of the unset wording, in either language.
        "The variable is now unset.",
        "`RELEASE_APPROVED_SHA` was unset, so every dispatch stopped at the approval job.",
        "The variable is not yet set.",
        "`RELEASE_APPROVED_SHA` is not currently set.",
        "`RELEASE_APPROVED_SHA` 目前没有设置。",
        "`RELEASE_APPROVED_SHA` 未被设置。",
        "`RELEASE_APPROVED_SHA` 当前未设置。",
        "变量现在未设置。",
        "变量仍然未设置。",
        "变量依然未设置。",
        "变量目前 未设置。",
        # 还 with a space before 未 or 没有, which HOLD_MARKERS (还未, 还没有) does not see.
        "变量还 未设置。",
        "变量还 没有设置。",
        "`RELEASE_APPROVED_SHA` 还 未设置。",
        # A plain statement with an unrelated condition after it, and past deployments with an adverb or another verb.
        "The variable is unset if you look at the settings today.",
        "`RELEASE_APPROVED_SHA` is unset when you look at the settings.",
        "The website was first deployed to GitHub Pages when v1.0.0 was tagged.",
        "The deployment was approved when v1.0.0 was tagged.",
        "The website is already live, so nothing else is needed.",
        # "Nothing is deployed" as the state of the site, also with a condition after it or one that is not the guard's.
        "Nothing is deployed.",
        "Nothing is deployed if you look today.",
        "Nothing has been deployed.",
        "When you look today, nothing is deployed.",
        "For the tag v1.1.0, when you look today, nothing is deployed.",
        "When you look today, the tag v1.1.0 exists, so nothing is deployed.",  # a guard word outside the condition
        # A condition inside an earlier clause joined with "and" does not cover the unset variable.
        "The tag v1.1.0 exists if you check, and the variable is unset.",
        # Known false positives: accurate serial lists of conditions whose last item is the unset variable, with and
        # without a comma before "or". Only a clause that opens with "or" or "and" (COORDINATED) continues the one
        # before it, so the condition of the first item does not reach past the middle one. Write "if" before the last
        # item ("..., or if the variable is unset, ...") or give it a sentence of its own. A reading of such lists also
        # let asides and hedges in the last item pass ("or rather ...", "the variable (it seems) is unset").
        "If the tag is wrong, the branch is wrong, or the variable is unset, the run fails closed.",
        "If the tag is wrong, the branch is wrong or the variable is unset, the run fails closed.",
        # Variants of the accurate sentences below that still tell an event: a fronted date, a version in the clause, a
        # comma before the event verb; and held releases whose phrase names a version, also without a copula.
        "2026-10-11，所有者批准后才部署到 GitHub Pages。",
        "On 2026-10-11, the owner approved the deployment when the tests passed.",
        "The deployment of v1.1.0 ran when the owner approved it.",
        "The deployment, as planned, happened on 2026-10-11 when the owner approved it.",
        "The release of v1.1.0 is on hold.",
        "Deployment of v1.1.0 is paused.",
        "Release of v1.1.0 on hold.",
        # One sample per alternative of the rules, refused for that alternative alone, so that deleting the alternative
        # lets it pass. Flat claims: each adverb, verb form, subject and preposition, and a claim in a code span.
        "The website is now live.",
        *(
            f"The website was {adverb} deployed, and the next release will replace it."
            for adverb in ("successfully", "finally", "recently", "just", "last")
        ),
        "The pages are live.",
        "The pages were deployed, and the next release will replace them.",
        "The pages have been deployed, and the next release will replace them.",
        "The website went live.",
        "The live site shows the catalog.",
        "The live website shows the catalog.",
        "The GitHub Pages site at the project URL shows the catalog.",
        *(f"The website is published {word} GitHub Pages." for word in ("with", "to", "at", "by")),
        "The catalog is released as v1.1.0, and the next release will follow.",
        "See https://fridrichmethod.github.io/dynamix/ for the catalog.",
        "See [the catalog](https://fridrichmethod.github.io/dynamix/).",  # a claim that only the raw text shows
        "The website is `live`.",
        # Link syntax hides no claim or hold wording (plain()): an image such as a badge, brackets inside the link
        # text (also empty ones), an empty link text and an empty destination.
        "The website is ![live](badge.svg).",
        "[The website [EN]](archive.html) is not deployed.",
        "[The website []](archive.html) is not deployed.",
        "The website[](archive.html) is not deployed.",
        "The website is [live]().",
        *(f"网站{words}，之后的推送不会部署。" for words in ("已部署", "已经部署", "已上线", "已经上线")),
        "网站已发布。",
        "网站已经发布。",
        "网站目前已发布。",
        "站点已发布。",
        "GitHub Pages 已发布。",
        "v1.1.0 已发布。",
        # Unguarded statements, one word of RELEASE_WORDS each.
        "A release deploys the website.",
        "The tagged commit gets deployed.",
        "Deploying the website takes a few minutes.",
        "Deployments use the tag's commit.",
        "Maintainers deploy the website from a tag.",
        "The archive is released with each tag.",
        "GitHub Releases carry the archive.",
        "Version tags name the commits.",
        "Each version tag names a commit.",
        "网站随标签上线。",
        "版本标签指向提交。",
        # Hold wording: each subject, copula, adverb, negation and participle of a website that is not deployed, and of
        # a held release, deployment or publication (with a to/on phrase, without a copula, or as a noun).
        "The site is not deployed.",
        "The pages are not deployed.",
        "The website was not deployed.",
        "The pages were not deployed.",
        "The website has not been deployed.",
        "The pages have not been deployed.",
        "The website is still not deployed.",
        "The site is now not live.",
        "The website isn't deployed.",
        "The website isn’t deployed until the next release.",
        "The website is _not_ deployed until the next release.",
        "The website is not yet deployed.",
        "The website is not currently deployed.",
        "The website is not published.",
        "The website is not released.",
        *(
            f"Publication {verb} paused."
            for verb in ("is", "stays", "remains", "was", "has been", "is still", "is currently", "is now")
        ),
        *(f"Releases {verb} paused." for verb in ("are", "stay", "remain", "were", "have been")),
        *(f"Publication is {word}." for word in ("on hold", "suspended", "frozen", "halted")),
        "Publication to GitHub Pages is paused.",
        "Publication on GitHub Pages is paused.",
        *(f"Publication {word}." for word in ("paused", "held", "suspended", "frozen", "halted")),
        "Release paused.",
        "Releases paused.",
        "Deployment paused until the owner decides.",
        "The release hold stays in place.",
        "There is no version tag.",
        "There is no GitHub Release.",
        "A Release has not been published.",
        "The catalog has not been released.",
        "The tag has not been created.",
        "None is deployed.",
        "None are deployed.",
        # Each Chinese form of hold wording.
        *(f"这个版本{words}。" for words in ("还没有部署", "还未部署", "尚未被部署", "尚未发布", "尚未上线")),
        "所有者批准的提交尚未设置，因此每次触发都会停在批准任务。",
        *(f"网站{words}。" for words in ("未部署", "没有部署", "未被部署", "未发布", "未上线")),
        "发布已暂停。",
        *(f"{words}，以后会恢复。" for words in ("网站上线已暂停", "部署暂停", "暂停部署", "暂停网站部署", "暂停上线")),
        "暂停发布。",
        "网站暂时不部署。",
        *(f"该版本{words}。" for words in ("暂未部署", "暂时没有部署", "暂未被部署", "暂不发布", "暂不上线")),
        "目前没有版本标签。",
        # The unset variable with "stays", a typographic apostrophe or 尚没有, and words that are no condition before it
        # (若干, 当然, 当中, 当天, 当日, 当时) or after it (暂时, 及时, 同时, 当时, 时间, 时刻).
        "The variable stays unset.",
        "The variable isn’t set yet.",
        "变量尚没有设置。",
        "若干变量目前未设置。",
        *(f"{word}变量未设置。" for word in ("当然", "这当中", "当天", "当日", "当时")),
        "变量未设置导致触发暂时停止。",
        "变量未设置会被及时发现。",
        "变量未设置同时触发失败关闭。",
        "变量未设置是当时的状态。",
        "变量未设置的时间很长。",
        "变量未设置的时刻已经记录在案。",
        # A condition before a clause break does not cover the unset variable after it, nor one before a 、 whose item
        # the wording does not open; 时 after a 、 closes a list only when the wording ends its item and a ， follows 时.
        "如果查看设置，会发现变量未设置。",
        "若查看设置可见：变量未设置。",
        "Check the settings if you are unsure: the variable is unset.",
        "如果查看设置、会发现变量未设置。",
        "如果查看设置、会发现变量未设置，因此每次触发都会停在批准任务。",
        "变量未设置、查看设置时会发现。",
        "变量未设置会被发现、查看设置时。",
        # The wording opens no item when other words follow the 或 of its item; and only a ， after the wording ends the
        # list, not a ， before the list nor an ASCII comma.
        "如果查看设置、或会发现变量未设置，因此每次触发都会停在批准任务。",
        "如果标签不对，或分支不对、变量未设置。",
        "若标签错误、变量未设置, 触发会失败关闭。",
        # Events: each date form, a date as a clause of its own (at the end, after each preposition, with a space or a
        # code span after it), each Chinese marker and each event verb.
        *(
            f"The deployment on {month} 11 needed only the owner's approval."
            for month in "January February March April May June July August September October November December".split()
        ),
        "The deployment on october 11 needed only the owner's approval.",
        "网站于2026-10-11部署到 GitHub Pages，只需要所有者批准。",
        "The deployment needed only the owner's approval at 2026-10-11T12:00Z.",
        "部署在 2026 年只需要所有者批准。",
        "部署在2026年只需要所有者批准。",
        "部署在 10 月 11 日只需要所有者批准。",
        "部署在10月11日只需要所有者批准。",
        "The deployment needed only the owner's approval, on 2026-10-11.",
        "By 2026-10-11, the deployment needed only the owner's approval.",
        "Since 2026-10-11, the deployment needs only the owner's approval.",
        "于10月11日，部署只需要所有者批准。",
        "在 2026-10-11，部署只需要所有者批准。",
        "At 2026-10-11 `12:00 UTC`, the deployment needed only the owner's approval.",
        "In 2026-10-11, the deployment needed only the owner's approval.",
        "On 2026-10-11 `UTC`, the deployment needed only the owner's approval.",
        "2026-10-11 ，部署只需要所有者批准。",
        "于 10 月 11 日 ，部署只需要所有者批准。",
        "2026-10-11、2026-10-12，部署只需要所有者批准。",  # dates listed with 、 as the clause
        # Any item listed with 、 that is a date dates the sentence, also after another item or before one.
        "来源检查、2026-10-11，部署只需要所有者批准。",
        "2026-10-11、2026-10-12 两天，部署只需要所有者批准。",
        "On **2026-10-11**, the deployment needed only the owner's approval.",  # emphasis hides no date
        # The raw sentence counts too: a date in a link destination dates the deployment it names.
        "The deployment needed only the owner's approval ([run log](logs/2026-10-11.txt)).",
        # 、 joins the items of one clause, so the date dates the deployment it names (a ， would end the clause).
        "来源于 2026-10-10 检查、未部署任何内容。",
        "网站在所有者批准后才部署了。",
        "网站在所有者批准后才上线了。",
        "GitHub Release 在所有者批准后才发布了。",
        "网站只在所有者批准后才部署过。",
        "网站已于上周部署，之后的推送不会部署。",
        *(f"The deployment {verb} only after the tests passed." for verb in ("took place", "occurred", "succeeded")),
        *(f"The deployment {verb} only after the tests passed." for verb in ("completed", "finished", "went out")),
        *(f"The deployment went {word} only after the tests passed." for word in ("ahead", "through")),
        *(f"The deployment was {verb} only after the tests passed." for verb in ("triggered", "started", "performed")),
        *(f"The deployment was {verb} only after the tests passed." for verb in ("carried out", "rolled out")),
        "The deployments were triggered only after the tests passed.",
        # The prose between two code spans of two backticks is prose (INLINE_CODE): an event and an unguarded statement
        # there; and a run of backticks that no run of the same length closes prints as text.
        "``v1.1.0`` was released on 2026-10-11 from ``main``.",
        "``v1.1.0`` is the current release, and the site deploys from ``main``.",
        "网站由 ``main`` 部署于 2026-10-11，来自 ``v1.1.0``。",
        "The ```deploy`` job publishes the site.",
        # A guard word covers its own sentence only, whichever mark or line break ends it.
        *(f"A push never deploys{mark} The deployment uses the tag." for mark in ".!"),
        "A push never deploys; the deployment uses the tag.",
        "Will a push deploy? The deployment uses the tag.",
        "- A push never deploys\n- The deployment uses the tag",
        *(f"推送不会部署{mark}部署使用标签的提交。" for mark in "。！；"),
        "推送会部署吗？部署使用标签的提交。",
    )
    OUTSIDE_ALLOWED = (
        "A push to `main` never deploys the website.",
        "A push to`main`never deploys the website.",  # a code span stands between the words around it
        "推送到 `main` 不会部署网站。",
        # How the guard works: the unset variable is a condition, not the state of the site.
        "If the variable is unset or names any other commit, the dispatch fails closed at the approval job.",
        "If `RELEASE_APPROVED_SHA` is unset or names another commit, the dispatch fails closed.",
        "A dispatch fails closed when `RELEASE_APPROVED_SHA` is unset.",
        "The variable, if it is unset, makes every dispatch fail closed.",
        "如果 `RELEASE_APPROVED_SHA` 未设置或指向其他提交，触发会在批准任务处失败关闭。",
        "`RELEASE_APPROVED_SHA` 未设置时，触发不会部署任何内容。",
        "A deployment runs only when the approval job passes.",
        "| `official:c4cat` | [C4Cat](https://c4-cat.com/) | 读取一次页面 | 即将上线/联系方式的占位页面，没有游戏目录；仅作身份证据。 |",
        "Catalog entries held by two or more sources of this family: 0.",
        "| `upstream_status` | Records that are removed or unset upstream. |",
        "| `upstream_status` | 上游状态为 `removed` 或未设置的记录。 |",
        "A composer's release cover is not the illustrator's original.",
        "If `RELEASE_APPROVED_SHA` is not set, the dispatch fails closed.",
        "变量目前未设置时，触发会失败关闭。",
        # "Nothing is deployed" as the outcome of a condition of the guard, which names what the guard checks.
        "When the variable is unset or names another commit, nothing is deployed.",
        "If a tag is not `vX.Y.Z`, nothing is deployed.",
        "Unless the Release is published, nothing is deployed.",
        "If it is a prerelease, nothing is deployed.",
        "When it is not on `main`, nothing is deployed.",
        "When a guard refuses the run, nothing is deployed.",
        "If the lookup fails, nothing is deployed.",
        "If it is a draft, nothing is deployed.",
        "When a push starts the run, nothing is deployed.",
        "If a pull request starts the run, nothing is deployed.",
        "When a schedule starts the run, nothing is deployed.",
        "If the version has a leading zero, nothing is deployed.",
        "When the approval job does not pass, nothing is deployed.",
        "When you look today, if the tag is not on `main`, nothing is deployed.",  # a later condition of the guard
        # Accurate sentences: an event verb in a subordinate clause, a date of something else, an unrelated
        # "frozen", and the unset variable as a condition (的情况下, whether, a clause joined by "or", a version inside
        # the clause).
        "The deploy job runs only after the tests succeeded.",
        "Sources were checked on 2026-10-10, and nothing here deploys the website.",
        "No deployment depends on 12026-10-11 or 2026-10-111, which are numbers, not dates.",
        "Each release of the frozen catalog carries the archive.",
        "`RELEASE_APPROVED_SHA` 未设置的情况下，触发会失败关闭。",
        "The approval job checks whether `RELEASE_APPROVED_SHA` is unset or names another commit.",
        "If `release_sha` is wrong, or `RELEASE_APPROVED_SHA` is unset, the dispatch fails closed.",
        "If the tag v1.1.0 names another commit or the variable is unset, the dispatch fails closed.",
        "For a dispatch, if `release_sha` is wrong, or the variable is unset, the dispatch fails closed.",
        # One sample per alternative of the rules, allowed only through that alternative. Each guard word alone in its
        # sentence.
        "A deployment requires a published release.",
        "Deployments require a published release.",
        "The deploy job does not run for a prerelease.",
        "A second Pages run is held while the first one deploys.",  # the github-pages concurrency group
        "The concurrency group can hold a second Pages run while the first one deploys.",
        "A prerelease gets no deployment.",
        "None of the pull requests deploys the website.",
        "The website stays as it is until the next deployment.",
        "A release run deploys unless its Release is a prerelease.",
        "A run on an older tag would use that tag's own workflow, and the environment refuses its deployment.",
        "The next release will deploy the website again.",
        "A pull request cannot deploy the website.",
        "Future releases deploy the same way.",
        "Later releases deploy the same way.",
        "Every release is tested before it deploys.",
        "The deploy job runs if the approval and build jobs pass.",
        "The deploy job runs when the approval and build jobs pass.",
        "A pull request doesn't deploy the website.",
        "The last job of the Pages workflow is ``deploy``.",  # a code span of two backticks is code too
        "The last job of the Pages workflow is ```deploy```.",  # and one of three
        "The last step runs ``echo `date` > deployed.txt``.",  # a span of two backticks holds single ones
        "拉取请求不部署网站。",
        "预发布版本未部署到 GitHub Pages。",
        "只有已发布的版本部署网站。",
        "网站只能从版本标签部署。",
        "测试通过之前，部署任务一直等待。",
        "将来的版本同样部署到 GitHub Pages。",
        "以后的版本同样部署到 GitHub Pages。",
        "如果 Release 已发布且标签的提交在 `main` 上，运行就部署网站。",
        "测试通过后才部署网站。",
        "部署须有已发布的 Release。",
        "部署需要已发布的 Release。",
        "手动触发的运行也部署标签的提交。",
        "届时网站随新版本部署。",
        "下一个版本会部署网站。",
        # "None is deployed" after a condition of the guard, each condition word, a condition that opens a clause which
        # "and", 或, 并且 or 且 continues, and conditions listed with 、, before or after them.
        "If a tag is not `vX.Y.Z`, none is deployed.",
        "Whenever a tag is not `vX.Y.Z`, nothing is deployed.",
        "While the Release is a draft, nothing is deployed.",
        "As long as the Release is a prerelease, nothing is deployed.",
        "In case of a refusal, nothing is deployed.",
        *(f"{word}变量未设置，触发会失败关闭。" for word in ("若", "假如", "要是", "一旦", "只要", "每当")),
        "变量未设置的话，触发会失败关闭。",
        "If the tag is wrong, and the variable is unset, the dispatch fails closed.",
        "倘若标签不对，或变量未设置，触发会失败关闭。",
        *(f"如果标签不对，{word}变量未设置，触发会失败关闭。" for word in ("并且", "且")),
        "若标签错误、变量未设置或指向其他提交，触发会失败关闭。",
        "若标签错误、分支错误、变量未设置，触发会失败关闭。",  # the wording opens the last of three items
        "倘若标签不对、或者变量未设置，触发会失败关闭。",
        "变量未设置、标签错误时，触发会失败关闭。",
        # A 、 in an earlier one of the joined clauses lists items of that clause, so the condition still opens them.
        "若标签错误、分支错误，或变量未设置，触发会失败关闭。",
        "若标签错误、分支错误，或者变量未设置，触发会失败关闭。",
        "如果标签不对、分支不对，并且变量未设置，触发会失败关闭。",
        "若标签错误、分支错误，或者发布用的变量未设置，触发会失败关闭。",
        # An event verb after each subordinator.
        "The deploy job never runs before the test job has succeeded.",
        *(f"The deploy job runs only {word} the test job has succeeded." for word in ("once", "when", "if")),
        *(f"The deploy job does not run {word} the test job has succeeded." for word in ("until", "till", "unless")),
        "The deploy job is skipped whenever the test job has not succeeded.",
        "No deployment starts while the previous one has not completed.",
        "The deploy job will wait for the build job, since the approval job succeeded.",
        "The deploy job depends on whether the test job succeeded and never deploys otherwise.",
        "Only a run that was started on a `vX.Y.Z` tag deploys.",
        "The deploy job deploys only the commit which was approved by the approval job.",
        "Someone who ran a dispatch on a branch gets no deployment.",
    )

    def test_release_wording_rule_outside_the_readmes(self):
        for text in self.OUTSIDE_REFUSED:
            with self.subTest(refused=text):
                self.assertNotEqual(release_wording_problems(text + "\n"), [])
        for text in self.OUTSIDE_ALLOWED:
            with self.subTest(allowed=text):
                self.assertEqual(release_wording_problems(text + "\n"), [])


if __name__ == "__main__":
    unittest.main()
