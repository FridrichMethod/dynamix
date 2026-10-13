# Validation, GitHub publication and release hold

The requested final implementation state is **READY_FOR_RELEASE**: reviewed main pushed to GitHub; tests and artifact preparation successful; task branches/worktrees cleaned; release actions still held. This planning package itself is not in that state.

## Gate 1 — reproducible acquisition and source accounting

- Every registered source has a dated disposition, and every discovered in-scope candidate has a reconciled outcome. An incomplete API page sequence is visible and cannot be called complete.
- Two English wiki inventories are unioned with independent provenance. Original Dynamix scope is positively established. Artist originals take priority, with blocked/unavailable sources reported honestly.
- Actual bytes, sniffed format, dimensions, length and SHA-256 match stored files. Source-reported mismatches remain visible; exact duplicate files retain all source records. Distinct crops/revisions stay archived.
- Coverage separately reports songs, artwork assets, references/screenshots, source records, unique bytes, variants, unresolved mapping, unknown credits, low-resolution-only songs and missing files.
- Offline tests use committed fixtures. Live-source acquisition evidence is supplementary and should not make ordinary CI depend on a wiki or social platform being online.

## Gate 2 — code, browser and visual acceptance

Use meaningful regression cases from [data contracts](07-data-contracts.md) and [agent handoffs](05-agent-handoffs.md). Compare reference and Dynamix behavior at 1440×900 and 375×812. Validate HTTP, `file://`, and an HTTP `/dynamix/` base path. Verify both languages, non-ASCII song names, empty results, pagination, keyboard/focus, viewer/slideshow links, original download hashes, screenshot UI, thumbnail fallback and zero unplanned remote requests.

Record initial grid transfer bytes at the same viewport. Target the reference's <2 MB image transfer for a normal initial desktop grid using previews; separate browser overhead and original-viewer loads. Avoid interpreting a missing/empty gallery as a performance success. Keep the measured value and browser/version with the result.

Every worker's focused tests must pass; independent reviewers then inspect the integrated head. Resolve all blocking findings and record nonblocking limitations. Tests added by the implementer do not replace independent review of acquisition classifications and source evidence.

## Gate 3 — full offline run on committed implementation

The following are target commands; align exact flags with implemented CLIs during bootstrap. Do not report them as run in this planning session. Reuse installed project dependencies or create a local venv; pin a tested encoder version for deterministic preview bytes.

```sh
.venv/bin/python -I scripts/fetch_wikis.py --verify
.venv/bin/python -I scripts/fetch_artists.py --verify
.venv/bin/python -I scripts/fetch_official.py --verify
.venv/bin/python -I scripts/fetch_archives.py --verify
.venv/bin/python -I scripts/build_thumbnails.py --check
.venv/bin/python -I scripts/build_catalog.py --verify --check
.venv/bin/python -I scripts/build_coverage.py --check
git diff --check
```

Run every unit test with a nonzero aggregate exit on failure. This Python runner also rejects accidentally discovering no tests:

```sh
.venv/bin/python -I - <<'PY'
from pathlib import Path
import subprocess
import sys
tests = sorted(Path('tests').glob('test_*.py'))
if not tests:
    raise SystemExit('No tests discovered')
failed = []
for test in tests:
    if subprocess.run([sys.executable, '-I', str(test)]).returncode:
        failed.append(str(test))
if failed:
    raise SystemExit('Failed: ' + ', '.join(failed))
PY
```

Serve the checkout on loopback using an owned process, then run the adapted reference browser suite with the installed browser:

```sh
.venv/bin/python -I tests/browser_smoke.py --base http://127.0.0.1:8765 --browser /usr/bin/google-chrome
```

Resolve the actual Chrome path; report browser availability rather than inventing success. Add explicit offline and subpath cases to that suite. Stop only the server process created by this task. A full thumbnail re-encode verification is useful once on the release candidate; repeated full-corpus checks after no relevant changes are unnecessary.

## Gate 4 — packaging and storage

Stage Git-tracked public files into a new output directory outside the checkout. Exclude `.git`, scripts, tests, environment/cache data, workflows, raw fetch logs, and `docs/implementation/**`. Include only actual public source documentation, catalog/assets, local UI, page entry points, browser metadata and attribution. Reject symlinks/path traversal and fail if required files are missing. Inspect the staged artifact and run browser checks against it as well as the source tree.

Preserve all published originals byte-for-byte. Enforce **950,000,000 bytes** total staged site and inspect every new Git blob before commit; ordinary GitHub pushes reject files above **100 MiB**. GitHub's documented published Pages cap is 1 GB; measure originals plus previews plus metadata, not just the asset folder. ([GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits), [GitHub large files](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github))

If storage exceeds the selected delivery model, keep it a reported blocker with a concrete measured distribution proposal. Do not “fix” this by recompressing originals, dropping records, quietly buying LFS storage, or publishing release attachments while releases are held.

## Gate 5 — release hold design

*Superseded on 2026-10-11 for `pages.yml`: item 3 and the guard shown below describe the original approval-variable design. The owner decision at the end of this gate replaced it with release-triggered deployment.*

Use separate validation/artifact and deployment workflows:

1. `tests.yml`: push, pull request and reusable call; read-only content permissions; full offline and browser acceptance; no deployment or tag/release commands.
2. `prepare-site.yml` or a tests job: prepare/validate an allowlisted artifact. Uploading a CI artifact is allowed; do not call `configure-pages` or `deploy-pages` during ordinary pushes.
3. `pages.yml`: **manual dispatch only**. Require a full `release_sha` input; deployment job runs only when the dispatch targets `main`, that input equals `github.sha`, and repository variable `RELEASE_APPROVED_SHA` exactly equals the same SHA. The variable is unset during hold. Use tests on that same immutable checkout and minimum deploy permissions only in the deployment job.
4. Source updates: manual read-only report generation only during hold. No schedule that creates bot branches/PRs and no automatic metadata merges.

A proposed deployment guard (test its behavior; do not rely on the illustration alone):

```yaml
if: >-
  github.event_name == 'workflow_dispatch' &&
  github.ref == 'refs/heads/main' &&
  inputs.release_sha == github.sha &&
  vars.RELEASE_APPROVED_SHA == github.sha
```

Missing approval variables must fail closed. Test that push, PR, wrong branch, wrong SHA and unset variable cannot deploy. Do not set `RELEASE_APPROVED_SHA`, dispatch this workflow, create a tag, or publish a GitHub Release as part of this implementation. These steps await a later explicit release request. Ship draft release notes as a local/committed document if useful; do not create an actual GitHub Release during hold.

Inspect GitHub Pages settings before the first push to any existing repository: legacy branch-based Pages can deploy independently of workflow guards. A newly created repo should leave Pages unconfigured until release. If an existing repo has Pages deployment enabled, reconcile with the owner and hold requirement before pushing deployable content; never assume an absent YAML push trigger is sufficient. Do not change unrelated repository settings.

**Owner decision (2026-10-11, after release v1.0.0):** the approval-variable design above is replaced by a release-triggered one, the same in this repository and in DEEMO. Publishing a GitHub Release with a `vX.Y.Z` tag deploys that tag's commit, and a manual dispatch on such a tag redeploys it. Deployment still fails closed: the ref must be a `vX.Y.Z` tag, a release must not be a prerelease, the tag's commit must be on `main`, and the full tests workflow must pass on it before any artifact is built. Pushes, pull requests and schedules never deploy. The `github-pages` environment allows `main` and `v*` tags, and the `RELEASE_APPROVED_SHA` variable is no longer used. Creating a release remains an explicit owner action.

**Owner decisions (2026-10-11, after release v1.1.0):** a run deploys a `vX.Y.Z` tag only when the tag has a published GitHub Release that is not a prerelease. The approval and build jobs look the Release up with one read-only request to the GitHub API and fail closed on any other answer. This holds for a dispatch, for a release run and for a re-run of one, since a re-run keeps the event of the run it repeats. A `vX.Y.Z` tag has three numbers without leading zeros (`v1.10.0`, not `v01.1.0`). The deploy job compares the prerelease flag through `toJSON()`, so a missing flag does not count as `false`, and it runs only for the commit that the approval job approved. A repository ruleset ("Release tags v*") lets only repository admins create, move or delete `v*` tags; since a run takes `pages.yml` from the tag's commit, only an admin can choose the workflow that deploys a tag. For the same reason, a run on a tag before `v1.1.1` would use that tag's own `pages.yml`, which does not look the Release up.

**Owner decision (2026-10-12, before release v1.1.1):** the `github-pages` environment accepts deployments only from tags from `v1.1.1` on (tag rules `v1.1.[1-9]*`, `v1.[2-9]*`, `v1.[1-9][0-9]*`, `v[2-9]*` and `v[1-9][0-9]*`) and no longer from `main`. The deploy job of a run on an older tag is therefore refused at the environment, and every deployment through the `github-pages` environment comes from a tag that only an admin can create.

**Review fixes (2026-10-12, after release v1.1.1):** a re-run of the deploy job alone, or of any jobs without the approval job, deploys nothing, because the deploy job requires the approval job's record of the current attempt; the whole run is re-run instead. A last job, "Re-run check", then fails such a re-run with an error saying that it deployed nothing, so the run does not end green. The approval and build jobs also compare `github.sha` with the tag as origin has it (one `git ls-remote` without credentials) and fail closed when origin's tag names another commit, is missing or cannot be read, because `actions/checkout` refetches the tag at the run's commit, so the checkout cannot show a tag moved since the run started. Since a run takes `pages.yml` from the tag's commit, a run on `v1.1.1` uses that tag's own workflow, which has neither check.

## Gate 6 — main and GitHub

The intended target is **`FridrichMethod/dynamix`**, inferred from the matching DEEMO remote and the user's publication request. Verify identity and destination read-only before creating or pushing:

```sh
gh auth status
gh api user --jq .login
gh repo view FridrichMethod/dynamix --json nameWithOwner,url,isPrivate,defaultBranchRef
```

Do not print tokens. A failed `repo view` may indicate missing access/network rather than nonexistence; distinguish the reason before creation. If the repo already exists, inspect its heads/contents/settings and preserve unrelated work. Do not overwrite history, change visibility, or replace an existing remote merely to match this plan. If authenticated identity cannot publish to the intended owner, finish local validation and request the smallest required authentication/owner correction.

For a confirmed nonexistent destination, after reviewed local `main` is ready and clean:

```sh
gh repo create FridrichMethod/dynamix --public --source=. --remote=origin \
  --description "Original Dynamix artwork archive with source provenance"
git push -u origin main
```

These commands belong in the original `main` checkout, not a worker checkout. If the verified GitHub repository already exists, skip repository creation and use its verified remote. If the GitHub repository is absent but a correct local `origin` is already configured, create only the remote repository with `gh repo create FridrichMethod/dynamix --public --description "Original Dynamix artwork archive with source provenance"` (omit `--source` and `--remote`), then push through that existing origin. A local remote URL alone does not prove the GitHub repository exists.

Use ordinary fast-forward push; if remote main advanced, fetch/reconcile/review/retest rather than force-push. Creating a public repository publishes source and media; inspect the complete tracked tree, not only the Pages artifact. The [GitHub CLI repository creation manual](https://cli.github.com/manual/gh_repo_create) documents `--source`, `--remote`, and visibility options.

Verify local `git rev-parse HEAD` equals `git ls-remote origin refs/heads/main`, then inspect CI runs for that exact commit. Keep scheduled mutation workflows inactive, Pages unconfigured/guarded, approval SHA unset, and release/tag lists unchanged by this task. Existing remote tags/releases, if any, must be reported rather than deleted.

## Gate 7 — cleanup and final evidence

Follow the explicit ancestry and clean-worktree checks in [the workflow](04-multiagent-workflow.md). Remove only this task's intermediate branches/worktrees after integration and remote verification. Keep main and unrelated existing work. A dirty or unmerged task worktree is a concrete unfinished cleanup item, not a reason to force-delete work.

Final implementation report must contain:

- repository URL, final main SHA, reference SHA, source-research/acquisition date;
- tests and CI links tied to that SHA, independent reviews and resolved findings;
- measured source/song/artwork/unique-file/variant/failure counts and coverage gaps;
- artifact digest/size, original integrity checks and browser evidence;
- local/remote clean-state and task branch/worktree cleanup evidence;
- explicit release hold state and exact future release actions.

Commit a validation report for a known reviewed content SHA. If adding that report creates a later commit, run final CI on the new main SHA; do not claim a self-referential commit hash inside the same commit. Keep the final push/CI/cleanup receipt in the coordinator's final response or an external run log so writing the receipt does not dirty the release-ready checkout. If any code/data changes after review, repeat affected checks and review before calling it ready.

Suggested final status vocabulary: `PLANNING_COMPLETE`, `IMPLEMENTING`, `REVIEW_REQUIRED`, `READY_FOR_RELEASE`, `RELEASED`. Use `READY_FOR_RELEASE` only when all implementation gates pass; use the final report to name concrete blockers otherwise.
