# Multiagent execution and Git workflow

This is the concrete “ultracode” orchestration contract. It works with Codex or Claude and does not depend on a named command. Use at most the available concurrency; this plan assumes **one coordinator plus three workers**.

## Ownership and waves

The coordinator serializes repository initialization, worktree creation, commits in its own checkout, integration, generated outputs, remote mutations, and cleanup. Workers may commit only in their assigned worktree. A reviewer reads a separate detached worktree. No two write-capable agents share a checkout even when their file lists differ.

| Wave | Coordinator | Worker 1 | Worker 2 | Worker 3 |
| --- | --- | --- | --- | --- |
| 0 | Inspect, bootstrap, contracts, fixture and common fetch helpers | Read-only source follow-up | Read-only UI reference audit | Read-only contract review |
| 1 | Maintain contract and review completed units | Wiki adapters + manifests/assets | Artist/official/archive adapters + manifests/assets | UI/templates/i18n + source-independent fixture |
| 2 | Integrate reviewed adapter/UI branches | Catalog/preview/coverage implementation | CI and packaging/release hold | Bilingual source/attribution docs and mapping audit |
| 3 | Build final corpus and resolve integration | Read-only code reviewer | Read-only data/coverage reviewer | Read-only browser/visual reviewer |
| 4 | Merge main, publish repository, verify remote, clean task state | Scoped fixes only if assigned a new worktree | Verification as assigned | Verification as assigned |

Workers finish and relinquish ownership before reassignment. A task may have several small acceptance units, but avoid gratuitous WIP commits. Every common-interface change is proposed to the coordinator first, committed once, and then explicitly merged into dependent worktrees while those workers are paused. Never silently change schema during concurrent acquisition.

Only the coordinator generates or edits merged `data/catalog.{json,js}`, `data/thumbs.json`, `assets/thumbs/**`, `index.html`, final coverage reports, and release evidence. Workers test against temporary outputs/fixtures. Source workers own disjoint family paths and manifests, and never rebuild the shared catalog in the coordinator checkout.

## Bootstrap: coordinator only

Reinspect the workspace; this packet's initial non-Git state may have changed. Check `git status`, `git worktree list`, active agent ownership, and current diffs before mutations. If it is still non-Git, the user-authorized implementation can initialize `main`, create an appropriate `.gitignore`, and deliberately stage this packet, instruction files, and ignore file for an initial planning commit. Do not run `git init` inside an unexpected parent repository.

Use a unique writable worktree root, for example `/tmp/dynamix-implementation-20261010-<run-id>`. Worktrees are transient; commit and integrate completed work before cleanup or machine restart. Record the actual path outside tracked product data. Reference path stays `/home/fridrichmethod/data/repos/deemo`, not `../deemo` relative to a `/tmp` worktree.

Example commands after the initial commit, with `DYNAMIX_WT_ROOT` set to that new, unused directory:

```sh
git worktree add -b impl/integration "$DYNAMIX_WT_ROOT/integration" main
# The coordinator now writes only in this integration checkout.
# Commit the agreed schema/fixture/helpers here before branching workers.
git -C "$DYNAMIX_WT_ROOT/integration" rev-parse HEAD
git worktree add -b impl/wikis "$DYNAMIX_WT_ROOT/wikis" impl/integration
git worktree add -b impl/primary-sources "$DYNAMIX_WT_ROOT/primary-sources" impl/integration
git worktree add -b impl/ui "$DYNAMIX_WT_ROOT/ui" impl/integration
```

These are sequenced operations. Do not reuse branch names or directories owned by another run. Each worker receives exact base SHA, directory, branch, path ownership and acceptance criteria before writing. Follow current sandbox permissions for worktree paths; do not create sibling directories outside writable roots without authorization.

## Review and integration

Each completed task returns: base/head SHAs; change summary; named files; tests and outcomes; source counts/failures where relevant; known limitations; and its final clean/dirty status. The coordinator verifies the branch diff is limited to the assignment. Review before merge; fix blocking findings on that branch or a new scoped branch without overwriting another agent's changes.

Integrate reviewed topics into `impl/integration` with deliberate merges. The policy is **small atomic task commits plus a small number of meaningful merge commits**, not rewriting another agent's or user's history. Do not create WIP history that needs later destructive cleanup. This preserves ancestry for safe branch deletion.

```sh
git -C "$DYNAMIX_WT_ROOT/integration" merge --no-ff impl/wikis -m "Merge wiki acquisition"
git -C "$DYNAMIX_WT_ROOT/integration" merge --no-ff impl/primary-sources -m "Merge primary source acquisition"
git -C "$DYNAMIX_WT_ROOT/integration" merge --no-ff impl/ui -m "Merge static gallery interface"
```

Run relevant integrated checks after each merge. If a conflict appears, stop that merge sequence; inspect both changes and resolve deliberately. Do not choose blanket ours/theirs. New wave-2 branches start from the reviewed integration head and follow the same rule. Avoid squash/cherry-pick integration for this task, since it loses branch ancestry and complicates the requested safe cleanup.

Create the review checkout at the committed integration SHA using `git worktree add --detach`. Reviewers do not fix files there. If the implementation changes after review, record the new SHA and rerun affected review/checks. A full final run must cover the actual final implementation tree.

## Main integration and publication

Before advancing `main`, stop workers, verify a clean integration checkout, finish the acceptance gates, and commit generated data/docs. Check main for concurrent changes. If main advanced, merge its latest state into integration and repeat affected review/validation; never reset main to an old recorded base.

From the original checkout, only when it is clean and on `main`:

```sh
git merge --ff-only impl/integration
git status --short --branch
git log --graph --oneline --decorate -20
```

If `--ff-only` fails, diagnose ancestry; do not force it. Publish and verify as specified in [06-validation-and-release.md](06-validation-and-release.md). No intermediate branches need to be pushed for normal local multiagent implementation. The main push is authorized by the implementation request; it does not authorize releasing the site.

## Cleanup after successful integration and remote verification

List task-owned worktrees and branches explicitly. Verify each is clean, no live agent uses it, and each branch tip is an ancestor of final main. Remove ignored caches/virtual environments only if they prevent ordinary removal and are verified task outputs; do not escalate automatically to force removal. Preserve unexpected files and report them.

Example for one completed topic:

```sh
git -C "$DYNAMIX_WT_ROOT/wikis" status --porcelain
git merge-base --is-ancestor impl/wikis main
git worktree remove "$DYNAMIX_WT_ROOT/wikis"
git branch -d impl/wikis
```

Repeat with the explicit task inventory, including review worktrees, then remove the clean integration worktree and `git branch -d impl/integration`. Use `rmdir` for the now-empty task container if desired. Do not use `rm -rf`, `git clean -fdx`, `git branch -D`, resets, bulk globs, or deletion of unrelated worktrees. If safe cleanup fails, keep the work and report the specific reason.

If this run intentionally created remote topic branches, verify that each is this run's branch and fully merged before deleting it with the canonical `git push origin --delete <exact-branch>` command. Never delete preexisting branches. Final `git worktree list`, local branch list, remote branch list, and status should show no intermediate state owned by this task. Deleting branch names does not erase reachable commits; that is expected.

Command behavior is documented in the official [Git worktree manual](https://git-scm.com/docs/git-worktree) and [Git merge manual](https://git-scm.com/docs/git-merge). Use ordinary ancestry-preserving operations to keep both history and cleanup reviewable.
