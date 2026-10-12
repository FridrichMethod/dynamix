# Validation report

Coordinator's record for the implementation requested on 2026-10-10. It describes the reviewed content commit **`effc4be6c6fad521d1cd4fcc77fbf2b5980812fe`** (`impl/integration`, later fast-forwarded into `main`). Code and data were reviewed in full at `db88679`; `effc4be` adds only the CI fix for the test fixtures and the public DEEMO reference (see [CI on the first push](#ci-on-the-first-push)). This report is committed after that commit and changes only `docs/implementation/`; no code, data or generated output. The push, the CI run on the final `main` SHA, and the worktree cleanup are recorded in the coordinator's final response, not here, so the release-ready checkout stays clean (see [06](06-validation-and-release.md#gate-7--cleanup-and-final-evidence)).

**State: READY_FOR_RELEASE once the push and CI listed under [Remaining release-ready steps](#remaining-release-ready-steps) succeed. Release hold in force: Pages not deployed, no approval SHA, no tags, no GitHub Release.**

**Update 2026-10-11:** the push and CI succeeded, and on the owner's explicit instruction the hold was lifted: release `v1.0.0` was deployed to GitHub Pages and published as a GitHub Release. The README's Releases section describes it. This report otherwise records the state before that release; the content of releases `v1.1.0` and `v1.1.1` is recorded under [Release v1.1.0](#release-v110-2026-10-11) and [Release v1.1.1](#release-v111-2026-10-12).

## Identity

| Item | Value |
| --- | --- |
| Repository | `FridrichMethod/dynamix`, to be created by this task after this report is committed; before creation, `gh api repos/FridrichMethod/dynamix` answered 404 |
| Reviewed content SHA | `effc4be6c6fad521d1cd4fcc77fbf2b5980812fe` (code and data unchanged since `db88679`) |
| Base | `828da4a` (planning packet), unchanged. Everything since descends from it through ancestry-preserving `--no-ff` merges, and no commit was rewritten. |
| Reference | DEEMO `b14bf0d5f5a05e5382c319f3fd5019a4936820b8`, read-only (only `git show`/reads; its acquisition scripts were never run) |
| Acquisition / source checks | 2026-10-10 to 2026-10-11 (UTC). Manifest times: wikis `2026-10-11T04:27:27Z`, artists `2026-10-10T21:00:40Z`, official `2026-10-11T00:14:38Z`, archives `2026-10-11T04:50:56Z` |
| Toolchain | Python 3.14.8, Pillow 12.3.0 with libwebp 1.6.0 (preview encoder pin), Playwright 1.62.0, Google Chrome 155.0.8059.39 |
| Scope | Original Dynamix (C4Cat) only; Universe-only files excluded and counted |

## Integration

One writer per worktree; the coordinator alone merged, generated outputs and touched the remote. Merges into `impl/integration`, in order (first-parent):

| Merge | Branch | Content |
| --- | --- | --- |
| `ba7b555` | `impl/contracts` | schema v1 library, contract fixture |
| `3e422d2` | `impl/ui` | static gallery and slideshow interface |
| `fb2e191` | `impl/catalog` | catalog, preview and coverage builders |
| `8b2d511` | `impl/ci` | tests, packaging, release-hold workflows |
| `f747660` | `impl/catalog` | primary-record rule, title check, staged-site estimate |
| `518391f` | `impl/ui` | short-screen slideshow, zh-CN punctuation |
| `59362c9` | `impl/primary-sources` | artists, official channels, archives |
| `6552a79` | `impl/docs` | English/Chinese docs, statistics renderer |
| `cf5a0d4` | `impl/wikis` | Miraheze and Fandom inventories, Japanese lists, song index |
| `1ea4344` | `impl/final-fixes` | final-review fixes |
| `aa43a66` | `impl/final-fixes` | delta-review follow-ups |
| `48be578` | `impl/ci-zlib` | fixture PNGs independent of the zlib build; public DEEMO reference |
| `effc4be` | `impl/ci-zlib` | palette-colour key test |

Coordinator commits on the integration branch record contract decisions in [09](09-frozen-contracts.md), adopt `data/sources/song-index.json` unchanged from the wikis proposal (`5c6899b`), and regenerate the outputs (`7707050`, then `db88679`). All nine task branches are ancestors of the reviewed SHA.

## Gate 3 — exact-head checks

Run on the clean integration checkout (offline) at `db88679`, and repeated in full at `48be578` with identical results. `effc4be` changes only `tests/test_fixtures_contract.py` and a docstring; all 33 test files pass there under both zlib builds.

| Command | Result |
| --- | --- |
| `fetch_wikis.py --verify` | rc 0; 6,701 records, 3,364 unique SHA-256, 2,226,855,913 B; 1 recorded failure |
| `fetch_artists.py --verify`, `fetch_official.py --verify`, `fetch_archives.py --verify` | rc 0 each, 0 errors, 0 warnings |
| `build_thumbnails.py --verify` (re-encodes every preview) and `--check` | rc 0, rc 0 |
| `build_catalog.py --verify --check` | rc 0 |
| `build_coverage.py --check`, `update_docs_stats.py --check` | rc 0, rc 0 |
| `git diff --check`; whitespace over the tracked tree (binary assets and the verbatim vendor MIT notices excluded) | rc 0, rc 0 |
| every `tests/test_*.py` as `python -I -B tests/<file>` | 33 files, 0 failing |

The verbatim `licenses/pace-MIT.txt` ends with a blank line. It is byte-identical to DEEMO's copy at the reference commit (SHA-256 `7415a0a1…`, pinned by `tests/test_layout.py`), so it is excluded from the whitespace check instead of edited.

## Browser evidence

`tests/browser_smoke.py` drove Chrome 155.0.8059.39 through Playwright. A run fails on any console error, HTTP error, off-origin request, dialog or skipped check.

| Mode | Served | Result |
| --- | --- | --- |
| `--fixture` | UI fixture sites at an HTTP root, under `/dynamix/`, `/empty/`, `/many/`, `/hostile/` and `/edges/`, and from `file://` | 49 passed, 0 failed, 0 skipped, 0 monitor problems (again at `48be578`) |
| `--base` checkout | the clean checkout at `db88679`, loopback HTTP root, plus `file://` (no public file changed afterwards) | 22 passed, 0 failed, 0 skipped, 0 monitor problems |
| `--base` staged | the staged Pages artifact under `/dynamix/` | 22 passed, 0 failed, 0 skipped, 0 monitor problems |

First-screen grid images on the real catalog at 1440×900 total 616,150 B. The `--base` checks, as named in the run log:
- filters, search, sorts and URL state; Back/Forward restoring filters and the viewer;
- the viewer, keyboard, focus, deep links and download targets; grid previews and the bounded fallback;
- inventory statuses and failure counts; catalog strings rendered as text;
- the language toggle, `?lang=` and localStorage;
- slideshow deep links, controls and names; the screenshot overlay; short screens keeping notes and caption clear of the controls; screenshots that cut no notes text silently and keep the page's line breaks inside words; Tab order;
- the notice without JavaScript; English and Chinese layouts at desktop and phone sizes; the missing-catalog message; the manifest and icons;
- the archive and slideshow opened from `file://`.

## Pages artifact (prepared, not deployed)

`scripts/prepare_pages.py --output <new dir>` on the clean checkout at `db88679`, and again at `48be578` with the identical manifest:

| Item | Value |
| --- | --- |
| Files / bytes | 4,849 files, 930,395,703 B (budget 950,000,000 B; 19,604,297 B remaining) |
| Tiers | 2,403 site-tier originals (801,936,070 B), 2,398 previews (57,060,406 B), 48 page files (71,399,227 B) |
| Not staged | 1,370 archive-tier originals (542,108,765 B), linked to the repository through `archive_url` |
| `omitted_over_budget` | empty (`data/catalog.json` is staged) |
| `matches_commit` | true; no staged file differs from its blob |
| Manifest | `sha256sum -c` passes over every staged file; `manifest_sha256` `f9ba0bb6fbdc92c831992d2f5d7c532877e901cfc3fac8679c6e155d54895581` (the identical payload staged at `7707050` had `2eab5b48…`; only the research-leads time in the catalog differs) |

## Independent reviews

Each implementation wave was reviewed by agents that did not write the work, and its findings were fixed and verified before the merge. The final round reviewed the integrated candidate `3cc3786` through six lenses: acquisition code; build, packaging and UI code; data and provenance; browser; release hold and security; docs.

| Stage | Outcome |
| --- | --- |
| Final reviews of `3cc3786` | 0 blocking, 1 major, about 27 minor |
| The major | `fetch_wikis` kept requesting a file host after a 401/403 or challenge. Fixed: the remaining files of that host are recorded `blocked` without a request. |
| Fix round (`impl/final-fixes`, 19 commits) | 27 fixed, 2 partly fixed, 1 left to the owner |
| Independent verification of the fixes (`313c8cf`) | 0 blocking or major; 7 minor. Each original finding was reproduced on the base and checked at the head, and mutation tests confirmed the new tests. |
| Coordinator and second fix round | the 7 minors resolved or documented below |
| Independent delta review (`313c8cf..7707050`) | 0 blocking or major. It confirmed the verifier's findings 1–4 and 6 resolved, 5 (NanoKun field and quote) correctly left open, and 7 unchanged. It reproduced every generated output byte for byte from a clean archive. It staged the site independently, with the same digest. Field-level data diff: no sha256, size, path or dimension changed. 2 minor findings, 1 low: a lead registry item still `awaiting_download`; two adapter holds without tests; the 09 rule wording. |
| CI fix review (`impl/ci-zlib`) | 0 blocking or major. It reproduced the CI failure on `7412ef4` with the stock zlib and showed both tests pass at the fix under both builds. Mutations of the generator call sites and of every part of the key are caught; the minor findings (untested key parts, the docstring scope, undocumented reuse limits) were fixed and rechecked. |
| Follow-ups and recheck (`7707050..db88679`) | the Xetta lead moved to `unresolved_role`; tests for the X and App Store holds (mutation-checked); 09 reworded. The recheck found F1–F3 resolved and nothing new. The 12 mutation cases are all killed, and `tests/run_all.py` passes 33 modules (831 tests). |

Highlights of what the final round fixed:
- empty MediaWiki answers and empty Wayback CDX answers no longer count as a complete enumeration;
- primary runs exit 1 when candidates stay failed or blocked, always hold the family lock, and space every host across sources, redirect hops included;
- one kind per SHA-256 is checked, and song IDs come from the song index;
- the RAVON/Stellights partner art has no C4Cat rights holder;
- the wiki Code_1–4 files are classified as gameplay screenshots;
- credit evidence carries the cited `Template:Song` field and quote (148 of 150 entries);
- only the three frozen pending prefixes are used, and joint credits are kept as written;
- screenshot text wrapping and the 375×812 first screen are fixed;
- the docs were corrected in both languages, and the archive page links its own `data/catalog.json` again.

## Measured collection

All counts come from `data/coverage.json` and `data/catalog.json` at `db88679` (unchanged at `effc4be`). Definitions are in [docs/coverage.md](../coverage.md).

| Measure | Value |
| --- | --- |
| Sources | 104: 65 available, 5 partially available, 1 temporarily unavailable, 4 manual access, 15 metadata only, 14 excluded |
| Source snapshots | 93 complete enumerations, 11 incomplete, 0 not enumerated |
| Candidates | 10,183: 7,138 downloaded, 33 duplicate bytes, 2,880 excluded, 7 failed, 35 blocked, 90 pending |
| Largest exclusions | person photos 885, video placeholders 572, Universe-only 387, game UI 291, animated captures 288 |
| Source asset records | 7,138 (wikis 6,701, artists 76, official 243, archives 118) |
| Unique files | 3,773 by SHA-256 (1,344,044,835 B): PNG 2,404, JPEG 1,367, GIF 1, PDF 1; 3,365 exact-duplicate records |
| Kinds | song art 632, pack cover 14, character 712, background 566, part 318, promotional 160, store screenshot 58, gameplay screenshot 223, process 1, reference 1,089 |
| Gallery / slideshow | 2,684 gallery images, 715 slides |
| Variants | 71 variant groups (92 entries) |
| Transformed records | 101; source-reported mismatches 1,419 |
| Songs | song index 633; original-game denominator 537. Confirmed song art for 531 (98.9%), inferred only for 6, missing for 0. |
| Song art quality | confirmed artist or official original: 18 songs; best long edge ≥ 1920 px: 54; low resolution only (< 1920 px): 477 |
| Credits | unknown illustrator for 256 songs and 2,055 catalog entries |
| Unmapped | 386 entries unmapped, 370 inferred only, 1 conflict |

No image was upscaled, cropped, recompressed or re-encoded. Originals keep their downloaded bytes, and previews live separately under `assets/thumbs/`.

## Coverage gaps and access limits

- **Incomplete enumerations (11):**
  - `artists:artstation:sandgodoven` and `artists:bilibili:ariiol`: manual access. Anonymous reads got a challenge page (HTTP 403) and Bilibili risk control (code -352); refusals are recorded, never bypassed.
  - `artists:pixiv:3315403`: NanoKun's deleted Dynamix posts; each of 5 work IDs answers HTTP 404, and no archived capture of the originals was found.
  - `official:facebook`, `official:facebook-c4cats`, `official:soundcloud-c4cat`, `official:x`, `official:youtube`: partly available or metadata only.
  - `official:website`: temporarily unavailable; `dynamix.c4-cat.com` did not resolve on 2026-10-10. Its historical captures are archived through `archives:wayback`.
  - `wikis:atwiki-ja` and `wikis:rhythmgame-ja`: song-list cross-checks only; they hold no files.
  - None of these is reported as complete.
- **Recorded failures (10):**
  - the HKSTP appendix PDF (HTTP 404);
  - one ArtStation project (challenge, HTTP 403);
  - one bilibili opus (risk control);
  - one pixiv original (`artists:pixiv:2624455`, access refused with HTTP 403; not bypassed);
  - the five deleted pixiv works above (HTTP 404);
  - one Fandom file (`Limstella.png`, HTTP 404).
  - Blocked and pending candidates (35 and 90) are listed per source in the archive's source inventory.
- **Inferred-only songs (6):** `file-30604f03`, `funk-time`, `moonlight-of-sand-castle`, `set-free-kiv-edition-6cec5627`, `spooky-macaron`, `the-dystopia-s-tomorrow`.
  - For Moonlight of Sand Castle, Template:Song names an `Imgsuffix` file that neither wiki lists. The embedded jacket therefore carries only the composer credit.
  - For the same reason, two primary credit entries for its illustrator NanoKun (`artists:soundcloud:ebico:479b7216a438330a`, `archives:press:146dd98c90a32da1`) keep field and quote unset. No committed record states the template field verbatim.
- **Resolution:** most song art is the wikis' copy at below 1920 px (477 of 537 songs). Artist or official originals were found for 18 songs only. No master-resolution claim is made where the source does not support it.
- **Leads not fetched:**
  - the multi-gigabyte Fandom image dump on archive.org is held as `unresolved_role`: one archive of many files;
  - the Xetta piano grayscale cover is held as `unresolved_role`: a registry lead whose file URL is known but whose track page was never recorded. No primary candidate uses `awaiting_download`.
- **Completeness:** no universal-completeness claim is made. Empty or error answers keep an enumeration open; they never close it.

## Known limitations

- **Withdrawn bytes stay in Git history.** History holds 47 image blobs, 33,182,689 B in total, that no current record presents:
  - a wiki upscale, first in `a418229`;
  - four Miraheze eyecatches that are the cover art of a separate release, first in `6beba72`;
  - 33 App Store JPEG re-encodes replaced by Apple's PNG renditions, first in `21a8d7a`;
  - eight illustrations from lalaluhat's Wix and X sources, withdrawn while they await review, first in `765b5aa`;
  - one official X interface strip, first in `74bf4e4`.
  - History is not rewritten. Both READMEs disclose this.
- **Local reference path in history.** The base commit `828da4a` named the DEEMO reference by an absolute path on the maintainer's machine (`AGENTS.md` and `docs/implementation/02`, `04`, `05`). The tip names the public repository `https://github.com/FridrichMethod/deemo` at the same pinned commit instead; the base commit keeps the old wording because history is not rewritten.
- **Large data files.** `data/catalog.json` is 40.2 MB and `data/catalog.js` 29.3 MB; the pages load the script only. If a later payload exceeds the budget, `prepare_pages.py` leaves `catalog.json` out first and records it. The archive page's manifest link then has no target on the site, but the repository copy remains.
- **Live sources were not rechecked at the reviewed head.** The offline `--verify` checks and the per-source snapshots are the evidence. `scripts/check_sources.py --report` is the manual, read-only way to compare live metadata later.
- **Third-party artwork.** It is not covered by the code license. Attribution and source-specific notes are in `NOTICE` and the catalog.

## CI on the first push

The first push of `main` (`7412ef4`) ran `tests.yml` as [run 38119962148](https://github.com/FridrichMethod/dynamix/actions/runs/38119962148). The browser smoke job and every offline data check passed: catalog, previews, coverage, documentation statistics, manifests, whitespace, and an unchanged checkout. Two unit tests failed: `test_fixtures_contract.test_generator_reproduces_committed_bytes` and `test_layout_fixture.test_generator_reproduces_the_committed_fixture`.

Cause: Pillow's `_imaging` links `libz.so.1` dynamically and compresses PNG data with whichever zlib the process has loaded. The fixtures had been generated under Homebrew's Python, which loads zlib-ng; the runner's Python uses stock zlib 1.3. The same pixels therefore produced different PNG bytes. Preloading the system zlib reproduced the failure locally.

Fix (`impl/ci-zlib`, merged as `48be578` and `effc4be`): both fixture generators keep the committed bytes of a PNG that decodes to exactly the drawn image (`tests/fixtures/pinned_png.py`), and encode only a changed drawing. No committed fixture byte changed, and no real-data output is affected; previews are WebP, and data files hold no zlib output. All 33 test files pass under both zlib builds, and regenerating both fixtures in place under the stock zlib leaves the tree unchanged.

## Release hold

*Historical record of the v1.0.0 implementation. On 2026-10-11 the owner replaced the approval-variable gate described here with release-triggered deployment ([06, Gate 5](06-validation-and-release.md#gate-5--release-hold-design)); `RELEASE_APPROVED_SHA` is no longer used.*

- `tests.yml` runs on push, pull request and reusable call with read-only contents permission. It tests and uploads an ordinary CI artifact of the staged site; it never deploys, tags or publishes.
- `pages.yml` is manual dispatch only. Its deploy job requires the dispatch to target `main`, `inputs.release_sha == github.sha`, and `vars.RELEASE_APPROVED_SHA == github.sha`. The variable is unset, so the job fails closed. The release review's evaluator applies GitHub's expression rules and runs the real shell guard. Across 14 scenarios, only the approved dispatch on `main` deploys. Rejected: push, pull request, a feature branch, a tag ref, a `MAIN`-cased branch, a wrong, short or uppercase `release_sha`, an unset variable, a variable naming another SHA, an uppercase variable, and whitespace-padded values.
- `check-sources.yml` is manual dispatch only, read-only, and has no schedule.
- No workflow has a schedule, tag, release, `git push` or `gh` call, and every action is pinned to a full SHA. This task configures no Pages site, sets no repository variable, and creates no tag or GitHub Release.

Future release actions, **not performed** and awaiting an explicit release instruction:
1. Configure Pages for GitHub Actions in the repository settings.
2. Set `RELEASE_APPROVED_SHA` to the reviewed `main` SHA.
3. Dispatch `pages.yml` on `main` with the same SHA.
4. Optionally tag and publish release notes.

## Remaining release-ready steps

These steps are recorded in the final response, not in this file:
- fast-forward `main` to the commit containing this report;
- create the public repository and push `main`;
- confirm that the local and remote `main` SHAs match and that CI passes on that SHA;
- confirm the hold state (Pages API 404, no variables, tags or releases);
- remove this task's clean, merged worktrees and branches.

## Code-quality tooling and independent review (2026-10-11)

A later task added code-quality tooling and an independent, adversarially verified review. This section describes its reviewed content commit **`7babef7ab0b0cb0be2f851242ab84fa7cbd463ff`** (`quality/integration`, base `93d1d13` = release v1.0.0). The push, CI run and cleanup receipts are kept in the coordinator's final response, not here. The release state is unchanged: no Pages dispatch, no change to `RELEASE_APPROVED_SHA`, no tag and no GitHub Release; a push to `main` still only runs the tests workflow.

**Tooling** (contract in [09](09-frozen-contracts.md#tooling-contract-2026-10-11-code-quality-task)):

| Check | Baseline at `93d1d13` | At the content commit |
| --- | --- | --- |
| ruff lint, stage-1 rules `E4 E7 E9 F W I B UP PLE` | 106 findings (112 after formatting) | 0 |
| ruff format, line length 120 | 84 files to reformat | formatted; the mechanical commit `cedea23` is in `.git-blame-ignore-revs` |
| mypy stage 1 (`scripts/lib`, non-strict, Python 3.10) | 15 errors | 0 |
| ESLint (check only) on `src/*.js`, `src/*.css` | 1 error (a CSS parse error) | 0 |
| actionlint with shellcheck on the workflows | 0 | 0 |
| Branch coverage of `scripts/` by the unit tests, every `-I` subprocess measured | 90.5 % | 92.4 % (`fail_under` 92) |

The formatting commit changes no AST under Python 3.14. The final review found that the `py314` target had let ruff write Python 3.14-only `except A, B:` clauses and pyupgrade use `datetime.UTC`, so the scripts no longer imported on Python 3.13 or older; both were reverted (`3e12dc8`), ruff and mypy now check against Python 3.10, and the full suite and every `--verify`/`--check` pass on Python 3.10, 3.11, 3.12, 3.13 and 3.14.

**Review.** Seven independent read-only reviewers covered `scripts/wikis/{classify,wikitext,reviews,variants}.py`, `scripts/lib/schema.py`, the platform adapters in `scripts/primary/adapters/`, the release commit `93d1d13` with `tests/test_docs*.py`, and a lens sweep with mutation probes of `lib/net.py`, `lib/paths.py`, `lib/manifest.py`, `wikis/acquire.py`, `wikis/workdir.py`, `primary/runner.py` and `src/archive.js`; a final reviewer read the whole integrated change set. Every finding was checked by a separate verifier told to refute it, and only confirmed findings were acted on: 51 reported, 10 confirmed major, 37 confirmed minor, 2 plausible, 2 refuted; none blocking. Every confirmed major finding is fixed, each with a regression test that a separate re-verifier saw fail without the fix and pass with it. Among them: the DNS-rebinding check on `https://` connections (`lib/net.py`), pixel identity over every frame with EXIF orientation (`primary/runner.py`), Universe-only and promotional-review classification (`wikis/classify.py`), Wayback and unread-list candidates that are no longer dropped while a list is incomplete, a failed page read no longer turning direct seeds into pending, link keys with spaces, the Bilibili account check and silently dropped TapTap entries. Minor findings were fixed where the fix was small and safe; the rest are open and listed in the coordinator's report.

**Exact-head checks at the content commit**, with Pillow 12.3.0 / libwebp 1.6.0, under both zlib-ng and the stock zlib that CI uses: `tests/run_all.py` (34 modules, also under coverage), the four `fetch_*.py --verify`, `build_catalog.py --verify --check`, `build_thumbnails.py --check` and `--verify` (all 2,398 previews re-encoded byte-identically), `build_coverage.py --check`, `update_docs_stats.py --check`, both `git diff --check` forms, `pre-commit run --all-files`, `browser_smoke.py --fixture` and `--base` on the served checkout and the staged site under `/dynamix/`. Nothing under `data/`, `assets/`, `index.html`, `tests/fixtures/`, `src/vendor/` or `licenses/` changed. The staged Pages payload (`manifest_sha256` `36ea2d1da5c2a56bc553e1388967ab9088a8eae317d6af729aceec8bc6fa7a74`, 4,849 files, 930,407,965 bytes) differs from the v1.0.0 payload only in `README.md`, `README.zh-CN.md`, `docs/sources-wikis.md`, `docs/sources-wikis.zh-CN.md` (development and caption documentation) and `src/slideshow.css` (an invalid declaration that browsers already dropped was removed; rendering is unchanged). Those files reach the website only with a later approved release.

## Release v1.1.0 (2026-10-11)

This section records the content of release `v1.1.0`: the reviewed content commit **`b647f0818fe1f2373aaa645c63b9ffa53ba6e772`** (`quality2/integration`). The commit that adds this section changes only this file. The section above describes `7babef7`; its statements about the release state and the open minor findings give the state at that commit.

**After `7babef7`.** Four more fix rounds followed. Each fix branch was re-verified by a separate agent before it was merged, and two final reviews read the whole change set; none left a blocking or major finding open.

- **Deployment.** Release-triggered deployment replaced the approval-variable gate (owner decision in [06, Gate 5](06-validation-and-release.md#gate-5--release-hold-design)). `pages.yml` runs only on a published release or a manual dispatch. Its approval and build jobs refuse, in the C locale, a ref that is not a `vX.Y.Z` tag, a release that is a draft or a prerelease, and a tag whose commit is not on `main`; the full Tests workflow then runs on the tag's commit before the artifact is built. `RELEASE_APPROVED_SHA` is no longer read. A test keeps the guard steps and jobs free of keys that could neutralise them.
- **Owner decisions** (2026-10-11; the first two are recorded in [09](09-frozen-contracts.md)). A 401 or 403 of a fetcher-derived URL with page-listed fallbacks falls back like a 404 (DX3-02). An "Artwork in/for X" caption records another game only for the seven reviewed `OTHER_GAMES` entries (RAVON, Arcaea, Lanota, Cytus, Cytus II, Phigros, Rotaeno); every other name, Dynamix's own events included, records no association and carries a curator note (a BMS release event such as PABAT still marks an external release). The owner also chose to remove the always-empty `mismatches.duplicate_bytes_other_source` list from `data/coverage.json` (`668cbc5`), since the schema refuses the case it would list (DX2-01); the documentation statistics were regenerated.
- **Other fixes.** A `page_links` hold persists across runs while a linking page stays unread; an unreadable page keeps an earlier rejected-submission exclusion; Bilibili counts Cloudflare markers only in a markup answer; catalog credit evidence must agree with its role lists.

**Exact-head checks at the content commit**, under both zlib-ng and the stock zlib that CI uses: `tests/run_all.py` (34 of 34 modules, also under coverage: 92.5 % branch coverage of `scripts/`, `fail_under` 92), the four `fetch_*.py --verify`, `build_catalog.py --verify --check`, `build_thumbnails.py --check`, `build_coverage.py --check`, `update_docs_stats.py --check`, both `git diff --check` forms, and `browser_smoke.py --fixture` and `--base` on the served checkout and on the staged site under `/dynamix/`. The checkout stayed clean. The Tests workflow passed on the same commit, with its lint (`pre-commit run --all-files`), offline-check, browser and site-artifact jobs. Nothing under `assets/`, `index.html`, `tests/fixtures/`, `src/vendor/` or `licenses/` changed, and under `data/` only `data/coverage.json`. The staged Pages payload (`manifest_sha256` `2f017c45b77675acb845f29a48cb5fa196517bdb5b8ba29f9fd287ab383d7aca`, 4,849 files, 930,411,606 bytes) differs from the v1.0.0 payload only in `data/coverage.json`, `docs/coverage.md`, `docs/coverage.zh-CN.md`, `docs/sources-wikis.md`, `docs/sources-wikis.zh-CN.md`, `README.md`, `README.zh-CN.md` and `src/slideshow.css`.

**Open minor items**, none of which affects committed data:

- A manual dispatch on a `vX.Y.Z` tag on `main` deploys it without looking up the tag's GitHub Release; the READMEs say to use a dispatch only to redeploy a released tag. Enforcing it would need a Releases API call in the workflow.
- The schema does not yet require non-empty `game_evidence` on accepted candidates, apply the credit-role rule to family-manifest records, or require a URL or quote on every credit entry (DX2-07), because the primary runners still build such records in their tests. Committed data has no violation.
- A comma after the bare name ends a caption name, so "Artwork in Dynamix, Lanota Collaboration" records no Lanota, while the dash form does. No committed caption has either form.
- Two adapter cases need live access to settle. The Google Play adapter skips gallery images without the screenshot marker silently (the Google Play part of DX4-02), and only a live page shows whether that can drop a real screenshot. The seed `artists:bilibili:ariiol` has no `params.mid`; its v1.0.0 read was blocked by a challenge, and pictures read from it are held until the account ID is recorded.

## Release v1.1.1 (2026-10-12)

This section records the content of release `v1.1.1`: the reviewed content commit **`60894f87172e7390a2e7dd9a82ba3be16b880671`** (`quality6/final`). The commit that adds this section changes only this file. The section above describes `b647f08`; its open minor items give the state at release `v1.1.0`.

**After `v1.1.0`.** Three fix rounds closed the open minor items that need no live access. Each fix branch was re-verified by a separate agent before it was merged, and a final review read the whole change set; its findings, all minor, were fixed in the last round. A pre-release code review of the whole change set (security, correctness, and quality with tests and docs, each finding verified by a separate agent) confirmed no critical or high finding. Its one medium finding, an overstatement in this section, is fixed, and its low findings are left for `v1.1.2`. No blocking or major finding is open.

- **Deployment** (owner decisions in [06, Gate 5](06-validation-and-release.md#gate-5--release-hold-design)). Every run, a release run, a re-run of one and a dispatch alike, looks the tag's GitHub Release up with one read-only request in the approval and build jobs and goes on only for a published Release that is not a prerelease. A `vX.Y.Z` tag has three numbers without leading zeros. The deploy job compares the prerelease flag through `toJSON()` and runs only for the commit that the approval job recorded. `tests/test_workflows.py` allows exactly this one request. The repository ruleset "Release tags v*" lets only repository admins create, move or delete `v*` tags, and the `github-pages` environment accepts deployments only from tags from `v1.1.1` on, no longer from `main` (owner decision of 2026-10-12, in Gate 5).
- **Schema** (DX2-07, recorded in [09](09-frozen-contracts.md)). Accepted candidates carry game evidence, seeds of the identity adapters need `identity_quotes`, the credit-role rule covers family-manifest records, and every catalog credit entry has a URL or a quote. Committed data has no violation.
- **Captions.** After the bare game name and a comma, an occasion of a listed game continues the name as after a dash (coordinator decision in [09](09-frozen-contracts.md)). No committed caption changes classification.
- **Other fixes.** `page_links` compares "Also linked from" notes whole, and the alternatives of the documentation wording rules in `tests/test_docs.py` that the fix-round verifiers named are pinned by samples.

**Exact-head checks at the content commit**, under both zlib-ng and the stock zlib that CI uses: `tests/run_all.py` (34 of 34 modules, also under coverage: 92.5 % branch coverage of `scripts/`, `fail_under` 92), the four `fetch_*.py --verify`, `build_catalog.py --verify --check`, `build_thumbnails.py --check`, `build_coverage.py --check`, `update_docs_stats.py --check`, both `git diff --check` forms, and `browser_smoke.py --fixture` and `--base` on the served checkout and on the staged site under `/dynamix/`. The checkout stayed clean. Nothing under `data/`, `assets/`, `index.html`, `tests/fixtures/`, `src/vendor/` or `licenses/` changed. The staged Pages payload (`manifest_sha256` `9e284ae37cd02efe862f1f1df01c17e7a1c16e6d06df388d613f534827b4c635`, 4,849 files, 930,414,285 bytes) differs from the `v1.1.0` payload only in `README.md`, `README.zh-CN.md`, `docs/sources-wikis.md` and `docs/sources-wikis.zh-CN.md`.

**Open minor items**, none of which affects committed data:

- The `v1.1.0` items on the dispatch Release check, on DX2-07 and on the comma form are resolved.
- The low findings of the pre-release review are left for `v1.1.2`. A re-run of the deploy job alone repeats no Release lookup; the documentation says to re-run the whole run. The `pages.yml` header and the docstring of `tests/test_workflows.py` say more than the tests check about the lookup requests. Some alternatives of the wording rules in `tests/test_docs.py` have no sample. Several new test functions are long, and `tests/test_workflows.py` has grown past 1,800 lines.
- Two adapter cases still need live access to settle. The Google Play adapter skips gallery images without the screenshot marker silently (the Google Play part of DX4-02), and only a live page shows whether that can drop a real screenshot. The seed `artists:bilibili:ariiol` has no `params.mid`; its v1.0.0 read was blocked by a challenge, and pictures read from it are held until the account ID is recorded.
