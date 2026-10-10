# Contract fixture (schema version 1)

A tiny repository-shaped tree for offline tests and for builders run with `--root tests/fixtures/contract`.
**Everything here is synthetic**: images are generated patterns, post IDs and URLs are placeholders, and no record
is evidence about real Dynamix artwork, credits or availability. `make_fixture.py` writes `data/` and `assets/`
deterministically; `tests/test_fixtures_contract.py` regenerates into a temporary directory and compares bytes.

```sh
python -I tests/fixtures/contract/make_fixture.py            # rewrite in place after a contract change
python -I tests/fixtures/contract/make_fixture.py --output DIR
```

| Scenario | Where |
| --- | --- |
| (a) identical bytes from `wikis:miraheze` and `wikis:fandom`; Fandom `source_reported` SHA-1/bytes differ (`sha1_match`/`bytes_match` false) | `data/sources/wikis.json` (both "Cosmic Pulse" assets), `assets/public/wikis/{miraheze,fandom}/Cosmic-Pulse--d3ae3e8c82b4.png`; same-source duplicate candidate `File:Cosmic_Pulse_copy.png` (`duplicate_bytes`) in `data/discovery/miraheze.json` |
| (b) distinct crop sharing `variant_group` `song:cosmic-pulse/cover` | Miraheze "Cosmic Pulse (crop)" |
| (c) artist original, larger (1200 px, slideshow-eligible), `artist_post`, `confirmed_primary`, `master_status` unverified | `data/sources/artists.json`, `data/discovery/x-ariiol_.json` (source `artists:x:ariiol_`) |
| (d) excluded `universe_only` (game `dynamix_universe`) and `animated_capture` GIF; artist `not_dynamix` exclusion | `data/discovery/miraheze.json`, `data/discovery/x-ariiol_.json` |
| (e) unknown illustrator (`[]`), `unmapped` | Miraheze "Dynamix background 03" |
| (f) failed download: Failure record + candidate `failed`; Fandom discovery incomplete with continuation | `data/sources/wikis.json` `failures`, `data/discovery/fandom.json` |
| (g) reference PDF in `archives` (archive tier, width/height null) | `data/sources/archives.json`, `assets/public/archives/hkstp/` |
| (h) WEBP served behind a `.png` URL (`format` WEBP, ext `webp`, `format_match` false, `transformed` true) | Fandom "Moonlight of Sand Castle" |
| (i) non-ASCII title (`交差して歪む過去と未来`, slug `file`) and a title containing `</script><script>` | Miraheze JPEG; artist asset title |
| (j) song index with a Universe-only song (`in_original_game` false) and an in-game song without artwork | `data/sources/song-index.json` |
| also | a `pending` candidate; `official` family with no assets (`fetched_at` null, `metadata_only`/`manual_access` sources) |
