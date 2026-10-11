# Validation report

Coordinator's record for the implementation requested on 2026-10-10. It describes the reviewed content commit **`db8867935d6700644fbd227cb7d94cef8f41df7c`** (`impl/integration`, later fast-forwarded into `main`). This report is committed after that commit and changes only `docs/implementation/`; no code, data or generated output. The push, the CI run on the final `main` SHA, and the worktree cleanup are recorded in the coordinator's final response, not here, so the release-ready checkout stays clean (see [06](06-validation-and-release.md#gate-7--cleanup-and-final-evidence)).

**State: READY_FOR_RELEASE once the push and CI listed under [Remaining release-ready steps](#remaining-release-ready-steps) succeed. Release hold in force: Pages not deployed, no approval SHA, no tags, no GitHub Release.**

## Identity

| Item | Value |
| --- | --- |
| Repository | `FridrichMethod/dynamix`, to be created by this task after this report is committed; before creation, `gh api repos/FridrichMethod/dynamix` answered 404 |
| Reviewed content SHA | `db8867935d6700644fbd227cb7d94cef8f41df7c` |
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

Coordinator commits on the integration branch record contract decisions in [09](09-frozen-contracts.md), adopt `data/sources/song-index.json` unchanged from the wikis proposal (`5c6899b`), and regenerate the outputs (`7707050`, then `db88679`). All eight task branches are ancestors of the reviewed SHA.

## Gate 3 — exact-head checks at `db88679`

Run on the clean integration checkout (offline):

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
| `--fixture` | UI fixture sites at an HTTP root, under `/dynamix/`, `/empty/`, `/many/`, `/hostile/` and `/edges/`, and from `file://` | 49 passed, 0 failed, 0 skipped, 0 monitor problems |
| `--base` checkout | the clean checkout at `db88679`, loopback HTTP root, plus `file://` | 22 passed, 0 failed, 0 skipped, 0 monitor problems |
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

`scripts/prepare_pages.py --output <new dir>` on the clean checkout at `db88679`:

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

All counts come from `data/coverage.json` and `data/catalog.json` at `db88679`. Definitions are in [docs/coverage.md](../coverage.md).

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
- **Local paths in the planning files.** The planning files written before implementation (`AGENTS.md` and `docs/implementation/02`, `04`, `05`) name the reference by its local path, `/home/fridrichmethod/data/repos/deemo`. They are in the base commit `828da4a`, so editing the tip would not remove the path from public history; they are left as written.
- **Large data files.** `data/catalog.json` is 40.2 MB and `data/catalog.js` 29.3 MB; the pages load the script only. If a later payload exceeds the budget, `prepare_pages.py` leaves `catalog.json` out first and records it. The archive page's manifest link then has no target on the site, but the repository copy remains.
- **Live sources were not rechecked at the reviewed head.** The offline `--verify` checks and the per-source snapshots are the evidence. `scripts/check_sources.py --report` is the manual, read-only way to compare live metadata later.
- **Third-party artwork.** It is not covered by the code license. Attribution and source-specific notes are in `NOTICE` and the catalog.

## Release hold

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
