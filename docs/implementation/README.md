# Implementation packet

Prepared **2026-10-10** for a later Codex/Claude multiagent implementation. Three research agents independently investigated official/artist sources, community sources, and the DEEMO reference; the coordinator consolidated the plan. Only the coordinator wrote this packet.

## Read order

| Document | Purpose |
| --- | --- |
| [01-plan.md](01-plan.md) | Scope, milestones, dependency order, completion criteria |
| [02-reference-audit.md](02-reference-audit.md) | Concrete DEEMO parity/reuse map and adaptations |
| [03-source-research.md](03-source-research.md) | Dated source inventory, evidence, resolution caveats, next probes |
| [04-multiagent-workflow.md](04-multiagent-workflow.md) | Worktrees, branches, serialized integration, safe cleanup |
| [05-agent-handoffs.md](05-agent-handoffs.md) | Copy-ready coordinator and worker assignments |
| [06-validation-and-release.md](06-validation-and-release.md) | Review gates, exact-head verification, GitHub push, release hold |
| [07-data-contracts.md](07-data-contracts.md) | Source/candidate/catalog contracts and deterministic behavior |
| [08-status-and-decisions.md](08-status-and-decisions.md) | Initial state, assumptions, evidence and execution log templates |

## Start implementation

Give the next coordinator this instruction:

> Implement this repository using `AGENTS.md` and `docs/implementation/README.md`. Follow the multiagent assignments, with one writer per worktree and a single integrator. Start by rechecking the current state and freezing the data contracts. Match the inspected DEEMO reference, acquire and account for all discoverable in-scope artwork from the documented public sources, and continue source discovery beyond the seed list. Review and test each task and the integrated head. Merge to main only after acceptance, push the public GitHub repository, remove clean task worktrees and merged intermediate branches, and stop in READY_FOR_RELEASE with Pages, tags, and Releases still held. Record actual outcomes and unresolved source gaps; do not claim universal artwork completeness.

The user requested an “ultracode” workflow. No installed skill or executable contract for that name was identified in the inspected local skill/command paths. Here it means the explicit research → contracts → parallel implementation → independent review → integration → validation → publication → cleanup workflow in this packet. Use the host's available agent orchestration; do not invent an `/ultracode` command or require a paid/unavailable plugin.

## Current deliverable versus future work

Current deliverable: Markdown instructions and source research. At the beginning of this planning session, `dynamix/` was empty and was not a Git repository. This session does not implement fetchers, download a production collection, initialize Git, create a GitHub repository, or deploy a website.

Future target: a reviewed implementation on `main`, public repository pushed to the verified destination, green checks and a prepared site artifact, no leftover task branches/worktrees, and a documented release hold. `READY_FOR_RELEASE` is a future acceptance state, not this packet's current status.
