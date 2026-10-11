# Dynamix project instructions

## Start here

Read `docs/implementation/README.md`, then the plan and your assigned handoff. This repository begins as a planning package; implement only when the user starts implementation. The intended reference is `https://github.com/FridrichMethod/deemo` at commit `b14bf0d5f5a05e5382c319f3fd5019a4936820b8` (use a local clone at that commit). Treat that repository as read-only.

## Product and data

- Match DEEMO's static archive/slideshow experience, local assets, English/Simplified Chinese UI, provenance viewer, offline browsing, thumbnails, and reproducible Python builds. Reuse suitable software with attribution; adapt game-specific data and branding.
- User-confirmed scope is original Dynamix by C4Cat only. Exclude Dynamix Universe-only assets. Keep game identity in discovery records; preserve evidence for shared artwork without treating a sequel-only file as original-game art.
- Preserve downloaded bytes. Do not AI-upscale, crop, remove watermarks, recompress, or call a site-original download an artist master without evidence. Derived previews live separately.
- Record exact source URLs, retrieval status, dimensions, detected format, byte length, hashes, attribution, and mapping evidence. Composer and illustrator are separate roles. Missing values remain unknown.
- Enumerate and account for accessible candidates; record blocked/failed/excluded sources and incomplete pagination. Never infer universal completeness from an empty/error response.
- Code licensing does not cover third-party artwork. Preserve accurate attribution and source-specific restrictions. Do not obtain paid/private material or bypass authentication and access controls.

## Collaboration and Git

- Inspect status and relevant diffs before editing; preserve other agents' and user changes.
- One write-capable agent per checkout. Use separate worktrees and branches; coordinator alone creates/removes worktrees, integrates branches, writes merged catalogs, and performs remote mutations.
- Declare worktree, branch, base SHA, owned paths, dependencies, and done conditions before each assignment. See `docs/implementation/04-multiagent-workflow.md` and `05-agent-handoffs.md`.
- Use small, reviewable commits; stage named paths. No AI attribution trailers. Do not reset, stash, amend, rebase, force-push, or discard existing work.
- Merge reviewed work into an integration branch, validate its exact head, then fast-forward `main`. Remove only clean task-owned worktrees and merged branches with ordinary Git commands. Preserve unrelated worktrees/branches.
- Keep project rules in `AGENTS.md`; `CLAUDE.md` is only an import stub.

## Verification and publication

- Follow `docs/implementation/06-validation-and-release.md`. Offline tests must not require the live source sites. Acquisition failures need visible status, not fabricated successful records.
- Run `pre-commit run --all-files` (ruff, ruff format, mypy, ESLint, actionlint, file hygiene) before committing; the tool versions and rules are in `pyproject.toml` and `.pre-commit-config.yaml`. No formatter, fixer or hook may rewrite byte-pinned or generated files (`assets/`, `data/`, `index.html`, `LICENSE`, `licenses/*.txt`, `src/vendor/`, `tests/fixtures/`), and ESLint never runs with `--fix` on the published `src/` files.
- Review both scientific-style data integrity (provenance, mapping, byte identity) and software correctness (escaping, paths, resumability, browser behavior, CI publication boundaries).
- The implementation request authorizes the coordinator's eventual GitHub creation/push for this project. Verify the destination and never overwrite an unrelated repository.
- **Release hold:** a push to `main` may run tests and prepare an artifact; it must not deploy Pages, create a version tag, publish a GitHub Release, or enable scheduled write automation. Releasing requires a later user instruction.
- No bulk copying of DEEMO's artwork, audio, fonts, history, manifests, or repository settings. Keep Apache/MIT attribution for software actually reused.
