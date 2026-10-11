# Agent assignments and handoff prompts

Read `AGENTS.md` and [workflow](04-multiagent-workflow.md) first. Every assignment needs an actual worktree path, branch, base SHA and exclusive file ownership. Replace bracketed assignment values before dispatch. The coordinator remains the only remote writer and integrator. Use the strongest available reasoning for contract design, subtle provenance issues and independent review; do not require model names unavailable to the receiving host.

## Common worker prompt

> Work only in `[worktree]`, branch `[branch]`, based on `[base SHA]`. Read `AGENTS.md`, `docs/implementation/01-plan.md`, `07-data-contracts.md` and your assignment below. Inspect status/diffs before writing. Own only `[paths]`; propose cross-boundary changes to the coordinator. Preserve user work and the read-only DEEMO reference (a local clone of `https://github.com/FridrichMethod/deemo` at `b14bf0d5f5a05e5382c319f3fd5019a4936820b8`). Implement the bounded assignment, add meaningful tests, inspect your diff, and commit named files in small coherent units. Do not push, open PRs, deploy, release, rewrite history, edit another checkout, or regenerate coordinator-owned outputs. Return base/head SHAs, changed paths, exact tests/results, limitations and clean/dirty status. If a dependency is missing, continue independent work and identify the precise contract needed.

## Coordinator — bootstrap and integration

Own root instructions, schema/contracts/shared helper bootstrap, integration checkout, generated outputs, final root docs, ownership ledger and remote actions. Follow [the plan](01-plan.md) through publication and cleanup, keeping the release hold intact.

Before spawning writers:

1. Confirm original Dynamix-only scope, exact reference SHA, current status, active agents and authenticated GitHub identity. Record intended `FridrichMethod/dynamix` and any existing remote state.
2. Establish the initial commit if needed; create a separate integration worktree. Preserve software notices and copy only a reviewed subset of reference code.
3. Freeze source IDs, schemas, shared downloader API, CLI semantics, catalog fixture and UI field shapes. Include a tiny fixture for duplicate bytes, distinct crops, mixed-game candidates, unknown credit and failed download.
4. Assign disjoint paths and source families; source workers can use the common downloader without independently editing it. Shared-helper fixes go through the coordinator or an explicitly reassigned sole owner.
5. Integrate reviewed branches in dependency order. Build actual catalog/previews once family manifests are integrated; keep generated-output ownership throughout.
6. Rotate independent reviewers into free slots; fix findings; publish only after all release-readiness gates. Record limitations and final cleanup evidence.

## A — Wiki discovery/acquisition

Branch: `impl/wikis`. Own `scripts/fetch_wikis.py`, wiki-specific modules, `tests/test_fetch_wikis.py`, `tests/fixtures/wikis/**`, `data/sources/wikis.json`, `data/discovery/{miraheze,fandom,atwiki-ja,rhythmgame-ja}.json`, and `assets/public/wikis/**`. Other names must be registered with the coordinator.

Implement Miraheze and Fandom enumeration using the verified endpoints; follow pagination; preserve source-reported versus actual metadata; resolve game and media roles from templates/usage; retain both migration histories. Add Japanese wiki metadata/attachments where useful and publicly accessible. Return a proposed reconciled song index to the coordinator rather than overwriting its shared index.

Tests must cover continuation, incomplete enumeration, removed/reuploaded images, API hash mismatch, CDN WEBP behind PNG suffix, Unicode filenames/case collisions, duplicate cross-wiki bytes, same-size different bytes, interrupted resume, and Universe-only/screenshot exclusions. Fixtures should include Cosmic Pulse attribution, `(XD)_D.jpg` semantic classification and false “HQ” assumptions without requiring live internet.

Done: metadata-only inventory works for both live English APIs, representative real downloads validate, accessible in-scope files are acquired or explicitly accounted for, offline verification passes, and missing mappings/access failures are reported. No target based on the total wiki image count.

## B — Artist, official and historical acquisition

Branch: `impl/primary-sources`. Own `scripts/fetch_{artists,official,archives}.py`, corresponding adapter modules/tests/fixtures, `data/sources/{artists,official,archives}.json`, their registered discovery files, and `assets/public/{artists,official,archives}/**`.

Prioritize creator-original art over collecting many store screenshots. Start with ariiol/Cosmic Pulse and Sand Suna/MassacrE; use the reconciled illustrator index to expand systematically. Implement narrow, explicit per-platform seeds and metadata parsing with bounded access, not unbounded whole-platform scraping. Support provenance-only/blocked records and manual public URL imports when a platform cannot be enumerated.

Handle multipage posts, compositions, official social permalinks, Apple/Google screenshot metadata, historical press/PDF references and composer-owned leads. Confirm each candidate belongs to original Dynamix and identify whether music cover art actually matches game artwork. Keep unavailable originals, paid items, rejected submissions and fanart out of successful artwork counts.

Done: source registry covers all documented source families and discovered creator seeds; successful assets preserve bytes; every seed has an explicit disposition; tests cover variant pages, blocked access, misleading URL dimensions, unrelated portfolio entries and rejected submissions; no asserted master resolution without evidence.

## C — Static frontend and localization

Branch: `impl/ui`. Own `archive.html`, `templates/slideshow.html`, `src/**`, approved `assets/site/**`, `site.webmanifest`, `browserconfig.xml`, and frontend-specific tests/fixtures agreed with the coordinator. The coordinator generates root `index.html`; test the template against the frozen fixture in a temporary output directory.

Port the reference archive/slideshow layout and interactions. Replace game branding, globals, localStorage keys, metadata and repository URLs consistently. Preserve responsive gallery/filter/sort/pagination, viewer original download/provenance/variants, slideshow deep links, autoplay/manual navigation, screenshot overlay and both languages. Use preview thumbnails in the grid and original bytes in viewer/downloads. Keep information hierarchy and spacing comparable to reference screenshots.

Use safe text rendering and local resources; system fonts and attributable project icons are acceptable. A screenshot button must function without copying the unrelated audio/font/media bundle. If reference JavaScript is reused, include its applicable license files through the coordinator. Keep unknown and low-resolution labels explicit without overwhelming browsing.

Done: desktop/mobile, keyboard, English/Chinese, URL language, stable asset links, `file://`, and `/dynamix/` subpath work; no external browsing requests or console errors; preview failure has a bounded fallback; both empty and populated fixtures render correctly; provenance strings cannot inject HTML.

## D — Catalog, previews and coverage

Branch: `impl/catalog`. Starts from the integration head containing the frozen contracts and reviewed wave-1 work. Own `scripts/build_{catalog,thumbnails,coverage}.py`, corresponding test modules/fixtures, and any explicitly reassigned schema validation utilities. Implement generators, but leave production generated outputs to the coordinator.

Adapt reference exact-SHA-256 dedup and stable ordering; remove legacy requirements. Aggregate every source/provenance record, distinguish variant groups, retain removed/superseded assets, generate an offline catalog and slideshow. Measure actual media, verify paths/hashes/decoding, implement deterministic previews and explicit source/song coverage accounting. Public docs must not equate record/image/song counts.

Done: offline fixture and production-manifest validation pass; rebuilding is deterministic; stale output is detected; distinct-byte variants survive; thumbnail generation/prune cannot alter originals; missing/incomplete sources remain visible; catalog and coverage numbers reconcile.

## E — CI, packaging and release hold

Branch: `impl/ci`. Own `.github/workflows/**`, `scripts/prepare_pages.py`, `scripts/check_sources.py`, CI/packaging/source-diff tests, and `.gitattributes` after coordinator assignment. Requirements changes need coordination with the catalog/frontend owners.

Implement offline unit/build checks and browser checks; use established project dependencies and pinned action revisions. Do not carry DEEMO legacy verification into Dynamix. Stage only tracked public files, reject symlinks/unsafe paths, exclude `docs/implementation/**`, and enforce byte/file budgets. Test that staged site preserves original hashes and that stale data fails.

Implement manual read-only source comparison and a guarded manual-only Pages workflow as specified in [validation/release](06-validation-and-release.md). Normal pushes may validate/package but cannot deploy or create source-update branches. Do not enable scheduled write automation. The Pages workflow has no effective deployment authorization until the later release instruction sets the exact approved SHA.

Done: passing/failing fixture checks prove test failures propagate; deployment guard is closed by default; push/PR events cannot deploy; publication allowlist and size tests pass; manual source checks report incomplete enumeration without destructive removal conclusions.

## F — Documentation, attribution and mapping audit

Branch: `impl/docs`. Own public `docs/sources-*.md`, Chinese counterparts, `licenses/README*`, and a proposed update to source-registry/mapping issues delivered to the coordinator. Root README/NOTICE/LICENSE changes are proposed patches or explicitly reassigned ownership, not concurrent edits.

Document actual per-source behavior, commands, source-access gaps, observed quality, mapping evidence and media rights separately from code. Produce English and Simplified Chinese versions. Cross-check samples from each source family, especially illustrator/composer confusion, shared-game art, resolutions, duplicates and missing songs. Do not simply copy DEEMO image counts, claims of completeness, rights holders or live-site URLs.

Done: documentation reflects implemented flags and measured counts; all reused software/media have appropriate attribution entries; no placeholders claim nonexistent downloads/deployments; metadata disagreements are enumerated for review.

## Independent reviewers — read only

Use a detached worktree at `[review SHA]`; report path/line, severity, concrete reproduction and expected behavior. Read the diff against the baseline and inspect the final combined tree. Never silently fix files in the review checkout.

- **Code reviewer:** downloader boundaries, retries/resume, shared state/concurrency, path/redirect safety, untrusted text rendering, deterministic generation, tests that can falsely pass, workflow token privileges and release hold.
- **Data reviewer:** original-game membership, role classification, exact source bytes versus transformed responses, artist/composer distinctions, crop/version preservation, source completeness accounting, attribution, high-resolution claims and count denominators.
- **Browser reviewer:** desktop/mobile reference parity, focus/keyboard/scroll, i18n and URL state, offline/local-only loading, deployment subpath, original download integrity, thumbnail payload and screenshot overlay.

Block integration for data loss, wrong-game bulk inclusion, fabricated provenance, code execution/injection, unsafe publication, broken core browsing or silent test failures. Treat minor style preferences as nonblocking. Every accepted finding needs a fix commit and a recheck; record unresolved limitations explicitly.

## Worker return template

```text
Task / agent / host:
Worktree / branch / base SHA / head SHA:
Owned paths and changed files:
Implemented behavior:
Evidence and source counts (if relevant):
Tests: exact commands, result, duration:
Not run: reason:
Review findings addressed / outstanding:
Dependencies or contract changes requested:
Known limitations:
Working tree status:
Ready for coordinator review: yes/no
```

The coordinator records agent/runtime, approximate effort if available, retries and outcome per task. This is execution evidence, not a reason to weaken acceptance or fabricate a model/token metric that the host does not expose.
