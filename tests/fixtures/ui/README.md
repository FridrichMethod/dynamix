# UI fixture

Synthetic mini site data in the frozen catalog shape (schema version 1, `docs/implementation/09-frozen-contracts.md`),
used by `tests/browser_smoke.py --fixture`, `tests/test_render_slideshow.py` and `tests/test_layout_fixture.py`.
Every image is flat geometry drawn by `build_fixture.py`; titles, people and URLs (example.org / example.com) are
fictional. Nothing here is evidence about real Dynamix artwork, artists or sources.

- `catalog.json` / `catalog.js`: 14 unique files from 16 source records, 2 failures, 7 sources. Two slides carry the most
  text a slide can (a song title over a file name that does not name it, two illustrators, a composer, five packs/events
  and a long source name), one in Latin and one in CJK script, for the short-screen layout checks.
  Provenance records carry their own `song_ids` and `collections` (the 2026-10-10 contract rule), the catalog
  entry lists their ordered union, and song IDs come from `scripts/lib` (a non-ASCII title gets the title-hash
  suffix, `song:file-568a196c`). `tests/test_layout_fixture.py` validates both catalogs with `scripts/lib`.
  `catalog.js` uses the frozen reference encoding (compact JSON, `<` as `\u003c`, U+2028/U+2029 escaped).
- `catalog-empty.json` / `catalog-empty.js`: the same envelope with no sources, files or failures.
- `assets/public/...`: originals; `assets/thumbs/...`: WebP previews (long edge 480, quality 75, method 6).

Regenerate with `python -I tests/fixtures/ui/build_fixture.py`; `--check` exits 1 when the committed files differ.
