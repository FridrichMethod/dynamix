# UI fixture

Synthetic mini site data in the frozen catalog shape (schema version 1, `docs/implementation/09-frozen-contracts.md`),
used by `tests/browser_smoke.py --fixture`, `tests/test_render_slideshow.py` and `tests/test_layout_fixture.py`.
Every image is flat geometry drawn by `build_fixture.py`; titles, people and URLs (example.org / example.com) are
fictional. Nothing here is evidence about real Dynamix artwork, artists or sources.

- `catalog.json` / `catalog.js`: 12 unique files from 14 source records, 2 failures, 6 sources.
- `catalog-empty.json` / `catalog-empty.js`: the same envelope with no sources, files or failures.
- `assets/public/...`: originals; `assets/thumbs/...`: WebP previews (long edge 480, quality 75, method 6).

Regenerate with `python -I tests/fixtures/ui/build_fixture.py`; `--check` exits 1 when the committed files differ.
